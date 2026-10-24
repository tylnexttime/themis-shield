# Label taxonomy

This file defines the gold labels of the themis-shield evaluation set: what each label covers, where a span starts
and ends, and what is deliberately **not** labelled. Everything in the set is synthetic (see `README.md`).
The label list is repeated in code in `generate_a.py`, `generate_b.py`, `build.py` and `score.py`. If you change a
label, change all four.

A **gold span** is a run of characters that a de-identification gate must cover before the text leaves for a cloud
model. Offsets are Python code-point offsets into `text`, end exclusive, and `text[start:end] == value` for every span.

## 1. Direct identifiers

| Label | Covers | Span rule | Not this label |
|---|---|---|---|
| `PERSON` | Any natural person's name: full name, given or family name alone, `FAMILY, Given`, upper or lower case, initials used as a name ("AP to draft the roster"), nicknames ("Gosia", "Cliff"), names in native script (Greek, Arabic, Chinese, Japanese, Korean, Cyrillic, Devanagari, Thai, Hebrew) and Vietnamese family-first order. | Name tokens only. Titles (Dr, Ms, Senior Constable) and post-nominals (PhD, OAM, JP) sit outside the span. A quoted nickname inside a name (`Clifford "Cliff" Thornbury`) is part of the one span. A name broken across lines by OCR is one span. Every mention is its own span. | A bare role title ("the Privacy Officer"). A famous public figure in a public context (a hard negative, section 4). Organisation names, even ones that contain a surname-like word. |
| `USERNAME` | A login, handle or account name tied to one person (`kahurangi75`, `user=jsmith`, a GitHub handle). | The handle only. | Service accounts (`svc_booking`) and shared role mailboxes. |
| `EMAIL` | A personal or named staff email address, including obfuscated forms ("name dot family at example dot com") and an address wrapped across a line. | Whole address or whole obfuscated phrase. | Role mailboxes (`servicedesk@...`, `privacy@...`): hard negatives. |
| `PHONE` | A person's mobile or landline, any format: spaces, dots, dashes, brackets, `+61`, `+61 (0)`, digit-by-digit, spelled-out digits, split over a line break; also UK and US numbers. | Whole number. | Reception desks, switchboards and public enquiry lines: hard negatives. |
| `ADDRESS` | A home, postal or safe address of a person, single or multi-line, with or without unit, state and postcode. A postcode or suburb given **as where a person lives** (a verification answer, a column in a list of people) is also `ADDRESS`. | Whole address. When a table splits it into columns, each part is its own span. | Business, site and PO Box addresses of an organisation: hard negatives. A town named as the place a site is in. |
| `URL_PERSONAL` | A person's profile, portfolio, social, ORCID-style or blog URL. | Whole URL as written. | Organisation web pages and API endpoints. |

## 2. Identifier numbers

| Label | Covers | Check applied to every gold value |
|---|---|---|
| `INTERNAL_ID` | A number an organisation issues to a person: staff, employee, learner, customer, member, patient, frequent-flyer number, research participant code. Formats vary by issuer (`EMP-04417`, `HL12345`, `56945644`, `P-123-AB`). | Format only. |
| `TFN` | Australian Tax File Number, 9 digits, also spaced, dotted, zero-width-separated, full-width or line-broken. | Weighted sum (1,4,3,7,5,8,6,9,10) mod 11 = 0 |
| `MEDICARE` | Australian Medicare card number, 10 digits (8 + check digit + issue number). | Weights (1,3,7,9,1,3,7,9) mod 10 = digit 9; issue number 1 to 9 |
| `ABN` | An Australian Business Number belonging to a person (sole trader, contractor). | ABR mod-89 rule |
| `NATIONAL_ID` | A foreign national identifier: US Social Security number, UK National Insurance number. | SSNs only from 987-65-4320..4329 (reserved by the US SSA for advertising); NINOs only with the never-issued `QQ` prefix |
| `DRIVER_LICENCE` | A driver licence number (8 to 10 digits; no state checksum modelled). | Format only |
| `PASSPORT` | A passport number (one or two letters + 7 digits). | Format only |
| `BANK_ACCOUNT` | Australian BSB and account number, or an IBAN. | IBAN mod-97 = 1 (bank codes are invented: XBNK, ZPAY, XMPL, 9999...) |
| `PAYMENT_CARD` | A payment card number. | Luhn; only payment providers' **published test numbers** are used |
| `SECRET` | Passwords, PINs, CVVs, API keys, tokens, connection-string passwords, whether or not tied to a person. | Random strings with no real vendor prefix, so secret scanners do not mistake them for live keys |

