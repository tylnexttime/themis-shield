# Frozen evaluation plan: eval-kit-v0.2-frozen

Written and committed **before the v0.2 test split was opened**. Nothing below may change after a test result is
seen. The v0.1 plan is [`FROZEN-PLAN.md`](FROZEN-PLAN.md); the v0.2 development work is in
[`V02-DEV-LOG.md`](V02-DEV-LOG.md).

## Test split

| Item | Value |
|---|---|
| File | `eval/cases/test-c.jsonl` (added after this plan, together with its generator under `eval/generator_c/`) |
| sha256 | `39ea2f9595c959f19f2d15607003159f42349ce9ca485f0cf2777d674f2e9ab9` |
| Size | 371 cases, 3,686 gold spans, 95 clean cases (no personal information), 42 injection cases |
| Author | An independent author (another model family) who saw only `eval/taxonomy.md`, the scorer's case and prediction format, and the Harbourline profile (`eval/profiles/harbourline.json`). Not the v0.1 generators, not the v0.2 development cases, not the v0.2 code. |

Before either arm runs, the operator checks the file's sha256 against the value above; a mismatch stops the
evaluation. Each run's `config.json` records the hash again.

## Kit

| Item | Value |
|---|---|
| Code | The commit carrying tag `eval-kit-v0.2-frozen`: this plan's commit plus only `eval/cases/test-c.jsonl` and `eval/generator_c/`. No detector, policy, prompt, profile, catalogue, runner or scorer change after this plan. |
| Gate behaviour | `[detection] version = "v0.2"`, `[policy] version = "v0.2"`, `quasi_id_ask = false` (the defaults; iteration 2 of the development log). The iteration-3 change (branch `v0.2-it3-date-exemptions`) is **not** part of the kit. |
| Pipeline | `detect_batch(..., thorough=True)`: rules `core,au,intl` → PII-Tracer (CPU) → Gemma 4 E4B Q4_K_M → Gemma 4 26B-A4B → union, propagation (with the v0.2 table pass), allow-list → `policy.decide` |
| Weights | `gemma-4-E4B-it-Q4_K_M.gguf` (`-ngl 99`, ctx 16384); `gemma-4-26B-A4B-it-UD-IQ3_XXS.gguf` (`-ngl 99 -ncmoe 18 -c 16384`, mmap fallback variant) from `eval/config/catalogue-26b-iq3xxs.json` (sha256 `03224fb44d78350a…`) |
| Finder prompt | v2, unchanged since `eval-kit-v0.1-frozen` |
| Institution profile | `harbourline-eval` (sha256 `691a651584d64b04…`) |
| Finder context | "a training and research institute" |
| Label map / scorer | `eval/label_map.py`, `eval/score.py` (headline metrics are label-agnostic) |
| Engine | llama.cpp `llama-server` build b11760, prompt cache on |
| Harness | `eval/run_eval.py`: `--memory-settle-seconds 60`, `--chunk 40`, `--max-tokens 8192`; a FAILED stage stops the run (infrastructure); malformed output and timeouts count. Test guard: clean tree, HEAD tagged `eval-kit-v0.2-frozen`, no existing score in the run folder. |

## Dev numbers the rule is derived from

All 521 development cases (`eval/cases/dev.jsonl` + the retired v0.1 `eval/cases/test.jsonl`), one live finder
capture, both rows replayed from it (`V02-DEV-LOG.md`).

| Run | Released leaks | R_any | R_full | Sensitive R_any | Clean friction | Over-redaction | Failed stages |
|---|---|---|---|---|---|---|---|
| v02-baseline (v0.1 behaviour, `--gate-version v0.1`) | 1 | 0.9982 | 0.9864 | 0.9970 | 0.233 (14/60) | 0.332 (364/1096) | 0 |
| v02-it2 (v0.2 behaviour, the kit) | 0 | 0.9989 | 0.9871 | 0.9970 | 0.050 (3/60) | 0.279 (306/1096) | 0 |

The test split comes from another author, with templates and phrasings that never appear in dev, so the rule
allows a margin below the dev figures. v0.1 failed its rule because a policy branch that fired once on dev fired
eleven times on test; the friction limit leaves about three times the dev value.

## Arms (each run once on test, from the tag, clean tree)

1. **v0.2 (candidate)**, live, capturing every finder response:

   ```
   python eval/run_eval.py --split test --cases eval/cases/test-c.jsonl --run-name test-c-v02 \
       --frozen-tag eval-kit-v0.2-frozen --prompt v2 --catalogue eval/config/catalogue-26b-iq3xxs.json \
       --runtime-settings <file> --runs-dir <dir> --capture-finders <dir>/captures/test-c-v02
   ```

2. **v0.1 negative control**: the same tag and settings with v0.1 detection and policy, **replaying arm 1's
   capture**, so both arms see identical finder outputs and differ only in the v0.2 changes:

   ```
   python eval/run_eval.py --split test --cases eval/cases/test-c.jsonl --run-name test-c-v01-control \
       --frozen-tag eval-kit-v0.2-frozen --prompt v2 --catalogue eval/config/catalogue-26b-iq3xxs.json \
       --runtime-settings <file> --runs-dir <dir> --gate-version v0.1 --replay-finders <dir>/captures/test-c-v02
   ```

   Replay refuses any call whose prompt hash or model identity differs from the capture; a refusal is an
   infrastructure fault (see below), not a result.

Arm 2 runs only after arm 1 has finished and been scored. Run outputs and the capture live outside the repository.

## Crash policy

If a run crashes or stops on a FAILED stage, only the infrastructure fault is fixed (for example memory settling,
a model that would not load, a server that died, a replay refusal caused by the harness rather than by the kit). The
fix is recorded in the results with its commit, and the same run is resumed with `--resume` into the same run
folder (the capture is appended to and its last response per case and model counts). No detector, policy, prompt,
profile or scoring change is a crash fix. Malformed finder output and timeouts are results, not crashes. A run that
was scored is never re-run.

## Pass rule

**Instrument check (v0.1 control):** the control must show **clean friction ≥ 0.10 or ≥ 2 released leaks** on the
test split. Otherwise the split is not shown to exercise what v0.2 claims to fix, and the v0.2 result is reported as
**INCONCLUSIVE**, whatever its numbers.

**v0.2 PASSES** if, on the test split, all of these hold:

| Condition | Dev | Limit |
|---|---|---|
| Released leaks | 0 | **≤ 3**, and ≤ the control's |
| R_any | 0.9989 | **≥ 0.98**, and ≥ control − 0.001 |
| Sensitive R_any (HEALTH, DISABILITY, ETHNICITY, SEXUALITY, RELIGION, CRIMINAL, FAMILY_VIOLENCE, UNION pooled) | 0.997 | **≥ 0.95**, and ≥ control − 0.005 |
| Clean-case friction (ASK or BLOCK on cases without personal information) | 0.050 | **≤ 0.15**, and ≤ the control's |
| Over-redaction (hard negatives touched) | 0.279 | **≤ the control's** (no absolute limit; it is not a safety metric) |
| FAILED stages in the scored run | 0 | **0** |

Otherwise v0.2 **FAILS** the rule. Either way, both arms are reported side by side with every metric (the columns of
`RESULTS.md` §2 and the per-label table), and the kit is not changed afterwards.
