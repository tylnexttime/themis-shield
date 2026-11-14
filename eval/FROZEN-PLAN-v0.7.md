# Frozen evaluation plan: eval-kit-v0.7-frozen (split H)

**Pass rule written 13/11/2026, before split H was written.** Split H is authored after this commit by a pipeline
that the gate's designer does not read: the designer sees counts, rule codes, hashes and costs only, until every arm
has run. This rule is committed first; the sealed files' sha256 values and the actual counts are added at sealing, in
a commit that changes nothing else. Nothing below may change after a test result is seen. **Amended 13/11/2026, before
sealing and before any case was read: one more targeted top-up per half, and sealing with a stated small shortfall; see
"Amendment of 13/11/2026" at the end. It overrides the Convergence row and the "at most one top-up" sentence of the
Checks row, and nothing else.** Earlier plans:
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
| sha256 | `half-a.jsonl` `3f29c5735b4a5113396a961cde10d110ac6eebda21af4c9f960b7cdbca97fe0f`; `half-b.jsonl` `5544671eb45323179b45588461b6b96e266bb668a62cd6b1c33c504991c20069`. Profiles as written by the authors: `profile-a.json` `b8ed576de55aea623aef3eaeb02f2350ee0be4e8961056901310983f73e8b539`, `profile-b.json` `51660b78b0c93d507aeca2e6fa97f90dd75020fb2ef3a98ad1b8d28bd3029b0d`. Converted institution profiles: `test-h-a.json` `2592edea8b3f61a1720f6d943f52120c3283d6cd994f49ee00cb6402b837b9c7`, `test-h-b.json` `128c88fe125c6ab9ba34ce8dfa2fa9b9aaf31174266b3c1e21534e921ea42055`. Finder context files: `context-a.txt` `18b8cee8f5e890c4ebb922c237474113f7971b07547f42496e0dd8b97173f843`, `context-b.txt` `cad55bca4b76c6013f6fcefd4bd43eaa9b594c107b5fb3ae3e42dd033c623f63`. Conversion note: half B's profile lists its `buildings`, `committees` and `initiatives` (9, 9 and 10 entries) as one-key `{name}` objects, which `eval/convert_profile.py` cannot read (it takes strings there); they were unwrapped to strings by code (`prep_profiles.py`, which refuses any other shape) before the converter ran, with no hand edit; half B's sealed profile is unchanged, and half A needed no unwrapping. |
| Size | **A:** 76 cases (113 planned, 37 dropped), 27 clean, 1,514 gold spans, 1,468 hard negatives, 215 sensitive spans (143 indirect, 22 negative), 9 injection cases (4 without personal information), 7 hard-to-spot, 4 long documents, 292,185 characters. **B:** 82 cases (95 planned, 13 dropped), 27 clean, 1,074 gold spans, 1,102 hard negatives, 131 sensitive spans (87 indirect, 19 negative), 10 injection cases (4 without personal information), 11 hard-to-spot, 5 long documents, 239,786 characters. Pooled 158 cases. **Shortfall, sealed under the amendment of 13/11/2026:** half A is short in one coverage row, gold spans PASSPORT 6 of the required 7 (one span); validator failures 0, denylist hits 0, every other row passes. Half B passes every row. |
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

## Amendment of 13/11/2026 (before sealing): one targeted top-up per half

**Decided by Alder on 13/11/2026 at 00:00 UTC, on counts only, before anything was sealed and without reading a case,
a value, a profile term or a description of split H.** The thresholds, the arms, the instrument check, the ratchet, the
crash policy and the pass rule are unchanged. Only the authoring rule of the Convergence row changes, as set out here.