Span rule for all identifiers: the whole value with its own separators, nothing around it. A BSB and account written
together (`279-756 18236596`) is one span; written with words between ("BSB 279-756, account 18236596") they are two
`BANK_ACCOUNT` spans.

## 3. Dates, descriptions and sensitive statements

| Label | Covers | Span rule |
|---|---|---|
| `DATE_OF_BIRTH` | A date of birth in any format (DD/MM/YYYY, ISO, "3 March 2004", dotted, short year). | Date only. |
| `OTHER_DATE` | A date tied to a person: start, leave, incident, appointment, claim, expiry of a person's document or card, the date stamp of a personal chat message, a resit date. | Date only. |
| `QUASI_ID` | A description that singles out one person without naming them: "the only female diesel mechanic at the Dock 7 workshop", a job title that is unique in a small extract ("Sole night pharmacist" next to a DOB and postcode). | The identifying description. A place name inside it belongs to the span. |
| `HEALTH` | A physical or mental health condition, treatment, pregnancy or medical event stated about a person. | The predicate phrase including its verb ("is recovering from a heart attack", "has epilepsy and recently changed medication"); in tables and forms the cell value ("type 1 diabetes"). |
| `DISABILITY` | A disability, impairment or support need stated about a person ("uses a wheelchair", "Deaf, Auslan interpreter"). | As HEALTH. |
| `ETHNICITY` | Racial or ethnic origin, including Aboriginal or Torres Strait Islander status, Māori iwi affiliation and cultural background stated about a person. | As HEALTH; a form answer ("Yes, Aboriginal", "No") is the cell value. |
| `SEXUALITY` | Sexual orientation or relationship facts that reveal it. | As HEALTH. |
| `RELIGION` | Religious belief, practice or observance stated about a person, including a dietary requirement that states a faith ("halal", "kosher") when it is recorded against that person. | As HEALTH. |
| `CRIMINAL` | Criminal history: charges, convictions, bail, parole, orders, police checks. | As HEALTH. |
| `FAMILY_VIOLENCE` | A person's experience of family or domestic violence, protection orders, refuge, family-violence leave. | As HEALTH. |
| `UNION` | Trade-union membership or role ("is the union delegate for the depot"). | As HEALTH. |

**Sensitive statements are labelled only when they are about a person.** A policy question about disability support
or first aid for seizures carries no span.

**Indirect statements count.** The category does not have to be named: "did two years inside", "is on a community
corrections order", "has to see his parole officer every Monday" (`CRIMINAL`); "manages her bipolar with lithium",
"is on methadone" (`HEALTH`); "attends the mosque on Fridays", "leaves early on Fridays for Shabbat", "wears a hijab"
(`RELIGION`); "brought her girlfriend to the Christmas party", "has a husband" said of a man (`SEXUALITY`); "has an AVO
against his ex", "needs her address kept off every system because of her ex" (`FAMILY_VIOLENCE`); "is the shop steward
on B shift" (`UNION`); "has Ngāti Kahungunu whakapapa" (`ETHNICITY`); "relies on a hearing loop in meetings"
(`DISABILITY`). The span is the predicate phrase, as for direct statements.

**Negative statements are labelled** with the category they reveal, because stating a status is still personal
information: "has no criminal record" (`CRIMINAL`), "does not have a disability" (`DISABILITY`), "is not pregnant"
(`HEALTH`), "is not religious" (`RELIGION`). A form cell that states a status ("No") is labelled; a cell that states
nothing ("-", "Prefer not to say", "None", "Not stated") is not.

