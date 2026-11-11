# AGREEMENT: split F, gold (author, claude-opus-5-5) against blind labelling (claude-sonnet-5-5, effort xhigh)

Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not compared). Rates are 0-1.

## Before review

- documents compared: 240
- documents with no labeller output: 0
- documents where the labeller's echo differed from the text (aligned by diff): 2
- labeller spans dropped in alignment: 21
- labeller markup errors (lenient parse): 0
- labeller outputs cut off at max_tokens: 0
- documents with at least one disagreement item: 70
- any-label overlap recall (a gold span touched by any labeller span): 0.994

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 2412 | 2414 | 0.997 | 0.996 | 0.998 | 0.997 |
| USERNAME | 95 | 95 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 138 | 138 | 1.000 | 1.000 | 1.000 | 1.000 |
| PHONE | 71 | 72 | 1.000 | 0.986 | 1.000 | 0.986 |
| ADDRESS | 100 | 101 | 0.990 | 0.980 | 1.000 | 0.990 |
| URL_PERSONAL | 44 | 44 | 0.955 | 0.955 | 1.000 | 1.000 |
| INTERNAL_ID | 233 | 235 | 0.996 | 0.987 | 0.996 | 0.987 |
| TFN | 43 | 44 | 1.000 | 0.977 | 1.000 | 0.977 |
| MEDICARE | 42 | 44 | 0.976 | 0.932 | 1.000 | 1.000 |
| ABN | 36 | 36 | 1.000 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 39 | 39 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 35 | 35 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 34 | 34 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 43 | 43 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 37 | 38 | 1.000 | 0.974 | 1.000 | 0.974 |
| SECRET | 65 | 65 | 1.000 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 70 | 70 | 1.000 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 294 | 282 | 0.946 | 0.986 | 0.946 | 0.986 |
| QUASI_ID | 106 | 112 | 1.000 | 0.946 | 1.000 | 0.946 |
| HEALTH | 69 | 93 | 0.725 | 0.538 | 0.971 | 0.720 |
| DISABILITY | 48 | 48 | 0.854 | 0.854 | 1.000 | 0.979 |
| ETHNICITY | 52 | 52 | 0.865 | 0.865 | 0.981 | 1.000 |
| SEXUALITY | 46 | 46 | 0.717 | 0.717 | 1.000 | 1.000 |
| RELIGION | 48 | 49 | 0.875 | 0.857 | 1.000 | 0.980 |
| CRIMINAL | 56 | 63 | 0.786 | 0.698 | 1.000 | 0.889 |
| FAMILY_VIOLENCE | 51 | 51 | 0.824 | 0.824 | 0.980 | 0.980 |
| UNION | 52 | 52 | 0.885 | 0.885 | 1.000 | 1.000 |
| ALL | 4359 | 4395 | 0.975 | 0.967 | 0.994 | 0.986 |

## After review

- documents compared: 240
- review: cases with a disagreement item: 70
- review: items: 168
- review: items decided keep: 71
- review: items decided fix: 97
- review: cases changed by script: 51
- review: fixes rejected by the script (text changed / validator / no document): 0
- documents with at least one disagreement item left: 43
- any-label overlap recall (a gold span touched by any labeller span): 0.994

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 2419 | 2414 | 0.998 | 1.000 | 0.998 | 1.000 |
| USERNAME | 95 | 95 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 138 | 138 | 1.000 | 1.000 | 1.000 | 1.000 |
| PHONE | 71 | 72 | 1.000 | 0.986 | 1.000 | 0.986 |
| ADDRESS | 101 | 101 | 0.990 | 0.990 | 1.000 | 1.000 |
| URL_PERSONAL | 44 | 44 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 235 | 235 | 0.996 | 0.996 | 0.996 | 0.996 |
| TFN | 44 | 44 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 42 | 44 | 0.976 | 0.932 | 1.000 | 1.000 |
| ABN | 36 | 36 | 1.000 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 39 | 39 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 35 | 35 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 34 | 34 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 43 | 43 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 38 | 38 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 65 | 65 | 1.000 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 70 | 70 | 1.000 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 295 | 282 | 0.949 | 0.993 | 0.949 | 0.993 |
| QUASI_ID | 112 | 112 | 1.000 | 1.000 | 1.000 | 1.000 |
| HEALTH | 95 | 93 | 0.884 | 0.903 | 0.979 | 1.000 |
| DISABILITY | 49 | 48 | 0.898 | 0.917 | 1.000 | 1.000 |
| ETHNICITY | 52 | 52 | 0.923 | 0.923 | 0.981 | 1.000 |
| SEXUALITY | 46 | 46 | 0.870 | 0.870 | 1.000 | 1.000 |
| RELIGION | 49 | 49 | 0.939 | 0.939 | 1.000 | 1.000 |
| CRIMINAL | 63 | 63 | 0.889 | 0.889 | 1.000 | 1.000 |
| FAMILY_VIOLENCE | 52 | 51 | 0.846 | 0.863 | 0.981 | 1.000 |
| UNION | 52 | 52 | 0.942 | 0.942 | 1.000 | 1.000 |
| ALL | 4414 | 4395 | 0.984 | 0.988 | 0.994 | 0.999 |
