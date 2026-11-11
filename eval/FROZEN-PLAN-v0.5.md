# Frozen evaluation plan: eval-kit-v0.5-frozen (split F)

**Pass rule written 11/11/2026, before split F was written.** Split F is being authored now by a pipeline that the
gate's designer does not read: the designer sees counts, hashes and costs only, until the arms have run. This rule
is committed first; the sealed file's sha256 is added at sealing, in a commit that changes nothing else, and the kit
tag `eval-kit-v0.5-frozen` goes on that commit. Nothing below may change after a test result is seen. Earlier plans:
[`FROZEN-PLAN-v0.4.md`](FROZEN-PLAN-v0.4.md) (split E: FAIL), [`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md) (split D:
FAIL), [`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md), [`FROZEN-PLAN.md`](FROZEN-PLAN.md).

## What is being tested, and why split E cannot test it

Split E found one defect behind all 12 of 0.3.4's released leaks: the institutional allow-list trimmed the
organisation's acronym off a staff number (DD-17). The fix, `joined-terms`, was written from split E's misses, so
**split E cannot judge it**: split E was opened once, failed, and is now development data. The fix is measured on
split E and the other development sets only as development evidence (the ratchet: no development set worse on any
safety measure). The verdict on the fix, and on everything else in the candidate, comes from split F alone.

## Test split F

| Item | Value |
|---|---|
| File | One half, `half-o.jsonl`, kept outside the repository and unread by the gate's designer until the result is published; then committed with the results as `eval/cases/test-f-o.jsonl`, with the pipeline, profile and provenance under `eval/generator_f/`. |
| sha256 | (added at sealing) |
| How it is written | **Per-document free prose**, unlike every earlier split (each of which was a model-written template generator). Each document is written whole by `claude-opus-5-5` at effort `high` through the Message Batches API, from a per-case specification made by a deterministic planner (genre, labels and counts, sensitive categories and whether each statement is direct, indirect or negative, hard-negative kinds, injection position, and pre-generated values for every checksummed or range-restricted identifier, phone and email). Gold is marked inline by the author and turned into offsets by code. |
| Size target | About 240 cases; at least 70 clean; every label at least 20 gold spans; every sensitive category at least 30, at least 65% indirect, each with negative statements; at least 20 anonymised tables with quasi-identifiers; at least 15 hard-to-spot formats; 25-30 injection cases; at least 6 long documents. |
| Organisation | One invented regional public health and community-care network, profiled (a new sector for these splits: earlier profiles were a training institute, two water utilities and a housing co-operative), and at least eight other invented workplaces, unprofiled. |
| Labeller | `claude-sonnet-5-5` at effort `xhigh`, blind, from the taxonomy and the plain text; one review round in which the author decides each disagreement. Agreement before and after review is published with the hash. |
| Checks | The shared validator (split E's, protocol constants only changed; its own checksum code; mutation test 26/26), the origin denylist, and a pointer check (a separate model call per case) that no invented person is tied to a real organisation or real public figure. Cases failing after one repair round are dropped, not hand-edited. |
| Families | Author and labeller Claude (Anthropic); gate Gemma (Google) plus PII-Tracer (a Qwen3-based classifier) and rules. **Limitation, stated in advance:** author and labeller share a family this time, so label agreement may overstate label quality; the author-versus-gate independence, which is what the verdict rests on, holds. |
| Orchestrator | A Claude model builds and runs the pipeline and reports only counts, hashes and costs to the gate's designer. |

## Kit

| Item | Value |
|---|---|
| Code | The commit carrying tag `eval-kit-v0.5-frozen`. No detector, policy, prompt, profile, catalogue, runner or scorer change after it. |
| Profile | Converted by `eval/convert_profile.py` without hand edits. Finder context: the first sentence of the profile's `description` (at most 120 characters, cut at a word), as for splits D and E. |
| Catalogues, typed tier, legend | Off, as for split E. |
| Pipeline, weights, engine | As for split E: rules `core,au,intl`; PII-Tracer; Gemma 4 E4B Q4_K_M; Gemma 4 26B-A4B UD-IQ3_XXS; llama.cpp b11760; `--prompt v2 --max-tokens 8192 --chunk 40 --memory-settle-seconds 60 --allow-stage-failures`. |

## Arms (each run once, from the tag, clean tree, under the heavy-run lock)

1. **Candidate:** the tagged code as shipped, every default detection switch on (`joined-terms` included,
   `finder-whitespace-trust` off), **live**, capturing every finder answer.
2. **Previous release (0.3.4):** the tagged code with `joined-terms` off (the 23 switches 0.3.4 shipped with). This
   switch changes no finder prompt, so this arm is the candidate's live finder capture **replayed** with the switch
   off: the same finder answers, so the difference between arms 1 and 2 is the fix alone. If the replay refuses a
   call (a prompt hash differs), the arm runs live instead and the result says so.
3. **Control (0.3.0):** the tagged code with split E's control switch list (the eight v0.4 switches and
   `joined-terms` off). Two of the off switches change the finder prompts, so the control runs **live** with its own
   capture.

## Crash policy

As in `FROZEN-PLAN-v0.4.md`: infrastructure faults are fixed, recorded and resumed, never scored; a finder that
answers malformed output or runs out of budget is a result (the stage fails, the document is BLOCKed and counted); a
failing detector canary on a live arm stops the run as an infrastructure fault; a scored run is never re-run.

## Pass rule

The thresholds are split D's and E's. They do not loosen.

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on split F.
Otherwise the result is **INCONCLUSIVE**.

**The candidate PASSES** if, on split F, all of these hold:

| Condition | Limit |
|---|---|
| Released leaks | **≤ 3**, and ≤ the control's, and ≤ the previous release's |
| R_any | **≥ 0.98**, and ≥ control − 0.001, and ≥ previous − 0.001 |
| Sensitive R_any (the eight categories pooled) | **≥ 0.95**, and ≥ control − 0.005, and ≥ previous − 0.005 |
| QUASI_ID R_any | ≥ control − 0.005 |
| Clean-case friction | **≤ 0.15**, and ≤ the control's |
| Over-redaction (hard negatives touched) | ≤ the control's |
| FAILED stages from infrastructure | **0** |

The ratchet against the previous release is new in this plan: it makes "no worse than the release before it" part of
the verdict rather than a development check. There is no fallback configuration: the candidate passes or fails, and
the result says which conditions failed.

**Reported, not gating:** released leaks by label and by format; sensitive R_any per category and for indirect and
negative statements separately; over-redaction by hard-negative kind (meeting dates, letter dates and case numbers
named); held-back unprotected spans by reason; released leaks inside injection cases; documents blocked by finder
failures; seconds per document.

**Blindness:** until all three arms have run, the designer reads only the numeric fields of each arm's `score.json`
(the scorer's text report prints leaked values).
