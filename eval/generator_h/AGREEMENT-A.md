# AGREEMENT: split H half A, gold (author mistralai/mistral-large-2512, blind labeller kimi-k3.1-pro) against blind labelling

Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not compared). Rates are 0-1.

## Before review (first round and second top-up documents, each before its own review)

- documents compared: 76
- documents with no labeller output: 0
- documents where the labeller's echo differed from the text (aligned by diff): 2
- labeller spans dropped in alignment: 1
- labeller markup errors (lenient parse): 3
- labeller outputs cut off at max_tokens: 0
- documents with at least one disagreement item: 56
- labeller model:kimi-k3.1-pro: 76
- any-label overlap recall (a gold span touched by any labeller span): 0.878

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 732 | 722 | 0.825 | 0.837 | 0.870 | 0.884 |
| USERNAME | 12 | 9 | 0.667 | 0.889 | 0.667 | 0.889 |
| EMAIL | 25 | 66 | 1.000 | 0.379 | 1.000 | 0.379 |
| PHONE | 10 | 14 | 1.000 | 0.714 | 1.000 | 0.714 |
| ADDRESS | 35 | 46 | 0.943 | 0.717 | 0.971 | 0.739 |
| URL_PERSONAL | 7 | 7 | 0.714 | 0.714 | 0.714 | 0.714 |
| INTERNAL_ID | 66 | 123 | 0.939 | 0.504 | 0.939 | 0.504 |
| TFN | 8 | 18 | 1.000 | 0.444 | 1.000 | 0.444 |
| MEDICARE | 9 | 10 | 1.000 | 0.900 | 1.000 | 0.900 |
| ABN | 8 | 4 | 0.375 | 0.750 | 0.375 | 0.750 |
| NATIONAL_ID | 7 | 5 | 0.714 | 1.000 | 0.714 | 1.000 |
| DRIVER_LICENCE | 9 | 10 | 0.889 | 0.800 | 0.889 | 0.800 |
| PASSPORT | 6 | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 11 | 12 | 1.000 | 0.917 | 1.000 | 0.917 |
| PAYMENT_CARD | 12 | 12 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 11 | 13 | 1.000 | 0.846 | 1.000 | 0.846 |
| DATE_OF_BIRTH | 20 | 30 | 1.000 | 0.667 | 1.000 | 0.667 |
| OTHER_DATE | 42 | 175 | 0.762 | 0.183 | 0.881 | 0.211 |
| QUASI_ID | 42 | 43 | 0.976 | 0.953 | 0.976 | 0.953 |
| HEALTH | 41 | 73 | 0.366 | 0.205 | 0.805 | 0.452 |
| DISABILITY | 21 | 43 | 0.571 | 0.279 | 0.667 | 0.326 |
| ETHNICITY | 18 | 18 | 0.222 | 0.222 | 0.444 | 0.444 |
| SEXUALITY | 18 | 12 | 0.389 | 0.583 | 0.611 | 0.917 |
| RELIGION | 18 | 28 | 0.556 | 0.357 | 0.944 | 0.607 |
| CRIMINAL | 15 | 9 | 0.333 | 0.556 | 0.533 | 0.889 |
| FAMILY_VIOLENCE | 15 | 9 | 0.333 | 0.556 | 0.533 | 0.889 |
| UNION | 22 | 27 | 0.682 | 0.556 | 0.864 | 0.667 |
| ALL | 1240 | 1544 | 0.795 | 0.639 | 0.863 | 0.693 |

## After review (final gold of every labelled document)

- documents compared: 76
- review: cases with a disagreement item: 56
- review: items: 703
- review: items decided keep: 150
- review: items decided fix: 553
- review: cases changed by script: 38
- review: fixes rejected by the script (overlap / text changed / validator / quota): 18
- review: requests that failed: 0
- documents with at least one disagreement item left: 40
- any-label overlap recall (a gold span touched by any labeller span): 0.911

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 765 | 722 | 0.856 | 0.907 | 0.876 | 0.929 |
| USERNAME | 12 | 9 | 0.750 | 1.000 | 0.750 | 1.000 |
| EMAIL | 47 | 66 | 1.000 | 0.712 | 1.000 | 0.712 |
| PHONE | 13 | 14 | 1.000 | 0.929 | 1.000 | 0.929 |
| ADDRESS | 40 | 46 | 0.950 | 0.826 | 0.975 | 0.848 |
| URL_PERSONAL | 9 | 7 | 0.667 | 0.857 | 0.778 | 1.000 |
| INTERNAL_ID | 116 | 123 | 0.966 | 0.911 | 0.966 | 0.911 |
| TFN | 9 | 18 | 1.000 | 0.500 | 1.000 | 0.500 |
| MEDICARE | 9 | 10 | 1.000 | 0.900 | 1.000 | 0.900 |
| ABN | 8 | 4 | 0.375 | 0.750 | 0.375 | 0.750 |
| NATIONAL_ID | 7 | 5 | 0.714 | 1.000 | 0.714 | 1.000 |
| DRIVER_LICENCE | 9 | 10 | 0.889 | 0.800 | 0.889 | 0.800 |
| PASSPORT | 6 | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT | 12 | 12 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAYMENT_CARD | 12 | 12 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 13 | 13 | 1.000 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 22 | 30 | 1.000 | 0.733 | 1.000 | 0.733 |
| OTHER_DATE | 146 | 175 | 0.966 | 0.806 | 0.966 | 0.806 |
| QUASI_ID | 44 | 43 | 0.977 | 1.000 | 0.977 | 1.000 |
| HEALTH | 66 | 73 | 0.773 | 0.699 | 0.909 | 0.822 |
| DISABILITY | 38 | 43 | 0.947 | 0.837 | 0.947 | 0.837 |
| ETHNICITY | 17 | 18 | 0.647 | 0.611 | 0.765 | 0.722 |
| SEXUALITY | 15 | 12 | 0.800 | 1.000 | 0.800 | 1.000 |
| RELIGION | 26 | 28 | 0.808 | 0.750 | 0.962 | 0.893 |
| CRIMINAL | 13 | 9 | 0.462 | 0.667 | 0.615 | 0.889 |
| FAMILY_VIOLENCE | 12 | 9 | 0.583 | 0.778 | 0.667 | 0.889 |
| UNION | 28 | 27 | 0.893 | 0.926 | 0.964 | 0.963 |
| ALL | 1514 | 1544 | 0.880 | 0.863 | 0.904 | 0.887 |
