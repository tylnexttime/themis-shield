# Brief: write one half of a held-out test set for a privacy gate

You are writing a **synthetic test set** for a privacy gate: software that reads a document before it is sent to a
cloud AI model, finds personal and sensitive information, and then redacts it, holds the document for a human, or
blocks it. Your cases will be used **once**, to test a frozen version of that gate. You have not seen the gate, and
you must not try to: write the documents real organisations and people actually produce, labelled exactly by the
taxonomy.

Your inputs are only the files in this `inputs/` folder:
- `taxonomy.md`: the 27 labels, span rules, hard-negative kinds, case-level rules. It is the authority on labelling.
- `format.md`: the case format (JSON lines) and the rules the build must enforce.
- `profile-schema.md`: how to describe a fictional organisation.
- this brief.

Do not read, search for or use anything else on this computer or the web (no other folders, no other test sets, no
code of the gate). Record in `PROVENANCE.md` exactly which files you read.

## What to deliver (in your own half folder)

1. `profile.json`: a deployment profile of **one new, invented organisation** of your choice (not education, not a
   training or research institute; for example a regional water utility, a hospital network, a ports authority, a
   housing co-operative, a broadcaster). Institutional terms only. Phones only from the fiction ranges below; emails
   only on reserved domains.
2. `generate.py`: a **deterministic** generator, Python 3.12 standard library only, fixed seed, with its own checksum
   code, writing `cases.jsonl` and `manifest.json`. `python generate.py --self-check` must rebuild in memory and
   confirm byte-identical output. Your build must enforce every rule in `format.md` "What the build must check" and
   fail loudly on any breach.
3. `cases.jsonl`: the cases, ids prefixed with your half letter (`EK-` or `EM-`), `"generator": "<your half>"`,
   `"split": "test-e"`.
4. `manifest.json`: counts by label, genre, organisation, injection position; the seed; sha256 of `cases.jsonl`.
5. `PROVENANCE.md`: who you are (model id), the date, the files you read, and how you built the set.

## Size and quotas (your half)

- **200 cases** (190-210 is fine).
- About **half** the cases come from your profiled organisation: every hard negative in those cases is a value from
  your `profile.json` or matches one of its reference formats. The other half come from **at least six other invented
  workplaces** of your choice, with hard negatives annotated per case and no profile.
- **Clean documents (no personal information at all): at least 60.** They must be full of the look-alikes in
  taxonomy section 4, and must include, among others: fiction excerpts (`fiction_statement`, `fictional_character`),
  public news about organisations or public figures (`public_news`, `public_figure`), policy and FAQ text that
  mentions health, disability, religion, criminal checks or union matters without any individual
  (`policy_statement`), holiday and event notices (`public_event`, `public_date`), role mailboxes and switchboard
  numbers of organisations other than your profiled one (`role_mailbox`, `business_phone`), code and configuration
  files whose secrets are references to a secret manager or template placeholders rather than secret values, and
  text with durations and quantities ("24 hours", "within 3 months", "90 days") near dates.
- **Every label in the taxonomy: at least 20 gold spans** in your half. For `UNION`, `CRIMINAL` and `ETHNICITY`: at
  least 30 each. At least 60% of sensitive-statement spans are **indirect** (the category is never named; see
  taxonomy section 3), and every sensitive category has some negative statements.
- **Quasi-identifiers in tables:** at least 20 "anonymised" tables (CSV, TSV, markdown) where names were removed but
  a rare description still singles out one person (`QUASI_ID`), with ordinary job titles in the same columns left
  unlabelled.
- **Hard-to-spot formats:** at least 15 cases where personal data is written so that a simple pattern would miss it
  (spelled-out or split numbers, names in native script inside prose, identifiers split across lines or cells,
  obfuscated emails, nicknames and initials), labelled exactly per taxonomy.
- **Prompt injection:** about 12% of cases (about 24) carry an instruction aimed at an AI that might read the
  document (`injection` record per `format.md`), with positions before/inside/after balanced, at least five with no
  personal information, and payload styles of your own invention.
- **Genres:** emails, meeting minutes, tickets, chat logs, forms, letters, reports, CSV/TSV, markdown tables,
  code and config, logs, news, fiction, FAQs. Several scripts and languages in names and some passages (for example
  Japanese, Korean, Chinese, Vietnamese, Polish, Arabic, Greek, Hebrew, Thai). **At least four long documents** of
  8,000 to 26,000 characters.

## Safety of values (mandatory)

- Phones only in fiction ranges: ACMA mobiles 0491 570 000 to 0491 579 999; `(0x) 5550 xxxx` and `(0x) 7010 xxxx`;
  1300/1800 975 707 to 711; Ofcom 07700 900xxx; NANP 555-01xx.
- Emails only on `example.com`, `example.net`, `example.org` or `*.example`.
- TFN (mod 11), Medicare (mod 10 plus issue number), ABN (mod 89), IBAN (mod 97) and payment cards (Luhn) must be
  checksum-valid by your own code; payment cards only from card networks' published test numbers. SSNs only
  987-65-4320 to 4329; UK NINOs only with the `QQ` prefix. Secrets are random strings with no vendor prefix.
- Every person is invented. Famous people appear only as `public_figure` hard negatives in a clearly public context.

## How you will be checked

An independent validator re-checks every rule in `format.md` with a third checksum implementation, and a labeller
from another model family re-labels a sample of your texts blind against the taxonomy. Disagreements are adjudicated
against the taxonomy. Label carefully: when the taxonomy says a span is or isn't labelled, follow it exactly, and
keep every repeat of a labelled value labelled.
