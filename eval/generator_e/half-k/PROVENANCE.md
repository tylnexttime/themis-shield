# Provenance — half K, split E (test-e)

## Author

- Model: `deepseek/deepseek-v4.1-flash-1017` (DeepSeek), reached through
  OpenRouter, acting as the author of half K of the held-out synthetic test
  set for the privacy gate.
- Date: 30 October 2026. The whole run (authoring, build, review round)
  took place on that date. The generator itself uses no clock or network;
  it is deterministic from a fixed seed.

## Input files read

I read only these four files, and nothing else on the machine:

- `inputs/taxonomy.md` — the label taxonomy (27 labels, span rules, hard-negative kinds, case-level rules).
- `inputs/format.md` — case JSON-lines format and the rules the build must enforce.
- `inputs/profile-schema.md` — the deployment-profile schema.
- `inputs/brief.md` — the task brief and quotas.

No other folders, test sets, gate code or web resources were consulted.
The only other files read were my own deliverables and notes in this half
folder: `profile.json`, `generate.py`, `cases.jsonl`, `manifest.json`,
`review_items.md` (supplied for the review round) and my own decision notes.
Every person, identifier and event in the set is invented; any resemblance
to real people or organisations is coincidental.

## Deliverables (this folder)

- `profile.json` — one invented regional water utility, **Karratong Water
  Authority (KWA)**: institutional terms only (sites, buildings, role
  mailboxes, fiction-range phone lines, programme/committee names, code and
  reference-number formats, issued personal-identifier formats). Its ABN
  `51 846 293 816` is checksum-valid under the mod-89 rule but invented.
- `generate.py` — a deterministic generator (Python 3.12, standard library
  only, fixed seed `712631`). `python -I generate.py` writes `cases.jsonl`
  and `manifest.json`; `python -I generate.py --self-check` rebuilds in
  memory, confirms byte-identical output, and re-checks the brief's quotas.
- `cases.jsonl` — 210 cases, ids `EK-001`..`EK-210`, `"generator": "K"`,
  `"split": "test-e"`.
- `manifest.json` — counts by label, genre, organisation and injection
  position, the seed, and the sha256 of `cases.jsonl`.
- `review_decisions.md` — my adjudication of every item in the review round
  (see below).

## What the build enforces (in `check_case` / `all_case_errors`)

For every case: `text[start:end] == value` for every span and hard negative;
spans sorted, in-bounds, no outer whitespace, non-overlapping; hard negatives
never overlap gold. Checksums by this generator's own code, with known-answer
values in the pools: TFN (mod 11), Medicare (mod 10 + issue number), ABN
(mod 89), IBAN (mod 97), payment cards (Luhn, published network test numbers
only). SSNs only in 987-65-4320..4329; UK NINOs only with the `QQ` prefix.
Every `PHONE` span and every institutional phone is in an ACMA / Ofcom / NANP
fiction range, and a whole-text scan rejects any mobile-like string outside
the ACMA `0491 570 000`..`0491 579 999` block. Every email span and every
email-like string is on a reserved domain (`*.example`, `example.com/net/org`).
Every repeat of a labelled value is itself labelled or institutional.
`case_level` agrees with the spans; negative genres carry no spans; long
documents are 8,000–26,000 characters; no duplicate ids or texts; injection
records are valid and never overlap gold.

For profiled-organisation cases the build also drops any hard negative that
is not a value from `profile.json` or a match of one of its reference-number
formats, so an allow-list can be configured from the profile alone.

## How the set was built

I worked through tools from the start (read/write/edit files, `run_build`,
`run_validator`), writing `profile.json` first, then `generate.py` in
increments, building and validating repeatedly and fixing errors as the
build and validator reported them. The generator alternates profiled
(Karratong) and non-profiled documents; non-profiled cases are drawn from
eight other invented workplaces (Pinehaven Housing Co-operative, Northgate
Ambulance Service, Sableline Broadcasting, Cormorant Ports Authority, Halden
& Ferris Lawyers, Verdant Grocer Group, Quillon Freight, Mallee District
Health Service), each with its own hard negatives annotated per case.
Documents are built through a small segment builder (`Doc`) that records span
and hard-negative offsets as text is emitted, so offsets are exact by
construction.

