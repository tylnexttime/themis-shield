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
| `detectors/union.py`, `propagate.py`, `allowlist.py`, `profile_adapter.py` | real | Evidence union, propagation across tables, names and initials, institutional allow-list from a profile. |
| `detectors/markings.py`, `routing.py` | real | Security-marking gate and the marking-based route, applied before any PII decision. |
| `detectors/contracts.py`, `types.py` | real | Value-free evidence types and the label set. |
| `policy.py` | real | RELEASE / ASK / BLOCK from the evidence. |
| `normalise.py` | skeleton | Specification of input normalisation with mapping back to original characters. |

### Substitution, vault, restoration

| Module | Status | What it does |
|---|---|---|
| `placeholders.py` | real | Opaque tokens and conservative entity linking (see below). |
| `substitute.py` | real | Offset-safe replacement of original text, with value-free metadata. |
| `vault.py`, `protectors.py`, `dpapi.py` | real | Per-user SQLite token store, AES-256-GCM; the user key wrapped by DPAPI (Windows) or a passphrase (scrypt). |
| `restore.py` | real | One-pass local restoration of tokens in the answer, tolerant of small syntax damage. |
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
item.
