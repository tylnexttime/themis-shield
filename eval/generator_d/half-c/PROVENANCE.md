# PROVENANCE: half C (ids `DC-`, generator "C", split "test-d")

- **Author:** Claude Sonnet 5.5 (model id `claude-sonnet-5-5`), run through Claude Code as a sub-agent.
- **Date:** 27/10/2026.
- **Seed:** 20261027 (fixed in `generate.py`; every case draws from its own `random.Random` keyed on the seed, the plan position, the template and the attempt number).

## Files read

Only the four files in `<split-d>\inputs\`, each read in full:

1. `brief.md`
2. `taxonomy.md`
3. `format.md`
4. `profile-schema.md`

Nothing else was opened, searched or fetched: no other folder under `<workspace>`, no other author's half, no validator, no gate code, no web page. One directory listing (`ls`) was run over `inputs\`, `half-c\` and `sealed\` to confirm the paths; `sealed\` was empty. The final step copies `cases.jsonl` into `sealed\half-c.jsonl` and writes `sealed\half-c.sha256`, as instructed.

## What is in this folder

| File | What it is |
|---|---|
| `profile.json` | Deployment profile of one invented organisation, "Fernhollow Housing Co-operative Ltd" (a member-owned housing co-operative; not education or research). Written by `generate.py`. |
| `generate.py` | The generator: Python 3.12 standard library only, deterministic, own checksum code, own validation. |
| `cases.jsonl` | The cases (JSON lines, ids `DC-<genre>-<nnn>`). |
| `manifest.json` | Counts by label, genre, organisation, injection position and subtype; quotas; the seed; sha256 of `cases.jsonl`. |
| `PROVENANCE.md` | This file. |
| `tools/` | Four small helper scripts I used while building (`show.py` prints a case with its labels marked, `recheck.py` re-verifies checksums and email domains from `cases.jsonl` with a second implementation, `audit.py` looks for unlabelled look-alikes, `selftest.py` tampers with cases in memory and confirms the build's checks fail). They read only `generate.py` and `cases.jsonl`. |

## How the set was built

- **Structure of `generate.py`:** (1) core: checksum functions (TFN mod 11, Medicare mod 10 with issue number, ABN mod 89, Luhn, IBAN mod 97), fiction-range phone tests, the document builder; (2) data: invented name pools in 15 scripts and orders, value generators, sensitive-statement pools, the profile and nine invented workplaces; (3) templates; (4) the plan, validation, manifest and output.
- **Offsets are never typed.** Each document is a list of plain strings and labelled parts (gold span, hard negative, injection payload). Rendering joins them and computes the code-point offsets, so `text[start:end] == value` holds by construction and is re-checked. Every mention of a person goes through the builder, so every repeat of a name, number or address is labelled.
- **Organisations.** 98 of 205 cases come from the profiled organisation (every hard negative in them is a value from `profile.json` or matches one of its reference-number or code formats; the build fails otherwise). The rest come from nine other invented workplaces (a water utility, a freight company, a broadcaster, a credit union, a community health service, a ferry operator, a law firm, a shire council and an aged-care provider), with hard negatives annotated per case and no profile. All organisation, person, street, suburb and product names are invented; any resemblance to a real one is coincidental. Famous people appear only as `public_figure` in clearly public history or news items.
- **Plan.** The plan is an explicit list (`build_plan`) of template calls, so quotas are structural: 62 clean documents, 22 anonymised tables with a rare description (`QUASI_ID`), 31 hard-to-spot cases (spelled-out, split, zero-width or full-width numbers, native-script names inside English prose, obfuscated and line-wrapped emails, nicknames and initials, OCR-broken names and addresses, plus nine passages written in other languages), 24 injection cases (8 before, 8 inside, 8 after; 6 on documents with no personal information; 12 payload styles of my own invention), 6 long documents (10,000 to 21,000 characters), a few texts with CRLF line ends, a BOM or emoji outside the BMP.
- **Sensitive statements** come from phrase pools (one per category, positive and negative, third and first person, gender-constrained where the phrase is). Most are indirect in the sense of `taxonomy.md` section 3; form cells and some explicit statements are direct. The builder records which are which and reports the ratio in `manifest.json`. Where a sensitive phrase runs into a date, the date is split out as its own `OTHER_DATE` span.
- **Checks enforced by every build** (it stops with a message on any breach): known-answer tests for each checksum first; span and hard-negative offsets, ordering, overlap and outer whitespace; checksum validity for TFN, Medicare, ABN, payment cards (also restricted to published test numbers) and IBAN; SSNs only in 987-65-4320..4329; NINOs only with prefix QQ; every `PHONE` span and every institutional phone, plus a whole-text scan for mobile-like and landline-like strings, inside the fiction ranges; every email-like string on a reserved domain; every repeat of a labelled value (and every token of a labelled name) covered by a gold span or a hard negative; `case_level` agrees with the spans; clean and negative-genre documents have no spans; injection records valid and non-overlapping, with "before"/"after" having no gold on the wrong side and "inside" sitting between gold; long documents 8,000 to 26,000 characters; no duplicate ids or texts; no characters that break line-based readers; profile keys, ABN checksum, phones and emails; and the quota table from the brief (counts of cases, clean documents, gold spans per label with 30 for UNION, CRIMINAL and ETHNICITY, at least 60% indirect, negatives per category, tables, hard-to-spot cases, injections and their positions, long documents, other workplaces, profiled share, genres). `--denylist FILE` is supported (not used; nothing was derived from real documents).
- **Determinism.** `python -I generate.py` writes `profile.json`, `cases.jsonl` and `manifest.json`. `python -I generate.py --self-check` rebuilds everything in memory twice and compares byte for byte with the files on disk. The build was also run under several `PYTHONHASHSEED` values with identical output.
- **Revising.** A phrase, template or plan entry can be edited in `generate.py` and the build rerun; only the cases that use it change, because each case has its own random stream.
