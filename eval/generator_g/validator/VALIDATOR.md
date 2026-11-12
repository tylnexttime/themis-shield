# VALIDATOR: split G

Validator: `split-g/validator/check_cases.py`, copied from `split-f/validator/check_cases.py` on 12/11/2026 and adapted in
**protocol constants only** (id prefix, split name, half letters, mutation fixture ids and the quota numbers scaled to the
split-G targets). The unmodified split-F copy is kept as `check_cases.split-f.orig.py` so the diff can be reproduced
(line numbers are those of that file; 23 lines differ, every one listed below; no other line was touched):

| line (split-F file) | split F | split G |
|---|---|---|
| 2 | `"""check_cases.py: independent validator for one half of the split-f held-out test set.` | `"""check_cases.py: independent validator for one half of the split-g held-out test set.` |
| 10 | `python -I check_cases.py <cases.jsonl> [--profile profile.json] [--quotas] [--half O]` | `python -I check_cases.py <cases.jsonl> [--profile profile.json] [--quotas] [--half R]` |
| 474 | `if not cid.startswith(f"F{half}-"):` | `if not cid.startswith(f"G{half}-"):` |
| 475 | `rep.fail("IDS", cid, f"id must start with F{half}-")` | `rep.fail("IDS", cid, f"id must start with G{half}-")` |
| 478 | `if case["split"] != "test-f":` | `if case["split"] != "test-g":` |
| 479 | `rep.fail("IDS", cid, "split must be 'test-f'")` | `rep.fail("IDS", cid, "split must be 'test-g'")` |
| 871 | `rep.quota("cases", n, "220-250 (about 240)", 220 <= n <= 250)` | `rep.quota("cases", n, "90-110 (about 100)", 90 <= n <= 110)` |
| 873 | `rep.quota("clean documents (no spans)", len(clean), ">= 60", len(clean) >= 60)` | `rep.quota("clean documents (no spans)", len(clean), ">= 30", len(clean) >= 30)` |
| 876 | `need = 30 if lab in ("UNION", "CRIMINAL", "ETHNICITY") else 20` | `need = 12 if lab in SENSITIVE else 8` |
| 887 | `">= 60%", share >= 0.60)` | `">= 65%", share >= 0.65)` |
| 889 | `rep.quota("indirect share of sensitive spans", "not marked in cases", ">= 60%", None)` | `rep.quota("indirect share of sensitive spans", "not marked in cases", ">= 65%", None)` |
| 902 | `rep.quota("tables (CSV/TSV/markdown) with a QUASI_ID span", len(qt), ">= 20", len(qt) >= 20)` | `rep.quota("tables (CSV/TSV/markdown) with a QUASI_ID span", len(qt), ">= 8", len(qt) >= 8)` |
| 907 | `rep.quota("hard-to-spot format cases (as marked)", len(hk), ">= 15", len(hk) >= 15)` | `rep.quota("hard-to-spot format cases (as marked)", len(hk), ">= 6", len(hk) >= 6)` |
| 909 | `rep.quota("hard-to-spot format cases", "not marked in cases", ">= 15", None)` | `rep.quota("hard-to-spot format cases", "not marked in cases", ">= 6", None)` |
| 913 | `rep.quota("injected cases", len(inj), "about 12% (about 24; 20-30)", 20 <= len(inj) <= 30)` | `rep.quota("injected cases", len(inj), "11-14", 11 <= len(inj) <= 14)` |
| 917 | `rep.quota("injected cases with no personal information", inj_clean, ">= 5", inj_clean >= 5)` | `rep.quota("injected cases with no personal information", inj_clean, ">= 3", inj_clean >= 3)` |
| 922 | `rep.quota("long documents (8,000-26,000 chars)", len(longs), ">= 4", len(longs) >= 4)` | `rep.quota("long documents (8,000-26,000 chars)", len(longs), ">= 3", len(longs) >= 3)` |
| 957 | `rep.quota("other invented workplaces", len(others), ">= 6", len(others) >= 6)` | `rep.quota("other invented workplaces", len(others), ">= 5", len(others) >= 5)` |
| 960 | `rep.quota("other invented workplaces", "no organisation field in cases", ">= 6", None)` | `rep.quota("other invented workplaces", "no organisation field in cases", ">= 5", None)` |
| 1032 | `def _build(pieces: list[tuple[str, str \| None]], cid: str = "FO-FIX-001", hns_kinds: dict \| None = None) -> dict:` | `def _build(pieces: list[tuple[str, str \| None]], cid: str = "GR-FIX-001", hns_kinds: dict \| None = None) -> dict:` |
| 1042 | `return {"id": cid, "generator": "O", "split": "test-f", "genre": "email", "template": "fixture",` | `return {"id": cid, "generator": "R", "split": "test-g", "genre": "email", "template": "fixture",` |
| 1065 | `check_all(cases, rep, "O")` | `check_all(cases, rep, "R")` |
| 1160 | `ap.add_argument("--half", help="half letter (O): enforces id prefix F<half>-, generator, split test-f")` | `ap.add_argument("--half", help="half letter (R, S or T): enforces id prefix G<half>-, generator, split test-g")` |

Quota numbers now in force (per half): cases 90-110; clean documents >= 30; every label >= 8 gold spans and every
sensitive category >= 12; indirect share of sensitive spans >= 65%; tables with a QUASI_ID span >= 8; hard-to-spot >= 6;
injected cases 11-14, positions balanced (each >= 25% of the injections), >= 3 injected cases without personal information;
long documents (8,000-26,000 characters as the validator counts them) >= 3; other invented workplaces >= 5. Unchanged:
scripts/languages >= 5, profiled share 40-60%, all 14 genre families, the clean-document look-alike kinds, every rule code,
the checksum implementations, the known-answer tests and the quota definitions. The plan's own targets (clean >= 30,
scripts >= 6, long documents 9,000-24,000 characters >= 3, injection payload styles >= 6, and the info rows) are checked
by `extra_quotas.py` in the split-G root, adapted the same way.

The split-E original is not copied (the split-F file is the base of this diff).

## Known-answer tests and mutation test (12/11/2026, venv Python 3.12.10)

`python -I check_cases.py --kat`: known-answer tests: ok (61 published test cards loaded)

`python -I check_cases.py --mutation-test`: **26/26 mutations rejected** (the same 26 mutations as in splits D, E and F).

## Denylist check

`denylist_check.py` is split F's script, unchanged except that the denylist path is passed explicitly (`--denylist
C:\dev\themis-work\tools\origin-denylist.txt`, which is also its default). It prints only the pattern count, rule code
`DENYLIST`, case ids and hit counts, never a pattern or a matched string.

## How a half is checked

`python -I check_cases.py halves/<x>/cases.jsonl --profile halves/<x>/profile.json --quotas --half <X>`, then
`python denylist_check.py ... --profile ... --denylist <file>`, then `python extra_quotas.py ... --profile ...`. The
orchestrator wrappers (`validate.py`, `finalize.py`) call the validator's own functions (`check_all`, `check_profile_hns`)
and CLI; they do not re-implement any rule.
