# Frozen evaluation plan: eval-kit-v0.6-frozen (split G)

**Pass rule written 12/11/2026, before split G was written.** Split G is authored after this commit by a pipeline
that the gate's designer does not read: the designer sees counts, rule codes, hashes and costs only, until every arm
has run. This rule is committed first; the sealed files' sha256 values and the actual counts are added at sealing, in
a commit that changes nothing else, and the kit tag `eval-kit-v0.6-frozen` goes on that commit. Nothing below may
change after a test result is seen. Earlier plans: [`FROZEN-PLAN-v0.5.md`](FROZEN-PLAN-v0.5.md) (split F: FAIL),
[`FROZEN-PLAN-v0.4.md`](FROZEN-PLAN-v0.4.md) (split E: FAIL), [`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md) (split D:
FAIL), [`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md), [`FROZEN-PLAN.md`](FROZEN-PLAN.md).

## What is being tested, and why split F cannot test it

0.4.0 was released on 12/11/2026 as a preview. Its frozen test, split F, failed (released leaks 12, sensitive R_any
0.937, QUASI_ID one span behind the control, friction 0.2125; [`RESULTS-v0.5.md`](RESULTS-v0.5.md)). Everything added
after split F's kit tag (the review fixes, `location-columns`, `secret-config-lookalikes`, `propagate-detection-view`,
`negative-statements`, `unique-roles`, `quasi-cells`) was written from split F's misses or measured on it, so **split F
cannot judge 0.4.0**: it was opened once and is now development data. The verdict on **0.4.0 as released** comes from
split G alone.

Split F was written and labelled by Claude models, which broke the roadmap's three-families rule (its plan said so in
advance). Split G is written and labelled by **other model families**, and no family both writes and labels a half.

## Amendment before sealing (12/11/2026): split G is halves R and T pooled

Written after authoring ended and **before any arm ran on any half**. The decision was taken by Alder (the steward)
at 03:20 UTC on 12/11/2026 from counts, rule codes and hashes only: no case, value, profile term or description had
been read by Alder or by the deputy orchestrator. The thresholds, the arms, the pooling, the crash policy and the pass
rule below are **unchanged**; only which halves form split G changes.

**What happened to each half (counts):**

