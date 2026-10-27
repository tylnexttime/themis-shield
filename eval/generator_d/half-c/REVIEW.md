# REVIEW: half C, one round against the blind labeller's 60 disagreement items

Source: `half-q\xc\labeller\disagreements_before.json` and `AGREEMENT.md`. Every change was made in `generate.py` (never by hand in `cases.jsonl`), then `python -I generate.py` and `--self-check` were rerun. Result: 27 items fixed, 33 kept.

## Fixed (27 items)

| Case(s) | Items | Kind | Reason (taxonomy.md) |
|---|---|---|---|
| DC-LG-003 | 20 | labeller_only, INTERNAL_ID | Gold treats the bare 8-digit numbers after a callback as `reference_number` hard negatives (section 4: reference numbers built to look like personal identifiers). In the text nothing said they were not a person's number. The generator now writes "re work order" before each, so the context identifies them as references. Gold unchanged. |
| DC-CS-019 | 1 | nested_label, OTHER_DATE inside CRIMINAL | Section 3: when a sensitive phrase contains a date, the date is its own `OTHER_DATE` span and the sensitive span stops before it. The cell "Convicted 2016 - fraud" broke that rule. The cell values no longer contain a year ("Convicted - fraud", "On parole"). |
| DC-EM-022 | 1 | nested_label, OTHER_DATE inside SEXUALITY | Same section 3 rule: "last year" inside the sensitive phrase reads as a date. Relative-time words were removed from every sensitive phrase in the pools ("last year", "next month", "this year", "last spring", "next spring", "next winter", "this month", "early next year"), so no sensitive span contains a date-like word. |
| DC-EM-029 | 1 | labeller_only, UNION ("release for the union training") | Gold left this unlabelled, but the phrase can be read as a union-role statement (section 3 indirect statements), so the case was ambiguous. The template now says "release for the training course" and the subject no longer says "Union release". The union statement in the email stays labelled. Same change applies to DC-EM-012 text. |
| DC-EM-013 | 1 | gold_only, UNION | The gold phrase ("elected by the depot to speak for the workers") was a weak indirect union-role statement that a reader could miss. Reworded to "was elected delegate by the depot crew", a clearer indirect role statement (section 3: "is the shop steward on B shift"). Still labelled UNION. |
| DC-TS-001 | 3 (nested_label, label_mismatch, case_level) | bare "No" in a generic column | A lone "No" under a generic header ("Self-identified status", "Disclosures on file") does not say which category it answers, so `FAMILY_VIOLENCE` vs `ETHNICITY` was undecidable from the text. Section 3 labels a form answer ("Yes, Aboriginal", "No") as the cell value when the question is stated. Bare "No" now appears only where a question names the category (the form templates); the generic extract columns and the long CSV use explicit negatives instead ("No family violence disclosed", "Non-Indigenous", "Not disabled"). |

## Kept (33 items), gold stands

| Case(s) | Items | Kind | Reason (taxonomy.md) |
|---|---|---|---|
| DC-CH-001, 002, 005; DC-LG-005 (x3); DC-RP-007; DC-RP-012 | 8 | nested_label: USERNAME inside a URL | `URL_PERSONAL` span rule: "Whole URL as written". Spans never overlap, so the handle inside a personal URL is part of the URL span. The standalone handles elsewhere in the same texts are labelled `USERNAME`. |
| DC-CH-007 | 1 | nested_label: USERNAME inside an obfuscated email | `EMAIL` span rule: "Whole address or whole obfuscated phrase". |
| DC-RP-008 | 1 | nested_label: HEALTH vs CRIMINAL ("is on a community corrections order") | Section 3 lists exactly this phrase as an indirect `CRIMINAL` statement. Labeller error. |
| DC-EM-012 | 2 | gold_only OTHER_DATE (leave dates) | Section 3: `OTHER_DATE` is a date tied to a person, including leave. The release dates are the addressee's leave. |
| DC-EM-031 | 1 | labeller_only OTHER_DATE ("Thursday") | A weekday name is a `weekday` look-alike, not a date tied to a person (section 4). |
| DC-LT-001 | 2 | gold_only ADDRESS; labeller_unmappable | The document uses CRLF line ends. The labeller returned the address with LF, so its offsets could not be mapped. This is an offset-mapping artefact, not a labelling difference: both sides label the address. |
| DC-LT-006, DC-MN-007 (2), DC-TK-003 | 4 | gold_only PERSON (given-name-only mentions) | Section 1: "Every mention is its own span", and given names alone are `PERSON`. |
| DC-MN-003, DC-MN-004, DC-MN-006, DC-RP-006 (x4) | 7 | gold_only PERSON (initials) | Section 1: "initials used as a name ("AP to draft the roster")" are `PERSON`. |
| DC-MN-007 | 1 | gold_only OTHER_DATE (date the panel approved the person's request) | Section 3: a date tied to a person (a decision on their request). |
| DC-RP-001, DC-RP-003 | 2 | gold_only SECRET (first-aid cabinet code) | Section 2: `SECRET` covers PINs and codes; the labeller labelled the comparable key-safe and gate codes elsewhere in the set, so these two were the inconsistent ones. |
| DC-RP-008 | 4 | gold_only (CRIMINAL, FAMILY_VIOLENCE, DISABILITY, ETHNICITY statements in quoted first person) | Section 3: statements about a person, direct or indirect, are labelled as the predicate phrase. Long-document recall misses by the labeller. |

Net effect on the set: gold spans are the same except that the cells and phrases above no longer contain a year or relative-time word, and some phrases were reworded; counts per label and all quotas still hold (build passes the quota check).
