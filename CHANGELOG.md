# Changelog

All notable changes to themis-shield. Dates are day-first (DD/MM/YYYY).

## Unreleased

### Split E result (development: 0.3.4 now has a fresh-split verdict)

0.3.4 **fails** its frozen rule on split E ([results](eval/RESULTS-v0.4.md)): 12 released leaks, all the
organisation prefix `KWA-` left beside a substituted staff number (DEV-DEBT DD-17), and one more look-alike touched
than 0.3.0. Every other condition passed, including sensitive recall 1.000 and clean friction 0.083. Split E
(`eval/cases/test-e-k.jsonl`, generator under `eval/generator_e/`) is published and is now development data.

### Placeholder legend (v0.4, optional, off by default)

- `[legend] enabled = true` adds one system message to the request sent to the cloud, listing every placeholder it
  carries with the kind of value it hides ("a bank account number", "a date of birth", "a person"), and asks the
  model to keep each placeholder exactly as written. The legend is part of the protected request: checked for
  originals and approved with everything else the cloud sees. Chat requests only.
- A sensitive statement is described generically ("a sensitive statement about a person"). `[legend]
  sensitive_categories = true`, also off by default, names the category instead ("a statement about a person's
  health"), which tells the cloud such a fact exists.
- A placeholder first sent in an earlier turn is described by its class ("an identifier"); history still pairs each
  client message with what was sent for it, the legend left out.

## 0.3.4 (30/10/2026)

**Development measurement only**, like 0.3.2 and 0.3.3: no fresh split has tested it, and a vetting record still quotes
0.3.0's frozen FAIL as its method baseline.

### Friction on clean documents

Split D's clean documents were held for two reasons a rule can tell apart: configuration files that name where a secret
lives, and PII-Tracer values that cannot be what their label says when both LLM finders read the document as clean.
Two switches, on by default:

- `secret-references`: a SECRET finding (from any detector) is dropped when every line it touches assigns only a
  reference (`os.environ['X']`, `process.env.X`, `var.x`, `{{ lookup(...) }}`, `vault:secret/...`, `<your-api-key>`,
  `REPLACE_ME`, a URL whose password slot is a template), declares one (`variable "x" {`), or plainly holds no value
  (an import, a lone brace). A literal value, a default (`os.getenv('X', 'sk-...')`), or any other line keeps the label.
- `tracer-evidence-sanity`: an amount labelled an identifier, a word labelled a URL and a lone role word labelled a
  person are still substituted but no longer hold the document. A date with no birth context labelled a date of birth,
  and a place without a number labelled an address, are treated the same way only when no detector found a person in
  the document: a missed person beside a birth date or a street is the double miss the hold is for.

| Set (development data) | Friction | Released leaks, R_any, R_full, sensitive and QI R_any |
|---|---|---|
| split D half Q | 0.286 -> **0.064** | unchanged |
| split D half C | 0.323 -> **0.161** | unchanged |
| split C | 0.147 -> 0.137 | unchanged |
| v0.1 set | unchanged | unchanged |

A first version of `secret-references` failed this measurement and never shipped: it also passed prose lines with a
short value ("Temporary password: Dune!9301z", "temporary PIN 2970") and released 35 values. A test now runs the rule
over every labelled secret in `eval/cases` and fails if any reads as a reference (DEV-DEBT DD-16).

## 0.3.3 (30/10/2026)

**Development measurement only**, like 0.3.2: no fresh split has tested it, and a vetting record still quotes 0.3.0's
frozen FAIL as its method baseline.

### Sensitive statements (v0.4 item 4)

After 0.3.2, every sensitive statement the gate still missed on the development sets sat in a held document; most
were table cells under a sensitive heading and a person's trade-union role in prose. Two deterministic passes, fixed
vocabulary only (`detectors/statements.py`), each a named switch, on by default:

- `sensitive-columns`: a table column headed police check, criminal history, convictions, union, medical condition,
  disability, religion, sexuality, ethnicity or Indigenous status marks every non-empty cell as a statement of that
  category. A "Clear" or "non-member" still describes the person.
- `union-roles`: a clause that gives a person a trade-union role or act (shop steward, union delegate or member, a
  delegate for the crew, paying or collecting dues, joining the union, the picket line) is a UNION statement. Policy
  text ("Union delegates may attend ..."), credit and student unions, a data steward and a conference delegate are
  not. The statement is the clause, so a log line keeps its time, ticket number and site.

| Set (development data) | Sensitive R_any | R_any | Hard negatives touched | Friction |
|---|---|---|---|---|
| split C | 0.955 -> **0.982** | 0.9905 -> 0.9957 | 562 -> 562 | unchanged |
| split D half C | 0.986 -> **0.995** | 0.9928 -> 0.9941 | 475 -> 476 | unchanged |
| v0.1 set, split D half Q | unchanged | 0.9989 -> 0.9991, unchanged | unchanged | unchanged |

### Fixes

- `themis vet` held a document with a sensitive statement as BLOCK `substitution-failed` instead of ASK: a finder's
  sensitive finding has no statement span. Vet now uses the finding's own span (DEV-DEBT DD-15).

## 0.3.2 (30/10/2026)

**Development measurement only.** These are the first v0.4 detection fixes, measured on the development sets (the
v0.1 set, split C, and split D, which became development data after its v0.3 run). No fresh split has tested them:
a vetting record still quotes 0.3.0's frozen result (FAIL) as the method baseline, and says it is inherited.

### Detection

- `column-identifiers`: a table column headed as a personal identifier (licence, passport, Medicare, tax file,
  national ID, bank account, card, or a staff, employee, student, member, customer, client or patient number) is
  protected cell by cell. Organisation identifiers (incident, order, invoice, asset, ticket, case, reference), row
  counters and properties of an identifier (type, class, expiry, issue date, balance) are not; a bare "ID" counts
  only beside a name or another identifier column. An unquoted comma that shifts later cells protects both candidate
  cells. Such a cell is never cancelled by the organisation's terms or formats.
- `bare-number-formats`: a profile format with no letters in it allow-listed every number of that shape. On split D
  an eight-digit work-order format cancelled every licence number the column pass found. Such a format now counts
  only after its own key words ("Work order 12345678"); a nameless one allow-lists nothing.
- `finder-windows`: a document over 6,000 characters is read by the LLM finders in line-bounded windows (the table
  header repeated, one line of overlap), and every window's values are matched against the whole document. Four
  split D documents (10,000-21,000 characters, 394-504 personal spans) had overflowed one answer and been BLOCKed.
- `detectors-detection-view`: PII-Tracer and the LLM finders read the detection view (invisible characters removed,
  NFKC per character), like the rules and the marking gate, with spans mapped back to the source.
- All four are named switches, on by default; `[detection] fixes = "none"` still reproduces 0.2.1.
- A failed stage now names its kind from a fixed list (`stage-failed:not-enough-memory`, `:target-unavailable`,
  `:runtime`, `:transport`, ...; never a message).

### Measured on development data

Confirmation replay at the release commit, all four development sets, with the earlier finder captures and, for the
54 documents whose prompts changed, today's live answers (both checked: replaying today's capture reproduces the
live run byte for byte):

