# AGREEMENT: split G half T, gold (author glm-5.3, blind labeller deepseek-v4.1-flash-1017) against blind labelling

Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not compared). Rates are 0-1.

## Before review

- documents compared: 88
- documents with no labeller output: 0
- documents where the labeller's echo differed from the text (aligned by diff): 3
- labeller spans dropped in alignment: 0
- labeller markup errors (lenient parse): 0
- labeller outputs cut off at max_tokens: 0
- documents with at least one disagreement item: 56
- labeller model:deepseek-v4.1-flash-1017: 88
- any-label overlap recall (a gold span touched by any labeller span): 0.960

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 480 | 631 | 0.983 | 0.748 | 0.992 | 0.754 |
| USERNAME | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 39 | 47 | 1.000 | 0.830 | 1.000 | 0.830 |
| PHONE | 20 | 20 | 0.950 | 0.950 | 1.000 | 1.000 |
| ADDRESS | 21 | 22 | 0.952 | 0.909 | 0.952 | 0.909 |
| URL_PERSONAL | 16 | 16 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 38 | 70 | 0.711 | 0.386 | 0.711 | 0.386 |
| TFN | 20 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 14 | 12 | 0.857 | 1.000 | 0.857 | 1.000 |
| ABN | 14 | 10 | 0.643 | 0.900 | 0.643 | 0.900 |
| NATIONAL_ID | 17 | 17 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 17 | 17 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 11 | 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 10 | 10 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 16 | 16 | 0.812 | 0.812 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 30 | 42 | 1.000 | 0.714 | 1.000 | 0.714 |
| OTHER_DATE | 64 | 97 | 0.906 | 0.598 | 0.906 | 0.598 |
| QUASI_ID | 33 | 40 | 0.848 | 0.700 | 0.848 | 0.700 |
| HEALTH | 13 | 32 | 0.615 | 0.250 | 1.000 | 0.406 |
| DISABILITY | 16 | 23 | 0.438 | 0.304 | 0.875 | 0.609 |
| ETHNICITY | 12 | 21 | 0.333 | 0.190 | 0.917 | 0.524 |
| SEXUALITY | 13 | 8 | 0.154 | 0.250 | 0.538 | 0.875 |
| RELIGION | 8 | 24 | 0.375 | 0.125 | 1.000 | 0.333 |
| CRIMINAL | 19 | 28 | 0.368 | 0.250 | 1.000 | 0.679 |
| FAMILY_VIOLENCE | 19 | 21 | 0.368 | 0.333 | 0.737 | 0.667 |
| UNION | 9 | 10 | 0.444 | 0.400 | 0.778 | 0.700 |
| ALL | 1005 | 1301 | 0.892 | 0.689 | 0.950 | 0.734 |

## After review

- documents compared: 88
- review: cases with a disagreement item: 56
- review: items: 438
- review: items decided keep: 155
- review: items decided fix: 283
- review: cases changed by script: 38
- review: fixes rejected by the script (text changed / validator / no document): 7
- review: requests that failed: 0
- documents with at least one disagreement item left: 46
- any-label overlap recall (a gold span touched by any labeller span): 0.966

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 581 | 631 | 0.986 | 0.908 | 0.993 | 0.914 |
| USERNAME | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 46 | 47 | 1.000 | 0.979 | 1.000 | 0.979 |
| PHONE | 20 | 20 | 0.950 | 0.950 | 1.000 | 1.000 |
| ADDRESS | 22 | 22 | 0.955 | 0.955 | 0.955 | 0.955 |
| URL_PERSONAL | 16 | 16 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 77 | 70 | 0.857 | 0.943 | 0.857 | 0.943 |
| TFN | 20 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 14 | 12 | 0.857 | 1.000 | 0.857 | 1.000 |
| ABN | 11 | 10 | 0.818 | 0.900 | 0.818 | 0.900 |
| NATIONAL_ID | 17 | 17 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE | 17 | 17 | 1.000 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 11 | 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 18 | 18 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 10 | 10 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 16 | 16 | 0.812 | 0.812 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 42 | 42 | 1.000 | 1.000 | 1.000 | 1.000 |
| OTHER_DATE | 83 | 97 | 0.928 | 0.794 | 0.928 | 0.794 |
| QUASI_ID | 45 | 40 | 0.889 | 1.000 | 0.889 | 1.000 |
| HEALTH | 22 | 32 | 0.773 | 0.531 | 1.000 | 0.688 |
| DISABILITY | 22 | 23 | 0.636 | 0.609 | 0.909 | 0.870 |
| ETHNICITY | 22 | 21 | 0.682 | 0.714 | 0.955 | 1.000 |
| SEXUALITY | 15 | 8 | 0.200 | 0.375 | 0.533 | 1.000 |
| RELIGION | 21 | 24 | 0.714 | 0.625 | 1.000 | 0.875 |
| CRIMINAL | 24 | 28 | 0.542 | 0.464 | 1.000 | 0.857 |
| FAMILY_VIOLENCE | 24 | 21 | 0.542 | 0.619 | 0.792 | 0.905 |
| UNION | 12 | 10 | 0.583 | 0.700 | 0.833 | 1.000 |
| ALL | 1246 | 1301 | 0.917 | 0.878 | 0.961 | 0.921 |
