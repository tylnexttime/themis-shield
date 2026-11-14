# AGREEMENT: split H half B, gold (author deepseek-v4.1-flash, blind labeller openai/gpt-oss-120b) against blind labelling

Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not compared). Rates are 0-1.

## Before review (first round and second top-up documents, each before its own review)

- documents compared: 82
- documents with no labeller output: 0
- documents where the labeller's echo differed from the text (aligned by diff): 48
- labeller spans dropped in alignment: 15
- labeller markup errors (lenient parse): 7
- labeller outputs cut off at max_tokens: 0
- documents with at least one disagreement item: 65
- labeller model:openai/gpt-oss-120b: 82
- any-label overlap recall (a gold span touched by any labeller span): 0.820

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 517 | 537 | 0.925 | 0.890 | 0.948 | 0.912 |
| USERNAME | 11 | 6 | 0.545 | 1.000 | 0.545 | 1.000 |
| EMAIL | 22 | 79 | 0.955 | 0.266 | 1.000 | 0.278 |
| PHONE | 22 | 47 | 0.818 | 0.383 | 0.818 | 0.383 |
| ADDRESS | 15 | 29 | 0.800 | 0.414 | 0.867 | 0.448 |
| URL_PERSONAL | 11 | 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 100 | 55 | 0.400 | 0.727 | 0.400 | 0.727 |
| TFN | 12 | 13 | 1.000 | 0.923 | 1.000 | 0.923 |
| MEDICARE | 12 | 10 | 0.750 | 0.900 | 0.750 | 0.900 |
| ABN | 11 | 0 | 0.000 | n/a | 0.000 | n/a |
| NATIONAL_ID | 8 | 6 | 0.750 | 1.000 | 0.750 | 1.000 |
| DRIVER_LICENCE | 9 | 5 | 0.556 | 1.000 | 0.556 | 1.000 |
| PASSPORT | 10 | 9 | 0.800 | 0.889 | 0.800 | 0.889 |
| BANK_ACCOUNT | 11 | 10 | 0.727 | 0.800 | 0.909 | 1.000 |
| PAYMENT_CARD | 10 | 10 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 14 | 11 | 0.429 | 0.545 | 0.429 | 0.545 |
| DATE_OF_BIRTH | 16 | 17 | 0.875 | 0.824 | 1.000 | 0.941 |
| OTHER_DATE | 38 | 150 | 0.816 | 0.207 | 0.816 | 0.207 |
| QUASI_ID | 30 | 29 | 0.667 | 0.690 | 0.700 | 0.724 |
| HEALTH | 13 | 42 | 0.077 | 0.024 | 0.769 | 0.238 |
| DISABILITY | 13 | 12 | 0.231 | 0.250 | 0.308 | 0.333 |
| ETHNICITY | 10 | 7 | 0.300 | 0.429 | 0.500 | 0.714 |
| SEXUALITY | 15 | 9 | 0.000 | 0.000 | 0.400 | 0.667 |
| RELIGION | 14 | 15 | 0.071 | 0.067 | 0.643 | 0.600 |
| CRIMINAL | 13 | 11 | 0.000 | 0.000 | 0.538 | 0.636 |
| FAMILY_VIOLENCE | 15 | 16 | 0.267 | 0.250 | 0.667 | 0.688 |
| UNION | 18 | 9 | 0.111 | 0.222 | 0.333 | 0.667 |
| ALL | 990 | 1155 | 0.736 | 0.631 | 0.799 | 0.686 |

## After review (final gold of every labelled document)

- documents compared: 82
- review: cases with a disagreement item: 65
- review: items: 600
- review: items decided keep: 400
- review: items decided fix: 200
- review: cases changed by script: 29
- review: fixes rejected by the script (overlap / text changed / validator / quota): 10
- review: requests that failed: 0
- documents with at least one disagreement item left: 62
- any-label overlap recall (a gold span touched by any labeller span): 0.845

| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |
|---|---|---|---|---|---|---|
| PERSON | 534 | 537 | 0.942 | 0.937 | 0.959 | 0.953 |
| USERNAME | 8 | 6 | 0.750 | 1.000 | 0.750 | 1.000 |
| EMAIL | 34 | 79 | 0.971 | 0.418 | 1.000 | 0.430 |
| PHONE | 26 | 47 | 0.846 | 0.468 | 0.846 | 0.468 |
| ADDRESS | 20 | 29 | 0.850 | 0.586 | 0.900 | 0.621 |
| URL_PERSONAL | 11 | 11 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 101 | 55 | 0.406 | 0.745 | 0.406 | 0.745 |
| TFN | 13 | 13 | 1.000 | 1.000 | 1.000 | 1.000 |
| MEDICARE | 12 | 10 | 0.750 | 0.900 | 0.750 | 0.900 |
| ABN | 9 | 0 | 0.000 | n/a | 0.000 | n/a |
| NATIONAL_ID | 8 | 6 | 0.750 | 1.000 | 0.750 | 1.000 |
| DRIVER_LICENCE | 9 | 5 | 0.556 | 1.000 | 0.556 | 1.000 |
| PASSPORT | 10 | 9 | 0.800 | 0.889 | 0.800 | 0.889 |
| BANK_ACCOUNT | 11 | 10 | 0.818 | 0.900 | 0.909 | 1.000 |
| PAYMENT_CARD | 10 | 10 | 1.000 | 1.000 | 1.000 | 1.000 |
| SECRET | 15 | 11 | 0.467 | 0.636 | 0.467 | 0.636 |
| DATE_OF_BIRTH | 16 | 17 | 0.875 | 0.824 | 1.000 | 0.941 |
| OTHER_DATE | 62 | 150 | 0.887 | 0.367 | 0.887 | 0.367 |
| QUASI_ID | 34 | 29 | 0.706 | 0.828 | 0.735 | 0.862 |
| HEALTH | 30 | 42 | 0.700 | 0.500 | 0.900 | 0.643 |
| DISABILITY | 11 | 12 | 0.364 | 0.333 | 0.455 | 0.417 |
| ETHNICITY | 11 | 7 | 0.455 | 0.714 | 0.636 | 1.000 |
| SEXUALITY | 14 | 9 | 0.286 | 0.444 | 0.500 | 0.778 |
| RELIGION | 17 | 15 | 0.412 | 0.467 | 0.706 | 0.800 |
| CRIMINAL | 15 | 11 | 0.400 | 0.545 | 0.600 | 0.818 |
| FAMILY_VIOLENCE | 15 | 16 | 0.600 | 0.562 | 0.667 | 0.625 |
| UNION | 18 | 9 | 0.222 | 0.444 | 0.333 | 0.667 |
| ALL | 1074 | 1155 | 0.794 | 0.739 | 0.830 | 0.771 |
