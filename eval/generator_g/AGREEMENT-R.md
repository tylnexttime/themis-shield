# AGREEMENT: split G half R, gold (author kimi-k3, blind labeller deepseek-v4-pro-0813) against blind labelling

Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not compared). Rates are 0-1.

## Before review

- documents compared: 92
- documents with no labeller output: 0
- documents where the labeller's echo differed from the text (aligned by diff): 4
- labeller spans dropped in alignment: 0
- labeller markup errors (lenient parse): 1
- labeller outputs cut off at max_tokens: 0
- documents with at least one disagreement item: 58
- labeller model:deepseek-v4-pro-0813: 92
- any-label overlap recall (a gold span touched by any labeller span): 0.907

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 800 | 784 | 0.946 | 0.966 | 0.953 | 0.972 |
| USERNAME | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 44 | 58 | 1.000 | 0.759 | 1.000 | 0.759 |
| PHONE | 30 | 32 | 1.000 | 0.938 | 1.000 | 0.938 |
| ADDRESS | 24 | 22 | 0.875 | 0.955 | 0.875 | 0.955 |
| URL_PERSONAL | 15 | 15 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 58 | 61 | 0.914 | 0.869 | 0.914 | 0.869 |
| TFN | 19 | 19 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 16 | 19 | 1.000 | 0.842 | 1.000 | 0.842 |
| ABN | 16 | 13 | 0.812 | 1.000 | 0.812 | 1.000 |
| NATIONAL_ID | 16 | 16 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 14 | 14 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 14 | 16 | 1.000 | 0.875 | 1.000 | 0.875 |
| BANK_ACCOUNT | 15 | 14 | 0.933 | 1.000 | 0.933 | 1.000 |
| PAYMENT_CARD | 19 | 19 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 27 | 32 | 1.000 | 0.844 | 1.000 | 0.844 |
| DATE_OF_BIRTH | 28 | 28 | 1.000 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 71 | 77 | 0.803 | 0.740 | 0.803 | 0.740 |
| QUASI_ID | 42 | 39 | 0.714 | 0.769 | 0.833 | 0.897 |
| HEALTH | 30 | 23 | 0.200 | 0.261 | 0.633 | 0.826 |
| DISABILITY | 13 | 8 | 0.385 | 0.625 | 0.462 | 0.750 |
| ETHNICITY | 20 | 4 | 0.050 | 0.250 | 0.200 | 1.000 |
| SEXUALITY | 12 | 3 | 0.167 | 0.667 | 0.167 | 0.667 |
| RELIGION | 18 | 15 | 0.389 | 0.467 | 0.667 | 0.800 |
| CRIMINAL | 18 | 20 | 0.222 | 0.200 | 0.778 | 0.700 |
| FAMILY_VIOLENCE | 20 | 15 | 0.250 | 0.333 | 0.650 | 0.867 |
| UNION | 20 | 17 | 0.300 | 0.353 | 0.700 | 0.824 |
| ALL | 1437 | 1401 | 0.864 | 0.886 | 0.904 | 0.927 |

## After review

- documents compared: 92
- review: cases with a disagreement item: 58
- review: items: 294
- review: items decided keep: 202
- review: items decided fix: 92
- review: cases changed by script: 28
- review: fixes rejected by the script (text changed / validator / no document): 7
- review: requests that failed: 0
- documents with at least one disagreement item left: 51
- any-label overlap recall (a gold span touched by any labeller span): 0.914

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 805 | 784 | 0.948 | 0.973 | 0.954 | 0.980 |
| USERNAME | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 53 | 58 | 1.000 | 0.914 | 1.000 | 0.914 |
| PHONE | 32 | 32 | 1.000 | 1.000 | 1.000 | 1.000 |
| ADDRESS | 24 | 22 | 0.917 | 1.000 | 0.917 | 1.000 |
| URL_PERSONAL | 15 | 15 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 66 | 61 | 0.924 | 1.000 | 0.924 | 1.000 |
| TFN | 19 | 19 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 16 | 19 | 1.000 | 0.842 | 1.000 | 0.842 |
| ABN | 15 | 13 | 0.867 | 1.000 | 0.867 | 1.000 |
| NATIONAL_ID | 16 | 16 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 14 | 14 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 14 | 16 | 1.000 | 0.875 | 1.000 | 0.875 |
| BANK_ACCOUNT | 14 | 14 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 19 | 19 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 27 | 32 | 1.000 | 0.844 | 1.000 | 0.844 |
| DATE_OF_BIRTH | 28 | 28 | 1.000 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 67 | 77 | 0.851 | 0.740 | 0.851 | 0.740 |
| QUASI_ID | 42 | 39 | 0.857 | 0.923 | 0.857 | 0.923 |
| HEALTH | 32 | 23 | 0.312 | 0.435 | 0.688 | 0.957 |
| DISABILITY | 15 | 8 | 0.400 | 0.750 | 0.467 | 0.875 |
| ETHNICITY | 14 | 4 | 0.071 | 0.250 | 0.286 | 1.000 |
| SEXUALITY | 13 | 3 | 0.231 | 1.000 | 0.231 | 1.000 |
| RELIGION | 25 | 15 | 0.480 | 0.800 | 0.600 | 1.000 |
| CRIMINAL | 23 | 20 | 0.478 | 0.550 | 0.826 | 0.950 |
| FAMILY_VIOLENCE | 22 | 15 | 0.318 | 0.467 | 0.682 | 1.000 |
| UNION | 22 | 17 | 0.455 | 0.588 | 0.682 | 0.882 |
| ALL | 1470 | 1401 | 0.882 | 0.926 | 0.913 | 0.958 |
