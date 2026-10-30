# AGREEMENT: half K

Blind labeller: `z-ai/glm-5.3-flash` (Z.ai) under forced ZDR, temperature 0, one case per call; it saw only taxonomy.md and the case text and returned `{label, value}` spans plus case_level. Values were mapped to offsets by exact substring match, every occurrence (token-aligned occurrences first, raw substring only if none). Raw outputs: `split-e/orch-state/half-x/labeller/raw/`. Gold = the author's spans.

Definitions: recall = share of gold spans overlapped by a labeller span (label-sensitive: same label; blind: any label); precision = share of labeller spans overlapping a gold span (same rule); exact = share of gold spans with a labeller span of identical offsets and label. Cohen's kappa on case_level, per case.

| metric | before | after |
|---|---|---|
| gold spans | 3235 | 2731 |
| labeller spans (mapped) | 2644 | 3170 |
| recall (label-sensitive) | 0.809 | 0.967 |
| precision (label-sensitive) | 0.992 | 0.834 |
| recall (label-blind) | 0.809 | 0.967 |
| precision (label-blind) | 0.992 | 0.834 |
| exact match (label-sensitive) | 0.804 | 0.962 |
| exact match (label-blind) | 0.804 | 0.962 |
| kappa contains_pi | 0.988 | 1.000 |
| kappa health | 0.977 | 1.000 |
| kappa disability | 1.000 | 1.000 |
| kappa ethnicity | 0.895 | 0.950 |
| kappa sexuality | 1.000 | 1.000 |
| kappa religion | 1.000 | 1.000 |
| kappa criminal | 1.000 | 1.000 |
| kappa family_violence | 1.000 | 1.000 |
| kappa union | 0.957 | 1.000 |
| disagreement items | 643 | 617 |

Disagreement items by kind, before: {"labeller_only": 20, "case_level": 4, "gold_only": 618, "nested_label": 1}
after: {"gold_only": 91, "labeller_only": 525, "case_level": 1}

## Per label (recall / precision, label-sensitive; exact)

| label | gold before | R | P | exact | gold after | R | P | exact |
|---|---|---|---|---|---|---|---|---|
| ABN | 32 | 1.000 | 1.000 | 1.000 | 32 | 1.000 | 1.000 | 1.000 |
| ADDRESS | 97 | 0.691 | 0.985 | 0.691 | 97 | 0.691 | 0.985 | 0.691 |
| BANK_ACCOUNT | 34 | 1.000 | 0.943 | 0.941 | 36 | 1.000 | 1.000 | 0.944 |
| CRIMINAL | 31 | 0.968 | 1.000 | 0.871 | 31 | 1.000 | 1.000 | 0.968 |
| DATE_OF_BIRTH | 32 | 1.000 | 1.000 | 1.000 | 32 | 1.000 | 1.000 | 1.000 |
| DISABILITY | 36 | 1.000 | 1.000 | 0.972 | 36 | 1.000 | 1.000 | 0.944 |
| DRIVER_LICENCE | 23 | 1.000 | 1.000 | 1.000 | 23 | 1.000 | 1.000 | 1.000 |
| EMAIL | 165 | 0.988 | 0.994 | 0.988 | 165 | 1.000 | 0.994 | 1.000 |
| ETHNICITY | 33 | 0.939 | 1.000 | 0.939 | 33 | 1.000 | 1.000 | 1.000 |
| FAMILY_VIOLENCE | 31 | 1.000 | 1.000 | 0.935 | 31 | 1.000 | 1.000 | 0.968 |
| HEALTH | 48 | 0.958 | 1.000 | 0.938 | 48 | 1.000 | 1.000 | 0.979 |
| INTERNAL_ID | 321 | 0.850 | 0.986 | 0.850 | 321 | 0.860 | 0.986 | 0.860 |
| MEDICARE | 45 | 1.000 | 1.000 | 1.000 | 45 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 34 | 1.000 | 1.000 | 1.000 | 34 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 693 | 0.247 | 0.966 | 0.247 | 180 | 0.972 | 0.252 | 0.972 |
| PASSPORT | 28 | 1.000 | 1.000 | 1.000 | 28 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 31 | 1.000 | 1.000 | 1.000 | 31 | 1.000 | 1.000 | 1.000 |
| PERSON | 1123 | 0.996 | 0.997 | 0.996 | 1128 | 0.994 | 1.000 | 0.994 |
| PHONE | 90 | 0.989 | 0.989 | 0.989 | 89 | 1.000 | 1.000 | 1.000 |
| QUASI_ID | 78 | 1.000 | 1.000 | 1.000 | 78 | 1.000 | 1.000 | 1.000 |
| RELIGION | 30 | 0.967 | 1.000 | 0.967 | 30 | 1.000 | 1.000 | 1.000 |
| SECRET | 37 | 1.000 | 0.974 | 1.000 | 38 | 1.000 | 1.000 | 1.000 |
| SEXUALITY | 23 | 1.000 | 1.000 | 0.957 | 23 | 1.000 | 1.000 | 0.957 |
| TFN | 54 | 1.000 | 1.000 | 1.000 | 54 | 1.000 | 1.000 | 1.000 |
| UNION | 34 | 0.882 | 0.968 | 0.794 | 35 | 0.886 | 1.000 | 0.829 |
| URL_PERSONAL | 30 | 1.000 | 1.000 | 1.000 | 30 | 1.000 | 1.000 | 1.000 |
| USERNAME | 22 | 1.000 | 0.957 | 0.864 | 23 | 1.000 | 1.000 | 0.826 |

## Author's review round (one round, by tools): counts only

The author received all 643 disagreement items (`review_items.md`, R1-R643, taxonomy rows quoted) and decided each
one against the taxonomy, fixing its generator, not the cases. Decisions file: `review_decisions.md` (author's).
Coverage checked by the orchestrator: 643/643 item numbers covered; 551 marked fixed, 92 marked kept (with
reasons in the author's file; not reproduced here to keep this file numbers-only). The author has the final word
on gold.

After the review: 210 cases (156 texts changed, 54 unchanged, 0 added, 0 dropped); build, self-check and
validator passed (exit 0, all quotas met). The labeller re-labelled every changed text blind; unchanged texts
reuse the cached blind labels. Remaining disagreement items after review: 617 (not sent back: one review round
only).

