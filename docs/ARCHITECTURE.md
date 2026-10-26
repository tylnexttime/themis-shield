# Architecture

A map of the `themis_shield` package for people reading the code. Each module
is marked **real** (wired into the running gate and tested), **partial** (real
parts plus a skeleton) or **skeleton** (a specification: types and
`NotImplementedError` stubs, not used by `themis serve`).

## Request path in one paragraph

`themis serve` (`web/__main__.py`) checks the configuration and environment
(`startup.py`), builds a `Router` from the TOML file (`integration.py`) and
serves the loopback web app (`web/app.py`). A chat request is checked for
security markings first (`detectors/markings.py`, `routing.py`); a
`RESTRICTED`-style banner keeps it local. Otherwise the text goes through the
detectors (`detectors/engine.py`: rule packs, PII-Tracer, two local LLM
finders, union, propagation, allow-list), the policy decides RELEASE, ASK or
BLOCK (`policy.py`), found values are replaced with opaque tokens
(`substitute.py`, `placeholders.py`) whose originals go into the encrypted
vault (`vault.py`), and the clean package is logged in the job register
(`register/`) where a second identity approves it. Only then, after checking
that no original value appears in the outgoing JSON, does the request go to
the cloud (`cloud/openai_compat.py`). The answer's tokens are restored locally
(`restore.py`).

## Module map

### Entry points and wiring

| Module | Status | What it does |
|---|---|---|
| `cli.py`, `__main__.py` | real | The `themis` command: `serve`, `hooks`, `register`, `runtime`, `profiles`, `--version`. |
| `startup.py` | real | Value-free startup checks and reason codes for `themis serve`. |
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
| `detectors/engine.py` | real | Tiered detection, batched so each local model loads once. |
| `detectors/rules.py` | real | Checksum, contact and credential rules in packs: `core`, `au` (TFN, Medicare, ABN, USI, phones), `intl` (E.164, IBAN). |
| `detectors/pii_tracer.py`, `labels.py` | real | PII-Tracer on CPU with exact offset mapping; label adapter. |
| `detectors/llm_finder.py` | real | Local LLM finders, prompt v1 and v2, structured JSON output. |
| `detectors/escalation.py` | real | Tier-selection rule with fixed reason codes. |
| `detectors/union.py`, `propagate.py`, `allowlist.py`, `profile_adapter.py` | real | Evidence union, propagation across tables, names and initials, a column-aware quasi-identifier pass for identifier-bearing tables (v0.2), institutional allow-list from a profile. |
| `detectors/markings.py`, `routing.py` | real | Security-marking gate and the marking-based route, applied before any PII decision. |
| `detectors/contracts.py`, `types.py` | real | Value-free evidence types and the label set. |
| `policy.py` | real | RELEASE / ASK / BLOCK from the evidence. `[policy] version` (`v0.2` default, `v0.1` kept as the negative control) and `quasi_id_ask`. |
| `detectors/settings.py` | real | `[detection] version`: `v0.2` (default) turns on the tier-1 fixes and the column-aware table pass; `v0.1` restores the evaluated v0.1 detection. |
| `normalise.py` | partial | Real: `detection_view`, the shared detection view (Unicode default-ignorable characters removed, NFKC per character, each view character mapped back to its source character) used by the rule packs, the marking gate and the injection signal. Skeleton: the document-level `normalise` and `OffsetMap` contract. |

### Substitution, vault, restoration

