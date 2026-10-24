# Frozen evaluation plan: eval-kit-v0.1-frozen

Written and committed **before the test split was opened**. Nothing below may change after a test result is seen.

## Kit

| Item | Value |
|---|---|
| Cases | `eval/cases/dev.jsonl` sha256 `d663cca97191640518d93ed12938722285100e94666c57c56cc196d9d3568c25`; `eval/cases/test.jsonl` sha256 `f1fbcb4311eb7579c1f309c3b9595e55b7dc253d73d7c0592196e0be258db792` |
| Pipeline | `detect_batch(..., thorough=True)`: rules `core,au,intl` → PII-Tracer (CPU) → Gemma 4 E4B Q4_K_M → Gemma 4 26B-A4B → union, propagation, allow-list → `policy.decide` |
| 26B weights | `gemma-4-26B-A4B-it-UD-IQ3_XXS.gguf`, `-ngl 99 -ncmoe 18 -c 16384` (`eval/config/catalogue-26b-iq3xxs.json`), chosen by the quantisation rule written before the dev baselines |
| Institution profile | `harbourline-eval` (converted from `eval/profiles/harbourline.json` by `eval/convert_profile.py`) |
| Finder context | "a training and research institute" |
| Label map | `eval/label_map.py` (headline metrics are label-agnostic) |
| Engine | llama.cpp `llama-server` build b11760, prompt cache on |
| Harness | `--memory-settle-seconds 60`; a FAILED stage stops the run (infrastructure), malformed output and timeouts count |

## Arms (both run once on test, from this tag, clean tree)

1. **v2 (candidate)**: finder prompt v2 as tuned on dev (iteration 2, run `dev-v2-it2`).
   `python eval/run_eval.py --split test --run-name test-v2 --frozen-tag eval-kit-v0.1-frozen --prompt v2 --catalogue eval/config/catalogue-26b-iq3xxs.json --runtime-settings <file> --runs-dir <dir>`
2. **v1 negative control**: the prompt as ported, byte for byte (v1 with the demo profile's do-not-list items), same
   weights, same everything else (only the prompt cache, now on, differs from the dev baseline run). It shows the instrument catches leaks: on dev it released 12 leaks
   (`dev-v1-iq3xxs`).
   `python eval/run_eval.py --split test --run-name test-v1-control --frozen-tag eval-kit-v0.1-frozen --prompt v1 --do-not-flag-from harbourline-demo --catalogue eval/config/catalogue-26b-iq3xxs.json --runtime-settings <file> --runs-dir <dir>`

If a run crashes, only the crash is fixed (recorded in the report) and the same run is resumed with `--resume`.

## Dev numbers the rule is derived from

| Run | R_any | R_full | sensitive R_any | released leaks | clean friction |
|---|---|---|---|---|---|
| dev-v1-iq3xxs (control prompt) | 0.986 | 0.950 | 0.909 | 12 | 0.250 (6/24) |
| dev-v2-it2 (frozen prompt) | 0.999 | 0.989 | 0.993 | 0 | 0.125 (3/24) |

Test uses templates and phrasings that never appear in dev, so the rule allows a margin below the dev figures.

## Pass rule

**Instrument check (v1 negative control):** the control arm must release **at least 5** leaks on test. If it releases
fewer, the instrument is not shown to catch leaks on this split and the v2 result is reported as **INCONCLUSIVE**,
whatever its numbers.

**v2 PASSES** if, on test, all of these hold:

- released leaks **≤ 5**;
- R_any **≥ 0.98**;
- sensitive-label R_any (HEALTH, DISABILITY, ETHNICITY, SEXUALITY, RELIGION, CRIMINAL, FAMILY_VIOLENCE, UNION pooled)
  **≥ 0.95**;
- friction on cases without personal information (ASK or BLOCK) **≤ 0.25**;
- no stage FAILED in the scored run.

Otherwise v2 **FAILS** the rule. Either way, both arms are reported side by side with every metric, and the kit is not
changed afterwards.