| Set | Released leaks | R_any | R_full | Sensitive R_any | Clean friction | Hard negatives touched |
|---|---|---|---|---|---|---|
| v0.1 set | 0 -> 0 | 0.9989 -> 0.9989 | 0.9871 -> 0.9871 | 0.997 -> 0.997 | 0.050 -> 0.050 | 306 -> 306 |
| split C | 0 -> 0 | 0.9902 -> 0.9905 | 0.9786 -> 0.9788 | 0.954 -> 0.955 | 0.147 -> 0.147 | 600 -> 562 |
| split D half Q | 2 -> 2 | 0.9809 -> 0.9809 | 0.9801 -> 0.9801 | 0.984 -> 0.984 | 0.286 -> 0.286 | 75 -> 75 |
| split D half C | **26 -> 3** | 0.925 -> **0.993** | 0.893 -> **0.989** | 0.666 -> **0.986** | 0.323 -> 0.323 | 477 -> 475 |

Split D pooled: 28 released spans -> 5. Unprotected spans held only by a finder failure on half C: 274 -> 0. Most of
half C's gain is the long, dense documents now being read at all; for those documents the comparison is today's
live finder answers against 27/10's captured ones, so it includes run-to-run variation. Development data, not an
exam: friction is unchanged and still above the 0.15 rule, and indirect sensitive statements remain the next item.