| Module | Status | What it does |
|---|---|---|
| `placeholders.py` | real | Opaque tokens and conservative entity linking (see below). |
| `substitute.py` | real | Offset-safe replacement of original text, with value-free metadata. |
| `vault.py`, `protectors.py`, `dpapi.py` | real | Per-user SQLite token store, AES-256-GCM; the user key wrapped by DPAPI (Windows) or a passphrase (scrypt). |
| `restore.py` | real | One-pass local restoration of tokens in the answer, tolerant of small syntax damage. Exact only: a damaged identifier is never guessed (see [Restore safety](#restore-safety)). |
| `summary.py`, `history.py` | real | Summaries from the substituted text only; redacted conversation history. |

### Release, register and approval

| Module | Status | What it does |
|---|---|---|
| `router.py`, `router_flow.py` | real | Orchestration: register, markings, detection, substitution, approval, cloud, restoration. |
| `chat_boundary.py` | real | The accepted Chat Completions subset; only the configured cloud model can be addressed. |
| `cloud/` | real | Fixed-destination OpenAI-compatible transport; API key read from the named environment variable at send time. |
| `register/sqlite_store.py`, `models.py`, `two_stage.py`, `reporting.py`, `cli.py`, `pilot.py` | real | The SQLite job register (append-only history tables), intake validation, two-stage approval, reports, the `themis register` CLI and the off-by-default automatic approval fixture. |
| `register/client.py` | partial | Real: the register contracts (`JobRecord`, `JobStatus`, approval validation, offline journal) used everywhere. Skeleton: `JobRegisterClient` protocol methods and `HttpRegisterClient`, a non-networking stub for a remote register. |
| `handover.py`, `app_services.py` | real | Handover mode: a deterministic, re-scanned package for manual use; safe projections for the web app. |
| `ledger.py` | skeleton | Specification of an append-only evidence ledger per request (its contract test is `xfail`). |
| `audit.py` | skeleton | Specification of value-free audit rows. The register's audit table is the working audit trail. |
| `adapters/` | skeleton | Specifications for walking Anthropic Messages and OpenAI Responses payloads. The gate accepts Chat Completions only. |
| `clients.py` | real | Client profiles: Kilo Code as an API provider; Claude Code through managed hooks with its own login. |
| `register/gate_records.py` | real, v0.3 | Append-only decision records (model output and catalogue override kept apart, with the known-miss register version) and cloud trip-wire results and reviews. Written only when a v0.3 catalogue is enabled. |

### v0.3 catalogues (all off by default)

| Module | Status | What it does |
|---|---|---|
| `catalogues/known_miss.py` | real, off by default | The known-miss register (rules, context shapes, keyed fingerprints of high-entropy strings) and the final sweep over outbound text. |
| `catalogues/false_positives.py` | real, off by default | The reviewed false-positive catalogue (`no_hold` and `allow` entries), scoped and expiring. |
| `catalogues/tripwire.py` | real, off by default | The optional cloud trip-wire: fixed instruction, strict JSON reply, fail closed. |
| `catalogues/gate.py` | real | Configuration (`[known_miss]`, `[false_positives]`, `[tripwire]`) and how the router applies them; a no-op when all are off. |
| `catalogues/cli.py`, `store.py` | real | `themis kmr`, `themis fpc`, `themis tripwire`; shared append-only SQLite plumbing. |

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
| `runtime/scheduler.py`, `lease.py`, `modes.py` | real | One resident model at a time, FIFO queue, per-user GPU lease. |
| `runtime/catalogue.py`, `settings.py`, `planning.py`, `calibration.py` | real | Model catalogue, runtime settings, launch arguments, device-keyed calibration. |
| `runtime/children.py`, `resources.py`, `occupancy.py`, `backends.py` | real | Owned llama-server processes and the stale-process reaper, resource snapshots, memory held by attached runtimes, back-end status. |
| `inference/llamacpp.py`, `openai_local.py`, `json_output.py` | real | Owned llama-server lifecycle; loopback OpenAI-compatible client; strict JSON output. |
| `inference/strata.py`, `marian.py`, `ollama.py` | real, optional | Attach to a user-run Strata server; Marian launch planning from saved profiles; Ollama client. |
| `inference/laptop_llamacpp.py` | real | Compatibility client for an independently run llama-server. |
| `inference/dgx_spark.py` | skeleton | A target that reports itself unavailable until configured. |

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
the match is unique. Literal text that looks like a token is escaped (doubled
delimiters, backslashes and colons), so a document cannot forge a token that
restoration would resolve.

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
  on the substituted text. Its release needs a second identity to approve the
  clean package in the register, and that approval is bound to the SHA-256 of
  the exact outgoing request (`manifest_hash`): any change voids it. The
  remaining automatic check is the exact-value one above.

The cost of the asymmetry is that a value the detectors missed the first time
is not caught by a second detector pass on the web path; it relies on the
Operator reading the clean package. Making the two paths match is an open
item. With `[known_miss]` enabled (v0.3), both paths also run the known-miss
final sweep on their outbound text: it catches repeats of misses a person has
already confirmed, not new ones.

## v0.3 catalogues: a gate that learns from its own runs

Three features, each **off by default** (`enabled = false` in `[known_miss]`, `[false_positives]` and
`[tripwire]`; see `config/themis.example.toml`). With all three off the router takes exactly the v0.2.1 path:
`decide(evidence)` is returned unchanged, nothing is swept, no trip-wire request is sent and the new register
tables stay empty (`tests/test_catalogues_off.py` compares routes, reasons, cloud requests and the audit trail
with and without the configuration). None of the three ever stores an original value. Operator-confirmed entries
feed training and the regression set, never a held-out test split (docs/ROADMAP.md, "The rule we keep").

```
 detect ─► decide ─► [false-positive catalogue: set aside reviewed look-alikes] ─► substitute
       ─► FINAL SWEEP (known-miss register, on the outbound JSON) ─► requester / Operator approval
       ─► [cloud trip-wire] ─► the task ─► restore locally
```

### Known-miss register and final sweep

`catalogues/known_miss.py`, managed with `themis kmr` (`add`, `list`, `verify`, `reject`, `promote`,
`export-regression-stub`, `refresh`, `rotate-key`). A local SQLite file (default `<app data>/known-miss.db`)
whose entries and history refuse UPDATE and DELETE through triggers, like the job register.

- **Entries.** `entry_kind` is `rule` (a pattern for a format), `context` (a phrase shape that carried a
  quasi-identifier) or `fingerprint`. Each has an entity type (a gate label), a source (`operator`,
  `cloud_tripwire`, `peer_check`, `evaluation`), a UTC creation time, the job id that showed the miss and a link
  to a synthetic regression case. Patterns must generalise (a character class, escape class or quantifier) and
  cannot match empty text, so an entry is a shape, not a stored value or quoted surrounding text.
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
  normal ASK path (requester confirmation, Operator approval); nothing is silently redacted. A snapshot whose
  latest event is older than `max_snapshot_age_days` (default 7) adds `ASK:known-miss-stale` for cloud-bound
  work; `themis kmr refresh` attests the register is current. A missing, corrupt or unkeyable register raises
  `BLOCK:known-miss-unreadable`. An entry that starts matching after a package was approved without a hold
  refuses that package (`KNOWN_MISS_HIT`); the work is re-submitted as a new job. The decision record stores the
  register version (latest event id) and age the sweep used.
- **Scope.** The sweep runs inside `Router._protect`, so the hooks path gets it too, but only the web/API path
  writes the decision record today.

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

`catalogues/tripwire.py`; results reviewed with `themis tripwire` (`review`, `list`, `stats`).

- **When.** On the web/provider path only, after both approval stages, after the exact-value check and before the
  task turn. Never for BLOCK or local-only work (they never reach the cloud). It runs once per approved package.
- **What it sends.** The approved de-identified messages (and tools) as one JSON document in a user message,
  behind a fixed first system instruction (`tripwire-v1`): list residual personal information by character span or
  placeholder, never guess who anyone is, reply with exactly `{"residual": [...], "stop": bool}`. The request is
  checked for original values like any other and bound to the job's current approval (`TRIPWIRE_SENT` audit
  event). It sees what it was about to see anyway; **it detects, it does not prevent**.
- **Outcomes.** A clear reply is recorded and changes nothing: silence is not evidence and never upgrades a
  decision. A flag stops the job (Held-Offline, audited), records only spans, placeholder ids and a fixed category,
  and points the Operator to `themis tripwire review JOB --confirm` (the job is refused and a known-miss proposal
  is offered) or `--reject` (the job returns to Approved for the same package and is not re-checked). A malformed
  reply (prose, code fences, extra keys, inconsistent `stop`, spans outside the document, unknown placeholders),
  including one produced by an instruction injected into the document, refuses the job; a reply that contains an
  original value, or a transport failure, does too.
- **Precision.** Each Operator verdict is stored; `themis tripwire stats` reports confirmed, rejected and pending
  flags and flag precision, so we know whether the round trip earns its cost.
- **Injection.** The trip-wire is an LLM reading untrusted text, so an injection can suppress its flags; that only
  weakens an extra layer. The document is only ever user content; the instruction is always first.

### Restore safety

Restoration (`restore.py`) is exact: a placeholder's class letter and six hex digits must match a token in the
conversation's map (case and a little spacing are tolerated around an intact identifier). An unknown or damaged
placeholder (five or seven digits, a non-hex digit, a missing colon or closer) is left visible in the answer and
counted, never guessed. Since v0.3 the allocator also never issues an identifier one character away from one
already issued in the conversation (one hex digit changed, or two adjacent digits swapped, whatever the class
letter), so a single damaged character can never turn one person's placeholder into another person's: it restores
the original placeholder's value or nothing (`tests/test_restore_safety.py`).
