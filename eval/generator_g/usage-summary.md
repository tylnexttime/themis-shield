# Usage summary (Alibaba Model Studio; tokens as returned in `usage`)

| model | phase | calls | prompt | completion | reasoning (in completion) | cached (in prompt) | total |
|---|---|---|---|---|---|---|---|
| deepseek-v4-pro-0813 | label | 96 | 389389 | 79504 | 0 | 305408 | 468893 |
| deepseek-v4-pro-0813 | pointer | 98 | 85356 | 5301 | 0 | 0 | 90657 |
| deepseek-v4-pro-0813 | pointer2 | 3 | 2361 | 113 | 0 | 256 | 2474 |
| deepseek-v4-pro-0813 | pointerb | 98 | 93392 | 3756 | 0 | 6912 | 97148 |
| deepseek-v4-pro-0813 | probe | 20 | 14240 | 1239 | 0 | 0 | 15479 |
| **deepseek-v4-pro-0813** | **cumulative** | 315 | 584738 | 89913 | 0 | 312576 | **674651** (free quota at start 1,000,000) |
| deepseek-v4.1-flash-1017 | label | 88 | 373449 | 76719 | 0 | 289024 | 450168 |
| deepseek-v4.1-flash-1017 | pointer | 99 | 88858 | 4512 | 0 | 0 | 93370 |
| deepseek-v4.1-flash-1017 | pointer2 | 16 | 10465 | 810 | 0 | 1536 | 11275 |
| deepseek-v4.1-flash-1017 | probe | 5 | 4650 | 720 | 0 | 0 | 5370 |
| **deepseek-v4.1-flash-1017** | **cumulative** | 208 | 477422 | 82761 | 0 | 290560 | **560183** (free quota at start 1,000,000) |
| glm-5.3 | author | 99 | 549727 | 130915 | 37341 | 496640 | 680642 |
| glm-5.3 | probe | 12 | 17265 | 5448 | 2924 | 11776 | 22713 |
| glm-5.3 | profile | 1 | 1709 | 3211 | 576 | 0 | 4920 |
| glm-5.3 | repair | 63 | 228374 | 77113 | 12697 | 112640 | 305487 |
| glm-5.3 | review | 56 | 202437 | 72771 | 14438 | 98560 | 275208 |
| glm-5.3 | topup | 14 | 76216 | 8055 | 627 | 71680 | 84271 |
| **glm-5.3** | **cumulative** | 245 | 1075728 | 297513 | 68603 | 791296 | **1373241** (free quota at start 1,000,000) |
| kimi-k3 | author | 99 | 549521 | 101495 | 0 | 437760 | 651016 |
| kimi-k3 | probe | 9 | 22075 | 10241 | 8000 | 3072 | 32316 |
| kimi-k3 | profile | 1 | 1725 | 2467 | 0 | 0 | 4192 |
| kimi-k3 | repair | 66 | 248288 | 74251 | 0 | 21504 | 322539 |
| kimi-k3 | review | 58 | 202641 | 47456 | 0 | 26112 | 250097 |
| kimi-k3 | topup | 2 | 10885 | 1279 | 0 | 9216 | 12164 |
| **kimi-k3** | **cumulative** | 235 | 1035135 | 237189 | 8000 | 497664 | **1272324** (free quota at start 857,000) |
| kimi-k3.1-pro | label | 85 | 350312 | 112885 | 0 | 165888 | 463197 |
| kimi-k3.1-pro | pointer | 98 | 106200 | 4762 | 0 | 0 | 110962 |
| kimi-k3.1-pro | pointer2 | 31 | 26250 | 1615 | 0 | 2048 | 27865 |
| kimi-k3.1-pro | probe | 5 | 4588 | 669 | 0 | 0 | 5257 |
| **kimi-k3.1-pro** | **cumulative** | 219 | 487350 | 119931 | 0 | 167936 | **607281** (free quota at start 1,000,000) |
| qwen3.8-max-0902 | author | 99 | 552933 | 125594 | 0 | 502784 | 678527 |
| qwen3.8-max-0902 | probe | 7 | 17957 | 53284 | 50999 | 7168 | 71241 |
| qwen3.8-max-0902 | profile | 1 | 1785 | 2044 | 0 | 0 | 3829 |
| qwen3.8-max-0902 | repair | 61 | 261820 | 90270 | 0 | 76416 | 352090 |
| qwen3.8-max-0902 | topup | 41 | 207719 | 33211 | 0 | 195328 | 240930 |
| **qwen3.8-max-0902** | **cumulative** | 209 | 1042214 | 304403 | 50999 | 781696 | **1346617** (free quota at start 808,000) |

All models together: 5,834,297 total tokens (guards: 2,000,000 per model, 8,000,000 all).
