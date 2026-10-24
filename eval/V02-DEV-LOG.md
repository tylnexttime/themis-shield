# v0.2 development log

The v0.2 detection and policy changes, tuned on development data only. This log records the decision criteria
**before** the first fix run, then every run in order. Nothing here touches the v0.2 held-out test split, which is
written by an independent author and has not been seen.

## Development set

The whole v0.1 set is development data now: it has been seen.

| File | Cases | sha256 |
|---|---|---|
| `eval/cases/dev.jsonl` | 225 | `d663cca97191640518d93ed12938722285100e94666c57c56cc196d9d3568c25` |
| `eval/cases/test.jsonl` (v0.1 test, retired to dev) | 296 | `f1fbcb4311eb7579c1f309c3b9595e55b7dc253d73d7c0592196e0be258db792` |
| **v0.2 dev** (both files, `--cases eval/cases/dev.jsonl eval/cases/test.jsonl`) | **521** | |

521 cases, 4,483 gold spans, 60 clean cases (no personal information), 1,096 hard negatives (63 + 43 = 106 of
them sensitive look-alikes), 73 injection cases.

## Method: capture once, replay every fix

The finder prompt (v2) does not change in v0.2, and every fix sits before or after the LLM finders. So:

1. One **live** run over all 521 cases with the shipped default stack writes every finder response to a capture
   folder (`--capture-finders`): Gemma 4 E4B Q4_K_M and Gemma 4 26B-A4B UD-IQ3_XXS (`-ngl 99 -ncmoe 18 -c 16384`),
   rule packs `core,au,intl`, institution profile `harbourline-eval`, finder context "a training and research
   institute", prompt v2, v0.1 detection and policy (`--gate-version v0.1`).
2. Every later run **replays** that capture (`--replay-finders`): the rules, PII-Tracer (CPU), union, propagation,
   allow-list and policy run for real; the finder responses are served from the capture. Replay refuses any call
   whose prompt hash (messages, schema, max_tokens) or model identity (weights file, launch arguments, context)
   differs from the capture.
3. Before any fix, replaying the capture with the unchanged code must reproduce the live run's predictions and
   decisions byte for byte.

## Decision criteria (written before the first fix run)

Every candidate is a full replay over all 521 cases, scored with `eval/score.py` through `eval/run_eval.py`.

**Optimised, in strict priority order** (a later metric only breaks ties on the earlier ones; "tie" means equal):

1. **Released leaks**: gold spans not fully covered in cases the gate RELEASEd. Lower is better.
2. **Clean-case friction**: ASK or BLOCK on the 60 cases without personal information. Lower is better.
3. **Over-redaction**: hard negatives touched by any prediction, of 1,096. Lower is better.

**Guardrails** (a candidate that breaks one is rejected, whatever it gains):

- R_any must not fall below the baseline's.
- Sensitive-label R_any (HEALTH, DISABILITY, ETHNICITY, SEXUALITY, RELIGION, CRIMINAL, FAMILY_VIOLENCE, UNION
  pooled) must not fall below the baseline's.
- No FAILED stage in the run; the replay must serve every call (no refusal).

Reported for every run but not optimised: R_full, any-label and typed precision, partials and untouched spans,
decisions, ASK and BLOCK rates, reason counts, injection-case released leaks, sensitive look-alikes touched.

**Iterations.** At most three recorded tuning iterations, each a full replay over all 521 cases. A run that only
checks the harness (the replay proof, the baseline replay, the v0.1 control replay) is not an iteration.

**QUASI_ID in policy: two arms.** Whether a found QUASI_ID holds the document for ASK is decided by a pre-registered
two-arm comparison inside one iteration: the same code replayed with `--quasi-id-ask off` (substitute only) and
`--quasi-id-ask on` (ASK). Both arms are reported in full. The arm is picked by the criteria above (released leaks,
then clean friction, then over-redaction); if all three tie, **off** wins, because it asks less of people on every
document, not only clean ones (the total ASK count is the fourth tie-breaker).