Coverage highlights: 60 clean documents packed with the brief's look-alike
kinds (fiction, public news, policy/FAQ, holidays/events, role mailboxes and
switchboards of other organisations, secret-manager references in code,
durations near dates); 21 anonymised CSV/TSV/markdown tables where names were
removed but a rare description still singles out one person (`QUASI_ID`);
15 hard-to-spot cases (spelled-out and split numbers, native-script names,
obfuscated emails, nicknames and initials, identifiers split across lines and
cells); 22 injected cases with balanced before/inside/after positions and 8
clean injections; and 6 long documents. Twelve scripts/languages appear in
names and passages (Japanese, Korean, Chinese, Vietnamese, Polish, Arabic,
Greek, Hebrew, Thai, Cyrillic, Devanagari, Māori).

All 27 taxonomy labels clear their per-half span floors (UNION, CRIMINAL,
ETHNICITY at 30+); 84% of sensitive-statement spans are indirect; every
sensitive category carries at least one negative statement.

## Review round

A labeller from another model family labelled all 210 cases blind (seeing
only `taxonomy.md` and each case text); its disagreements with my gold were
returned as `review_items.md` (643 items, R1–R643). I read the whole file,
decided each item against the taxonomy, and — where my gold was wrong — fixed
the **generator** (never the cases by hand), rebuilding and re-validating
until both passed again. `review_decisions.md` records one line per item
(grouped where similar), `fixed` or `kept` with the taxonomy rule cited.

Fixes: raw/stray names in email headers and greetings now emitted as PERSON
spans (R1–R4); the union phrase in minutes now a UNION span, correcting
case_level (R5–R6); an institutional enquiry line changed from PHONE to a
`business_phone` hard negative (R7); log `user=<handle>` and
`session token=<value>` now USERNAME and SECRET spans (R8–R9); a date inside
a HEALTH span split out as its own OTHER_DATE (R10); a country-of-birth cell
and a bare "No" corrected so ETHNICITY states an ethnicity and RELIGION a
stated status (R14–R17); the injury table given a named-worker column so its
HEALTH cells are about a person (R18–R20); the payslip BSB and account
written with words between now two BANK_ACCOUNT spans (R37–R38); a
`last_login` column changed from a hard negative to person-tied OTHER_DATE
(R44–R47); and the sampling-register date columns in the two long reports
unlabelled, since a sample-collection date is not "a date tied to a person"
(R116–R643).

Kept (gold correct under the taxonomy, labeller missed or mislabelled):
INTERNAL_ID record codes and ADDRESS suburb-of-residence columns in the
anonymised extracts (R11–R13, R21–R32, R48–R114); the exfiltration address
inside an injection payload, which is not a named person's email (R33);
annotated site, business-phone and cost-centre hard negatives (R34, R36,
R40–R43); a person-tied incident date (R39); and a QUASI_ID that carries an
ethnicity word inside it, which takes no separate sensitive span (R115).

After the review round: OTHER_DATE fell from ~700 to 180 spans; all other
label counts and every quota are still comfortably met.

## Verification (final state)

`run_build` passes (static check clean; `generate.py` and `--self-check` both
exit 0; second run byte-identical). `run_validator` (independent validator
with a third checksum implementation, `--quotas --half K`) exits 0 with 0
failures, 0 warnings, and every quota met: 210 cases; 60 clean; all 27 labels
at or above their floors (UNION 35, CRIMINAL 31, ETHNICITY 33); 84% indirect
(224/267); 8/8 sensitive categories with a negative statement; 21 quasi-
identifier tables; 15 hard-to-spot cases; 22 injections (8 clean, positions
balanced); 6 long documents; 14/14 genre families; 9/9 clean-document
look-alike kinds; 12 scripts; 99 profiled cases (47%) from 8 other
workplaces.
