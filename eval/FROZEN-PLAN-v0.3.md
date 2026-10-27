# Frozen evaluation plan: eval-kit-v0.3-frozen

Written and committed **before the v0.3 test split (split D) was opened**. Nothing below may change after a test
result is seen. Earlier plans: [`FROZEN-PLAN.md`](FROZEN-PLAN.md) (v0.1), [`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md).
The development work is in [`V03-DEV-LOG.md`](V03-DEV-LOG.md).

## Test split D

| Item | Value |
|---|---|
| Files | Half Q and half C, kept **outside the repository and private until the v0.3 result is published**; then committed with the results as `eval/cases/test-d-q.jsonl` and `eval/cases/test-d-c.jsonl`, with their generators, profiles and provenance under `eval/generator_d/`. The hashes below are the commitment. |
| sha256, half Q | `3fb5dc5ec227056cbe3f458356d36bf2089490f9b344cad6c4516bafe8d3a07a` |
| sha256, half C | `59e127c1414c369cc075833dbd0bdbdc0872a233b671fc6da961888d914063f0` (after its review round; first sealed as `e4bce530…`) |
| Size | 404 cases (Q 199, C 205); 5,841 gold spans (Q 1,259, C 4,582); 125 clean cases (Q 63, C 62); 48 injection cases (24 each); 2,312 hard negatives (Q 380, C 1,932) |
| Authors | **Half Q:** `qwen/qwen3.8-2.4t-a95b` (Qwen family) through OpenRouter with zero data retention enforced on every call (providers Novita and Together); a Claude model relayed messages, ran the build and the validator, and wrote no generator code or case content. After 15 chat rounds without converging, the author was given the same working conditions as half C's author (tools to edit, build and validate its own files; 190 tool calls). Its first provenance file named itself a Claude model; the model identity here comes from the API call log, not from the model. **Half C:** a Claude model (Sonnet 5.5) working alone. Both saw only `taxonomy.md`, the case-format and build-rule sections of the evaluation README, a profile schema without values, and the same one-page brief; neither saw the gate, its prompts, the earlier splits, their generators, the roadmap or each other's half. Each wrote its own fictional organisation profile. |
| Labeller | `deepseek/deepseek-v4.1-flash-1017` (DeepSeek family) re-labelled both halves blind from the taxonomy and the text; disagreements went back to each half's author once; the author has the final word on gold. **Half Q:** span recall 0.986 / precision 0.644 before its review, 0.983 / 0.988 after (378 → 43 disagreements; 23 items kept with reasons). **Half C:** 0.995 / 0.993 blind (60 disagreements); its author then fixed 27 items in its generator and kept 33. A shared validator with a third checksum implementation passed both halves (0 rule failures). |
| Contamination checks | 8-gram overlap, counts only: against the rendered finder prompt 0 (both halves); against the 892 published cases 6 distinct 8-grams in 8 cases (Q) and 19 in 14 cases (C), presumably stock phrasing, not inspected; against the typed-tier training generator 0 (Q) and 2 in 7 cases (C), which matters only for the encoder (not in this kit). |

The person who designed and tuned the gate (Alder) has not read any case of split D. One disclosure: half C's author's review summary named a handful of phrase-level edits (a reworded union phrase, first-aid cabinet codes labelled as secrets, some case ids); no gate change was made on that basis. Before any arm runs, the
operator checks both files' sha256 against the values above; a mismatch stops the evaluation. Each run's
`config.json` records the hashes again.

**Why two halves.** All earlier generators and the gate's prompt examples come from Claude models. Half Q is the
first test text from another family. Every metric is reported for each half and for D pooled; the difference
between the halves under the control arm is reported as a measurement of how much the author's family matters.
The verdict uses D pooled.

## Kit

| Item | Value |
|---|---|
| Code | The commit carrying tag `eval-kit-v0.3-frozen`, which is this plan's commit. No detector, policy, prompt, profile, catalogue, runner or scorer change after it. Split D is read from outside the repository and checked against the hashes above. |
| Profiles | Each half runs with its own author-written organisation profile, converted by `eval/convert_profile.py` without hand edits: `test-d-q` sha256 `aff1d2a9…0a78`, `test-d-c` sha256 `0d1a2237…df838d`. Finder context: the first sentence of the profile's `description` (at most 120 characters, cut at a word). |
| Detection | `[detection] version = "v0.2"`, `fixes` at the shipped defaults (every switch listed in `V03-DEV-LOG.md`, all on except `finder-whitespace-trust`, which was measured and not adopted) |
| Typed tier (arm 2 only) | `--typed on` with the shipped defaults: families **Q-M, Q-N, Q-P** (contact owner; date, duration or number; private person or public figure). Q-S and Q-I were rejected on development data under the rule written before the first typed run (Q-S raised released leaks; Q-I raised friction). Prefill-only on the resident 26B; temperature 1, margin 0.2, label-mass floor 0.9, two option orders, strict single-family sole cause; never a marking, secret, injection, unmatched-value or failed-stage hold. |
| Catalogues | known-miss register, false-positive catalogue and trip-wire **off** (they act on a deployment's own history; there is none here) |
| Pipeline, weights, engine, harness | As in `FROZEN-PLAN-v0.2.md` (rules `core,au,intl`; PII-Tracer; Gemma 4 E4B Q4_K_M; Gemma 4 26B-A4B UD-IQ3_XXS `-ncmoe 18`; llama.cpp b11760; `--memory-settle-seconds 60 --chunk 40 --max-tokens 8192`) |

## Dev numbers the rule is derived from

Replays over all 892 development cases (the v0.1 set, 521; split C, 371). Split C numbers are **in-sample**: the
v0.3 fixes were written after reading split C's failures.

| Set | Configuration | Released leaks | R_any | Sensitive R_any | Quasi-ID R_any | Clean friction | Over-redaction |
|---|---|---|---|---|---|---|---|
| v0.1 set (521) | v0.2.1 = control | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 | 306 |
| v0.1 set (521) | arm 1 | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 | 306 |
| v0.1 set (521) | arm 2 | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 | 306 |
| split C (371, in-sample) | v0.2.1 = control | 0 | 0.9902 | 0.9537 | 0.9762 | 0.200 | 605 |
| split C (371, in-sample) | arm 1 | 0 | 0.9902 | 0.9537 | 0.9762 | 0.147 | 600 |
| split C (371, in-sample) | arm 2 | 0 | 0.9902 | 0.9537 | 0.9762 | 0.116 | 600 |
| real slice (78, private) | v0.2.1 = control | 11 | 0.974 | n/a | n/a | 0.235 | 722 |
| real slice (78, private) | arm 1 | 11 | 0.974 | n/a | n/a | 0.176 | 723 |

Split C's friction gains are in-sample; the rule's 0.15 friction limit sits just above arm 2's in-sample 0.116 and
arm 1's 0.147, so on a fresh split from other authors, a FAIL on friction is a real possibility, and it would be
reported as such.

## Arms (each run once on D, from the tag, clean tree)

Each arm runs per half (each half has its own profile) with `run_d.sh ARM HALF` (outside the repository), and the
two halves are pooled for the verdict by `pool_score.py ARM` (concatenated predictions and gold, standard scorer).

1. **v0.3 deterministic (arm 1)**: live, capturing every finder response, half Q then half C.
2. **v0.3 with the typed tier (arm 2, the candidate)**: arm 1's configuration plus `--typed on`, **replaying arm 1's
   finder capture**; typed calls live on the resident 26B and captured.
3. **v0.2.1 control**: `--detection-fixes none`, typed off, replaying arm 1's capture.

Common settings: `--split test --frozen-tag eval-kit-v0.3-frozen --rule-packs core,au,intl --prompt v2
--max-tokens 8192 --chunk 40 --memory-settle-seconds 60 --allow-stage-failures`; the half's sha256 is checked before
each run. The shared lock file is held for the whole sequence, and no other evaluation runs beside it.

Arms 2 and 3 differ from arm 1 only after the finders. A replay refusal is an infrastructure fault, not a result.

**Not in this kit: the fine-tuned encoder (arm 3 of the roadmap).** It was trained on development data only
([`ENCODER-RESULTS-2026-10-27.md`](ENCODER-RESULTS-2026-10-27.md)) and found to be confidently wrong; it will be
tested on a fresh split under its own plan, never on D.

## Crash policy and stage failures

- **Infrastructure faults** (a model that will not load for lack of memory, a server that dies, a replay refusal
  caused by the harness) are fixed, recorded, and the run is resumed with `--resume`. Before every live arm, no other
  evaluation process may run, and the shared lock file is held for the whole run.
- **A finder that answers malformed output or runs out of its token budget is a result, not a crash.** The gate
  fails closed: the stage is FAILED and the document is BLOCKed, exactly as in deployment. Every arm therefore runs
  with `--allow-stage-failures`, and the results report how many documents were blocked this way. (Written after the
  first real-data run, 27/10/2026, where a long table full of personal values overflowed the 26B's 8,192-token answer;
  the fix, finders listing each distinct value once, changes the finder prompt and is not in this kit.)
- **Detector canary:** each run records the canary result in its `config.json`. A failing canary on a live arm stops
  the run as an infrastructure fault.
- Malformed typed-tier answers resolve to the existing hold (no stage failure). A scored run is never re-run.

## Pass rule

**Instrument check (control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on D pooled.
Otherwise the result is **INCONCLUSIVE**.

**The candidate (arm 2) PASSES** if, on D pooled, all of these hold:

| Condition | Limit |
|---|---|
| Released leaks | **≤ 3**, and ≤ the control's |
| R_any | **≥ 0.98**, and ≥ control − 0.001 |
| Sensitive R_any (the eight categories pooled) | **≥ 0.95**, and ≥ control − 0.005 |
| QUASI_ID R_any | ≥ control − 0.005 |
| Clean-case friction | **≤ 0.15**, and ≤ the control's |
| Over-redaction | ≤ the control's |
| FAILED stages from infrastructure | **0** (infrastructure faults are resumed, never scored); finder-output failures are BLOCK decisions under the crash policy, counted in friction when the case is clean, and reported |
| Typed-tier answers that were malformed or off-format | every one resolved to the existing hold (checked in code and in the run's decision records) |

**Selection, written in advance.** Arm 2 is the v0.3 candidate. Arm 1 is reported as an ablation. If arm 2 fails
and arm 1 passes every condition above, v0.3 ships with the typed tier off by default, and the results say plainly
that this choice between two pre-declared configurations was made on D. If both fail, v0.3 FAILS its rule and is
released (if at all) as a preview with that verdict, as v0.1 and v0.2 were.

**Reported, not gating:** every metric per half; the control's half-Q minus half-C difference (the family effect);
indirect UNION / CRIMINAL / ETHNICITY R_any; typed-tier unsure rate, order disagreement and added seconds per
document (median, p95); released leaks inside injection cases.