Spans never overlap. When a sensitive phrase contains a date ("is pregnant and wants to avoid night shifts after
9 September"), the date is split out as its own `OTHER_DATE` span and the sensitive span stops before it.

## 4. Hard negatives (not personal information)

Each case lists, in `hard_negatives`, look-alikes that are **institutional or public** and must not be redacted, with
offsets and a `kind`. A gate that redacts them is over-redacting; the scorer reports the rate. Kinds used:

- Organisation names and business numbers (`organisation`, `organisation_abn`, `business_identifier`).
- Sites, buildings, rooms, site codes, business and site addresses (`site`, `building`, `room`, `site_code`,
  `business_address`, `site_address`). Generator B also uses bare site names inside personal addresses (staff live in
  the town the site is in): there the town is part of the gold `ADDRESS` and not a hard negative.
- Role mailboxes, reception and switchboard numbers (`role_mailbox`, `business_phone`, `reception_phone`).
- Role titles without a name (`role_title`).
- Programme, module, project, product and service names and codes (`programme_code`, `programme_title`,
  `module_code`, `project_code`, `project_title`, `product_code`, `service_name`, `event_name`).
- Reference numbers built to look like personal identifiers: tickets, cases, orders, invoices, purchase orders whose
  digits match the learner-number format, asset tags, cost centres, build numbers, software versions
  (`reference_number`, `ticket_number`, `case_number`, `order_number`, `invoice_number`, `asset_id`, `cost_centre`,
  `build_number`, `software_version`, `form_number`, `payroll_group`, `provider_number`, `client_account`).
- Public dates, meeting dates, letter and release dates, weekday names in email headers (`public_date`,
  `meeting_date`, `letter_date`, `release_date`, `weekday`).
- Famous historical figures named in a clearly public context (`public_figure`), and public places (`public_place`).
- **Sensitive look-alikes with no private individual**: a fictional character and what happens to them
  (`fictional_character`, `fiction_statement`: "Jean Valjean served nineteen years in prison for stealing bread"), a
  public news item about an organisation in court (`public_news`), committee and staff-network names (`committee`:
  "Diversity and Inclusion Committee", "Staff Pride Network"), initiatives and courses (`initiative`: "health and safety
  audit", "Mental Health Week", "Ramadan iftar evening"; `programme_title`: "Mental Health First Aid Refresher"), public
  events (`public_event`: "Ramadan", "Pride Month") and general policy statements (`policy_statement`: "staff with a
  disability can request reasonable adjustments"). Some cases put one of these next to a real indirect disclosure.

In generator B every hard negative is drawn from `profiles/harbourline.json` (and the build fails otherwise), so an
allow-list can be configured from the profile alone. Generator A has no profile: its hard negatives are annotated per
case and model what a gate meets without one.

Grey calls, documented so nobody mistakes them for errors: a provider number of a clinic is a hard negative; a
licence or passport expiry is `OTHER_DATE`; "halal" recorded against a traveller is `RELIGION`; a `QUASI_ID` such as
"the only Deaf staff member at Wyndhollow" carries a disability word inside it but no separate sensitive span; gender-
affirming surgery is labelled `HEALTH`.

## 5. Case level and label mappings

- `case_level.contains_pi` is true exactly when the case has at least one gold span.
- `case_level.sensitive` lists the categories of its sensitive spans: `health`, `disability`, `ethnicity`,
  `sexuality`, `religion`, `criminal`, `family_violence`, `union`. The build enforces both rules.

`score.py --labelset` maps other label schemes onto these labels; a candidate label may stand for several gold labels:

| Scheme | Mapping |
|---|---|
| `native` | identity |
| `openai_pf` (eight coarse `private_*` labels) | private_person: PERSON, USERNAME; private_date: DATE_OF_BIRTH, OTHER_DATE; private_url: URL_PERSONAL; private_address: ADDRESS; account_number: all section 2 identifiers except SECRET; private_email: EMAIL; private_phone: PHONE; secret: SECRET |
| `pii_tracer` | `openai_pf` plus other_pii: QUASI_ID, every sensitive label and every identifier |
| `presidio` | PERSON, EMAIL_ADDRESS, PHONE_NUMBER, LOCATION (ADDRESS), DATE_TIME, URL, AU_TFN, AU_MEDICARE, AU_ABN, AU_ACN (ABN, TFN), CREDIT_CARD (PAYMENT_CARD), IBAN_CODE, US_BANK_NUMBER, US_SSN, UK_NINO, US_DRIVER_LICENSE, US_PASSPORT, NRP (ETHNICITY, RELIGION), MEDICAL_LICENSE (identifiers) |

The headline metrics (full-coverage recall, leaks, released leaks) ignore labels: a gold span is protected if it is
covered, whatever label the gate gave it.
