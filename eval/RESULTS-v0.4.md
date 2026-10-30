# themis-shield 0.3.x on split E: evaluation results

Split E (half K), run once on the frozen kit `eval-kit-v0.4-frozen` (30/10/2026) under
[`FROZEN-PLAN-v0.4.md`](FROZEN-PLAN-v0.4.md). It tests 0.3.4, the release carrying every fix written from split D's
misses, against 0.3.0 as the control, on text that none of those fixes had seen.

## 1. Headline

- **Verdict: 0.3.4 FAILS its pass rule on two conditions:** released leaks (12, against a limit of 3) and
  over-redaction (412 look-alikes touched, one more than the control's 411). The instrument check passed (control:
  20 released leaks), so this is a FAIL, not INCONCLUSIVE. Nothing in the kit was changed after the result.
- **What leaked is narrow.** All 12 released spans are one staff-number format of the profiled organisation
  (`KWA-EMP-` and five digits) in prose. In every one, the gate found and substituted the personal part (`EMP-` and the
  digits) and left the organisation's own prefix, `KWA-`, in the text: the institutional allow-list trimmed the
  organisation's acronym off the front of a personal identifier (DEV-DEBT DD-17). The scorer counts any uncovered
  letter of a gold span, so each is a released leak. No personal digits were released. The control released the
  same 12 plus 8 more of the same format in two CSV tables, which 0.3.2's identifier columns now protect whole.
- **Everything else passed, most of it by a margin:** recall (R_any 0.993), sensitive statements (267 of 267,
  **1.000**; split D: 0.740), quasi-identifiers (1.000), and **clean-document friction 0.083** (5 of 60 held), the
  first time any release has met the 0.15 friction rule on a fresh split. No injection case released a leak.
- **The fixes from split D generalised.** On this new author's text, against 0.3.0: released leaks 20 to 12, R_full
  0.966 to 0.979, sensitive recall 0.985 to 1.000, friction 0.183 to 0.083, and no failed stages (0.3.0 failed on
  three of the six long documents, which 0.3.2's finder windows read whole).

## 2. Results (split E, half K: 210 cases, 2,731 gold spans, 60 clean, 1,501 hard negatives, 22 injection cases)

| Measure | 0.3.0 (control) | **0.3.4 (candidate)** | Rule | Candidate |
|---|---|---|---|---|
| Released leaks (spans) | 20 (all `INTERNAL_ID`) | **12** (all `INTERNAL_ID`, the `KWA-` prefix only) | ≤ 3 and ≤ control | **FAIL** |
| R_any / R_full | 0.9912 / 0.9656 | **0.9927 / 0.9788** | R_any ≥ 0.98 and ≥ control − 0.001 | met |
| Sensitive R_any (8 categories, 267 spans) | 0.985 (263) | **1.000 (267)** | ≥ 0.95 and ≥ control − 0.005 | met |
| Quasi-identifier R_any | 1.000 | **1.000** | ≥ control − 0.005 | met |
| Clean-case friction | 0.183 (11/60) | **0.083 (5/60)** | ≤ 0.15 and ≤ control | met |
| Over-redaction (hard negatives touched) | 411 / 1,501 (0.274) | **412** (0.275) | ≤ control | **FAIL** (+1 meeting date) |
| Failed stages from infrastructure | 0 | **0** | 0 | met |
| Finder stage failures (results) | 4, in 3 long documents (BLOCK) | **0** | reported | |
| Decisions (RELEASE / ASK / BLOCK) | 114 / 85 / 11 | 120 / 84 / 6 | | |
| Typed precision (any label) | 0.862 | 0.851 | reported | |

**Why the clean documents held:** candidate: injection signal 3 (by design), sensitive 1, empty-finder disagreement
1. Control: injection 3, empty-finder 5, secret 2, sensitive 1.

**Over-redaction.** The one extra look-alike is a meeting date (123 of 128 touched, control 122). Both arms touch
nearly every meeting date and every case number in this split; that is the next friction-and-utility problem, and
it belongs to v0.4's "keep the work possible" work rather than to detection.

**Indirect sensitive statements:** UNION, CRIMINAL and ETHNICITY R_any 1.000 in both arms.

## 3. Sensitivity score (reported, not gating)

In review, the author took 519 dates out of gold in two cases that the blind labeller marks as `OTHER_DATE`; the plan
registered a second score with those dates counted as gold (`half-k.alt-otherdate.jsonl`, built by script before any
arm ran). The verdict does not change, and released leaks are identical:

| Measure | 0.3.0 | 0.3.4 |
|---|---|---|
| Released leaks | 20 | 12 |
| R_any / R_full | 0.918 / 0.896 | 0.954 / 0.942 |

Neither arm protects most of those dates. Whether they are personal (a chat message's date stamp) or public (a log or
meeting date) is exactly what the author and the labeller disagreed on; now that split E is development data, they
can be read and settled.

## 4. The split

- **Half K** was written by `deepseek/deepseek-v4.1-flash-1017` (DeepSeek) with tools, from split D's brief; 278 tool
  calls; validator clean; every quota met. Labelled blind by `z-ai/glm-5.3-flash` (Z.ai): span recall / precision
  0.809 / 0.992 blind, 0.967 / 0.834 after the author's review (551 items fixed, 92 kept). Contamination, counts
  only: 14 distinct 8-grams in 15 cases shared with the published cases, 0 with the finder prompts.
- **Half M** (`xiaomi/mimo-v2.6-flash`) did not converge: 250 tool calls, no passing build. It was stopped and is not
  part of split E, as the plan provides, so the author-family effect could not be measured this time.
- Every call ran with zero data retention enforced. Total spend: **US$0.78** (split D: US$20.40).
- Files: [`cases/test-e-k.jsonl`](cases/test-e-k.jsonl) (sealed sha256 `25f93e2d…4c7d`, published copy `e0bcd276…cb18`),
  the generator, profile, provenance, agreement, review decisions and the half M log under
  [`generator_e/`](generator_e). The published copy differs from the sealed file in one invented place name, used in five table cases and the generator, replaced by another of the same length (every offset unchanged; the published generator rebuilds the published file exactly).

## 5. Timings

Candidate 71 minutes, control 65 minutes (live, on an 8 GB laptop GPU, both Gemma finders). Median 8.4 s per
document, p95 41 s, in both arms.

## 6. What this changes

1. **DD-17 (open, first):** the allow-list must not trim an institutional term that is joined to the rest of a
   finding's value (`KWA-EMP-92315`); an acronym inside an identifier is part of the identifier. Measured on all
   development sets, split E included, which is now development data.
2. The `OTHER_DATE` dispute in two cases is read and settled on the taxonomy.
3. Meeting dates and case numbers: almost all are touched. That is a utility cost the placeholder legend and
   per-task profiles are meant to reduce; it needs the utility measurement, not a detection change.
4. The next exam (split F) needs a second author from another family, under its own plan, and the same thresholds.

0.3.4 remains a **preview**, as every release before it: it fails its frozen rule, narrowly and on a defect we can
name, and it is better than its predecessor on every safety measure.