| Half | Author / labeller | Planned / dropped / final cases | Outcome after one repair round and one top-up |
|---|---|---|---|
| R (port and freight authority) | `kimi-k3` / `deepseek-v4-pro-0813` | 101 / 9 / 92 | Converged: validator 0 failures with every quota met, origin denylist 0, mutation test 26/26. |
| S (credit union) | `qwen3.8-max-0902` / `kimi-k3.1-pro` | 137 / 52 / 85 | Did not converge: 85 cases (< 90); HEALTH, DISABILITY, ETHNICITY, RELIGION, FAMILY_VIOLENCE and UNION under 12 gold spans each; negative statements in 5 of 8 categories; 5 quasi-identifier tables (< 8); 8 injection cases (< 11). |
| T (municipal council) | `glm-5.3` / `deepseek-v4.1-flash-1017` | 113 / 25 / 88 | Short only on size: 88 cases (two under the floor of 90); every content quota met after the review round; validator 0 failures. Four cases hit the origin denylist in their `organisation` metadata field only (the plan's sector label for one invented, unprofiled workplace), in no document text. |

No model was substituted (`kimi-k3.1-pro`, absent from the account's model list, was accepted by the endpoint).

**The decision.**
1. **Split G = halves R and T, pooled. Half S is dropped**: it stays unsealed, is not run and is not published as a
   test half. It is far from convergence on content (six sensitive categories short), not only on size.
2. **Half T is accepted at 88 cases.** The 90-110 row was a size target, not a pass condition; every content quota of
   half T is met. The shortfall is recorded here, as plan v0.4 recorded its own. No second top-up was run.
3. **Half T's metadata label is renamed by script** (`rename_org_t.py` in the authoring workspace): the one workplace
   label that hits the origin denylist becomes the neutral `Other workplace 4` in the four cases that carry it. The
   gate does not read this field. Document texts, gold spans and offsets are byte-identical before and after: a
   sha256 over each case's id, text and spans is identical in 88 of 88 cases (aggregate
   `00c338c8ea27e0c5d1890904059329158dbbe3c130ae4bcaa077e735806caff5` before and after); only the `organisation`
   field changed, in exactly those four cases; the validator is then clean apart from the accepted size row, the
   origin denylist finds 0 hits, the mutation test rejects 26 of 26.
4. Arms run on the halves in the order R, T; the verdict uses R and T pooled; each half is also reported apart.

**Why.** R and T keep two author families (Moonshot, Zhipu) with two DeepSeek labellers, so the three-families rule
still holds per half; half S's shortfall is in the sensitive categories the pass rule measures, so it could not be
accepted without weakening the test, while half T's is two documents of size with all content quotas met; running R
alone (plan v0.4's precedent) would discard a converged-on-content half; a second top-up for T would break the
one-top-up rule instead. Removing the Qwen-authored half also removes the limitation stated below for half S.

**Deviation on half R's pointer check.** With the original pointer prompt `deepseek-v4-pro-0813` flagged 68 of 98
half R documents, with reasons that treated invented names as real public figures. The check was re-run once with
the same question plus "every private person is invented; a name that merely sounds real is not a flag; when in doubt
false" (on a 15-text probe it flagged only the two planted real-world positives), and flagged 3 of 98. Half T kept
the original prompt (10 of 99 flagged on the first pass). The two halves' pointer checks are therefore not identical.

**Measured label agreement per half** (blind labeller vs the author's gold; exact recall / exact precision / overlap
recall / overlap precision):

| Half | Before review | After review |
|---|---|---|
| R | 0.864 / 0.886 / 0.904 / 0.927 | 0.882 / 0.926 / 0.913 / 0.958 |
| T | 0.892 / 0.689 / 0.950 / 0.734 | 0.917 / 0.878 / 0.961 / 0.921 |
| S (dropped; no review run) | 0.832 / 0.720 / 0.869 / 0.752 | n/a |

Split F (Claude on Claude) measured 0.975 / 0.967 exact: across model families the labels agree less, so split G's
gold carries more labelling disagreement than split F's.

## Test split G

| Item | Value |
|---|---|
| Files | Three halves, `half-r.jsonl`, `half-s.jsonl`, `half-t.jsonl`, kept outside the repository and unread by the gate's designer until the result is published; then committed with the results as `eval/cases/test-g-r.jsonl`, `test-g-s.jsonl`, `test-g-t.jsonl`, with the pipeline, profiles and provenance under `eval/generator_g/`. |
| sha256 | Added at sealing. |
| Size | Added at sealing (actual counts per half). |
| How it is written | **Per-document free prose**, as for split F: a deterministic planner (fixed seed, one plan per half) writes a specification per case (genre, length band, labels and counts, sensitive categories and whether each statement is direct, indirect or negative, hard-negative kinds, injection position, and pre-generated safe values for every checksummed or range-restricted identifier, phone and email); each document is written whole by the half's author model with gold marked inline; offsets are computed by code, never by a model. |
| Size target, per half | About 100 cases (90-110); at least 30 clean; every taxonomy label at least 8 gold spans; every sensitive category at least 12 gold spans, at least 65% of sensitive spans indirect, every category with negative statements; at least 8 "anonymised" tables with quasi-identifiers, location columns included; at least 6 hard-to-spot formats; 11-14 injection cases with positions balanced and at least 3 with no personal information; at least 3 long documents (9,000-24,000 characters). As in split F, the plan over-weights meeting dates, letter dates and case numbers as hard negatives in documents that also hold personal dates and personal identifiers, and organisation-issued personal identifiers written with the organisation's prefix. |
| Organisations | Each half has its own invented, profiled Australian organisation, written by that half's author from the shared profile schema, in a sector not used before (earlier: a training institute, two water utilities, a housing co-operative, a health and community-care network): **R** a regional port and freight authority; **S** a credit union (member-owned bank); **T** a municipal council. Each half also has several other invented workplaces, unprofiled. |
| Checks | The shared validator (split F's copy; only protocol constants changed: id prefix `G<half>-`, split `test-g`, halves R/S/T, and the quota numbers scaled to the size target above; its own checksum code; mutation test 26/26), the origin denylist, and a pointer check (a separate call per case, by the half's labeller model) that no invented person is tied to a real organisation or a real public figure. Failing cases get one repair round; cases still failing are dropped, not hand-edited; a quota shortfall gets at most one top-up. |
| Labels | Each half's labeller labels blind, from the taxonomy and the plain text. One review round: the half's author decides each disagreement. Agreement (exact-offset and overlap recall/precision) before and after review is published per half with the hashes. |
| Convergence | A half whose author does not converge (validator clean with every quota met after one repair round and one top-up) is **dropped, not hand-edited**, as plans v0.4 and v0.5 provide. Split G needs at least two halves; with fewer there is no verdict. |
| Orchestrator | A Claude model (Sonnet 5.5) builds and runs the pipeline, writes its prompts (adapted from split F's) and reports only counts, rule codes, hashes and costs to the gate's designer. |

### Model families

| Half | Author | Blind labeller and pointer check |
|---|---|---|
| R | `kimi-k3` (Moonshot) | `deepseek-v4-pro-0813` (DeepSeek) |
| S | `qwen3.8-max-0902` (Alibaba, Qwen) | `kimi-k3.1-pro` (Moonshot) |
| T | `glm-5.3` (Zhipu) | `deepseek-v4.1-flash-1017` (DeepSeek) |

The gate: Gemma 4 (Google) finders, PII-Tracer (a Qwen3-based classifier) and rules. All six models are served
through one Alibaba Cloud Model Studio account (Singapore, OpenAI-compatible chat completions); every request's
parameters and token usage are recorded, without headers.

**Substitution.** If a model code is refused, or an author cannot follow the markup after one prompt revision made on
throwaway probe specifications that are not part of the split, or a model runs out of its quota during a phase, it
is replaced from the same account in the order `qwen3.8-flash`, `deepseek-v4.1-flash`, `qwen3.8-27b`, skipping any
substitute of the same family as the other model of that half (so that author and labeller never share a family)
and any code already doing another job. Every substitution is recorded at sealing and in the result. At the time of
writing, `kimi-k3.1-pro` is not in the account's model list; if it is refused, half S's labeller becomes
`deepseek-v4.1-flash` (`qwen3.8-flash` is skipped: half S's author is a Qwen model).

**Limitations, stated in advance.**
- PII-Tracer is Qwen3-based and half S's author is a Qwen model, so on half S the author and one of the gate's models
  share a family. The pooled verdict stands; half S is reported apart for that reason, as every half is.
- Two halves have DeepSeek labellers. Agreement measures label consistency between two families, not truth; the
  after-review figures are partly circular (the author edits its own gold after seeing the labeller).
- The prompts the authors and labellers receive were written by a Claude model (the orchestrator), and the taxonomy
  and case format were written with Claude models.

### Data handling

Alibaba Cloud's published Model Studio FAQ says customer data is not used for model training; no zero-retention
setting is documented. Split G is therefore authored under **"no training per the provider's terms; retention not
stated"**, unlike splits D and E (zero data retention enforced). Every person, organisation, place, identifier and
document in split G is invented; no real personal data and no work material is sent.

## Kit

| Item | Value |
|---|---|
| Code | The commit carrying tag `eval-kit-v0.6-frozen`: **0.4.0 as released** (`main` at `14dd8fc`, tag `v0.4.0`) plus this plan and its sealing; no code, detector, policy, prompt, profile, catalogue, runner or scorer change. |
| Profiles | Each half's profile converted by `eval/convert_profile.py` without hand edits. Finder context: the first sentence of the profile's `description` (at most 120 characters, cut at a word), as for splits D, E and F. |
| Catalogues, typed tier, legend | Off, as for splits E and F. |
| Pipeline, weights, engine | As for split F: rules `core,au,intl`; PII-Tracer; Gemma 4 E4B Q4_K_M; Gemma 4 26B-A4B UD-IQ3_XXS; llama.cpp b11760; `--prompt v2 --max-tokens 8192 --chunk 40 --memory-settle-seconds 60 --allow-stage-failures`. |

## Arms (each run once per half, from the tag, clean tree, under the heavy-run lock)

Each arm runs on each half with that half's profile, halves in the order R, S, T, and within a half candidate,
previous, control. Before every run the half's sha256 is checked against this plan at the tag; a mismatch stops the
evaluation.

1. **Candidate (0.4.0):** the tagged code as released, every default detection switch on (the 30 of
   `DEFAULT_DETECTION_FIXES`; `finder-whitespace-trust` off), **live**, capturing every finder answer.
2. **Previous release (0.3.4):** the tagged code with 0.3.4's 23 default switches (the 30 minus `joined-terms`,
   `location-columns`, `secret-config-lookalikes`, `propagate-detection-view`, `negative-statements`, `unique-roles`,
   `quasi-cells`). None of these seven changes a finder prompt, so this arm is the candidate's live finder capture
   **replayed** with 0.3.4's switches: the same finder answers, so the difference between arms 1 and 2 is the seven
   switches. If the replay refuses a call (a prompt hash differs), the arm runs live instead and the result says so.
   Changes in 0.4.0 that are not behind a switch (among them the review fixes to `secret-references`) are in every
   arm, so this arm is 0.3.4's switch list on 0.4.0's code, not the 0.3.4 package.
3. **Control (0.3.0):** the tagged code with the control switch list of plans v0.4 and v0.5 (the 15 switches 0.3.0
   shipped with). Two of the off switches change the finder prompts, so the control runs **live** with its own
   capture.

**Pooling.** For each arm, the halves' predictions (with their decisions) and gold are concatenated and scored by the
standard scorer (`eval/score.py --split all --labelset themis --gold <pooled gold>`), as split D's `pool_score.py`
did. **The verdict uses split G pooled** over the halves that were sealed. Every measure is also reported for each
half separately: the differences between halves measure how much the author's family matters; they are not a second
verdict.

## Crash policy

As in `FROZEN-PLAN-v0.4.md`: infrastructure faults are fixed, recorded and resumed, never scored; a finder that
answers malformed output or runs out of budget is a result (the stage fails, the document is BLOCKed and counted); a
failing detector canary on a live arm stops the run as an infrastructure fault; a scored run is never re-run. A live
arm starts only on a quiet machine (at least 13 GB of available memory and no competing evaluation), because a model
load refused for memory mid-run would be scored as a BLOCK.

## Pass rule

The thresholds are those of splits D, E and F. They do not loosen. All measures are on split G pooled.

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on split G
pooled. Otherwise the result is **INCONCLUSIVE**.

**The candidate PASSES** if, on split G pooled, all of these hold:

| Condition | Limit |
|---|---|
| Released leaks | **≤ 3**, and ≤ the control's, and ≤ the previous release's |
| R_any | **≥ 0.98**, and ≥ control − 0.001, and ≥ previous − 0.001 |
| Sensitive R_any (the eight categories pooled) | **≥ 0.95**, and ≥ control − 0.005, and ≥ previous − 0.005 |
| QUASI_ID R_any | ≥ control − 0.005 |
| Clean-case friction | **≤ 0.15**, and ≤ the control's |
| Over-redaction (hard negatives touched) | ≤ the control's |
| FAILED stages from infrastructure (candidate, all halves) | **0** |

The ratchet against the previous release is part of the verdict, as in plan v0.5. There is no fallback configuration:
the candidate passes or fails, and the result says which conditions failed and by how much.

**Reported, not gating:** every measure above for each half; released leaks by label and by format; sensitive R_any
per category and for direct, indirect and negative statements separately; over-redaction by hard-negative kind
(meeting dates, letter dates and case numbers named); held-back unprotected spans by reason; released leaks inside
injection cases; documents blocked by finder failures; seconds per document; label agreement per half.

**Blindness:** until every arm has run on every half, the designer reads only the numeric fields of each arm's
`score.json` (the scorer's text report prints leaked values), and no case, value, profile term or description.
