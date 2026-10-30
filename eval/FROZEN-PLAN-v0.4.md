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
| sha256, half K | *added at sealing* |
| sha256, half M | *added at sealing* |
| Authors | **Half K:** `deepseek/deepseek-v4.1-flash-1017` (DeepSeek family). **Half M:** `xiaomi/mimo-v2.6-flash` (Xiaomi family). Both through OpenRouter with zero data retention enforced on every call. Each saw only `taxonomy.md`, the case format and build rules, a profile schema without values, and split D's one-page brief (ids, half letters and split name changed, nothing else); neither saw the gate, its prompts, the earlier splits, their generators, the roadmap or the other half. Each writes its own fictional organisation. Both work with tools (read, edit, build, validate their own files) from the first message. |
| Labeller | `z-ai/glm-5.3-flash` (Z.ai family) re-labels every case of both halves blind from the taxonomy and the text; disagreements go back to each half's author once; the author has the final word on gold. Agreement before and after review is published with the hashes. |
| Orchestrator | A Claude model relays messages, runs builds and the shared validator (its own checksum implementation), and computes agreement; it writes no case content, and reports only counts, hashes and costs to the gate's designer. |
| Families | Authors DeepSeek and Xiaomi, labeller Z.ai, gate Gemma (plus PII-Tracer and rules). No Claude or Gemma model authors or labels. |

If a half does not converge (build, self-check and validator clean with every quota met) within its budget, it is
not hand-edited: split E runs on the half that did, and the plan says so in the sealing commit.

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

The halves are pooled for the verdict (concatenated predictions and gold, standard scorer, `--labelset themis`).
The shared lock file is held for each run, and no other evaluation runs beside it.

## Crash policy

As in `FROZEN-PLAN-v0.3.md`: infrastructure faults are fixed, recorded and resumed, never scored; a finder that
answers malformed output or runs out of budget is a result (the stage fails, the document is BLOCKed and counted);
a failing detector canary on a live arm stops the run as an infrastructure fault; a scored run is never re-run.

## Pass rule

The thresholds are split D's. They do not loosen.

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on E pooled.
Otherwise the result is **INCONCLUSIVE**.

**The candidate PASSES** if, on E pooled, all of these hold:

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

**Reported, not gating:** every metric per half; the control's half K minus half M difference (the family effect);
held-back unprotected spans by reason; indirect UNION, CRIMINAL and ETHNICITY R_any; released leaks inside injection
cases; documents blocked by finder failures; seconds per document.
