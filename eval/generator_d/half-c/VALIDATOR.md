# VALIDATOR: half C

Validator: `<split-d>\validator\check_cases.py` (independent checksum code; known-answer tests pass; mutation test 26/26), run as `python -I check_cases.py half-c\cases.jsonl --profile half-c\profile.json --quotas --half C`, read-only on half C.

**Rule checks (format.md 'What the build must check' + brief safety of values): 0 failures, 0 warnings: PASS.**

Overall exit code 1 only because of one quota row measured by a heuristic (below). Quota table:

| quota | result | target | status |
|---|---|---|---|
| cases | 205 | 190-210 (about 200) | ok |
| clean documents (no spans) | 62 | >= 60 | ok |
| gold spans PERSON | 1282 | >= 20 | ok |
| gold spans USERNAME | 214 | >= 20 | ok |
| gold spans EMAIL | 325 | >= 20 | ok |
| gold spans PHONE | 363 | >= 20 | ok |
| gold spans ADDRESS | 370 | >= 20 | ok |
| gold spans URL_PERSONAL | 34 | >= 20 | ok |
| gold spans INTERNAL_ID | 183 | >= 20 | ok |
| gold spans TFN | 38 | >= 20 | ok |
| gold spans MEDICARE | 69 | >= 20 | ok |
| gold spans ABN | 26 | >= 20 | ok |
| gold spans NATIONAL_ID | 29 | >= 20 | ok |
| gold spans DRIVER_LICENCE | 49 | >= 20 | ok |
| gold spans PASSPORT | 33 | >= 20 | ok |
| gold spans BANK_ACCOUNT | 73 | >= 20 | ok |
| gold spans PAYMENT_CARD | 29 | >= 20 | ok |
| gold spans SECRET | 37 | >= 20 | ok |
| gold spans DATE_OF_BIRTH | 347 | >= 20 | ok |
| gold spans OTHER_DATE | 431 | >= 20 | ok |
| gold spans QUASI_ID | 24 | >= 20 | ok |
| gold spans HEALTH | 85 | >= 20 | ok |
| gold spans DISABILITY | 75 | >= 20 | ok |
| gold spans ETHNICITY | 104 | >= 30 | ok |
| gold spans SEXUALITY | 68 | >= 20 | ok |
| gold spans RELIGION | 72 | >= 20 | ok |
| gold spans CRIMINAL | 79 | >= 30 | ok |
| gold spans FAMILY_VIOLENCE | 55 | >= 20 | ok |
| gold spans UNION | 87 | >= 30 | ok |
| indirect share of sensitive spans | not marked in cases | >= 60% | n/a |
| sensitive categories with a negative statement | not marked in cases | 8/8 | n/a |
| tables (CSV/TSV/markdown) with a QUASI_ID span | 22 | >= 20 | ok |
| hard-to-spot format cases | not marked in cases | >= 15 | n/a |
| injected cases | 24 | about 12% (about 24; 20-30) | ok |
| injection positions before/inside/after | {'before': 8, 'after': 8, 'inside': 8} | balanced (each >= 25% of injections) | ok |
| injected cases with no personal information | 6 | >= 5 | ok |
| long documents (8,000-26,000 chars) | 6 | >= 4 | ok |
| genre families present | 14/14 | all 14 | ok |
| clean docs carry the brief's look-alike kinds | 9/9 | all 9 | ok |
| clean code/config documents | 7 | >= 1 | ok |
| scripts/languages present | 12: ['Japanese kana', 'Korean', 'Han (Chinese/Japanese)', 'Vietnamese', 'Polish', 'Arabic', 'Greek', 'Hebrew', 'Thai', 'Cyrillic', 'Devanagari', 'Māori/macrons'] | >= 5 ('several') | ok |
| profiled-organisation cases (text mentions the organisation name or a form (no organisation field in cases)) | 59 = 29% | about half (40-60%) | SHORT |
| other invented workplaces | no organisation field in cases | >= 6 | n/a |

Notes: half C cases carry no `organisation` field, so the profiled-organisation share was estimated from texts that mention the profile's organisation name or forms (29%); this heuristic can undercount cases written from the organisation without naming it, and 'other invented workplaces' could not be counted. Indirect/negative sensitive statements and hard-to-spot cases are not marked in half C's cases, so those quotas could not be measured (n/a).
