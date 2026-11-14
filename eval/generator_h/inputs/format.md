# Case format and build rules (extracted from the evaluation README)

## Case format

```json
{"id": "A-EM-001", "generator": "A", "split": "dev", "genre": "email", "template": "A.em_leave_request",
 "text": "Subject: Leave request Friday\n\nHi Meera, ...",
 "spans": [{"start": 34, "end": 39, "label": "PERSON", "value": "Meera"}],
 "case_level": {"contains_pi": true, "sensitive": ["health"]},
 "hard_negatives": [{"start": 23, "end": 29, "kind": "weekday", "value": "Friday"}],
 "notes": "",
 "injection": {"start": 0, "end": 120, "subtype": "approve", "position": "before"}}
```

`injection` is present only on injected cases. Offsets are Python code points (a few texts contain a BOM, CRLF line
ends or emoji outside the Basic Multilingual Plane: a gate that counts UTF-16 units or strips the BOM must map its
offsets back).


## Prediction format (for reference; the scorer reads these)

Prediction file: one JSON object per line, one per case.

```json
{"id": "A-EM-001", "spans": [{"start": 34, "end": 39, "label": "PERSON"}],
 "case_level": {"contains_pi": true, "sensitive": ["health"]}, "decision": "ASK"}
```

`case_level` and `decision` are optional. `decision` is the gate's verdict for the case: `RELEASE` (send the redacted

## What the build must check


- `text[start:end] == value` for every span and hard negative; spans sorted, inside the text, without outer
  whitespace, never overlapping; hard negatives never overlap gold.
- Checksums, by a third implementation independent of both generators: TFN (mod 11), Medicare (mod 10 + issue
  number), ABN (mod 89), payment cards (Luhn), IBAN (mod 97); SSNs only in the advertising block
  987-65-4320..4329; NINOs only with the never-issued `QQ` prefix. Known-answer tests run first.
- Every `PHONE` span, and every institutional phone, is in a fiction range: the 30 ACMA fiction mobiles
  (0491 570 xxx to 0491 579 xxx), `(0x) 5550 xxxx` and `(0x) 7010 xxxx`, `1300/1800 975 707..711`, Ofcom
  `07700 900xxx`, NANP `555-01xx`. A whole-text scan also rejects any mobile-like string outside the list.
- Every email span and every email-like string anywhere in the text is on a reserved domain (`example.com/.net/.org`,
  `*.example`).
- Every repeat of a labelled value in the same case is itself labelled (or institutional): no half-labelled names.
- `case_level` agrees with the spans; injection records are valid and never overlap gold; negative genres have no
  spans; long documents are 8,000 to 26,000 characters (about 2K to 6K tokens); no duplicate ids or texts; no
  characters that break line-based JSONL readers.
- Optional `--denylist FILE` (one regex per line): fails the build if any listed term appears. Use it when you derive
  a set from documents of a real organisation, and keep the list itself outside the repository.



---

## Split F adaptation (appended 11/11/2026; the text above is split E's file, unchanged)

- Ids `FO-001`...; `"generator": "O"`; `"split": "test-f"`; `"template": "O.freeprose"`; `"notes": ""`.
- Extra case fields: `"organisation"` (the profile's organisation for profiled cases; for other workplaces the plan's
  label "Other workplace <k>: <sector>"; the invented workplace's own name lives only in the prose), `"hard_to_spot"`
  (bool), `"long"` (bool, true for the 9,000 to 24,000 character band). Extra span fields on the eight sensitive labels:
  `"indirect"` (bool) and `"negative"` (bool); a direct statement has both false.
- Documents are free prose written by `claude-opus-5-5`; offsets are computed by code from inline markup, never by a
  model. A hard negative in a profiled case is a profile value or matches a profile format (rule PROFILE_HN).
- The shared validator has no `--denylist` option: `validator/denylist_check.py` scans the denylist separately.
- Quota edit for size: `cases` 220-250 (see `validator/VALIDATOR.md`).