### Evaluation tooling

- Finder replays are keyed by (case, model, prompt hash), so a windowed document replays every window, and
  `--replay-finders` takes several capture folders (a later one wins).
- The recording client attributes finder calls to the document the engine announces, not by counting calls: a
  windowed document had shifted every later document's capture label in its chunk (detection was unaffected).
- The offset-map contract is property-tested against the detection view; the unused `OffsetMap` skeleton is gone.
- `pytest --shift-clock-days N` runs the suite with the wall clock moved ahead (no date-dependent test at +400 days).

### Development debt

- None open. `docs/DEV-DEBT.md` lists the fourteen items found and fixed or closed on 30/10/2026.

## 0.3.1 (30/10/2026)

No new frozen test: detection is unchanged, so 0.3.0's split D result (FAIL) is the method baseline a
vetting record quotes for this version, and it says so.

### Vetting records

- `themis vet <folder>`: de-identifies a data release (a folder of text files) with the chat path's own steps
  (markings first, tiered detection, policy, the false-positive catalogue and known-miss sweep when enabled), writes
  the derived documents under position-only names, keeps the codebook in a vault file of its own, and writes a
  **vetting record** (`vetting-record.json` and `.md`) in seven parts: identity, classification, the run, the method
  baseline and this release's effectiveness, codebook custody, the inspection checklist and two empty sign-off rows.
  The record holds counts, never values or file names; it is checked against every protected value before it is
  saved. A value found in one document and left in another holds that document. Unresolved ASKs keep the record
  `incomplete`; a person's resolution names the document by position and content hash. Exit codes 0 / 3 / 2. See
  `docs/VETTING-RECORD.md`.
- `themis_shield.vetting`: `vet_release`, `build_record`, `render_markdown`, `method_baseline`, for use from other
  tools. The method baseline quotes the published frozen result (0.3.0: split D, FAIL, 28 released spans).

### Evaluation tooling

- `score.py --labelset themis` maps the gate's labels and its `|`-joined union labels through `label_map.py`
  (`run_eval.py` already did). Split D's pooled scores had been computed with the native label set: the verdict and
  every label-blind measure stand, but typed recall was 0.899, not 0.690, and case-level any-sensitive recall 0.974,
  not 0.935 (docs/DEV-DEBT.md, DD-01).