**What a fix may not do.** Use gold spans, hard negatives or case ids at run time; key a rule on the eval set's value
pools (fiction phone ranges, reserved domains, published test numbers); or add eval phrases to the shipped code.
Deployment facts come from the institution profile only.

## Runs

Columns as in `RESULTS.md` §2. Over-redaction: hard negatives touched, of 1,096. Look-alike: sensitive look-alike
hard negatives touched, of 106. Clean friction: ASK or BLOCK on the 60 clean cases. All runs use the same finder
responses (the baseline capture); only the replays' code and switches differ.

| Run | Code / switches | R_full | R_any | Precision | Sens. R_any | Released leaks | Inj. leaks | ASK | BLOCK | Clean friction | Over-redaction | Look-alike | Wall min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v02-baseline-live (GPU capture) | `--gate-version v0.1` | 0.9864 | 0.9982 | 0.9218 | 0.9970 | 1 (QUASI_ID) | 0 | 350 | 43 | 0.233 (14/60) | 0.332 (364) | 9 | 113 |
| v02-baseline-replay | `--gate-version v0.1` | 0.9864 | 0.9982 | 0.9218 | 0.9970 | 1 (QUASI_ID) | 0 | 350 | 43 | 0.233 (14/60) | 0.332 (364) | 9 | 4 |
| v02-it1-qoff (iteration 1, arm off) | all fixes, `--quasi-id-ask off` | 0.9871 | 0.9989 | 0.9349 | 0.9970 | **0** | 0 | 340 | 43 | 0.067 (4/60) | 0.287 (315) | 9 | 3 |
| v02-it1-qon (iteration 1, arm on) | all fixes, `--quasi-id-ask on` | 0.9871 | 0.9989 | 0.9349 | 0.9970 | **0** | 0 | 354 | 43 | 0.067 (4/60) | 0.287 (315) | 9 | 3 |
| v02-it2 (iteration 2) | + contact whole-value fix, tracer confidence floor | 0.9871 | 0.9989 | 0.9367 | 0.9970 | **0** | 0 | 339 | 43 | 0.050 (3/60) | 0.279 (306) | 9 | 3 |

### Replay proof (before any fix ran)

- **20-case proof.** `proof-live`: a live run over 20 cases (18 drawn at random from the 521 short cases with seed
  20261024, plus `A-QI-007` and one clean case) with `--capture-finders`; `proof-replay`: the same cases replayed.
  `predictions.jsonl` (sha256 `73f889b1...6e2d`) and `decisions.jsonl` (`3db06742...6383`) are byte-identical.
  Replay refused a changed finder context ("prompt hash differs") and a changed 26B launch argument
  (`-ncmoe 20`: "model identities differ").
- **Baseline.** `v02-baseline-live` captured all 1,042 finder calls (521 cases x 2 finders; capture sha256
  `ba05c292...ffb4`) in 113 minutes; no stage failed, no llama-server was left running. Replaying it with
  `--gate-version v0.1` reproduced predictions (`7929abb2...f5fe`) and decisions (`6734b7c7...f1c`) byte for byte.
  The fix code was already in the tree but switched off by the version flag; the same check was repeated on the
  final code (`v02-final-v01-control`, identical again), so the v0.1 negative control is exact.
- The baseline differs from the v0.1 test run on the same cases (1 released leak instead of 5): finder outputs are
  not bit-for-bit reproducible across runs (prompt cache, `RESULTS.md` §8). Every v0.2 comparison below uses this one
  capture, so that noise is held constant.

### Baseline diagnosis

- **Clean friction 14/60.** 11 are `disagreement-empty-llm` on documents where both finders correctly returned
  nothing; PII-Tracer had flagged role mailboxes with a trailing full stop, public dates it called dates of birth,
  a weekday ("Tue"), or mailboxes the allow-list had already cancelled (the rule counted raw tracer findings). One
  more (`B-NG-002`, a plain question about postcodes) is the tracer calling "their postcode." an ADDRESS. 3 are
  `injection-signal` on clean injection cases, which is by design.
