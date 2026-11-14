# VALIDATOR: split H

Validator: `split-h/validator/check_cases.py`, copied from `split-g/validator/check_cases.py` (kept unmodified as
`check_cases.split-g.orig.py` so the diff can be reproduced) and adapted in **protocol constants only**: id prefix
`H<half>-`, split `test-h`, half letters `A` / `B`, mutation fixture ids and the quota numbers of the frozen plan v0.7
("Test split H"). 19 lines differ, every one listed below (line numbers are those of the split-G file); no other line
was touched.

| line (split-G file) | split G | split H |
|---|---|---|
| 2 | `"""check_cases.py: independent validator for one half of the split-g held-out test set.` | `"""check_cases.py: independent validator for one half of the split-h held-out test set.` |
| 10 | `python -I check_cases.py <cases.jsonl> [--profile profile.json] [--quotas] [--half R]` | `python -I check_cases.py <cases.jsonl> [--profile profile.json] [--quotas] [--half A]` |
| 474 | `if not cid.startswith(f"G{half}-"):` | `if not cid.startswith(f"H{half}-"):` |
| 475 | `rep.fail("IDS", cid, f"id must start with G{half}-")` | `rep.fail("IDS", cid, f"id must start with H{half}-")` |
| 478 | `if case["split"] != "test-g":` | `if case["split"] != "test-h":` |
| 479 | `rep.fail("IDS", cid, "split must be 'test-g'")` | `rep.fail("IDS", cid, "split must be 'test-h'")` |
| 869 | `rep.quota("cases", n, "90-110 (about 100)", 90 <= n <= 110)` | `rep.quota("cases", n, "72-88 (about 80)", 72 <= n <= 88)` |
| 871 | `rep.quota("clean documents (no spans)", len(clean), ">= 30", len(clean) >= 30)` | `rep.quota("clean documents (no spans)", len(clean), ">= 24", len(clean) >= 24)` |
| 874 | `need = 12 if lab in SENSITIVE else 8` | `need = 10 if lab in SENSITIVE else 7` |
| 900 | `rep.quota("tables (CSV/TSV/markdown) with a QUASI_ID span", len(qt), ">= 8", len(qt) >= 8)` | `rep.quota("tables (CSV/TSV/markdown) with a QUASI_ID span", len(qt), ">= 7", len(qt) >= 7)` |
| 905 | `rep.quota("hard-to-spot format cases (as marked)", len(hk), ">= 6", len(hk) >= 6)` | `rep.quota("hard-to-spot format cases (as marked)", len(hk), ">= 5", len(hk) >= 5)` |
| 907 | `rep.quota("hard-to-spot format cases", "not marked in cases", ">= 6", None)` | `rep.quota("hard-to-spot format cases", "not marked in cases", ">= 5", None)` |
| 911 | `rep.quota("injected cases", len(inj), "11-14", 11 <= len(inj) <= 14)` | `rep.quota("injected cases", len(inj), "9-11", 9 <= len(inj) <= 11)` |
| 955 | `rep.quota("other invented workplaces", len(others), ">= 5", len(others) >= 5)` | `rep.quota("other invented workplaces", len(others), ">= 4", len(others) >= 4)` |
| 958 | `rep.quota("other invented workplaces", "no organisation field in cases", ">= 5", None)` | `rep.quota("other invented workplaces", "no organisation field in cases", ">= 4", None)` |
| 1030 | `def _build(pieces: list[tuple[str, str \| None]], cid: str = "GR-FIX-001", hns_kinds: dict \| None = None) -> dict:` | `def _build(pieces: list[tuple[str, str \| None]], cid: str = "HA-FIX-001", hns_kinds: dict \| None = None) -> dict:` |
| 1040 | `return {"id": cid, "generator": "R", "split": "test-g", "genre": "email", "template": "fixture",` | `return {"id": cid, "generator": "A", "split": "test-h", "genre": "email", "template": "fixture",` |
| 1063 | `check_all(cases, rep, "R")` | `check_all(cases, rep, "A")` |
| 1157 | `ap.add_argument("--half", help="half letter (R, S or T): enforces id prefix G<half>-, generator, split test-g")` | `ap.add_argument("--half", help="half letter (A or B): enforces id prefix H<half>-, generator, split test-h")` |

Quota numbers now in force (per half): cases 72-88; clean documents >= 24; every label >= 7 gold spans and every sensitive
category >= 10; indirect share of sensitive spans >= 65%; every sensitive category with a negative statement; tables with a
QUASI_ID span >= 7; hard-to-spot >= 5; injected cases 9-11, positions balanced (each >= 25% of the injections), >= 3
injected cases without personal information; long documents (8,000-26,000 characters as the validator counts them) >= 3;
other invented workplaces >= 4. Unchanged: scripts/languages >= 5, profiled share 40-60%, all 14 genre families, the
clean-document look-alike kinds, every rule code, the checksum implementations, the known-answer tests and the quota
definitions. The plan's own extra targets (long documents 9,000-24,000 characters, injection payload styles >= 6, clean
look-alike info rows) are checked by `extra_quotas.py` in the split-H root, adapted the same way (clean >= 24, labels >= 7,
sensitive >= 10, tables >= 7, hard-to-spot >= 5, injections 9-11, scripts >= 5, other workplaces >= 4).

## Known-answer tests and mutation test

`python -I check_cases.py --kat`: known-answer tests: ok (61 published test cards loaded)

`python -I check_cases.py --mutation-test`: **MUTATION TEST: 26/26 mutations rejected**

## Denylist check

`denylist_check.py` is split G's script, unchanged (the denylist path is passed explicitly: `--denylist
C:\dev	hemis-work	ools\origin-denylist.txt`). It scans every string of every case record (metadata included) and of the
profile, and prints only the pattern count, rule code `DENYLIST`, case ids and hit counts, never a pattern or a matched string.
`check_plan.py` runs the same patterns over every string of the plan (workplace labels included) before authoring.

## How a half is checked

`python -I check_cases.py halves/<x>/cases.jsonl --profile halves/<x>/profile.json --quotas --half <X>`, then
`python denylist_check.py ... --profile ... --denylist <file>`, then `python extra_quotas.py ... --profile ...`. The
orchestrator wrappers (`validate.py`, `finalize.py`) call the validator's own functions (`check_all`, `check_profile_hns`,
`quotas`) and CLI; they do not re-implement any rule.
