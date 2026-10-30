# Frozen evaluation plan: eval-kit-v0.4-frozen (split E)

**Pass rule written 30/10/2026, before split E was written.** The two halves are being authored now by models outside
the gate's families; nobody who works on the gate has seen or will see a case before the arms run. The rule below
was committed first; the halves' sha256 are added at sealing, in a commit that changes nothing else, and the kit tag
`eval-kit-v0.4-frozen` goes on that commit. Nothing below may change after a test result is seen. Earlier plans:
[`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md) (split D: FAIL), [`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md),
[`FROZEN-PLAN.md`](FROZEN-PLAN.md).

## What is being tested

Releases 0.3.1 to 0.3.4 were measured on development data only (the v0.1 set, split C, and split D after its v0.3
run). Split E is the first fresh text they meet: the detection fixes written from split D's misses (identifier
columns, bare number formats, finder windows, detection-view readers, sensitive table columns, union roles) and the
friction fixes (secret references, implausible PII-Tracer evidence).

## Test split E

| Item | Value |
|---|---|
| Files | Half K and half M, kept outside the repository and private until the result is published; then committed with the results as `eval/cases/test-e-k.jsonl` and `eval/cases/test-e-m.jsonl`, with generators, profiles and provenance under `eval/generator_e/`. |
| sha256, half K | `25f93e2d272bf8fc8dd5a5228579d775a88da64d3a2e008e87f0e31c083e4c7d` |
| sha256, half M | none: **half M did not converge** (see below) |
| Size, half K | 210 cases, 2,731 gold spans, 60 clean cases, 1,501 hard negatives, 22 injection cases (before 8, inside 7, after 7; 8 with no personal information), 6 long documents; every quota met; shared validator 0 rule failures (mutation test 26/26) |
| Authors | **Half K:** `deepseek/deepseek-v4.1-flash-1017` (DeepSeek family). **Half M:** `xiaomi/mimo-v2.6-flash` (Xiaomi family). Both through OpenRouter with zero data retention enforced on every call. Each saw only `taxonomy.md`, the case format and build rules, a profile schema without values, and split D's one-page brief (ids, half letters and split name changed, nothing else); neither saw the gate, its prompts, the earlier splits, their generators, the roadmap or the other half. Each writes its own fictional organisation. Both work with tools (read, edit, build, validate their own files) from the first message. |
| Labeller | `z-ai/glm-5.3-flash` (Z.ai family) re-labels every case of both halves blind from the taxonomy and the text; disagreements go back to each half's author once; the author has the final word on gold. Agreement before and after review is published with the hashes. |
| Orchestrator | A Claude model relays messages, runs builds and the shared validator (its own checksum implementation), and computes agreement; it writes no case content, and reports only counts, hashes and costs to the gate's designer. |
| Families | Authors DeepSeek and Xiaomi, labeller Z.ai, gate Gemma (plus PII-Tracer and rules). No Claude or Gemma model authors or labels. |

If a half does not converge (build, self-check and validator clean with every quota met) within its budget, it is
not hand-edited: split E runs on the half that did, and the plan says so in the sealing commit.

**At sealing (30/10/2026, 05:4x UTC).** Half K converged (278 tool calls; author USD 0.46, labeller USD 0.18).
Half M did not: its author used its 250 tool calls without one passing build (23 builds, 72 failed edits) and was
stopped; nothing of it was labelled, sealed or edited. **Split E is half K alone**, so the family effect cannot be
measured this time; a second author would be a new split under its own plan, never added to this one.

**Labeller agreement, half K** (span recall / precision of gold against the labeller): 0.809 / 0.992 blind (643
disagreement items); the author fixed 551 and kept 92; 0.967 / 0.834 after review. 519 of the 525 labeller-only
spans after review are `OTHER_DATE` in two cases, dates the author took out of gold in review and the labeller
marks. The taxonomy makes a personal chat message's date stamp `OTHER_DATE` and a meeting or log date a public one;
under this protocol the author decides, and no one on the gate side has read those cases. So the verdict uses the
sealed gold, and a **sensitivity score** (reported, not gating) uses `half-k.alt-otherdate.jsonl` (sha256
`819acf71a6821bd568505e55f79e4da67cce889313840bbe6ac1f4e22c8142ab`): the sealed half with those 519 labeller spans
added as gold, built by script.