- The score report separates *leak* (a span touched by no prediction) from *touched-untyped* (touched, but no
  prediction's label maps to it), prints a fingerprint of the label table it used, warns when union labels go
  unmapped, counts discarded prediction spans, and splits held-but-unprotected spans by the hold's first reason.

### Fixes

- A trip-wire test began failing on 29/10/2026 by date alone (a fixed approval date in the harness, a wall-clock
  review). Every register method that takes `now` now defaults to the harness clock.

### Documentation

- `docs/DEV-DEBT.md`: the debt list, kept short. The roadmap's v0.3 section now says what shipped and what moved on;
  the README no longer says every test author was a Claude model.

## 0.3.0 (27/10/2026)

**Evaluation: FAIL.** On split D (404 cases written blind by a Qwen and a Claude author, re-labelled by a DeepSeek
model, committed by hash before the run), v0.3 with the typed tier released 28 personal spans (21 driver licence
numbers in table columns), reached R_any 0.937 and sensitive-statement R_any 0.740, and held 31 of 125 clean
documents (v0.2.1 control: 40). Details in `eval/RESULTS-v0.3.md`; split D and its generators are now published in
`eval/cases/test-d-*.jsonl` and `eval/generator_d/`.

### Deterministic friction fixes

Seven deterministic fixes for v0.2's known friction causes, each a named
switch (`[detection] fixes`, default "all"; "none" restores 0.2.1 exactly;
detection version "v0.1" turns them all off). Measured only by replaying the
captured v0.2 finder answers over the development data (the v0.1 set and
split C); no new held-out split has been run.

- `secret-placeholders`: a secret assignment whose whole value is a template
  placeholder or a secret-manager reference (`${VAULT:...}`, `<set in vault>`,
  `{{ secret }}`, `${{ secrets.X }}`, `%(name)s`, `$(VAR)`, `<REDACTED>`) is
  not a secret. A value glued to placeholder syntax is still caught, now as
  the whole token. Split C: the four configuration files blocked as SECRET are
  released.
- `organisation-contacts`: role mailboxes (generic function or department
  local parts) and Australian inbound numbers (13, 1300, 1800) are still
  substituted but no longer count as evidence for the empty-finder hold.
- `checksum-context`: a TFN-, Medicare- or ABN-valid number labelled by a
  reference word directly before it ("Invoice", "Order no.", "Ticket #") is a
  generic identifier in the rules (still substituted); "TFN" or "tax file" on
  the line keeps the TFN.
- `rule-completeness`: a payment-card rule (issuer prefix, length, Luhn) in
  the `intl` pack; an ACN validator in the `au` pack: a number introduced as
  an ACN that passes the ACN check is company evidence (generic identifier,
  not a TFN, and not evidence for the empty-finder hold). Australian
  validators are cross-tested against an independent implementation.
- `table-role-words`: a table cell that is only a common occupation or role
  word ("Teacher", "Registered nurse") is not a quasi-identifier candidate;
  qualified or rare roles still are.
- `title-tokens`: a leading title in a found name ("Captain", "Sister") is not
  propagated through the document as a name token.
- `contact-brackets`: a contact span that lost its opening bracket still
  matches the whole public number.
- Development replay, all fixes on: split C clean-case friction 0.200 ->
  0.147 (19 -> 14 of 95), over-redaction 605 -> 600, any-label precision
  0.761 -> 0.781; released leaks 0, R_any, sensitive and quasi-identifier
  recall unchanged. The v0.1 set is unchanged on every metric.

### Friction found on real documents (on by default)

A first run over a private slice of real documents (78, never published)
showed causes the synthetic sets never had: secret rules taking URL
components, prose words, card authorisation codes, document identifiers,
SWIFT codes and a passport's machine-readable zone for credentials, and
finder answers that do not match the text only because of line breaks or
no-break spaces. Each fix is a named switch:

- `secret-url-components`: a high-entropy string in a URL is a secret only as
  the value of a credential-named parameter (`token`, `password`, `key`,
  `sig`, `session`, `code` with `state`, ...); any other query value is
  substituted as an identifier; word-only URL paths are not emitted.
- `secret-prose-values`: "PIN <value>" needs digits; "password <word>." is
  not a value.
- `secret-auth-codes`, `secret-document-ids`, `secret-org-codes`,
  `secret-mrz`: card authorisation and promo codes, document and transaction
  identifiers, long digit strings, SWIFT/BIC and biller codes, BIC-prefixed
  bank references and run-together words, and machine-readable zones are
  relabelled (identifier, passport, person) and still substituted, without
  the SECRET block.
- `finder-whitespace`: a finder value not found verbatim is matched token
  for token with any whitespace run standing for any whitespace run (CR/LF,
  NBSP, narrow spaces), then on the NFKC view; such a value is substituted
  but still counts toward the unmatched-values hold. `finder-whitespace-trust`
  (off) would count it as matched: on the real slice that released two
  documents with genuine misses.
- `address-line-numbers`: a street or unit number alone on the line before
  an address joins the address.
- Real slice, replay: BLOCK:secret 17 -> 4 cases (the 4 left include both
  real secrets), clean friction 0.235 -> 0.176, R_full 0.962 -> 0.972,
  released leaks unchanged (11). Development sets: no ratchet change.

### Fixes from an independent review (before the freeze)

- A placeholder with a default value (`${NAME:-value}`, `${NAME:=value}`, `${prop:value}`) or a single word in angle
  brackets is a secret again: only a placeholder with no literal value is exempt (this closed a bypass in the new
  placeholder fix).
- `title-tokens` never drops a given name that is also a title word (Dean, Major, Justice, Sister...).
- A checksum-valid number relabelled by context keeps its checksum result, so the false-positive catalogue's `allow`
  can never reach it.

### Typed-decision tier (off by default)

- `[typed]`: short multiple-choice questions asked prefill-only of the resident 26B, answered by the option letters'
  probabilities in one forward pass, two option orders, label-mass floor and margin band; an unsure answer never
  lifts a hold. Families kept on development data: contact owner, date/duration/number, private person or public
  figure; each may lift a hold only when it is the hold's sole cause, and never a marking, secret, injection,
  unmatched-value or failed-stage hold. "Real person or fiction" was rejected (the 26B confidently called some real
  people fiction) and "unflagged sensitive sentence" was rejected (it only raised friction). `/v1/systemone` adapter
  for decision models (tested against a fake).

### Detector canary

- Fixed synthetic documents run through every detector at start-up, after every model load and periodically; a
  detector that finds less than its known minimum puts the gate in a degraded state that blocks cloud-bound work.
  Catches a model that "succeeds" with nothing (wrong weights, a chat-template change). Per-stage timings recorded.

### A gate that learns (all off by default)

- Known-miss register with a final sweep on the outbound text (rules, context shapes and keyed fingerprints of
  high-entropy strings only; patterns checked for catastrophic backtracking; staleness for imported snapshots only), a
  reviewed false-positive catalogue and an optional cloud trip-wire; see docs/ARCHITECTURE.md. With every feature off, routing, decisions, requests and the audit trail
  are unchanged (tested).

### Documentation

- `docs/ROADMAP.md`, `docs/CODEBOOK-AND-SYNTHETIC-DATA.md` (v0.4 design: typed placeholders with a codebook,
  optional partially synthetic values, per-job date shifting), `docs/SMALL-ENCODER.md` (a small classifier is a
  helper, never a judge) with `eval/ENCODER-RESULTS-2026-10-27.md`, `eval/V03-DEV-LOG.md`.

### Safety

- Safety fix, restore: the placeholder allocator never issues an identifier one hex digit, or one adjacent swap,
  away from one already issued in the conversation, so a cloud answer that damages a single character of a
  placeholder restores that placeholder's value or nothing, never another person's. Before, such near-neighbours
  could coexist: by our estimate about a 2.7% chance in a conversation with 100 placeholders.

## 0.2.1 (27/10/2026)

Security hardening: invisible Unicode characters and compatibility forms no
longer hide a security marking, the injection signal or a rule-pack value.

- Markings: a zero-width character, soft hyphen or similar inside a marking
  (SECRET with a zero-width joiner, CONFIDENTIAL with a zero-width space or a
  soft hyphen), or a marking in full-width letters, was not detected, so a
  local-only document could be released. The marking gate now also reads each
  surface through a shared detection view (every Unicode
  Default_Ignorable_Code_Point removed, NFKC per character) and keeps the
  stricter result: the classification never drops below the raw text's, and
  findings still carry offsets into the original text. Lines holding only
  invisible characters no longer push a banner out of the first three lines.
- Injection signal: fires on the raw text or the detection view, so one
  invisible character inside "ignore all previous instructions" no longer
  hides it.
- Rule packs: the view ignores every default-ignorable code point, not only
  U+200B, U+2060 and U+FEFF, so a ZWNJ, ZWJ, soft hyphen, variation selector
  or tag character inside a phone number or email address no longer hides it
  in whole or in part. Spans cover the whole original value, invisible
  characters included, so substitution removes them too.
- `normalise.detection_view` is the one shared helper; `rules.normalised_view`
  uses it.
- Replay of the captured v0.2 finder answers through this code reproduces the
  v0.2 final runs byte for byte on dev+test (521 cases) and split C (371
  cases): no prediction or decision changed, so every published number stands.
  The published cases contain no invisible character the v0.2 code mishandled,
  so the replay shows no regression; the new tests show the fix.
- Residual: the LLM finders and PII-Tracer still read the raw text (their
  prompts are hashed for capture and replay). Look-alike letters from other
  scripts that NFKC does not fold are not covered.

## 0.2.0 (25/10/2026)

- Tested once on a fresh held-out split written by an independent author (another Claude model): 0 released
  leaks in 3,686 gold spans, quasi-identifier R_any 0.976 (v0.1 policy: 0.738), clean friction 0.200 (v0.1
  policy: 0.358). Fails its own pass rule on friction (limit 0.15); see eval/RESULTS-v0.2.md.

- Policy: when both LLM finders return nothing, only PII-Tracer findings that
  survive the allow-list, with a confidence of at least 0.9 (a floor fitted on
  one development case), count as contrary evidence, by label; a tracer
  document sensitivity of 0.5 or more still holds the document; a date the
  tracer flags that cannot be a date of birth no longer holds a document.
  `[policy] quasi_id_ask` makes a found quasi-identifier hold the document
  for ASK.
- Detection: a column-aware pass marks rare categorical values (role, title,
  team, site, attribute) as quasi-identifiers in tables with identifier-like
  columns or an "anonymised" label; tracer dates with a time of day, bare
  weekdays and the organisation's public dates become OTHER_DATE, and a bare
  weekday or time is cancelled whichever stage found it (known cost: a
  personal date on a public date, or a weekday that was the only touch on a
  statement, goes unredacted); trailing
  punctuation no longer defeats the role-mailbox allow-list; a USI must
  contain a digit (this drops about 1 in 18 genuine USIs from the rule, in
  exchange for about 1 in 32 ordinary ten-letter words no longer matching); the organisation's own ABN is allow-listed even when
  checksum-valid.
- `[detection] version` and `[policy] version` (`v0.2` default; `v0.1`
  restores the evaluated v0.1 behaviour as a negative control).
- Evaluation runner: `--cases`, `--capture-finders` / `--replay-finders`
  (refuses a changed prompt, or a changed model file name, launch arguments
  or context; the weights' content is not hashed), `--gate-version`; timings moved to
  `timings.jsonl`. Development log in `eval/V02-DEV-LOG.md`.

## 0.1.0 (24/10/2026)

First public preview, for synthetic rehearsal only.

- Local gate in front of an OpenAI-compatible cloud endpoint: security
  markings checked first, then rule packs (`core`, `au`, `intl`), PII-Tracer
  and two local LLM finders; opaque placeholder tokens (`⟦P:3fa9c1⟧`) in the
  outgoing request, restored locally in the answer.
- Encrypted per-user vault (AES-256-GCM; DPAPI on Windows, passphrase
  elsewhere), a SQLite job register with second-party approval, a web app
  with an Operator console, and handover packages.
- Claude Code managed hooks (six events), closed by default and opened
  through the `[hooks]` config section.
- Local model runtime: one resident model at a time, owned llama-server
  processes, optional Strata attach and Marian launch planning.
- `themis serve` explains each startup refusal with a reason code;
  `themis --version`; `config/rehearsal.example.toml` for a working
  synthetic rehearsal.
- Evaluation kit and results, measured at tag `eval-kit-v0.1-frozen`
  (`eval/RESULTS.md`).
