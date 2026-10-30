# VALIDATOR: split E

Validator: `C:\dev\themis-work\split-e\validator\check_cases.py`, copied from
`split-d\validator\check_cases.py` on 30/10/2026 and adapted in **protocol constants only** (diff against the
split-D file, ignoring line endings):

| line | change |
|---|---|
| 2 | docstring: "split-d" -> "split-e" |
| 10 | usage example: `--half Q` -> `--half K` |
| 474-475 | id prefix check `D{half}-` -> `E{half}-` |
| 478-479 | split check `test-d` -> `test-e` |
| 1030 | mutation fixture id `DQ-FIX-001` -> `EK-FIX-001` |
| 1040 | mutation fixture `generator` `Q` -> `K`, `split` `test-d` -> `test-e` |
| 1063 | mutation test runs `check_all(..., "K")` |
| 1157 | `--half` help text: K or M, prefix `E<half>-`, split `test-e` |

Unchanged: the third, independent checksum implementation (TFN mod 11, Medicare mod 10 + issue number, ABN mod 89,
Luhn, IBAN mod 97 piecewise), the SSN advertising block and NINO `QQ` rule, the published-test-card list, every
rule code (JSONL, SCHEMA, IDS, SPAN_*, LABEL, HN_OVERLAP, CHECKSUM, CARD_TEST, FORMAT, SECRET_PREFIX, PHONE,
PHONE_SCAN, EMAIL, REPEAT, CASE_LEVEL, INJECTION, NEG_GENRE, LONG, PROFILE, PROFILE_HN, MANIFEST), the known-answer
tests and the quota definitions. The file is now written with LF line endings (the split-D copy had CRLF).

## Known-answer tests and mutation test

`python -I check_cases.py --mutation-test` (venv Python 3.13.14), 30/10/2026:

- known-answer tests: ok (61 published test cards loaded after the Luhn filter)
- **mutation test: 26/26 mutations rejected** (each mutation triggers the expected rule code; same 26 mutations as
  in split D: broken TFN, off-by-one span end/start, duplicated span, hard negative over gold, real-domain email in
  a span and in free text, non-fiction mobile in a span and in free text, dropped sensitive category, contains_pi
  false with spans, unlabelled repeat of a name, SSN outside the block, NINO without QQ, Luhn-invalid card,
  Luhn-valid non-test card, Medicare issue 0, bad ABN, bad IBAN, injection over gold, duplicate id, negative genre
  with spans, document over 26,000 characters, non-fiction institutional phone, vendor-prefixed secret, raw U+2028
  in a JSONL line).

Extra protocol check of the adapted constants (ad hoc, by import; not added to the file): a fixture case is
accepted with `--half K` as `EK-`/`K`/`test-e` and with `--half M` as `EM-`/`M`/`test-e`; it is rejected (rule
IDS) as `DQ-` under K, with generator `M` under K, with split `test-d` under K, and as `EK-` under M: 6/6 as
expected.

## How the halves are run

`python -I check_cases.py <half>/cases.jsonl --profile <half>/profile.json --quotas --half K|M`
(by the authors' `run_validator` tool and by the orchestrator's acceptance checks). Per-half results are recorded
as counts in `half-k/ORCHESTRATION.md` and `half-m/ORCHESTRATION.md`.