- **Released leak 1:** "Branch manager" in the `dob, postcode, role` table of `A-QI-008`.

### Iteration 1: all fixes, both QUASI_ID arms

Changes (each with unit tests; `[detection] version` / `[policy] version` keep v0.1 reachable):

1. Empty-finder rule counts only tracer findings that survive the final allow-list, by label; OTHER_DATE alone is
   not contrary evidence; tracer document sensitivity >= 0.5 still asks.
2. Column-aware table pass (`propagate.table_quasi_spans`): in tables with an identifier-like column (DOB, age,
   postcode, dates) or an anonymised / de-identified label, a categorical cell (role, title, team, site, attribute
   and synonyms) occurring at most twice in its column becomes QUASI_ID; a whole cell that is an organisation term is
   allow-listed.
3. `[policy] quasi_id_ask` switch.
4. Tier 1: tracer dates with a time of day, bare weekdays and the profile's public dates become OTHER_DATE; a bare
   weekday or time on its own is cancelled; trailing punctuation is stripped before the contact whole-value match;
   a USI needs a digit; the organisation's own ABN (profile) is allow-listed even when checksum-valid.

Result (arm off): released leaks 1 -> **0**; clean friction 14 -> 4 (the 10 empty-finder holds on timetables and
notices are gone); over-redaction 364 -> 315 (public dates -20, role mailboxes -16, own ABN -13, "Breakwater" as USI
-8, one site); precision 0.922 -> 0.935; R_any up, sensitive R_any equal. Costs: 9 reception phone numbers written
"(08) 7010 ..." became redacted: the new leading-bracket strip moved the span start off the public-number match (a
bug in fix 4). OTHER_DATE R_any fell from 1.000 to 0.993 (2 spans, see iteration 3).

**QUASI_ID-ASK decision (pre-registered two arms).** Arm on: released leaks 0, clean friction 4/60, over-redaction
315: identical to arm off on all three criteria, but 14 more documents held for ASK (354 vs 340), 13 of them for a
QUASI_ID PII-Tracer attached to a salary, a mobile number or a lone "M" (its coarse `other_pii` label), and one
(`A-QI-008`) whose table values the new pass already substitutes. By the written tie-break, **arm off is chosen**:
a found quasi-identifier is substituted, not asked about. The switch stays (`[policy] quasi_id_ask = true`).

### Iteration 2

From iteration 1's diagnostics: (a) the contact whole-value match accepts the span as found **or** with trailing
punctuation / leading brackets stripped (fixes the 9 phones); (b) a tracer finding counts as contrary evidence only
at tracer confidence >= 0.9 (or no score). On the 60 clean cases every tracer score was >= 0.96 except "their
postcode." at 0.78; the floor is a single threshold, not a per-case rule, but it was set after seeing that case.

Result: clean friction 4 -> **3** (all three are `injection-signal`, by design); over-redaction 315 -> **306**
(the 9 phones back); precision 0.937; every other metric equal. Guardrails hold.

### Iteration 3 (planned before running)

Iteration 2's per-label table shows OTHER_DATE R_any 0.993: two personal dates in generator-A documents
(`A-OB-006` a signing date, `A-LD-002` a refund date) fall on one of the fictional institute's public dates, so the
public-date exemption cancelled them once the tracer's DATE_OF_BIRTH label became OTHER_DATE. Both cases were held
(ASK), so these are not released leaks, but a person's date on a public day is a real pattern. Change: the date
exemptions (public dates, bare weekdays and times) never override a local finder that tagged the date; they cancel
only dates flagged by tier 1 alone. Expected: OTHER_DATE back to 1.000; some public dates both finders tagged stay
redacted (over-redaction may rise slightly). Accepted if the guardrails hold and released leaks and friction do not
rise.

