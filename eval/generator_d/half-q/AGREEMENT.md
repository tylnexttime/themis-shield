# AGREEMENT: half Q

Blind labeller: `deepseek/deepseek-v4.1-flash-1017` under forced ZDR, temperature 0, one case per call; it saw only taxonomy.md and the case text and returned `{label, value}` spans plus case_level. Values were mapped to offsets by exact substring match, every occurrence (token-aligned occurrences first, raw substring only if none). Raw outputs: `labeller/raw/`. Gold = the author's spans.

Definitions: recall = share of gold spans overlapped by a labeller span (label-sensitive: same label; blind: any label); precision = share of labeller spans overlapping a gold span (same rule); exact = share of gold spans with a labeller span of identical offsets and label. Cohen's kappa on case_level, per case.

| metric | before | after |
|---|---|---|
| gold spans | 830 | 1259 |
| labeller spans (mapped) | 1353 | 1303 |
| recall (label-sensitive) | 0.986 | 0.983 |
| precision (label-sensitive) | 0.644 | 0.988 |
| recall (label-blind) | 0.996 | 0.985 |
| precision (label-blind) | 0.805 | 0.998 |
| exact match (label-sensitive) | 0.763 | 0.963 |
| exact match (label-blind) | 0.764 | 0.963 |
| kappa contains_pi | 0.988 | 1.000 |
| kappa health | 0.972 | 0.918 |
| kappa disability | 0.973 | 1.000 |
| kappa ethnicity | 1.000 | 0.980 |
| kappa sexuality | 0.817 | 0.944 |
| kappa religion | 1.000 | 1.000 |
| kappa criminal | 0.961 | 1.000 |
| kappa family_violence | 0.947 | 0.972 |
| kappa union | 1.000 | 1.000 |
| disagreement items | 378 | 43 |

Disagreement items by kind, before: {"labeller_only": 264, "nested_label": 92, "label_mismatch": 9, "case_level": 10, "gold_only": 3}
after: {"nested_label": 12, "gold_only": 19, "case_level": 7, "labeller_only": 3, "label_mismatch": 2}

## Per label (recall / precision, label-sensitive; exact)

| label | gold before | R | P | exact | gold after | R | P | exact |
|---|---|---|---|---|---|---|---|---|
| ABN | 20 | 1.000 | 1.000 | 1.000 | 20 | 0.950 | 1.000 | 0.950 |
| ADDRESS | 20 | 1.000 | 1.000 | 0.900 | 20 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 21 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| CRIMINAL | 31 | 0.935 | 1.000 | 0.000 | 30 | 1.000 | 1.000 | 0.800 |
| DATE_OF_BIRTH | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| DISABILITY | 20 | 1.000 | 0.952 | 0.000 | 20 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| EMAIL | 20 | 1.000 | 0.625 | 1.000 | 20 | 1.000 | 0.909 | 1.000 |
| ETHNICITY | 30 | 1.000 | 1.000 | 0.033 | 30 | 0.967 | 1.000 | 0.967 |
| FAMILY_VIOLENCE | 20 | 1.000 | 0.909 | 0.000 | 20 | 0.950 | 1.000 | 0.950 |
| HEALTH | 20 | 0.950 | 0.950 | 0.100 | 20 | 1.000 | 0.833 | 0.850 |
| INTERNAL_ID | 20 | 1.000 | 0.150 | 1.000 | 121 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 20 | 0.850 | 0.102 | 0.850 | 168 | 0.917 | 1.000 | 0.869 |
| PASSPORT | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| PERSON | 276 | 1.000 | 0.637 | 0.996 | 458 | 0.996 | 0.990 | 0.991 |
| PHONE | 20 | 1.000 | 1.000 | 1.000 | 21 | 1.000 | 1.000 | 1.000 |
| QUASI_ID | 21 | 1.000 | 1.000 | 0.952 | 20 | 1.000 | 1.000 | 1.000 |
| RELIGION | 20 | 1.000 | 1.000 | 0.000 | 20 | 1.000 | 1.000 | 1.000 |
| SECRET | 20 | 1.000 | 1.000 | 0.950 | 20 | 1.000 | 1.000 | 1.000 |
| SEXUALITY | 21 | 0.714 | 1.000 | 0.000 | 21 | 0.905 | 1.000 | 0.619 |
| TFN | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| UNION | 30 | 1.000 | 1.000 | 0.000 | 30 | 1.000 | 1.000 | 0.967 |
| URL_PERSONAL | 20 | 1.000 | 1.000 | 1.000 | 20 | 1.000 | 1.000 | 1.000 |
| USERNAME | 20 | 1.000 | 0.588 | 1.000 | 20 | 1.000 | 0.833 | 1.000 |

## Author's review round (one round, by tools)

The author received all 378 disagreement items (`review_items.md`, R1-R378, with the taxonomy rows quoted) and decided each one against the taxonomy, fixing its generator, not the cases. Decisions: `review_decisions.md` (author's file). Item ranges marked fixed cover 359 item numbers and ranges marked kept cover 23 (the author's ranges overlap slightly; all 378 are covered). The author has the final word on gold.

Every item the author kept, with its reason (quoted from review_decisions.md; item numbers refer to review_items.md):

- R32-R36: kept - Form dates labelled OTHER_DATE; taxonomy: "A date tied to a person: start, leave, incident, appointment, claim" - these are dates on personal forms tied to the subject
- R37-R39: kept - Same as R32-R36; Travel Authorisation dates are tied to the person
- R123: kept - USERNAME inside URL_PERSONAL; taxonomy: URL_PERSONAL span = "Whole URL as written", spans never overlap
- R313: fixed - Name separated via render_sensitive; predicate "attended the Pride march with colleagues" kept as SEXUALITY (taxonomy: indirect statements count, "attended the Pride march" reveals orientation)
- R323: kept - USERNAME inside EMAIL; taxonomy: EMAIL span = "Whole address", spans never overlap
- R331: kept - USERNAME inside EMAIL; taxonomy: EMAIL span = "Whole address", spans never overlap
- R337: kept - USERNAME inside EMAIL; same reasoning as R323
- R340: kept - USERNAME inside EMAIL; same reasoning as R323
- R345: kept - USERNAME inside EMAIL; same reasoning as R323
- R348: fixed - Name separated via render_sensitive; predicate kept as SEXUALITY (taxonomy: indirect disclosure)
- R350: kept - USERNAME inside EMAIL; same reasoning as R323
- R356: kept - USERNAME inside EMAIL; same reasoning as R323
- R359: kept - "and her wife are expecting their first child" kept as single SEXUALITY span; taxonomy: span = "predicate phrase including its verb", spans never overlap; the primary disclosure is the relationship (wife) revealing orientation
- R360: kept - USERNAME inside EMAIL; same reasoning as R323
- R369: fixed - Name separated via render_sensitive; predicate "has a husband" kept as SEXUALITY (taxonomy lists it as indirect sexuality example)
- R372: kept - USERNAME inside EMAIL; same reasoning as R323
- R374: fixed - Name separated via render_sensitive; predicate kept as SEXUALITY

After the review the generator was rebuilt (199 cases; 139 case texts changed, 2 cases dropped, 60 unchanged); build, self-check and validator passed (exit 0, all quotas met), and the labeller re-labelled every changed text (unchanged texts reuse the cached blind labels). Remaining disagreements after the review are in `labeller/disagreements_after.json` (not sent back: one review round only).
