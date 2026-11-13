# Architecture

A map of the `themis_shield` package for people reading the code. Each module
is marked **real** (wired into the running gate and tested), **partial** (real
parts plus a skeleton) or **skeleton** (a specification: types and
`NotImplementedError` stubs, not used by `themis serve`).

## Request path in one paragraph

`themis serve` (`web/__main__.py`) checks the configuration and environment
(`startup.py`), builds a `Router` from the TOML file (`integration.py`), runs
the detector canary (`canary.py`: every detector against fixed synthetic
documents with known answers) and serves the loopback web app (`web/app.py`). A chat request is checked for
security markings first (`detectors/markings.py`, `routing.py`); a
`RESTRICTED`-style banner keeps it local. Otherwise the text goes through the
detectors (`detectors/engine.py`: rule packs, PII-Tracer, two local LLM
finders, union, propagation, allow-list), the policy decides RELEASE, ASK or
BLOCK (`policy.py`), found values are replaced with opaque tokens
(`substitute.py`, `placeholders.py`) whose originals go into the encrypted
vault (`vault.py`), and the clean package is logged in the job register
(`register/`) where the Operator approves it (with an Operator credential
separate from the client's, though not proof of a second human; see
[below](#why-the-hooks-path-re-scans-and-the-web-path-does-not)). Only then,
after checking that no original value appears in the outgoing JSON, does the
request go to the cloud (`cloud/openai_compat.py`, or
`cloud/anthropic_messages.py` for the native Messages leg). The answer's
tokens are restored locally (`restore.py`).

## Module map

### Entry points and wiring

| Module | Status | What it does |
|---|---|---|
| `cli.py`, `__main__.py` | real | The `themis` command: `serve`, `hooks`, `register`, `runtime`, `profiles`, `kmr`, `fpc`, `tripwire`, `vet`, `--version`. |
| `startup.py` | real | Value-free startup checks and reason codes for `themis serve`, including the start-up detector canary (`CANARY_FAILED`). |
| `integration.py` | real | TOML configuration to `Router`: gate flags, markings, rule packs, finder prompt, institution profile, cloud, vault, register, runtime, `[hooks]`. |
| `paths.py` | real | Per-user default locations (`%LOCALAPPDATA%\themis-shield` or `~/.local/share/themis-shield`). |
| `profiles/` | real | Shipped profiles (markings, institution, catalogue, cloud) plus an external, deployment-owned directory. |
| `timezone.py` | real | Business timezone for approval deadlines and day-first display. |
| `config.py` | partial | Real: `Destination`, `UseCasePolicy`, `DeploymentProfile` and the enums. Skeleton: `load_local_config` and `validate_destination` raise `NotImplementedError` (configuration is loaded by `integration.py` instead). |
| `server.py` | skeleton | An earlier specification of the loopback front door (`LocalGateServer`, credential custody and transport protocols). Not imported by the running gate; `web/` is the front door. |
| `workbench/` | skeleton | Placeholder for an optional workbench; exposes no API. |

### Detection and decision

| Module | Status | What it does |
|---|---|---|
| `detectors/engine.py` | real | Tiered detection, batched so each local model loads once. Per-tier and (v0.3) per-stage timings. |
| `canary.py` | real, v0.3, on by default | The detector canary: known-answer checks against silent detector failure, at start-up, after every model load and periodically; a failure blocks cloud-bound work (see [Detector canary](#detector-canary)). |
| `detectors/rules.py` | real | Checksum, contact and credential rules in packs: `core`, `au` (TFN, Medicare, ABN, USI, phones), `intl` (E.164, IBAN). |
| `detectors/pii_tracer.py`, `labels.py` | real | PII-Tracer on CPU with exact offset mapping; label adapter. |
| `detectors/llm_finder.py` | real | Local LLM finders, prompt v1 and v2, structured JSON output. |
| `detectors/escalation.py` | real | Tier-selection rule with fixed reason codes. |
| `detectors/union.py`, `propagate.py`, `allowlist.py`, `profile_adapter.py` | real | Evidence union, propagation across tables, names and initials, a column-aware quasi-identifier pass for identifier-bearing tables (v0.2), institutional allow-list from a profile. |
| `detectors/markings.py`, `routing.py` | real | Security-marking gate and the marking-based route, applied before any PII decision. |
| `detectors/contracts.py`, `types.py` | real | Value-free evidence types and the label set. |
| `policy.py` | real | RELEASE / ASK / BLOCK from the evidence. `[policy] version` (`v0.2` default, `v0.1` kept as the negative control) and `quasi_id_ask`. |
| `detectors/settings.py` | real | `[detection] version`: `v0.2` (default) turns on the tier-1 fixes and the column-aware table pass; `v0.1` restores the evaluated v0.1 detection. |
| `detectors/typed.py`, `detectors/typed_tier.py` | real, off by default | v0.3 typed-decision tier (T1.5): multiple-choice questions about what the cheaper tiers found, answered in one forward pass; the policy decides what an answer may change (see [below](#v03-typed-decision-tier-t15)). |
| `normalise.py` | real | `detection_view`, the shared detection view (Unicode default-ignorable characters removed, NFKC per character, each view character mapped back to its source character) used by the rule packs, the marking gate and the injection signal. It meets the conservative offset-map contract (property-tested); the older `OffsetMap` skeleton was removed in 0.3.2. |

### Substitution, vault, restoration

| Module | Status | What it does |
|---|---|---|
| `placeholders.py` | real | Opaque tokens and conservative entity linking (see below). |
| `substitute.py` | real | Offset-safe replacement of original text, with value-free metadata. |
| `legend.py` | real | The placeholder legend (v0.4): one system line per placeholder sent, saying what kind of value it hides. `[legend] enabled` (off by default) and `sensitive_categories` (off: a sensitive statement is described generically). |
| `vault.py`, `protectors.py`, `dpapi.py` | real | Per-user SQLite token store, AES-256-GCM; the user key wrapped by DPAPI (Windows) or a passphrase (scrypt). |
| `restore.py` | real | One-pass local restoration of tokens in the answer, tolerant of small syntax damage. Exact only: a damaged identifier is never guessed (see [Restore safety](#restore-safety)). |
| `summary.py`, `history.py` | real | Summaries from the substituted text only; redacted conversation history. |

### Release, register and approval

| Module | Status | What it does |
|---|---|---|
| `router.py`, `router_flow.py` | real | Orchestration: register, markings, detection, substitution, approval, cloud, restoration. |
| `chat_boundary.py` | real | The accepted Chat Completions subset; only the configured cloud model can be addressed. |
| `cloud/` | real | Fixed-destination transport, OpenAI-compatible by default; `api = "anthropic-messages"` selects the native Messages leg (`anthropic_messages.py`), which translates the approved package at send time, checks the translated body (`system` included) and translates the reply back; no tools. API key read from the named environment variable at send time. |
| `register/sqlite_store.py`, `models.py`, `two_stage.py`, `reporting.py`, `cli.py`, `pilot.py` | real | The SQLite job register (append-only history tables), intake validation, two-stage approval, reports, the `themis register` CLI and the off-by-default automatic approval fixture. |
| `register/client.py` | partial | Real: the register contracts (`JobRecord`, `JobStatus`, approval validation, offline journal) used everywhere. Skeleton: `JobRegisterClient` protocol methods and `HttpRegisterClient`, a non-networking stub for a remote register. |
| `review.py`, `register/review_records.py` | real | Operator review of a held job, point by point: where each hold came from in the de-identified text, a plain-English reason, the Operator's reveal of one original, and a value-free, append-only record of how each point was settled (see [below](#operator-review-point-by-point)). |
| `handover.py`, `app_services.py` | real | Handover mode: a deterministic, re-scanned package for manual use; safe projections for the web app. |
| `vetting.py` | real, v0.3.1 | `themis vet`: a data release through the chat path's own steps (markings, detection, policy, catalogue, final sweep), derived documents under position-only names, a codebook vault per release, and the vetting record, which holds counts, not document text, and is checked against every protected value of six characters or more ([VETTING-RECORD.md](VETTING-RECORD.md)). |
| `ledger.py` | skeleton | Specification of an append-only evidence ledger per request, for a central register service (roadmap, Later). Today the register's append-only records and decision records carry the evidence (`tests/test_register_immutability.py`). |
| `audit.py` | skeleton | Specification of value-free audit rows. The register's audit table is the working audit trail. |
| `adapters/` | skeleton | Specifications for walking Anthropic Messages and OpenAI Responses payloads. The gate accepts Chat Completions only. |
| `clients.py` | real | Client profiles: Kilo Code as an API provider; Claude Code through managed hooks with its own login. |
| `register/gate_records.py` | real, v0.3 | Append-only decision records (model output and catalogue override kept apart, with the known-miss register version) and cloud trip-wire rounds, loops and reviews. Written only when a v0.3 catalogue is enabled. |

### v0.3 catalogues (all off by default)

| Module | Status | What it does |
|---|---|---|
| `catalogues/known_miss.py` | real, off by default | The known-miss register (rules, context shapes, keyed fingerprints of high-entropy strings) and the final sweep over outbound text. |
| `catalogues/false_positives.py` | real, off by default | The reviewed false-positive catalogue (`no_hold` and `allow` entries), scoped and expiring. |
| `catalogues/tripwire.py` | real, off by default | The optional cloud trip-wire in two independent modes: a correction loop before approval (at most 3 rounds) and a check of the approved package that never replaces anything; fixed instruction, strict JSON reply, fail closed, known-miss proposals. |
| `catalogues/gate.py` | real | Configuration (`[known_miss]`, `[false_positives]`, `[tripwire]`) and how the router applies them; a no-op when all are off. |
| `catalogues/cli.py`, `store.py` | real | `themis kmr`, `themis fpc`, `themis tripwire`; shared append-only SQLite storage helpers. |

### Web app and hooks

| Module | Status | What it does |
|---|---|---|
| `web/app.py`, `web/detection.py`, `web/reasons.py`, `web/static/` | real | Loopback FastAPI app: chat, job register view, Operator console, handover; OpenAI-compatible `/v1/chat/completions` and `/v1/models`; plain-English reason catalogue. |
| `web/hooks_api.py` | real | Authenticated hook endpoints on the same server. |
| `hooks/__main__.py`, `client.py`, `claude_code_hooks.py`, `protocol.py` | real | The `themis hooks <event>` command Claude Code runs: bounded input, loopback client, non-zero exit on any failure. |
| `hooks/service.py`, `summary_service.py` | real | Server-side hook state and policy (`HookPolicy`, from `[hooks]`). |

### Local model runtime

| Module | Status | What it does |
|---|---|---|
| `runtime/scheduler.py`, `lease.py`, `modes.py` | real | One resident model at a time, FIFO queue, per-user GPU lease. The scheduler's `load_listener` (v0.3) runs the finder canary inside the slot after each model load. |
| `runtime/catalogue.py`, `settings.py`, `planning.py`, `calibration.py` | real | Model catalogue, runtime settings, launch arguments, device-keyed calibration. |
| `runtime/children.py`, `resources.py`, `occupancy.py`, `backends.py` | real | Owned llama-server processes and the stale-process reaper, resource snapshots, memory held by attached runtimes, back-end status. |
| `inference/llamacpp.py`, `openai_local.py`, `json_output.py` | real | Owned llama-server lifecycle; loopback OpenAI-compatible client; strict JSON output. |
| `inference/strata.py`, `marian.py`, `ollama.py` | real, optional | Attach to a user-run Strata server; Marian launch planning from saved profiles; Ollama client. |
| `inference/laptop_llamacpp.py` | real | Compatibility client for an independently run llama-server. |
| `inference/dgx_spark.py` | skeleton | A target that reports itself unavailable until configured. |

## Detector canary

Every stage fails closed when it **crashes**: an exception becomes `Status.FAILED` and the policy blocks. A stage
that **succeeds with nothing** is invisible to that rule. Wrong or damaged weights, a tokenizer that does not match
its model, or a chat-template change in the inference engine all produce a valid, empty answer. (A real case: with
the Gemma 4 chat template, llama.cpp turns thinking on unless `chat_template_kwargs: {enable_thinking: false}` is
passed, so the first token of a prefill became a thought-channel marker; a finder affected like that returns a
well-formed empty list.) The canary (`canary.py`, `[canary]`, **on by default**) is a known-answer test for that.

**Documents and expectations.** Three fixed synthetic documents, every value invented: a phone number from the
mobile range set aside for fiction, an address under a domain reserved for documentation (RFC 2606), a TFN and a
Medicare number whose check digits the code computes from sequential test-style bodies, a person's name that
appears twice (once as "Ms" and the surname), an indirect health statement (no category word), and a clean control
sentence. Expectations are conservative minimums, never exact span sets: at least one finding with an expected
label **overlapping** the expected place.

| Detector | Expectations (fixed names, as recorded) |
|---|---|
| `rules` | `EMAIL`; with the `au` pack also `PHONE`, `TFN`, `MEDICARE`; `CONTROL_CLEAN` (no finding at all in the control sentence: the rules are deterministic) |
| `pii-tracer` | `PERSON`, `EMAIL` |
| `propagate` | `REPEATED_NAME`: given only the first mention of the name, the second mention of the surname |
| `gemma4-e4b`, `gemma4-26b-a4b` | `PERSON`, `SENSITIVE_STATEMENT` (any of the nine statement labels on the statement) |

A detector whose stage itself fails (exception, timeout, malformed or truncated output, a model that will not load)
fails `STAGE_OK` with the stage's fixed error code. Finder canaries cap the answer at 1,024 tokens, so a model that
wanders into a thinking channel fails quickly instead of running long.

**When it runs.**

- **Start-up** (`at_startup`): `themis serve` runs every detector's canary after the model checks and before
  serving. A failure refuses to start: `CANARY_FAILED`, naming the detector and the failed expectation names (for
  example `pii-tracer: PERSON, EMAIL`). `--skip-model-check` skips it; the canary is then pending (below).
- **After every model load** (`after_reload`): the scheduler calls its `load_listener` inside the slot, after the
  model is healthy and before any document, and the finder canary runs on that very target (no extra load). On an
  8 GB GPU both finders load for every request, so this adds one short finder call per model per request; it also
  warms the prompt cache with the fixed system prompt. The PII-Tracer is not loaded by the scheduler; it is
  covered by start-up and the periodic check.
- **Periodically** (`interval_minutes`, default 30; `every_documents`, default off): the tier-1 canary (rules,
  PII-Tracer, propagation) runs before the next cloud-bound document once due; a finder's runs inside its next
  residency. A detector whose last canary failed is due again at once, so recovery does not wait an interval.
- **On demand**: the web app's "Run the detector canary now" (`POST /api/canary/run`, under the detection lock).

**Degraded state.** While any detector's latest canary failed, cloud-bound work is blocked with
`DETECTOR_CANARY_FAILED`; while a detector has never been checked (start-up skipped), with
`DETECTOR_CANARY_PENDING`. The check sits in `Router._protect` after detection, so it covers the web and API path and
the Claude Code hooks path, and in the handover export. Local-only work never reaches it and still runs. A refused
job gets a `DETECTOR_CANARY_FAILED` (or `_PENDING`) event in its audit trail before `REFUSED`. The web app shows a
band with the failing detector and expectation names (`GET /api/canary`), and `/health` carries the canary state.
The degraded state ends when a canary covering the failed detector passes.

**Records.** Every run is appended to the register's `canary_runs` table (append-only like the rest of the
register's history): version, trigger (`startup`, `reload`, `periodic`, `documents`, `retry`, `pending`, `manual`,
`eval`), pass or fail, and per detector the status, failed expectation names, a fixed error code and the duration.
The register refuses free text in any of these fields. The canary only ever handles its own synthetic documents.

**Evaluation runs.** `eval/run_eval.py` runs the canary once at the start of every run (and every resumed
session), with the gate's own finder client, so it appears in neither `llm_calls.jsonl` nor a finder capture, and
writes the result into `config.json` (`"canary"`). A failure stops the run as an infrastructure fault before any case
is processed, unless `--allow-canary-failure` is given. With `--replay-finders` the finder canaries are recorded as
`skipped` (`replay`): replay never calls a model. The rules, PII-Tracer and propagation canaries still run.

**Per-stage timings.** `DetectionResult.stage_timings_ms` holds each stage's wall time (`rules`, `pii-tracer`,
`propagate-tier1`, `allowlist-tier1`, each finder, `<finder>:acquire` for the residency on the first document of
a finder tier, `union-final`, `propagate-final`, `allowlist-final`). The per-tier `timings_ms` is unchanged, and the
runner's `timings.jsonl` adds `stage_timings_ms` beside `timings_ms`. Stage timings are diagnostic only: no decision
reads them and they take no part in comparing two results.

**Limits.** The canary proves each detector still finds a known answer on a short synthetic text; it does not prove
recall on real documents, and a detector damaged in a way that spares the canary passes. It is a smoke alarm, not
an evaluation.

## Placeholder tokens

Every replaced value becomes a token `⟦C:hhhhhh⟧`: the delimiters `⟦` and
`⟧`, one class letter, a colon and six random lower-case hex digits
(`secrets.token_hex(3)`), unique within a conversation. The cloud sees, for
example, `⟦P:3fa9c1⟧ reported ⟦S:91b2e0⟧`. There are no counters and no
label names, so a token says only the coarse class:

| Class | Letter | Labels |
|---|---|---|
| Person | `P` | `PERSON` |
| Contact | `C` | `EMAIL`, `PHONE`, `URL_PERSONAL` |
| Address | `A` | `ADDRESS` |
| Identifier | `I` | every other identifier label (TFN, Medicare, ABN, USI, IBAN, student and internal ids, ...) |
| Date | `D` | `DATE_OF_BIRTH`, `OTHER_DATE` |
| Statement | `S` | the nine sensitive statement labels (health, disability, Indigenous status, sexuality, religion, criminal record, family violence, ethnicity, union membership) |
| Quasi-identifier | `Q` | `QUASI_ID` |

`SECRET` findings are never tokenised: they block the request. Linking is
conservative: a bare surname or initials reuse a full name's token only when
the match is unique. Literal text that restoration could read as a token is
escaped (doubled delimiters; two more colons after a class letter that stands
alone and is followed by a hex digit; two more backslashes in an odd run before
a delimiter), so a document cannot forge a token that restoration would
resolve. Every other character goes out as written, and restoration undoes
only those escapes.

## Why the hooks path re-scans and the web path does not

Both paths call the same `Router._protect` (detect, decide, substitute) and
both check, before anything leaves, that no original value appears in the
outgoing JSON (`verify_json`). They differ after that:

- **Hooks** (`hooks/service.py`) run detection again on their own output, up
  to three passes, and fail closed with `HOOK_NON_CONVERGENCE` if new
  findings keep appearing. Hook payloads are arbitrary JSON (prompts, tool
  input and tool output), a resubmitted draft may carry tokens the server
  issued earlier, and tool input and output inside an approved job are
  released without a person looking at each call. Re-scanning the exact text
  that will be released is the check that stands in for that person.
- **The web and API path** (`router_flow.py`) does not re-run the detectors
  on the substituted text. Its release needs the Operator to approve the
  clean package in the register, and that approval is bound to the SHA-256 of
  the exact outgoing request (`manifest_hash`): any change voids it. The
  Operator is a configured identity name acting with its own credential: every
  `/api/operator/*` endpoint (`web/app.py`) needs the Operator token
  (`operator-token`, created beside `gate-token`, same format and checks) or
  an Operator browser session opened with a one-use code that only that token
  can mint (`themis serve --open-operator`). The client's gate token and the
  chat session opened with `--open` are refused there (403
  `OPERATOR_CREDENTIAL_REQUIRED`), and the Operator token is refused on `/v1`,
  the hooks API and the app's writes; it only reads the app's views. So an API
  client or agent holding only the gate token cannot approve its own job. Both
  token files sit under the same OS user's profile, so a process running as
  that user can read both: this is a separation of credentials, not proof
  that a second human approved. The remaining automatic check is the
  exact-value one above.

The cost of the asymmetry is that a value the detectors missed the first time
is not caught by a second detector pass on the web path; it relies on the
Operator reading the clean package. Making the two paths match is an open
item. With `[known_miss]` enabled (v0.3), both paths also run the known-miss
final sweep on their outbound text: it catches repeats of misses a person has
already confirmed, not new ones.

## Operator review, point by point

Every document ends in one of three states, and ASK is only a waypoint on the way there: **stopped** (a local-only
marking, an Operator refusal or an Operator stop), **de-identified and released**, or **released as-is** (nothing
personal or sensitive was found). Each job reports its `end_state` in the register views (`null` while it is open).

**What the Operator sees.** For a held job, the Operator console shows the de-identified text (each content surface's
substituted text, joined by blank lines) with each hold trigger highlighted and numbered, and beside each a one-line
reason and its place, for example "paragraph 2, line 4: The detectors disagreed on whether this is a name." The
points come from the decision evidence (`Router._protect` builds them with `review.draft`): for each ASK reason, the
findings behind it (sensitive statements, quasi-identifiers, the tracer findings behind an empty-finder or few-persons
disagreement, typed-tier sentences, injection phrases), mapped to their position in the de-identified text. A reason
with no single place (unmatched finder values, a stale known-miss snapshot) becomes a whole-document point. The
reason text comes from a fixed catalogue (`point_reason` in `web/reasons.py`); the point's value is never part of it.
Placeholders the gate put in without a hold are listed as "settled by the gate".

**Where it is kept.** The register records the points value-free for each bound clean package
(`review_points`: fixed reason, label, kind, character positions, `gate` or `operator`), and each Operator decision
(`review_resolutions` and `review_resolution_actors`: action, category, positions, who, when). Like the rest of the
register these tables refuse UPDATE and DELETE. The de-identified text itself stays in the router's memory only
(`ReviewState`), with the conversation whose vault holds the originals; after a restart or once the conversation
expires, the requester sends again to rebuild it.

**Originals, carefully.** For a highlighted point only, the signed-in Operator may ask for the original text
(`POST /api/operator/{job}/points/{point}/reveal`), because some points can only be judged that way (is "Blake" a
person or a product?). The value is read from the vault for that request, returned in that response only (no-store),
and shown inside that point on the page; re-rendering the queue, reloading or leaving the page drops it. No register
row, audit row or log line is written by a reveal. Like every `/api/operator/*` route it needs the Operator
credential; the client token and the chat session are refused.

**Three actions per point.** (With the cloud trip-wire's `before_human` mode on, the cloud model has read the
de-identified package, open points included, before any person sees this review.)

- *Not personal: leave it.* The text stays as shown. A placeholder the gate already put in stays a placeholder: this
  never sends an original.
- *Replace it.* The Operator confirms or adjusts the span (it must still cover the point, keep whole placeholders and
  stay within one content surface) and picks a category. The span's original becomes a placeholder in that job's
  codebook (its conversation's vault), marked `de-identified-by-operator`, and is restored locally like any other.
  The clean package goes back to "building"; the next send rebuilds it, binds the new hash and must contain the new
  placeholder, or the job stops (`OPERATOR_REPLACEMENT_NOT_APPLIED`). Confirming the gate's own placeholder with its
  own category changes nothing.
- *Stop the document.* The job is refused at once (`STOPPED_BY_OPERATOR`); nothing more is sent. "Nothing is
  sent" holds only while the cloud trip-wire's `before_human` mode is off: with it on, the cloud model has already
  read the de-identified package, held points included, before the Operator saw it, and the three actions act on text
  the cloud has read (see "Cloud trip-wire" below).

**Undo a mis-click.** Until stage two of the package is approved, the Operator may undo a point's "Not personal" or
"Replace it" (`POST /api/operator/{job}/points/{point}/undo`; the console shows an Undo button beside a settled point).
Nothing is erased: the settlement row stays, the undo is a row of its own (`review_undos` and `review_undo_actors`:
who and when, append-only like the rest), and the point is open again, so no stage is approved until it is settled
anew (a new settlement row, numbered by `attempt`). The console lists each undone settlement under its point. Undoing
a replacement that added a placeholder removes that placeholder from the job's codebook (its mapping and its
`de-identified-by-operator` mark), drops it from the placeholders the rebuilt package must contain, and sends a ready
package back to "building" (audited `PACKAGE_REOPENED`), so the next send rebuilds the package without it. Until
that send the review text (which still shows the removed placeholder) is dropped and no point of the job can be
settled (`REVIEW_REBUILD_REQUIRED`, FAB-03): otherwise a second "Replace it" on the stale placeholder would read as
confirming it and the value would go out in clear. Three
limits: a stop is never undone (the job is refused); what a replacement added is kept in memory only, like the review
text, so a replacement made before the gate restarted cannot be undone (`UNDO_UNAVAILABLE`); and a placeholder that is
already part of what was sent, or of another job's package, is kept and its replacement cannot be undone
(`PLACEHOLDER_IN_USE`). Only a placeholder marked as the Operator's can be removed this way
(`Vault.forget_operator_placeholder`); the gate's own placeholders go only with their conversation.

**The cloud trip-wire in the console.** When the trip-wire is on (see "Cloud trip-wire" below), the job's card has a
"Cloud trip-wire" section built from the register (`review.tripwire_view`; value-free: categories, counts, character
spans in the checked package, placeholder ids and proposal ids, never a value):

- *Correction loop (`before_human`).* Each round: what it found (count per category), whether it corrected the
  package, came back clean or was the last round still flagged (NOT CONVERGED, shown as a warning), the placeholder ids
  it added and its known-miss proposals, with each finding listed. The placeholders the loop added are highlighted in
  the text (marked "T" and the round) and listed among the settled placeholders as the trip-wire's
  (`REPLACED_BY_TRIPWIRE` in `review_points`). **They are not new open points**: the loop already replaced them, and
  the gate's own hold points stay the only ones an Operator must settle. A gate hold point that falls on such a
  placeholder stays an open point. When an Operator replacement makes the package run the loop again, the card shows
  the loop on the current package and, folded below it, the earlier loops of the job.
- *Findings the loop did not replace* (the last round of a NOT CONVERGED package) are not open points either: the
  Operator approves or refuses the package as before. Each is highlighted in the text only when its place is exact:
  its span must lie inside one JSON string value of the checked package, that value must be exactly the text of one
  content surface of the review, and decoding it must give the same text (`review.flag_span`). Otherwise it is listed
  only, never highlighted at a guessed place.
- *Check after approval (`after_human`).* A package it sent back (Held, `TRIPWIRE_FLAGGED`) now appears in the
  Operator queue with every finding listed, and highlighted where exact as above. The card offers "Re-approve this
  package" (`POST /api/operator/{job}/tripwire/reapprove`: the same package is sent unchanged, without another check)
  and "Refuse the job" (`.../tripwire/refuse`). These are the console's path to `themis tripwire review JOB
  --reapprove` / `--refuse` and record the same verdicts; they answer only while such a flag is pending
  (`TRIPWIRE_NOT_PENDING` otherwise).

**The release rule.** While a held point of the current package is unsettled, no stage is approved: the register
refuses it on every path (`_decide_stage`, and `_stages_valid` for an earlier approval), and the console disables the
approval buttons. The two-stage approval, refusal and escalation are otherwise unchanged. A job whose decision was
RELEASE has no held points and is approved as before.

**Nothing an Operator does in one job changes the gate by itself.** A replacement lives in that job's codebook only.
"Not personal" verdicts are counted per hold reason (`themis operator stats`, value-free), so the team can see which
reasons waste checks; they become false-positive catalogue entries only when someone deliberately proposes and
accepts them (`themis fpc`). Nothing promotes them automatically.

## v0.3 catalogues: a gate that learns from its own runs

Three features, each **off by default** (`enabled = false` in `[known_miss]`, `[false_positives]` and
`[tripwire]`; see `config/themis.example.toml`). With all three off the router takes exactly the v0.2.1 path:
`decide(evidence)` is returned unchanged, nothing is swept, no trip-wire request is sent and the new register
tables stay empty (`tests/test_catalogues_off.py` compares routes, reasons, cloud requests and the audit trail
with and without the configuration). None of the three ever stores an original value. Operator-confirmed entries
feed training and the regression set, never a held-out test split (docs/ROADMAP.md, "The rule we keep").

```
 detect ─► decide ─► [false-positive catalogue: set aside reviewed look-alikes] ─► substitute
       ─► FINAL SWEEP (known-miss register, on the outbound JSON)
       ─► [trip-wire before_human: check, correct, check again; at most 3 rounds]
       ─► requester / Operator approval
       ─► [trip-wire after_human: check the approved package; a finding goes back to the Operator]
       ─► the task ─► restore locally
```

### Known-miss register and final sweep

`catalogues/known_miss.py`, managed with `themis kmr` (`add`, `list`, `verify`, `reject`, `promote`,
`export-regression-stub`, `rotate-key`, `export`, `import`, `imports`). A local SQLite file (default
`<app data>/known-miss.db`) whose entries, history and imports refuse UPDATE and DELETE through triggers, like the
job register. The CLI never starts a new file by accident: a missing file is an error unless `--create` is given,
so a mistyped `--db` cannot leave a confirmed miss in a register the gate never reads.

- **Entries.** `entry_kind` is `rule` (a pattern for a format), `context` (a phrase shape that carried a
  quasi-identifier) or `fingerprint`. Each has an entity type (a gate label), a source (`operator`,
  `cloud_tripwire`, `peer_check`, `evaluation`), a UTC creation time, the job id that showed the miss and a link
  to a synthetic regression case. Patterns must generalise (a character class, escape class or quantifier) and
  cannot match empty text, so an entry is a shape, not a stored value or quoted surrounding text.
- **Patterns cannot hang the sweep.** Rule and context patterns (and the false-positive catalogue's context
  patterns) are checked with the standard library's own regular-expression parser, at `add` or import and again
  each time the register is loaded (an unsafe stored pattern blocks rather than hangs). Refused: backreferences
  and conditionals, lookahead and lookbehind, a quantified group containing another quantifier (`(a+)+$`),
  alternation inside a quantified group (`(a|aa)*b`), unbounded quantifiers and quantifiers above `{0,64}` (write
  `\d{1,20}`, not `\d+`), and a backtracking budget: the product over every variable-length quantifier of
  `hi - lo + 2` (a fixed count such as `{4}` counts 1) may not exceed 64. So `\w{0,6}\w{0,6}x` and
  `the only \w{1,30} on the night shift` pass; `\w{1,30}\w{1,30}` does not. The text each entry scans is
  bounded too: windows of 1,024 characters overlapping by 256 (a match up to 256 characters is always seen whole),
  and at most 1 MiB per entry per sweep, beyond which the sweep blocks instead of skipping text. Measured on the
  reference laptop, the costliest accepted shapes (`\w{1,7}\w{1,7}x` the worst) take about half a second per
  entry at that bound; `tests/test_known_miss.py` holds each of them under one second there.
- **Sweep deadline.** Cost still adds up over entries, so the whole sweep has a deadline
  (`[known_miss] sweep_deadline_seconds`, default 5). Past it the sweep stops between windows and the job goes to
  ASK (`ASK:known-miss-deadline`): never RELEASE, and never a hang under the conversation lock.
- **Lifecycle.** `proposed` → `verified` (by a person other than the proposer) → `in_sweep` → `built_in` (needs
  the release tag and a linked regression case) or `rejected`. Only `in_sweep` entries are swept. Status is the
  latest event in the append-only history. `export-regression-stub` writes a value-free stub (entry id, kind,
  entity type, expected outcome, empty text) for a freshly written synthetic case and records the link.
- **Fingerprints, and why only for high-entropy strings.** A fingerprint is HMAC-SHA-256 of the normalised value
  (detection view, case-folded, letters and digits only) under a 256-bit key wrapped by the vault's key protector
  (DPAPI on Windows, a passphrase with scrypt elsewhere; the null protector only with `insecure_test_only`). The
  value is read through a hidden prompt, hashed and discarded. A keyed hash of a low-entropy value (a date of
  birth, a phone number, a nine-digit identifier, an e-mail address, a name) is reversible by anyone holding the
  key, by hashing every candidate, and the key has to sit on the gate. So the register refuses fingerprints for
  dates, phone shapes, e-mail addresses, values with fewer than eight distinct characters and anything whose
  conservative estimate is under 64 bits (digit runs 3.3 bits per digit, letter runs of three or more 2 bits per
  letter, shorter letter runs 4.7), and tells the operator to add a rule instead. A central register should
  prefer rules, or an OPRF-style service (RFC 9497), over shipping the key.
- **Key rotation invalidates fingerprints.** Values are never stored, so nothing can be re-hashed under a new key.
  `themis kmr rotate-key --confirm-invalidates N` must name the number of live fingerprint entries it invalidates;
  the old wrapped key is deleted, those entries become `rejected` (note `KEY_ROTATED`) and must be added again.
- **The sweep.** `Router._protect` ends with `final_sweep`: after substitution and the exact-value check, on the
  outbound JSON exactly as it will be sent (message and tool text, `stop`, and decoded tool-call arguments). The
  gate's own placeholders are masked first. Rules and context shapes are matched on the shared detection view
  (`normalise.detection_view`), so invisible characters cannot hide a known shape; fingerprints are compared
  exactly, over windows of one to eight consecutive letter-and-digit runs (the candidate-then-lookup pattern of
  exact-data-match tools in docs/ROADMAP.md section 3, without hashing every n-gram of arbitrary length). Hits
  are value-free: entry id, kind, entity type, JSON path and character offsets.
- **Outcomes.** A hit adds `ASK:known-miss-hit`: the job is held with the findings listed and goes through the
  normal ASK path (requester confirmation, Operator approval); nothing is silently redacted. A missing, corrupt
  or unkeyable register raises `BLOCK:known-miss-unreadable`. When the sweep starts asking about a package that
  was built without a hold (an entry added later, an import gone stale, a deadline), a package not yet approved
  is raised to "confirmation required" and held (a person checks it); an approved package is refused (fail
  closed) and the work is re-submitted as a new job. Each case carries its own reason: `KNOWN_MISS_HIT`,
  `KNOWN_MISS_DEADLINE` or `KNOWN_MISS_STALE`. The decision record stores the register version (latest event
  id) and the imports in force, and a changed outcome for the same package appends a new row.
- **Staleness applies to imported snapshots only.** A local register cannot be stale: there is nothing for it to
  be behind. A register exported from a central or shared source (`themis kmr export`: in-sweep rule and context
  entries only; fingerprints never leave the gate whose key made them) can be imported on another gate
  (`themis kmr import`). The newest import per source is in force, an older export than the one in force is
  refused, and its age is measured from the source's export time recorded at import. An import older than
  `max_snapshot_age_days` (default 7) adds `ASK:known-miss-stale` for cloud-bound work until a current export is
  re-imported.
- **The hooks path.** The sweep runs inside `Router._protect`, so every release on the Claude Code hooks path is
  swept too, re-submissions included; outside a Chat Completions body every string value (the prompt, the tool
  output) is outbound text. A prompt whose approved package now asks is refused like on the web path, one not yet
  approved needs a person, and tool output with a known miss stops the job (it has no per-call human check).
- **Known gap (v0.3): the hooks path has no decision record.** Only the web/API path writes the `gate_decisions`
  record with the register version used.

### Reviewed false-positive catalogue

`catalogues/false_positives.py`, managed with `themis fpc` (`propose`, `accept`, `list`, `expire`); default file
`<app data>/false-positives.db`, append-only like the register.

- **Entries** are proposed from a run (the job id is recorded) and accepted by a person other than the proposer;
  nothing enters unreviewed. Each is scoped to a profile (`[false_positives] scope`, default the institution
  profile name), one label, and a phrase (compared on the detection view, case-folded, whitespace collapsed)
  and/or a context pattern (searched within 80 characters around the finding). Entries expire (at most 365 days;
  `themis fpc expire` ends one early).
- **Two strengths.** `no_hold` (default): the finding no longer counts towards a hold, but it is still
  substituted. `allow`: the finding is not substituted either; it needs a phrase of two or more words or a code
  with letters and digits, is refused for every `NEVER_TRIM` label (secrets, sensitive statements, dates of
  birth, quasi-identifiers) and never applies to a checksum-valid identifier (it falls back to `no_hold`).
  Neither strength can name `SECRET`.
- **How it decides.** `decide_with_catalogue` keeps the model's decision (`decide(evidence)`) and recomputes the
  decision from the same evidence with only the matched findings set aside. A finding matches only when its labels
  are exactly the entry's label; parts of a matched occurrence (propagation splits a phrase into words) are covered
  inside that occurrence only, so the same word elsewhere still counts. Markings, the injection signal, stage
  failures and secrets are not findings and are untouched; a BLOCK is never lifted. An unreadable catalogue is
  ignored and recorded (`unavailable`): the stricter decision stands.
- **No in-document markers.** Text under test can never switch the gate off: there is no ignore comment, pragma
  or marker of any kind; suppression lives only in this operator-held file.
- **Decision record.** With any catalogue enabled, `gate_decisions` in the job register keeps, per job and clean
  package, the model decision and reasons, the final decision and reasons, the overrides (entry id, strength,
  label, count) and the known-miss outcome, separately. Every disagreement is a free label for the catalogues.

### Cloud trip-wire

`catalogues/tripwire.py`; results read and reviewed with `themis tripwire` (`list`, `review`, `stats`). Two
independent modes, each off by default, so each organisation chooses either, both or neither:

| `[tripwire]` key | When it runs | What the cloud model reads | What a finding does |
|---|---|---|---|
| `before_human` | While the package is built, **before** requester confirmation and Operator approval | The de-identified package, **before any person has approved it**; again after each correction round (up to `max_rounds`, 3) | Replaced by a placeholder; the package is checked again; still flagged after the last round = NOT CONVERGED, and the Operator decides |
| `after_human` | Once, **after** the requester and the Operator approved the package, just before the task turn | Exactly the approved package (what the task turn would send anyway) | Nothing is replaced: the job goes back to the Operator with every finding listed, to re-approve that package or refuse it |

Both on: the loop corrects, then the check after approval double-checks the approved package (the cloud model reads
the final package twice, once as the loop's clean round and once after approval). Both off, or nothing set: the router
takes exactly the path it took before (the golden comparison in `tests/test_catalogues_off.py`). `enabled` is
**deprecated**: it is read only when neither toggle is present, and `enabled = true` then means `before_human` only.
In 0.3 and 0.4 `enabled = true` ran the check after approval; for that placement set `after_human = true`. Neither
mode runs for BLOCK or local-only work (they never reach the cloud), nor on the hooks path. Every finding with text
becomes a pending known-miss proposal in both modes (below), and both fail closed the same way.

#### `before_human`: the correction loop

- **When.** On the web/provider path only, once the gate has de-identified a cloud-bound package and while the
  package is still being built: **before** requester confirmation and Operator approval, and before the package hash
  is bound in the register. It runs once per package: a package that is already built is never checked again by the
  loop, and one built while `before_human` was off is refused once it is on (`TRIPWIRE_NOT_RUN`).
- **What it sends.** The whole de-identified package (messages and tools) as one JSON document in a user message,
  behind a fixed first system instruction (`tripwire-v1`): list residual personal information by character span or
  placeholder, never guess who anyone is, reply with exactly `{"residual": [...], "stop": bool}`, bare or as the
  only content of one fenced block (```` ```json ```` or ```` ``` ````) with nothing else around it. The request is
  checked for original values like any other and is allowed only while the job is cloud-bound and its package is
  still being built (`TRIPWIRE_SENT` audit event). **It detects, it does not prevent**, and because it runs before
  approval the cloud model reads the package before any person has looked at it: a finding means that text has
  already been read once.
- **The correction loop.** A round collects every finding (at most 64); it never stops at the first.
  1. Each finding with a span is mapped back to the original text it covers: the span is widened to whole JSON
     escapes, split around the gate's own placeholders, the gate's literal escapes are undone, and surrounding spaces
     and punctuation are trimmed (a value needs at least two characters, one a letter or digit). Each value is
     replaced by a placeholder of the category the trip-wire reported (`PERSON` gives `P`, an identifier label gives
     `I`, and so on; any other category gives a quasi-identifier placeholder `Q`). The original goes into the
     conversation vault like any other value, and the register records which placeholder ids the round added (source
     `cloud_tripwire`).
  2. Each value is first tried on a copy of the vault. It is kept only if the whole package still builds (the
     exact-value check on the package and on the next trip-wire request, which carries the fixed instruction) and the
     outbound text changes. A value that would leave an original in protocol text or in the instruction (a word such
     as "user" or "the"), or that changes nothing, is left. Kept values are then written to the vault and the package
     is rebuilt from the **same scan** (no second detection run), so the outbound text, the restore map and the counts
     all come from the one substitution code path.
  3. The corrected package goes back to the trip-wire, until a round finds nothing.
  4. **Cap: `max_rounds` (default 3, at most 3).** If the last round still finds something, its findings are
     replaced too, and the package goes to the Operator marked **NOT CONVERGED** (`TRIPWIRE_NOT_CONVERGED`). A round
     that could replace nothing (a finding named by placeholder only, or text the gate cannot rewrite) hands over the
     same way at once, because asking again about an unchanged package would only repeat the round. The cap also
     bounds a document whose injected instruction makes the trip-wire flag everything.
  5. Then the usual human steps. The response that holds the job for confirmation or approval lists each round
     (categories, counts, placeholder ids added and proposal ids; never an original value), and so do `themis
     tripwire list` and the job's card in the Operator console ("Operator review, point by point" above). The
     automatic pilot approval fixture never approves a NOT CONVERGED package.
#### `after_human`: the check of the approved package

- **When.** After both approval stages, after the exact-value check, just before the task turn; once per approved
  package. The request is bound to the job's current approval for exactly this package.
- **What it sends.** The same fixed instruction and the approved package as one JSON document: what the task turn is
  about to send anyway.
- **Outcomes.** A clean pass sends the task (and changes nothing the gate decided). Any finding stops the task:
  nothing is replaced, because a person approved exactly this package. The job goes back to the Operator (Held,
  audited `TRIPWIRE_FLAGGED`) with every finding listed (category, character span or placeholder id, proposal id;
  never a value). `themis tripwire review JOB --reapprove` (alias `--reject`: a false flag) returns the job to
  Approved, and the same package is sent unchanged without a second check; `--refuse` (alias `--confirm`: a real
  residual) refuses the job, and the work is re-submitted as a new job. The Operator console offers the same two
  verdicts in the job's card.

#### Both modes

- **Proposals.** Every finding with text becomes a **proposed** known-miss entry (source `cloud_tripwire`, proposer
  `cloud-tripwire`, the job id recorded): a keyed fingerprint when the value is high-entropy, otherwise a rule for its
  shape (letters, digits and spaces become counted classes, so `Mara Lindahl` becomes `[^\W\d_]{4}\s[^\W\d_]{7}`),
  never the value. One proposal per value per loop. Nothing is learned automatically: a proposal changes nothing
  until a person other than the proposer verifies it and puts it in the sweep (`themis kmr verify` and `promote`, or
  `reject`), and nothing is swept unless `[known_miss]` is enabled. With the trip-wire on, its proposals go to the
  known-miss register file (created if missing) even when the sweep is off.
- **Records.** `tripwire_records` holds one row per pass, with its mode (`stage`: `before_human` or `after_human`;
  rows written before the column existed all came from the check after approval). Per finding: its span, a fixed
  category and its proposal id, and for a loop round its round and the placeholder ids it added. `tripwire_loops` holds one row per finished
  loop (the final package hash, whether it converged, the per-round list). Both are value-free and append-only. The
  vault itself has no source column: which placeholders came from the trip-wire is recorded in the register.
- **Fail closed, as before.** A malformed reply (prose, text around a fence, two fences, extra keys, inconsistent
  `stop`, spans outside the document, unknown placeholders), including one produced by an instruction injected into
  the document, refuses the job (`TRIPWIRE_MALFORMED`) in either mode and in whichever round it comes. A transport or
  protocol failure, including a reply that contains an original value (a value the loop added in an earlier round
  counts), refuses it too under its own reason (`TRIPWIRE_UNAVAILABLE`). In the loop, a finding the trip-wire calls
  `SECRET` refuses the job (`BLOCK:secret`): the gate never replaces a secret with a placeholder (after approval it is
  listed and held like any other finding). Over-redaction is accepted (the loop only adds placeholders, and
  restoration returns the exact original text), and a clear reply never upgrades a decision.
- **Precision.** `themis tripwire review JOB` records a verdict on the job's latest unreviewed flagged pass. For a
  loop round it only records the verdict (repeat it for each round); for a flag on the approved package it
  re-approves or refuses the job, as above. `themis tripwire stats` reports confirmed, rejected and pending flagged
  passes, flag precision, and how many loops converged or did not.
- **Injection.** The trip-wire is an LLM reading untrusted text, so an injection can suppress its findings; that only
  weakens an extra layer. The document is only ever user content; the instruction is always first.
- **Not measured.** Whether either mode changes released leaks, recall, friction or over-redaction has not been
  measured on any split.

## v0.3 typed-decision tier (T1.5)

`detectors/typed.py` and `detectors/typed_tier.py`, configured by `[typed]` and **off by default** (`enabled =
false`: detection and decisions are exactly v0.3 without it). ROADMAP section 2 describes the idea; this is how it is
built.

- **Questions, not search.** After the finders, the tier asks short multiple-choice questions about what the cheaper
  tiers found: **Q-S** for each sentence holding a sensitive-statement finding (a real person, fiction or an example,
  general information, not sensitive), **Q-M** for each tracer email or phone behind an empty-finder hold (a person's
  own, an organisation's, unsure), **Q-N** for each tracer date of birth or number there (date of birth, another
  date, a duration or quantity, a phone, identifier or other number), **Q-P** for each tracer name there (a private
  individual, a public figure in a public role, unsure), and **Q-I** for each sentence of at least three words that no
  detector touched, on a document that would otherwise be released (seven special categories, or none). The wording
  is the training generator's, so a fine-tuned decision model can later answer identical questions. The candidate is
  wrapped in `[[ ]]` inside a paragraph-sized window (at most about 1,100 characters); a document cannot forge the
  markers.
- **One forward pass per order.** Arm 2 asks the resident chat model through llama-server's native endpoints:
  `/apply-template` (thinking off), `Answer:` appended, `/completion` with one predicted token and the top 20
  log-probabilities; an option's probability is the summed probability of its letter tokens (`` A`` and `A`, each
  checked to be one token at start-up, or the stage fails). `OpenAILocalTarget.native_json` allows only those
  endpoints and `/v1/systemone`, which a later encoder decision model answers with a probability per option (a chat
  model answers it with 501).
- **Fail closed to unsure.** Every question is asked in two fixed option orders (canonical and reversed, letters by
  position). A failed or malformed call, label mass under 0.9, the orders disagreeing on the top option, or a margin
  between the top two (averaged, after a per-family temperature) inside the band makes the answer unsure, and an
  unsure answer changes nothing. A tier that cannot start (no model, a letter that is not one token) fails its
  stage, and a failed stage blocks.
- **The policy decides.** The answers are evidence (`DetectionResult.typed`); `policy.decide` applies them. Q-S may
  lift `ASK:sensitive` and Q-M, Q-N, Q-P may lift `ASK:disagreement-empty-llm`, each only when every finding behind
  that hold has a confident lifting answer, the hold has no other cause and one family is its sole cause (unless
  `mixed_families`). It never lifts a security marking (decided before detection), a secret, the injection signal,
  unmatched finder values, the few-persons check, a quasi-identifier hold or a failed stage. An empty-finder hold
  raised by the document sensitivity score is never lifted either. Lifting a hold means RELEASE of the substituted
  text: no finding is removed. Q-I may only add `ASK:typed-indirect` (and, with `indirect_substitute`, substitute the
  sentence as a sensitive statement). What the policy did (`lifted:...`, `kept:...:<why>`, `added:...`) is recorded in
  `DecisionResult.typed`, apart from the reasons, and the evaluation runner writes the model's answers beside it.
- **Defaults from development.** With `enabled = true` and no `families`, only Q-M, Q-N and Q-P are on: on the two
  development sets they held every safety limit and each removed one held clean document (one case each, in-sample).
  Q-S lifted documents that still had a finder miss (released leaks rose on the v0.1 set at every temperature and
  margin tried: the 26B read requests about a colleague and documents claiming to be examples as fiction, with full
  confidence), and Q-I only added holds to clean documents (its targets, sentences no detector touched, held none of
  the missed sensitive statements, which sit beside a found name). Temperatures were fitted on development data
  (Q-S and Q-I about 3; the others too few to fit). Split D decides.
- **Evaluation.** `eval/run_eval.py --typed on` with `--capture-typed DIR` / `--replay-typed DIR` (prompt key and
  model identity, like the finder capture; replay refuses anything not captured) and `--typed-ask-all` (asks every
  question any setting could need, changes no decision), so read-out and policy settings can be tuned without a GPU.

### Restore safety

Restoration (`restore.py`) is exact: a placeholder's class letter and six hex digits must match a token in the
conversation's map (case and a little spacing are tolerated around an intact identifier). An unknown or damaged
placeholder (five or seven digits, a non-hex digit, a missing colon or closer) is left visible in the answer and
counted, never guessed. Since v0.3 the allocator also never issues an identifier one character away from one
already issued in the conversation (one hex digit changed, or two adjacent digits swapped, whatever the class
letter), so a single damaged character can never turn one person's placeholder into another person's: it restores
the original placeholder's value or nothing (`tests/test_restore_safety.py`).

A placeholder inside a URL is not restored (R-01, review SEC-03). It stays as the placeholder text, and the report
counts it as `withheld_in_url`. The router returns that count with `unknown_tokens`, `dropped_tokens` and
`restored_tokens` in the `themis` object (the response body, or the final chunk of a stream). "Inside a URL" means
any of these:

- a run that starts with a scheme (`[a-z][a-z0-9+.-]*://`, or `data:`, `mailto:`, `javascript:`, `http:`, `https:`,
  `ftp:`, `file:`, `ws:`, `wss:` without slashes) and ends at whitespace, `)`, `>`, `"`, `'` or the end of the text;
- a markdown link or image target `](...)`, with balanced parentheses;
- the value of an HTML/XML attribute named `src`, `href` or `action`, quoted or unquoted. A prefix such as
  `xlink:href` or `data-src` counts too.

The decision uses only the text before the token, exactly as the cloud wrote it, so it does not depend on how a
stream is split into chunks. A placeholder next to a URL, but not in it, is restored as before. Each string in
tool-call arguments (`restore_tool_arguments`, and the hooks' tool input) follows the same rule on its own. That
function still refuses invalid JSON, colliding keys and unknown tokens, and does not check what the restored string
will do.

Restoration is otherwise still lexical. A placeholder in a command, in code, in a tool argument that is not a URL,
or in any other non-URL context (a relative path outside the forms above, `srcset`, `formaction`, CSS `url(...)`, a
URL the client builds from parts) is restored to the real value. So a client that executes the reply, or builds a
request from it, can still send the value onwards. The rule also errs on the cautious side: prose shaped like an
attribute (`action = ⟦P:...⟧`) or an unclosed `](` is withheld.