| Run | Code / switches | R_full | R_any | Precision | Sens. R_any | Released leaks | Inj. leaks | ASK | BLOCK | Clean friction | Over-redaction | Look-alike | Wall min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v02-it3 (iteration 3, **not adopted**) | it2 + date exemptions yield to finder evidence | 0.9875 | 0.9993 | 0.9331 | 0.9970 | 0 | 0 | 339 | 43 | 0.050 (3/60) | 0.283 (310) | 9 | 3 |

Result: OTHER_DATE R_any back to 1.000 (untouched spans 5 -> 3), R_any 0.9989 -> 0.9993; released leaks and
friction unchanged; over-redaction 306 -> **310** (four public dates that both a finder and the tracer tagged in
minutes and an injection case stay redacted).

### Selection

By the criteria written before the first fix run, **iteration 2 is selected**: it ties iteration 3 on released
leaks (0) and clean friction (3/60) and wins on over-redaction (306 vs 310). R_any is a guardrail, not an optimised
metric, so iteration 3's recall gain does not count under the rule as written. The acceptance line in the
iteration-3 plan above ("accepted if ... released leaks and friction do not rise") was written after iteration 2
and would have changed the rule; the rule written first stands. The iteration-3 change is kept as a separate commit
on branch `v0.2-it3-date-exemptions` for a decision before the freeze: it closes a recall hole (a person's date on
a public day is cancelled when the tracer's label was normalised) at the cost of four touched public dates.

`v02-final` (the selected code with every switch at its default: detection v0.2, policy v0.2, `quasi_id_ask` off)
reproduces `v02-it2` byte for byte (predictions sha256 `1496dea0...4fef`, decisions `e0805385...c3f6`).
`v02-final-v01-control` (the same code with `--gate-version v0.1`) reproduces the baseline byte for byte.

### Final dev numbers (v02-final vs the v0.1 baseline, same finder responses)

| | v0.1 (baseline) | v0.2 (final) |
|---|---|---|
| Released leaks | 1 (QUASI_ID) | **0** |
| Clean-case friction | 0.233 (14/60) | **0.050 (3/60)**, all `injection-signal` |
| Over-redaction (hard negatives touched) | 0.332 (364/1096) | **0.279 (306/1096)** |
| R_any / R_full | 0.9982 / 0.9864 | 0.9989 / 0.9871 |
| Sensitive R_any / R_full | 0.9970 / 0.9327 | 0.9970 / 0.9327 |
| QUASI_ID R_any / R_full | 0.935 / 0.826 | 0.989 / 0.880 |
| OTHER_DATE R_any | 1.000 | 0.993 (2 spans, both in ASK cases) |
| Any-label / typed precision | 0.922 / 0.834 | 0.937 / 0.847 |
| Untouched / partial spans | 8 / 53 | 5 / 53 |
| RELEASE / ASK / BLOCK | 128 / 350 / 43 | 139 / 339 / 43 |
| `disagreement-empty-llm` cases | 12 | 0 |
| Sensitive look-alikes touched | 9/106 | 9/106 |
| Clean cases with any redaction (`contains_pi` false-positive rate) | 0.667 | 0.350 |

### What stays open

- `disagreement-empty-llm` never fired on a case with personal information in either run, so the dev set cannot
  show that the narrowed rule still catches a real double miss; only the unit tests do.
- The table pass fired only on the two `dob, postcode, role` tables (`A-QI-007`, `A-QI-008`); no other dev table
  has an identifier-like column next to a categorical one. Its precision on other layouts is unmeasured.
- The 0.9 tracer-confidence floor was set after seeing one dev case (`B-NG-002`).
- Over-redaction is still 28 %: tracer ADDRESS and STUDENT_ID on generator-A organisations' own addresses and
  reference numbers, which no profile describes.
