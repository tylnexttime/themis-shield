# Generator C: independent held-out test split

`generate_c.py` writes `cases_c.jsonl` (371 cases, 3686 gold spans, 2983 hard negatives) and
`manifest_c.json`. Seed 20261025; Python 3.12 standard library only; `--self-check` rebuilds and compares byte for byte.
sha256 of `cases_c.jsonl`: `39ea2f9595c959f19f2d15607003159f42349ce9ca485f0cf2777d674f2e9ab9`.

## Independence

Generator C was written by a separate author who read only `eval/taxonomy.md`, the format sections of `eval/README.md`,
`eval/score.py` (for the case and prediction schema) and `eval/profiles/harbourline.json`. It did not read generators A or B,
their cases, or the gate's code or prompts. Its templates (60), name pools (30 cultures, native-script forms in
Hangul, Hanzi, Arabic, Greek, Cyrillic, Devanagari, Thai, Hebrew, Japanese and Persian), indirect sensitive phrasings,
injection payloads (17 subtypes), ten invented workplaces, builder (a flat part list rendered to offsets) and
checksum code are its own.

## Design: measure what a gate is weakest at

- **Friction.** 95 clean documents full of institutional look-alikes: meeting notices with dates and times, role
  mailboxes with and without trailing punctuation, site and office directories, product and course codes, public holidays,
  policy and FAQ text whose sentences mention disability, prayer, police checks and union representation without a person,
  minutes with attendees by role only, fiction, news about organisations in court, a clean repository README, status pages,
  purchase orders between organisations, and two long council minutes.
- **Quasi-identifiers.** 57 anonymised tables (CSV, TSV, markdown) and survey extracts where names were removed but a rare
  job title, a tiny team, a DOB and a postcode still single out one person. Rare titles are `QUASI_ID`; common titles in the
  same table are not labelled, so a gate cannot score by redacting every role column.
