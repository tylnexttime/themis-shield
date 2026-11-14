# Usage summary (tokens as returned in `usage`; OpenRouter cost as reported)

| model | phase | calls | prompt | completion | reasoning (in completion) | cached (in prompt) | total | US$ |
|---|---|---|---|---|---|---|---|---|
| deepseek-v4.1-flash | author | 82 | 385631 | 82370 | 0 | 344576 | 468001 |  |
| deepseek-v4.1-flash | probe | 10 | 36306 | 6111 | 0 | 28928 | 42417 |  |
| deepseek-v4.1-flash | profile | 1 | 1781 | 1888 | 0 | 0 | 3669 |  |
| deepseek-v4.1-flash | repair | 32 | 135563 | 41538 | 0 | 66304 | 177101 |  |
| deepseek-v4.1-flash | review | 62 | 159612 | 2431 | 0 | 89088 | 162043 |  |
| deepseek-v4.1-flash | t2-review | 3 | 7106 | 96 | 0 | 3072 | 7202 |  |
| deepseek-v4.1-flash | topup | 10 | 44240 | 6342 | 0 | 40448 | 50582 |  |
| deepseek-v4.1-flash | topup2 | 3 | 11210 | 1944 | 0 | 9216 | 13154 |  |
| **deepseek-v4.1-flash** | **cumulative** | 203 | 781449 | 142720 | 0 | 581632 | **924169** (free quota at start 1,000,000) |  |
| kimi-k3.1-pro | label | 72 | 178266 | 69647 | 0 | 0 | 247913 |  |
| kimi-k3.1-pro | probe | 4 | 8949 | 620 | 0 | 2048 | 9569 |  |
| kimi-k3.1-pro | t2-label | 4 | 16291 | 11475 | 0 | 0 | 27766 |  |
| **kimi-k3.1-pro** | **cumulative** | 80 | 203506 | 81742 | 0 | 2048 | **285248** (free quota at start 393,000) |  |
| mistralai/mistral-large-2512 | author | 91 | 410912 | 123268 | 0 | 327808 | 534180 | 0.2496 |
| mistralai/mistral-large-2512 | probe | 10 | 38872 | 7666 | 0 | 19200 | 46538 | 0.0228 |
| mistralai/mistral-large-2512 | profile | 1 | 1839 | 2359 | 0 | 0 | 4198 | 0.0045 |
| mistralai/mistral-large-2512 | repair | 54 | 307319 | 95171 | 0 | 125568 | 402490 | 0.2497 |
| mistralai/mistral-large-2512 | review | 52 | 157273 | 3832 | 0 | 79872 | 161105 | 0.0499 |
| mistralai/mistral-large-2512 | t2-repair | 9 | 45912 | 22583 | 0 | 16128 | 68495 | 0.0516 |
| mistralai/mistral-large-2512 | t2-review | 4 | 12605 | 266 | 0 | 1664 | 12871 | 0.0061 |
| mistralai/mistral-large-2512 | topup | 18 | 88422 | 37572 | 0 | 66688 | 125994 | 0.0741 |
| mistralai/mistral-large-2512 | topup2 | 9 | 39291 | 18351 | 0 | 24960 | 57642 | 0.0388 |
| **mistralai/mistral-large-2512** | **cumulative** | 248 | 1102445 | 311068 | 0 | 661888 | **1413513** (serving: Mistral) | **0.7472** |
| openai/gpt-oss-120b | label | 79 | 186197 | 99027 | 32914 | 97160 | 285224 | 0.0436 |
| openai/gpt-oss-120b | pointer | 168 | 187669 | 12723 | 6243 | 31235 | 200392 | 0.0155 |
| openai/gpt-oss-120b | pointer2 | 11 | 11543 | 748 | 327 | 2283 | 12291 | 0.0007 |
| openai/gpt-oss-120b | probe | 5 | 5423 | 1583 | 802 | 244 | 7006 | 0.0015 |
| openai/gpt-oss-120b | t2-label | 3 | 6645 | 3123 | 1101 | 112 | 9768 | 0.0015 |
| openai/gpt-oss-120b | t2-pointer | 12 | 17888 | 1035 | 484 | 1108 | 18923 | 0.0019 |
| openai/gpt-oss-120b | t2-pointer2 | 4 | 10889 | 336 | 174 | 1696 | 11225 | 0.0008 |
| **openai/gpt-oss-120b** | **cumulative** | 282 | 426254 | 118575 | 42045 | 133838 | **544829** (serving: AkashML, CoreWeave, Crusoe, DeepInfra, DekaLLM, DigitalOcean, Groq, Mancer 2, Near AI, Parasail, PrimeIntellect, Sail Research, SiliconFlow, Venice) | **0.0655** |

All models together: 3,167,759 total tokens. OpenRouter spend: US$0.8127 (guard: refuse at US$4.80, hard limit US$5.00).