**What happened (counts and rule codes only; the orchestrator's report `w50-splith-REPORT.md`).** Each half was
planned, authored, pointer-checked and given one repair round, then one top-up as the plan allows.

| half | planned | valid after author and one repair round | top-up entries | top-up valid | valid at the convergence check | validator failures / denylist hits |
|---|---|---|---|---|---|---|
| A | 86 | 69 (80%) | 18 | 3 (17%) | 72 | 0 / 0 |
| B | 82 | 74 (90%) | 10 | 5 (50%) | 79 | 0 / 0 |

At the convergence check half A was short in four coverage rows (gold spans: ABN 5 of 7, PASSPORT 6 of 7,
FAMILY_VIOLENCE 9 of 10; long documents 2 of 3) and half B in one (gold spans DISABILITY 8 of 10); every other row
passed. Both halves were therefore dropped under the Convergence row and nothing was sealed. After the informational
labelling and review of the valid cases (not the sealed numbers) the same rows were short, with FAMILY_VIOLENCE at 7
in A and DISABILITY at 7 in B: the review's quota guard rejects only a fix that makes a new row fail, so the review
removed three gold spans from those two rows, which were already short (A FAMILY_VIOLENCE 9 to 7, B DISABILITY 8 to 7).

**Why the top-up missed.** The plan said top-up documents are dropped when they fail, without the repair round that the
planned entries get. Planned entries, with that round, survived at 80% and 90%; top-up entries, without it, at 17% and
50%, against the 62% that the 1.6x sizing assumed. The shortfalls are small, in coverage rows only, with no validator
failure and no denylist hit; the cause is a procedural omission (no repair for top-up documents), not a defect of a
model, of the validator or of the documents that were kept.

**Decision.** Each half gets **one more top-up, for the short rows only, with the same single repair round as the
planned entries**, and the sealing rule below. The amendment was made before sealing and before any arm; it is the
only change to the authoring rule.

**Rule for the second top-up** (counts only are reported; the same pipeline, prompts and models):

1. **Which rows and how many.** Run the validator (`--quotas`) and `extra_quotas.py` on the half's current valid cases
   (after the first review) at the start of the step. For every coverage row that fails, shortfall = its minimum minus
   its count. Plan **ceil(2.5 x shortfall)** gold spans of that label or category (at most three per entry), or that
   many documents for the long-documents row. No entry for any row that passes, and no margin rows. Measured on
   13/11/2026 before this commit, counts only: A ABN 2, PASSPORT 1, FAMILY_VIOLENCE 3, long documents 1, i.e. 5, 3 and
   8 gold spans and 3 long documents; B DISABILITY 3, i.e. 8 gold spans. Never beyond 88 cases in a half. Entries are for
   other workplaces, except those needed to keep the profiled share at 40% or more; safe values are drawn fresh from the
   shared used-set, so they stay unique across both halves, the plan and every top-up.
2. **The same repair.** Each half's own author writes the new documents. They go through the pointer check, **one
   repair round** (validator rule codes and pointer flags, as for the planned entries) and a pointer recheck of what
   was repaired. A document still failing is dropped, not hand-edited, and there is no second repair or third top-up.
3. **Labelling and review of the new documents only.** The half's blind labeller labels them (kimi-k3.1-pro in full for
   half A: its stop-on-exhaust is off), one review round for them under the same script rules (a fix is applied only if
   the validator stays clean and no row newly fails); the documents already labelled and reviewed are not touched.
   Then the validator (`--quotas`), the denylist and the extra quotas on the half; agreement before and after review
   is recomputed on the whole half and published with the hashes.
4. **Sealing.** A half that now passes every row is sealed: `sealed/half-a.jsonl` / `half-b.jsonl` with their sha256,
   profiles and finder contexts converted, in a sealing commit that carries the sha256 values and the actual counts only.
   A half that is **still short is sealed anyway, with the shortfall written into the sealing commit** (as split G's
   half T was accepted at 88 cases), **if it is short on at most two coverage rows and by at most two units in each**
   (a unit is a gold span; a document for the long-documents row). A coverage row is a minimum number of gold spans per
   label or per sensitive category, or of documents of one kind (clean, long, tables, hard-to-spot formats, scripts,
   other workplaces). The validator's failures, the denylist, the case band of 72 to 88, the injection band and
   positions, the indirect share, the negative-statement row and the profiled share must pass in full. A half outside
   this limit is dropped, and then, as before, there is no verdict.
5. **Not in this step.** The kit is not tagged and no arm is run. The candidate's tag is named later, in the commit that
   the plan already provides, before any arm runs.

**Limits, stated in advance.** The shortfall written at sealing, if any, is a known gap in coverage of one or two
sensitive or identifier rows; the verdict is pooled and the rows' per-category counts are reported with it. The review's
quota guard is unchanged, so the review of the new documents can again remove a span of a row that is short; that is
counted and reported, not corrected by hand.

## Kit named (14/11/2026, before any arm ran)

**Written on 14/11/2026 at 08:30 UTC, after sealing and before any arm of split H ran on either half**, as the Kit row
provides. Nothing above changes; the thresholds, arms, pass rule and crash policy are as written.

| Arm | Code and switches |
|---|---|
| **Candidate** | **Release 0.5.0** (tag `v0.5.0`, commit `59df76b`, public), every default detection switch on: the 31 of its `DEFAULT_DETECTION_FIXES`; the five opt-in switches (`finder-whitespace-trust`, `policy-statements`, `negative-statements-general-exemption`, `empty-finder-substitute`, `secret-config-references`) off. Run from the kit tag `eval-kit-v0.7-frozen`, which is this commit: release 0.5.0's code with this plan and its sealing on top, and no other change. |
| **Previous release** | **Release 0.4.0 as released** (tag `v0.4.0`, commit `14dd8fc`), its own code with its own default switches, from a detached worktree of `v0.4.0`, as arm 2 says. Replay of the candidate's finder capture if no call is refused, otherwise live with its own capture. |
| **Control** | **0.3.0's switch list as in plan v0.6** (the 15 switches: `secret-placeholders`, `organisation-contacts`, `checksum-context`, `rule-completeness`, `table-role-words`, `title-tokens`, `contact-brackets`, `secret-url-components`, `secret-prose-values`, `secret-auth-codes`, `secret-document-ids`, `secret-org-codes`, `secret-mrz`, `finder-whitespace`, `address-line-numbers`) on the kit's code, live, with its own capture. |