- **Indirect sensitive statements.** Most sensitive spans never name the category ("goes to dialysis on Tuesdays and
  Thursdays", "has to report to the police station every Friday", "leaves early on Fridays for Shabbat"). Negative statements
  are labelled. Look-alikes with no person (committee names, initiatives, policy statements, fiction, public news) sit in the
  same documents.
- **Injection.** 42 cases carry a payload aimed at an LLM finder: a forged output contract, a forged earlier gate pass,
  a forged tool result, a forged assistant turn, "these are placeholders", relabelling instructions, a legal threat, a task swap
  (translate instead), claimed consent, hidden HTML and markdown comments, YAML front matter, instructions in Spanish, Vietnamese
  and German, a partial-scope note, a chain-of-thought hijack, authority and urgency, a base64-encoded instruction and a fake
  evaluation-harness control note. Positions: {'after': 12, 'before': 15, 'inside': 15}. 4 injected cases contain no personal information;
  four contain only a quasi-identifier and a sensitive statement.
- **Formats a regex misses.** TFNs with zero-width separators, full-width digits and line breaks; names broken by OCR; phone numbers
  digit by digit, spelled out and split over a line; obfuscated emails ("name dot family at example dot com"); nicknames inside
  quotes; initials used as names; Vietnamese and Hungarian family-first order; names in native script.
- **Code and config.** `.env`, YAML, JSON and INI files, test fixtures, a seed SQL script, application logs and a long repository
  dump with passwords, tokens, signing keys, PINs and CVVs next to service accounts, build numbers and versions that must not
  be redacted.
- **Long documents.** 8 cases of 6609 to 9621 characters (an investigation report, a chat export, a code dump, a
  policy with an appendix case study, an 88-row residents table, two clean council minutes).
- **Organisations.** 183 cases from the Harbourline profile (every hard negative in them is a profile value or matches a
  profile reference format; the build rejects anything else) and 188 from ten invented workplaces: a veterinary hospital, a bakery
  co-operative, a community legal centre, a robotics company, an aged-care home, a shire council, a courier depot, a theatre
  company, a mining services camp and a credit union. Education is confined to the Harbourline half.

## Safety of the synthetic values

Phones only from fiction ranges (ACMA 0491 57x xxx, (0x) 5550/7010 xxxx, 1300/1800 975 707-711, Ofcom 07700 900xxx, NANP 555-01xx);
emails only on `example.com/net/org` and `*.example`; TFN (mod 11), Medicare (mod 10 + issue number), ABN (mod 89), IBAN (mod 97) and
Luhn all checksum-valid by the generator's own code, with payment cards drawn only from providers' published test numbers; SSNs only
987-65-4320..4329, NINOs only with the `QQ` prefix; secrets are random strings with no vendor prefix. Famous people appear only as
`public_figure` hard negatives (a lecture theatre or medal named after them). Everything else is invented.

## Build checks (the build fails on any)

`text[start:end] == value` for every span and hard negative; spans sorted, inside the text, no outer whitespace, never overlapping;
hard negatives never overlap gold or each other; injection records never overlap gold; checksums and reserved ranges per label;
a whole-text scan for any phone-like or email-like string outside the fiction ranges and reserved domains (including obfuscated
emails and spelled-out numbers); every repeat of a labelled identifier, name, date, address or secret is itself labelled; case
level agrees with spans; clean genres have no spans; long documents are 6,000 to 12,500 characters; no duplicate ids or texts;
no U+2028/2029; every label has at least 35 gold spans; at least 90 clean, 40 table and 40 injection cases; Harbourline hard
negatives all from the profile. Known-answer tests for every checksum run first.

## Counts

| Label | Gold spans |
|---|---|
| PERSON | 735 |
| USERNAME | 187 |
| INTERNAL_ID | 144 |
| TFN | 43 |
| MEDICARE | 50 |
| ABN | 49 |
| NATIONAL_ID | 46 |
| DRIVER_LICENCE | 41 |
| PASSPORT | 53 |
| BANK_ACCOUNT | 54 |
| PAYMENT_CARD | 50 |
| EMAIL | 128 |
| PHONE | 201 |
| ADDRESS | 262 |
| DATE_OF_BIRTH | 321 |
| OTHER_DATE | 324 |
| URL_PERSONAL | 51 |
| SECRET | 109 |
| QUASI_ID | 126 |
| HEALTH | 151 |
| DISABILITY | 108 |
| ETHNICITY | 59 |
| SEXUALITY | 57 |
| RELIGION | 143 |
| CRIMINAL | 75 |
| FAMILY_VIOLENCE | 45 |
| UNION | 74 |

| Genre | Cases |
|---|---|
| care_note | 6 |
| chat | 12 |
| code | 24 |
| email | 42 |
| fiction | 7 |
| form | 24 |
| hr_note | 44 |
| incident_report | 6 |
| invoice | 22 |
| letter | 6 |
| log | 10 |
| long_document | 7 |
| minutes | 6 |
| news | 7 |
| notice | 42 |
| policy | 12 |
| profile | 8 |
| referral | 15 |
| roster | 6 |
| survey | 16 |
| table | 41 |
| travel | 8 |

Hard negatives by kind: {'build_number': 40, 'building': 226, 'business_phone': 12, 'case_number': 38, 'committee': 159, 'cost_centre': 2, 'event_name': 50, 'fiction_statement': 14, 'fictional_character': 14, 'initiative': 188, 'invoice_number': 8, 'letter_date': 2, 'module_code': 16, 'order_number': 20, 'organisation': 260, 'organisation_abn': 33, 'payroll_group': 4, 'policy_statement': 8, 'product_code': 36, 'programme_code': 49, 'programme_title': 32, 'project_code': 36, 'project_title': 44, 'public_date': 75, 'public_event': 1, 'public_figure': 7, 'public_news': 7, 'public_place': 7, 'reception_phone': 98, 'reference_number': 227, 'role_mailbox': 327, 'role_title': 319, 'service_name': 58, 'site': 399, 'site_address': 45, 'software_version': 13, 'ticket_number': 58, 'weekday': 51}

## Known weaknesses

Templates are few relative to cases, so frames repeat with different fillers; the sensitive phrase pools are finite (about 20 per
category) and a gate that memorised them would look better than it is; dates in "sent" headers were deliberately avoided to
dodge a grey labelling call; table ages are unlabelled (no label exists); Western-order romanised names dominate the person
spans even with 30 cultures; the invented organisations' hard negatives were annotated by one author and may miss some
look-alikes the text contains (a missed look-alike lowers measured over-redaction, never recall).
