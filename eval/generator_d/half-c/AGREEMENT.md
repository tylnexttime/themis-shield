# AGREEMENT: half C

Blind labeller: `deepseek/deepseek-v4.1-flash-1017` under forced ZDR, temperature 0, one case per call; it saw only taxonomy.md and the case text and returned `{label, value}` spans plus case_level. Values were mapped to offsets by exact substring match, every occurrence (token-aligned occurrences first, raw substring only if none). Raw outputs: `labeller/raw/`. Gold = the author's spans.

Definitions: recall = share of gold spans overlapped by a labeller span (label-sensitive: same label; blind: any label); precision = share of labeller spans overlapping a gold span (same rule); exact = share of gold spans with a labeller span of identical offsets and label. Cohen's kappa on case_level, per case.

| metric | before |
|---|---|
| gold spans | 4581 |
| labeller spans (mapped) | 4876 |
| recall (label-sensitive) | 0.995 |
| precision (label-sensitive) | 0.993 |
| recall (label-blind) | 0.995 |
| precision (label-blind) | 0.995 |
| exact match (label-sensitive) | 0.988 |
| exact match (label-blind) | 0.988 |
| kappa contains_pi | 1.000 |
| kappa health | 1.000 |
| kappa disability | 1.000 |
| kappa ethnicity | 0.975 |
| kappa sexuality | 1.000 |
| kappa religion | 1.000 |
| kappa criminal | 1.000 |
| kappa family_violence | 0.975 |
| kappa union | 1.000 |
| disagreement items | 60 | 

Disagreement items by kind, before: {"nested_label": 13, "gold_only": 22, "labeller_only": 22, "labeller_unmappable": 1, "label_mismatch": 1, "case_level": 1}

## Per label (recall / precision, label-sensitive; exact)

| label | gold before | R | P | exact | 
|---|---|---|---|---|
| ABN | 26 | 1.000 | 1.000 | 1.000 |
| ADDRESS | 370 | 0.997 | 1.000 | 0.997 |
| BANK_ACCOUNT | 73 | 1.000 | 1.000 | 1.000 |
| CRIMINAL | 79 | 0.987 | 1.000 | 0.924 |
| DATE_OF_BIRTH | 347 | 1.000 | 1.000 | 1.000 |
| DISABILITY | 75 | 0.987 | 1.000 | 0.893 |
| DRIVER_LICENCE | 49 | 1.000 | 1.000 | 1.000 |
| EMAIL | 325 | 1.000 | 1.000 | 1.000 |
| ETHNICITY | 104 | 0.990 | 0.990 | 0.962 |
| FAMILY_VIOLENCE | 55 | 0.964 | 1.000 | 0.945 |
| HEALTH | 85 | 1.000 | 0.977 | 0.953 |
| INTERNAL_ID | 183 | 1.000 | 0.901 | 1.000 |
| MEDICARE | 69 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 29 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 431 | 0.993 | 0.993 | 0.993 |
| PASSPORT | 33 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 29 | 1.000 | 1.000 | 1.000 |
| PERSON | 1282 | 0.991 | 1.000 | 0.991 |
| PHONE | 363 | 1.000 | 1.000 | 1.000 |
| QUASI_ID | 24 | 1.000 | 1.000 | 1.000 |
| RELIGION | 72 | 1.000 | 1.000 | 0.958 |
| SECRET | 37 | 0.946 | 1.000 | 0.946 |
| SEXUALITY | 68 | 1.000 | 1.000 | 0.941 |
| TFN | 38 | 1.000 | 1.000 | 1.000 |
| UNION | 87 | 0.989 | 0.989 | 0.943 |
| URL_PERSONAL | 34 | 1.000 | 1.000 | 1.000 |
| USERNAME | 214 | 1.000 | 0.960 | 1.000 |

## Notes (half C)

- Run by the half-Q orchestrator after half Q was sealed, at the coordinator's request. Half C was not changed and its
  author was not contacted; there was no review round (any review is routed by the coordinator), so there is no
  "after" column.
- Working copy and labeller outputs: `<split-d>\half-q\xc\` (cases.jsonl copied verbatim,
  `labeller/raw/`, `labeller/labels_before.jsonl`, `labeller/metrics_before.json`,
  `labeller/disagreements_before.json` with every disagreement item).
- Validator verdict: see `VALIDATOR.md` in this folder.

