# Frozen evaluation plan: eval-kit-v0.7-frozen (split H)

**Pass rule written 13/11/2026, before split H was written.** Split H is authored after this commit by a pipeline
that the gate's designer does not read: the designer sees counts, rule codes, hashes and costs only, until every arm
has run. This rule is committed first; the sealed files' sha256 values and the actual counts are added at sealing, in
a commit that changes nothing else. Nothing below may change after a test result is seen. Earlier plans:
[`FROZEN-PLAN-v0.6.md`](FROZEN-PLAN-v0.6.md) (split G: FAIL), [`FROZEN-PLAN-v0.5.md`](FROZEN-PLAN-v0.5.md) (split F:
FAIL), [`FROZEN-PLAN-v0.4.md`](FROZEN-PLAN-v0.4.md) (split E: FAIL), [`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md)
(split D: FAIL), [`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md), [`FROZEN-PLAN.md`](FROZEN-PLAN.md).

This plan is split G's plan ([`FROZEN-PLAN-v0.6.md`](FROZEN-PLAN-v0.6.md)) with the changes listed here: a new
candidate and previous release, two halves of about 80 cases written and labelled by four model families, two new
sectors, and two providers. The thresholds, the pass rule, the instrument check, the ratchet against the previous
release and the crash policy are unchanged.

## What is being tested, and why split G cannot test it

