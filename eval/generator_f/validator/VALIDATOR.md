# VALIDATOR: split F

Validator: `split-f/validator/check_cases.py`, copied from `split-e/validator/check_cases.py` on 11/11/2026 and
adapted in **protocol constants only** plus the one permitted quota edit. The unmodified split-E copy is kept as
`check_cases.split-e.orig.py` so the diff can be reproduced (ignoring line endings):

| line | change |
|---|---|
| 2 | docstring: "split-e" -> "split-f" |
| 10 | usage example: `--half K` -> `--half O` |
| 474-475 | id prefix check `E{half}-` -> `F{half}-` (so `FO-` under `--half O`) |
| 478-479 | split check `test-e` -> `test-f` |
| 869 | **quota edit (size only):** `cases` range 190-210 -> **220-250** (split F has 240 cases, not 200); label text "220-250 (about 240)" |
| 1030 | mutation fixture id `EK-FIX-001` -> `FO-FIX-001` |
| 1040 | mutation fixture `generator` `K` -> `O`, `split` `test-e` -> `test-f` |
| 1063 | mutation test runs `check_all(..., "O")` |
| 1157 | `--half` help text: O, prefix `F<half>-`, split `test-f` |

No other numeric target was touched. The split-F plan's stricter targets (>= 70 clean documents, >= 30 gold spans
per sensitive category, >= 65% indirect, 25-30 injections, >= 6 long documents, >= 6 scripts, >= 8 other
workplaces, >= 15 hard-to-spot) are checked by `extra_quotas.py` in the split-F root, not by editing the validator.

Unchanged: the third, independent checksum implementation (TFN mod 11, Medicare mod 10 + issue number, ABN mod 89,
Luhn, IBAN mod 97 piecewise), the SSN advertising block and NINO `QQ` rule, the published-test-card list, every rule
code, the known-answer tests and the quota definitions.

## Deviation: no `--denylist` option in the shared validator

The task text and `format.md` refer to the validator's `--denylist`, but the split-E `check_cases.py` has no such
option (it was never implemented in the file; `format.md` only describes it). Adding it would have gone beyond
"protocol constants only", so the validator was left as is and a separate script, `denylist_check.py`, does the
scan: one regex per line from `tools/origin-denylist.txt`, case-insensitive, applied to every string of every case
record and of the profile. It prints only the pattern count, rule code `DENYLIST`, case ids and hit counts, never a
pattern or a matched string. It is run next to the validator in every round and on the sealed file.

## Known-answer tests and mutation test (11/11/2026, venv Python 3.12.10)

`python -I check_cases.py --kat`: ok (61 published test cards loaded after the Luhn filter).

`python -I check_cases.py --mutation-test`: **26/26 mutations rejected** (same 26 as in splits D and E: broken TFN,
off-by-one span end/start, duplicated span, hard negative over gold, real-domain email in a span and in free text,
non-fiction mobile in a span and in free text, dropped sensitive category, contains_pi false with spans, unlabelled
repeat of a name, SSN outside the block, NINO without QQ, Luhn-invalid card, Luhn-valid non-test card, Medicare
issue 0, bad ABN, bad IBAN, injection over gold, duplicate id, negative genre with spans, document over 26,000
characters, non-fiction institutional phone, vendor-prefixed secret, raw U+2028 in a JSONL line).

Ad hoc protocol check of the adapted constants (by import, not added to the file): a fixture is accepted as
`FO-`/`O`/`test-f` under `--half O`; rejected (rule IDS) as `EO-`, as `FK-`, with generator `K`, with split
`test-e`, and as `FO-` under `--half K`: 6/6 as expected.

## How the set is run

`python -I check_cases.py cases.jsonl --profile profile.json --quotas --half O`, then
`python denylist_check.py cases.jsonl --profile profile.json`, then `python extra_quotas.py cases.jsonl`.
The orchestrator wrappers (`validate.py`) call the validator's own functions (`check_case`, `check_all`,
`check_profile_hns`), they do not re-implement any rule.