**Contamination (counts only):** 14 distinct 8-grams in 15 of 210 cases shared with the published evaluation cases
(stock phrasing, as for split D; not inspected); 0 with the finder prompts.

## Kit

| Item | Value |
|---|---|
| Code | The commit carrying tag `eval-kit-v0.4-frozen`. No detector, policy, prompt, profile, catalogue, runner or scorer change after it. |
| Profiles | Each half runs with its own author-written organisation profile, converted by `eval/convert_profile.py` without hand edits. Finder context: the first sentence of the profile's `description` (at most 120 characters, cut at a word), as for split D. |
| Catalogues, typed tier, legend | Known-miss register, false-positive catalogue and trip-wire off; typed tier off (its shipped default); the placeholder legend does not touch detection and is off. |
| Pipeline, weights, engine | As for split D: rules `core,au,intl`; PII-Tracer; Gemma 4 E4B Q4_K_M; Gemma 4 26B-A4B UD-IQ3_XXS; llama.cpp b11760; `--prompt v2 --max-tokens 8192 --chunk 40 --memory-settle-seconds 60 --allow-stage-failures`. |

## Arms (each run once per half, from the tag, clean tree)

1. **Candidate: 0.3.4 as shipped**, every default detection switch on (23 switches, `finder-whitespace-trust` off),
   live, capturing every finder answer.
2. **Control: 0.3.0 as shipped**, `--detection-fixes secret-placeholders,organisation-contacts,checksum-context,`
   `rule-completeness,table-role-words,title-tokens,contact-brackets,secret-url-components,secret-prose-values,`
   `secret-auth-codes,secret-document-ids,secret-org-codes,secret-mrz,finder-whitespace,address-line-numbers`
   (the eight v0.4 switches off). Two of the eight change the finder prompts, so the control runs **live** with its
   own capture, not by replay.

The halves would have been pooled for the verdict; with half K alone, it is scored directly (standard scorer, `--labelset themis`).
The shared lock file is held for each run, and no other evaluation runs beside it.

## Crash policy

As in `FROZEN-PLAN-v0.3.md`: infrastructure faults are fixed, recorded and resumed, never scored; a finder that
answers malformed output or runs out of budget is a result (the stage fails, the document is BLOCKed and counted);
a failing detector canary on a live arm stops the run as an infrastructure fault; a scored run is never re-run.

## Pass rule

The thresholds are split D's. They do not loosen.

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on split E (half K).
Otherwise the result is **INCONCLUSIVE**.

**The candidate PASSES** if, on split E (half K), all of these hold:

| Condition | Limit |
|---|---|
| Released leaks | **≤ 3**, and ≤ the control's |
| R_any | **≥ 0.98**, and ≥ control − 0.001 |
| Sensitive R_any (the eight categories pooled) | **≥ 0.95**, and ≥ control − 0.005 |
| QUASI_ID R_any | ≥ control − 0.005 |
| Clean-case friction | **≤ 0.15**, and ≤ the control's |
| Over-redaction | ≤ the control's |
| FAILED stages from infrastructure | **0** (finder-output failures are BLOCK decisions under the crash policy, counted in friction when the case is clean, and reported) |

There is no fallback configuration: the candidate passes or fails. If it fails, the result says which conditions,
and 0.3.4 stays a preview with that verdict, as every release before it has.

**Reported, not gating:** the sensitivity score above; held-back unprotected spans by reason; indirect UNION, CRIMINAL and ETHNICITY R_any; released leaks inside injection
cases; documents blocked by finder failures; seconds per document.