0.4.0 (released 12/11/2026 as a preview) failed split G ([`RESULTS-v0.6.md`](RESULTS-v0.6.md)): sensitive R_any 0.939,
clean friction 0.308, over-redaction two look-alikes above the control; 0 released leaks. The work after split G
(adjudicating split G's disputed gold, friction, sensitive recall, DD-26) is written from split G's misses and measured
on it, so **split G cannot judge it**: split G is development data. The verdict on the next release candidate comes
from split H alone.

## Test split H

| Item | Value |
|---|---|
| Files | Two halves, `half-a.jsonl` and `half-b.jsonl`, kept outside the repository and unread by the gate's designer until the result is published; then committed with the results as `eval/cases/test-h-a.jsonl` and `test-h-b.jsonl`, with the pipeline, profiles and provenance under `eval/generator_h/`. |
| sha256 | Added at sealing. |
| Size | Added at sealing. |
| How it is written | **Per-document free prose**, as for splits F and G: a deterministic planner (fixed seed, one plan per half) writes a specification per case (genre, length band, labels and counts, sensitive categories and whether each statement is direct, indirect or negative, hard-negative kinds, injection position, and pre-generated safe values for every checksummed or range-restricted identifier, phone and email); each document is written whole by the half's author model with gold marked inline; offsets are computed by code, never by a model. |
| Size target, per half | Split G's quotas scaled to about 80 cases: **72-88 cases**; at least 24 clean; every taxonomy label at least 7 gold spans; every sensitive category at least 10 gold spans, at least 65% of sensitive spans indirect, every category with negative statements; at least 7 "anonymised" tables with quasi-identifiers, location columns included; at least 5 hard-to-spot formats; 9-11 injection cases with positions balanced and at least 3 with no personal information; at least 3 long documents (9,000-24,000 characters); at least 5 scripts or languages; at least 4 other invented workplaces. As in splits F and G, the plan over-weights meeting dates, letter dates and case numbers as hard negatives in documents that also hold personal dates and personal identifiers, and organisation-issued personal identifiers written with the organisation's prefix. |
| Organisations | Each half has its own invented, profiled Australian organisation, written by that half's author from the shared profile schema, in a sector not used before (earlier: a training institute, two water utilities, a housing co-operative, a health and community-care network, a port and freight authority, a credit union, a municipal council): **A** a regional airline and airport operator; **B** an independent school group. Each half also has several other invented workplaces, unprofiled. |
| Checks | The shared validator (split G's copy; only protocol constants changed: id prefix `H<half>-`, split `test-h`, halves A/B, and the quota numbers above; its own checksum code; mutation test 26/26), the origin denylist over every field of every case (metadata included), and a pointer check (a separate call per case, by `openai/gpt-oss-120b` for both halves, with split G's second pointer prompt from the start: "every private person is invented; a name that merely sounds real is not a flag; when in doubt false") that no invented person is tied to a real organisation or a real public figure. Failing cases get one repair round; cases still failing are dropped, not hand-edited; a quota shortfall gets at most one top-up. |
| Labels | Each half's labeller labels blind, from the taxonomy and the plain text. One review round: the half's author decides each disagreement. Agreement (exact-offset and overlap recall/precision) before and after review is published per half with the hashes. |
| Convergence | A half whose author does not converge (validator clean with every quota met after one repair round and one top-up) is **dropped, not hand-edited**. Split H needs both halves; with fewer there is no verdict. |
| Orchestrator | A Claude model (Sonnet 5.5) builds and runs the pipeline, writes its prompts (adapted from split G's) and reports only counts, rule codes, hashes, token counts and costs to the gate's designer. |

### Model families and providers

| Half | Author | Blind labeller | Pointer check |
|---|---|---|---|
| A | `mistralai/mistral-large-2512` (Mistral), via OpenRouter | `kimi-k3.1-pro` (Moonshot), via Alibaba Cloud Model Studio | `openai/gpt-oss-120b` (OpenAI), via OpenRouter |
| B | `deepseek-v4.1-flash` (DeepSeek), via Alibaba Cloud Model Studio | `openai/gpt-oss-120b` (OpenAI), via OpenRouter | `openai/gpt-oss-120b`, via OpenRouter |

No family both writes and labels a half. The gate: Gemma 4 (Google) finders, PII-Tracer (a Qwen3-based classifier)
and rules; no author or labeller is a Google or Qwen model. Every request's parameters, token usage and (OpenRouter)
reported cost and serving provider are recorded, without headers.

**Spare author.** If an author's model code is refused, or an author cannot follow the markup after one prompt
revision made on throwaway probe specifications that are not part of the split, that half's author becomes
`qwen3.8-flash` (Alibaba). PII-Tracer is Qwen3-based, so such a half shares a family with one of the gate's models:
it is recorded at sealing and its half is reported apart in the result (the pooled verdict stands). There is no
spare labeller: if `kimi-k3.1-pro` runs out of its quota while labelling half A, labelling stops, everything else is
finished, and half A is labelled only if the quota is extended; the gap is recorded.

### Data handling

Calls through **OpenRouter** force zero-data-retention routing (`provider.zdr = true`, `data_collection = deny`) and
log the serving provider of every response. Calls through **Alibaba Cloud Model Studio** (Singapore) are made under
its published terms: customer data is not used for training; no zero-retention setting is documented ("no training
per the provider's terms; retention not stated"). Every person, organisation, place, identifier and document in split
H is invented; no real personal data and no work material is sent.

**Limitations, stated in advance.**
- Agreement measures label consistency between two families, not truth; the after-review figures are partly circular
  (the author edits its own gold after seeing the labeller). Split G's cross-family agreement was 0.86-0.92 exact.
- One model (`openai/gpt-oss-120b`) labels half B and runs the pointer check on both halves.
- The prompts the authors and labellers receive were written by a Claude model (the orchestrator), and the taxonomy
  and case format were written with Claude models.

## Kit

| Item | Value |
|---|---|
| Code | **The release candidate that the steward tags after the 13/11/2026 development work**, with every default detection switch on. It is named by its tag in a commit to this plan that changes nothing else, made after sealing and **before any arm runs**. The kit tag `eval-kit-v0.7-frozen` goes on a commit holding that candidate's code, this plan and its sealing, with no code, detector, policy, prompt, profile, catalogue, runner or scorer change beyond the candidate tag. |
| Profiles | Each half's profile converted by `eval/convert_profile.py` without hand edits. Finder context: the first sentence of the profile's `description` (at most 120 characters, cut at a word), as for splits D to G. |
| Catalogues, typed tier, legend | Off, as for splits E, F and G. |
| Pipeline, weights, engine | As for splits F and G: rules `core,au,intl`; PII-Tracer; Gemma 4 E4B Q4_K_M; Gemma 4 26B-A4B UD-IQ3_XXS; llama.cpp b11760; `--prompt v2 --max-tokens 8192 --chunk 40 --memory-settle-seconds 60 --allow-stage-failures`. Local only: the laptop's 8 GB GPU and 31.5 GB RAM; no cloud model in the gate. |

## Arms (each run once per half, under the heavy-run lock, halves in the order A, B; within a half candidate, previous, control)

Before every run the half's sha256 is checked against this plan at the kit tag; a mismatch stops the evaluation.

1. **Candidate:** the kit's code (the tagged release candidate), every default detection switch on, **live**,
   capturing every finder answer.
2. **Previous release (0.4.0 as released):** the code at tag `v0.4.0` with its own default switches (the 30 of its
   `DEFAULT_DETECTION_FIXES`; `finder-whitespace-trust` off), from a detached worktree of `v0.4.0`. If the candidate's
   finder prompts are those of 0.4.0, this arm **replays** the candidate's live finder capture (the same finder
   answers, so the difference between arms 1 and 2 is the code since 0.4.0); if the replay refuses a call (a prompt
   hash differs), the arm runs **live** with its own capture instead, and the result says which.
3. **Control (0.3.0):** the kit's code with the control switch list of plans v0.4 to v0.6 (the 15 switches 0.3.0
   shipped with), **live**, with its own capture.

**Pooling.** For each arm, the halves' predictions (with their decisions) and gold are concatenated and scored by the
standard scorer (`eval/score.py --split all --labelset themis --gold <pooled gold>`), as for split G. **The verdict
uses split H pooled.** Every measure is also reported for each half separately; the differences between halves are
not a second verdict.

## Crash policy

As in `FROZEN-PLAN-v0.4.md` to `v0.6.md`: infrastructure faults are fixed, recorded and resumed, never scored; a
finder that answers malformed output or runs out of budget is a result (the stage fails, the document is BLOCKed and
counted); a failing detector canary on a live arm stops the run as an infrastructure fault; a scored run is never
re-run. A live arm starts only on a quiet machine (at least 13 GB of available memory and no competing evaluation),
because a model load refused for memory mid-run would be scored as a BLOCK.

## Pass rule

The thresholds are those of splits D to G. They do not loosen. All measures are on split H pooled.

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on split H
pooled. Otherwise the result is **INCONCLUSIVE**.

**The candidate PASSES** if, on split H pooled, all of these hold:

| Condition | Limit |
|---|---|
| Released leaks | **≤ 3**, and ≤ the control's, and ≤ the previous release's |
| R_any | **≥ 0.98**, and ≥ control − 0.001, and ≥ previous − 0.001 |
| Sensitive R_any (the eight categories pooled) | **≥ 0.95**, and ≥ control − 0.005, and ≥ previous − 0.005 |
| QUASI_ID R_any | ≥ control − 0.005 |
| Clean-case friction | **≤ 0.15**, and ≤ the control's |
| Over-redaction (hard negatives touched) | ≤ the control's |
| FAILED stages from infrastructure (candidate, all halves) | **0** |

The ratchet against the previous release is part of the verdict. There is no fallback configuration: the candidate
passes or fails, and the result says which conditions failed and by how much.

**Reported, not gating:** every measure above for each half; released leaks by label and by format; sensitive R_any
per category and for direct, indirect and negative statements separately; over-redaction by hard-negative kind
(meeting dates, letter dates and case numbers named); held-back unprotected spans by reason; released leaks inside
injection cases; documents blocked by finder failures; seconds per document; label agreement per half; tokens and
cost per authoring model.

**Blindness:** until every arm has run on every half, the designer reads only the numeric fields of each arm's
`score.json` (the scorer's text report prints leaked values), and no case, value, profile term or description.
