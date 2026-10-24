# themis-shield evaluation results

> **v0.2:** the v0.2 changes were tested on a fresh held-out split (split C) and failed their pass rule on
> friction (0.200 > 0.15), with 0 released leaks. See [`RESULTS-v0.2.md`](RESULTS-v0.2.md). This file covers v0.1.

24/10/2026. Per-run outputs (predictions, decisions, per-call finder timings, prompts with the document text masked,
config snapshots, score reports) were kept outside the repository; the commands to reproduce every run are in
[`FROZEN-PLAN.md`](FROZEN-PLAN.md) and [`README.md`](README.md).

## Publication note: provenance of these numbers

The runs below used our private development history. Commit ids and the tag name `eval-kit-v0.1-frozen` refer to
that history, which was squashed into this repository's first commit for publication. The case files are hash-pinned in [`FROZEN-PLAN.md`](FROZEN-PLAN.md),
and every file the **v2 arm** depends on is either byte-identical in this repository or differs only in comment and
docstring wording:

| File | sha256 (first 16 hex) | vs the frozen commit |
|---|---|---|
| `themis_shield/detectors/__init__.py` | `ec381d14fa50068e` | identical |
| `themis_shield/detectors/allowlist.py` | `13c0308ca5cce485` | identical |
| `themis_shield/detectors/contracts.py` | `eb2ddc77000c2f3e` | identical |
| `themis_shield/detectors/engine.py` | `f8bf91a172b98a33` | identical |
| `themis_shield/detectors/escalation.py` | `51d3030889cafb8b` | comments only |
| `themis_shield/detectors/labels.py` | `6b6aeefa52749678` | comments only |
| `themis_shield/detectors/llm_finder.py` | `e71d0053a840a4cb` | comments only |
| `themis_shield/detectors/markings.py` | `dcaaa33fb1d1f787` | identical |
| `themis_shield/detectors/pii_tracer.py` | `f2662824da32ab37` | identical |
| `themis_shield/detectors/profile_adapter.py` | `f832aefc36105861` | identical |
| `themis_shield/detectors/propagate.py` | `ae1da873af3e810c` | comments only |
| `themis_shield/detectors/rules.py` | `6cdc588404c929bb` | identical |
| `themis_shield/detectors/union.py` | `78a1e7a09e6b7a4a` | identical |
| `themis_shield/policy.py` | `0140f53f570f08a4` | identical |
| `themis_shield/types.py` | `14bccd5b14a79e3b` | comments only |
| `themis_shield/profiles/institution/harbourline-eval.json` | `691a651584d64b04` | identical |
| `eval/config/catalogue-26b-iq3xxs.json` | `03224fb44d78350a` | identical |
| `eval/run_eval.py` | `921d8005c67d7756` | identical |
| `eval/label_map.py` | `833d097a2c6875c6` | identical |
| `eval/convert_profile.py` | `fdf96940cb935410` | identical |
| `eval/score.py` | `72f5bf07c7c60d06` | identical |
| `eval/cases/dev.jsonl` | `d663cca971916405` | identical |
| `eval/cases/test.jsonl` | `f1fbcb4311eb7579` | identical |

Since the freeze, the runtime code that *launches* models has changed: the Strata and Marian back ends, and the
default catalogue now names the IQ3_XXS weights the kit already used. That changes how a model is started, not what
it is asked or how its answers are scored; the eval kit carries its own catalogue patch. The **v1 control** read its
"do not list" from the demo institution profile, which has been rewritten since, so the control reproduces in
substance, not byte for byte.

To re-run the test split on this release, check out tag `v0.1.0` and pass `--frozen-tag v0.1.0` to
`eval/run_eval.py` (the runner refuses the test split unless HEAD carries the tag you name).

## 1. Headline

- **Frozen kit:** tag `eval-kit-v0.1-frozen` on commit `9131f9fb54ec4305a803e4cb0a192227a9ebd92b`. Case-file sha256:
  dev `d663cca97191640518d93ed12938722285100e94666c57c56cc196d9d3568c25`, test
  `f1fbcb4311eb7579c1f309c3b9595e55b7dc253d73d7c0592196e0be258db792`. Both match the eval set's manifest.
- **Test, prompt v2 (frozen):** **5 released leaks**, all QUASI_ID and all from one generator-A table layout.
  R_any 0.997, R_full 0.984, any-label precision 0.926, sensitive-label R_any 1.000. No released leak in the 44
  injection cases. Decisions on the 296 cases: ASK 198, BLOCK 24, RELEASE 74. **Friction on the 36 clean cases:
  0.306.**
- **Test, v1 negative control** (the ported prompt, same weights): 12 released leaks, R_any 0.977, R_full 0.911,
  sensitive-label R_any 0.859, UNION R_any 0.082. So the instrument does catch leaks.
- **Pass rule (written into the kit before test): v2 FAILS it on friction.** Four conditions are met: released leaks
  5 ≤ 5, R_any 0.997 ≥ 0.98, sensitive R_any 1.000 ≥ 0.95, and no failed stage. Clean-case friction is 0.306, above
  the 0.25 limit. All 11 held clean cases are `ASK:disagreement-empty-llm`: both finders correctly returned nothing,
  and PII-Tracer flagged a date or a role mailbox (§6). The instrument check passed (control 12 ≥ 5), so the verdict
  is FAIL, not inconclusive.
- **Quantisation:** 26B-A4B **UD-IQ3_XXS adopted**, under the rule written before the baselines: same released leaks,
  sensitive R_any lower by 0.0035. It generates 1.5 times faster than IQ4_XS.

## 2. All runs

Dev has 225 cases, 1,882 gold spans and 24 clean cases. Test has 296 cases, 2,601 gold spans and 36 clean cases.

- Over-redaction: share of hard negatives touched.
- Look-alike: the sensitive look-alike kinds (fiction, public news, committee, initiative, public event, policy
  statement).
- Wall time: the whole split on one laptop.

| Run | Commit | Prompt / 26B | R_full | R_any | Precision | Sens. R_any | Released leaks | Inj. leaks | ASK | BLOCK | Clean friction | Over-redaction | Look-alike | Wall min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev-v1-iq4xs (baseline a) | a8d0140 | v1 / IQ4_XS | 0.953 | 0.986 | 0.910 | 0.912 | 12 | 0 | 141 | 20 | 0.250 | 0.342 (173/506) | 7/43 | 65 |
| dev-v1-iq3xxs (baseline b) | a8d0140 | v1 / IQ3_XXS | 0.950 | 0.986 | 0.911 | 0.909 | 12 | 0 | 143 | 23 | 0.250 | 0.338 (171/506) | 5/43 | 50 |
| dev-v2-it1 (**invalid: crashed**) | 8ec6096 | v2 / IQ3_XXS | 0.939 | 0.974 | 0.912 | 0.860 | 0 | 0 | 27 | 188 | 0.583 | 0.330 | 3/43 | 25 |
| dev-v2-it1-rerun (**stopped at chunk 1**) | a6bdc03 | v2 / IQ3_XXS | n/a | | | | | | | | | | | 5 |
| dev-v2-it1b (iteration 1) | 2ebf2cc | v2 / IQ3_XXS | 0.989 | 0.997 | 0.917 | 0.982 | 0 | 0 | 151 | 19 | 0.125 | 0.330 (167/506) | 3/43 | 50 |
| dev-v2-it2 (iteration 2, **frozen**) | a8882fd | v2 / IQ3_XXS | 0.989 | 0.999 | 0.918 | 0.993 | 0 | 0 | 150 | 19 | 0.125 | 0.332 (168/506) | 3/43 | 51 |
| **test-v2** | 9131f9f (tag) | v2 / IQ3_XXS | **0.984** | **0.997** | **0.926** | **1.000** | **5** | **0** | 198 | 24 | **0.306** | 0.329 (194/590) | 6/63 | 70 |
| **test-v1-control** | 9131f9f (tag) | v1 ported / IQ3_XXS | 0.911 | 0.977 | 0.921 | 0.859 | 12 | 0 | 183 | 24 | 0.056 | 0.324 (191/590) | 7/63 | 64 |

The crashed runs are not tuning iterations (§9). Only two of the three allowed iterations were used (§4).

## 3. Quantisation decision (rule written before any result)

The rule was recorded at 04:10 UTC, before the first baseline started: adopt IQ3_XXS only if its released leaks are
≤ IQ4_XS's and its sensitive-label R_any (8 labels pooled) is not lower by more than 0.01. Otherwise keep IQ4_XS.

| | IQ4_XS (`-ncmoe 20`) | IQ3_XXS (`-ncmoe 18`) | Rule |
|---|---|---|---|
| Released leaks | 12 | 12 | ≤: met |
| Sensitive R_any | 0.9123 (260/285) | 0.9088 (259/285) | drop 0.0035 ≤ 0.01: met |

**IQ3_XXS adopted.**

What the rule does not weigh, reported for honesty:
- R_full 0.950 vs 0.953.
- UNION R_any one span lower.
- BLOCK 23 vs 20: in three identity-record tables IQ3_XXS invented a SECRET. That is a false block, not a leak.

Gains:
- Generation 37.2 vs 23.8 tok/s.
- 26B time per document 6.8 s vs 10.0 s (median).
- Wall time 50 vs 65 minutes.

UD-IQ3_S remains an option between the two.

The eval kit carries its own catalogue patch, `eval/config/catalogue-26b-iq3xxs.json`, with the same mmap fallback
variant as the shipped entry. After the evaluation, the shipped catalogue was switched to IQ3_XXS as its default 26B.

## 4. Prompt v2: what changed, what it gained and cost on dev

### Changes

The prompt is in `themis_shield/detectors/llm_finder.py`. Setting `[detection] finder_prompt = "v1"` restores the
ported prompt; a sha256 test pins its rendering with the demo profile.

- **A fixed system message**, identical for every request of a deployment. It holds the instructions, a one-line
  definition for each of the 24 labels the finder handles, a sensitive-statement section, the deployment's
  do-not-list items and the rules. The user message holds only the document, with a fixed head and tail.
- **Generic labels.** v2 uses INTERNAL_ID and NATIONAL_ID instead of STUDENT_ID, USI, TFN, MEDICARE and ABN. It adds
  USERNAME, PAYMENT_CARD, UNION, and ETHNICITY (in place of INDIGENOUS).
  - These six are new members of the gate's `Label` enum.
  - ETHNICITY and UNION are sensitive in policy (ASK), in the allow-list (never trimmed) and in placeholders.
  - v1, the tracer aliases and the rule packs are unchanged.
- **Sensitive guide.** Each sensitive label gets 3 or 4 invented indirect phrasings and 2 look-alikes. Three rules
  go with them:
  - copy the statement from its verb;
  - a negative or "No" answer counts;
  - (iteration 2) a role or status written next to a name is a statement about that person.
- **Do-not-list from the profile.** There is a new optional institution-profile field, `finder_do_not_flag`.
  - The sector-specific examples moved from code into the opt-in `harbourline-demo` profile: course and unit codes,
    the provider registration number, and sector data-standard field names (since replaced by sector-neutral demo values).
  - The generic default list has no sector or country terms, and a test checks this.
- **Prompt cache.** The local client now sends `cache_prompt: true` (it used to send `false`) and records the
  server's `cache_n`. §8 shows how reuse was confirmed.
- **No eval text in the prompt.** `tests/test_finder_prompt.py` checks that the rendered v2 prompt shares no
  8-word sequence with any dev or test case. It checks the generic, eval-profile and demo-profile variants, and it
  passes.

### Iterations (dev only, each a full dev run)

1. **`dev-v2-it1b`**, v2 as first written, compared with baseline b.
   - Gains: R_full 0.950 → 0.989; R_any 0.986 → 0.997; released leaks 12 → 0; sensitive R_any 0.909 → 0.982;
     UNION R_any 0.27 → 0.90; sensitive R_full 0.69 → 0.94 (whole statements are now copied); clean friction
     6/24 → 3/24; precision 0.911 → 0.917; look-alikes touched 5 → 3.
   - Costs: `ASK:unmatched-llm-values` 6 → 14 cases (the finder paraphrases long statements more often); ASK
     143 → 151.
2. **`dev-v2-it2`.** The five untouched dev leaks were a union role in brackets after a name (3) and an indirect
   family-violence remark (2). We added the role-next-to-name rule and swapped one direct family-violence example for
   an indirect one.
   - Results: untouched leaks 5 → 2; R_any 0.997 → 0.999; sensitive R_any 0.982 → 0.993; partials 15 → 18.
   - We stopped here. The gain was small, and a third round would have chased individual dev phrases.

**Speed.** The v2 prefix is about 1,700 tokens and is reused: on test, 88 % of 26B prompt tokens came from the
cache. So v2's wall time matched v1's despite the longer prompt (51 vs 50 minutes on dev).

**Over-redaction hardly moved** (0.338 → 0.330). In iteration 1, 139 of the 167 touched hard negatives came from
tier 1 alone (rules and PII-Tracer), 24 from tier 1 and a finder together, and only 4 from a finder alone. A prompt
change cannot fix this.

## 5. Test results (frozen kit, each arm run once)

Both arms ran from the tagged, clean tree under the runner's test guard. Neither crashed, no stage failed, and no
llama-server was left running afterwards.

| Label | Gold | v2 R_full | v2 R_any | v1 control R_full | v1 control R_any |
|---|---|---|---|---|---|
| PERSON | 844 | 1.000 | 1.000 | 1.000 | 1.000 |
| USERNAME | 68 | 1.000 | 1.000 | 1.000 | 1.000 |
| INTERNAL_ID | 163 | 1.000 | 1.000 | 1.000 | 1.000 |
| TFN / MEDICARE / ABN | 71 / 59 / 52 | 1.000 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 49 | 1.000 | 1.000 | 1.000 | 1.000 |
| DRIVER_LICENCE / PASSPORT | 51 / 54 | 1.000 | 1.000 | 1.000 | 1.000 |
| BANK_ACCOUNT / PAYMENT_CARD | 45 / 55 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL / PHONE / ADDRESS | 111 / 136 / 46 | 1.000 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH / OTHER_DATE | 79 / 159 | 1.000 | 1.000 | 1.000 | 1.000 |
| URL_PERSONAL / SECRET | 57 / 65 | 1.000 | 1.000 | 1.000 | 1.000 |
| **QUASI_ID** | 53 | 0.736 | 0.849 | 0.623 | 0.887 |
| HEALTH | 63 | 0.889 | 1.000 | 0.635 | 1.000 |
| DISABILITY | 41 | 1.000 | 1.000 | 0.488 | 0.902 |
| ETHNICITY | 47 | 0.936 | 1.000 | 0.362 | 1.000 |
| SEXUALITY | 47 | 0.745 | 1.000 | 0.277 | 0.936 |
| RELIGION | 49 | 1.000 | 1.000 | 0.490 | 1.000 |
| CRIMINAL | 45 | 0.978 | 1.000 | 0.644 | 0.956 |
| FAMILY_VIOLENCE | 43 | 0.907 | 1.000 | 0.651 | 1.000 |
| UNION | 49 | 0.980 | 1.000 | 0.020 | 0.082 |
| **Overall** | 2,601 | **0.984** | **0.997** | 0.911 | 0.977 |

| Gate | v2 | v1 control |
|---|---|---|
| Released leaks | **5** (QUASI_ID 5) | 12 (QUASI_ID 5, UNION 3, DISABILITY 3, SEXUALITY 1) |
| Untouched / partial spans | 8 / 34 | 60 / 172 |
| Decisions RELEASE / ASK / BLOCK | 74 / 198 / 24 | 89 / 183 / 24 |
| ASK rate / BLOCK rate | 0.669 / 0.081 | 0.618 / 0.081 |
| Clean cases held for ASK (of 36) | 11 (all `disagreement-empty-llm`) | 2 |
| Injection cases / released leaks in them | 44 / 0 | 44 / 0 |
| Any-label / typed precision | 0.926 / 0.845 | 0.921 / 0.832 |
| Hard negatives touched | 194/590 (0.329) | 191/590 (0.324) |
| Sensitive look-alikes touched | 6/63 | 7/63 |
| Clean cases with any redaction | 22/36 | 24/36 |
| Reasons (cases) | sensitive 169, unmatched values 27, secret 24, empty-finder disagreement 11, injection 5, few persons 3 | sensitive 162, unmatched values 24, secret 24, injection 5, few persons 3 |

### Pass rule (from `eval/FROZEN-PLAN.md`)

- Instrument check: met (control 12 ≥ 5).
- v2 conditions:
  - released leaks 5 ≤ 5: met;
  - R_any 0.997 ≥ 0.98: met;
  - sensitive R_any 1.000 ≥ 0.95: met;
  - no failed stage: met;
  - **clean friction 0.306 > 0.25: not met.**

**Verdict: FAIL.** The kit was not changed afterwards.

### Released leaks on test (v2), by label

QUASI_ID (5). All five are in two generator-A cases built from the same template: an "anonymised" CSV with
`dob, postcode, role, sick_days` columns.

1. `A-QI-007` "Fleet controller": a unique job title in a row next to a DOB and postcode. The role cell was not
   redacted.
2. `A-QI-007` "Head chef" (first row): same table, same reason.
3. `A-QI-007` "Head chef" (second row): same.
4. `A-QI-007` "Branch manager": same.
5. `A-QI-008` "Branch manager": the same layout in a sibling case.

The DOBs and postcodes in those rows were redacted, so what was released is a job title cut off from its row's
identifiers. The v1 control released the same five.

Three more QUASI_ID spans ("the lab cleaner who sings at assemblies ...", in `B-QI-019` and `B-QI-020`) were missed
entirely. Those cases were held for ASK because of their sensitive statements, so the misses are not released leaks.

The v1 control's other released leaks, for comparison: a disability-scheme plan review in a chat log (3), union
activity phrased indirectly (3), and "has a husband" said of a man (1).

## 6. What the test run says about the gate (not the prompt)

- **The friction failure comes from the policy's empty-finder rule.** `ASK:disagreement-empty-llm` fires when both
  finders return nothing while PII-Tracer found something, or rated the document ≥ 0.5 sensitive. v2 is better at
  returning an empty list on clean documents, so it trips the rule where v1, which lists something, does not. All 11
  clean cases v2 held have this reason. On dev the rule fired on only 1 case, so the margin did not anticipate it.
- **Tier-1 false positives drive both friction and over-redaction.**
  - PII-Tracer labels public meeting dates and times ("10:31 AM") as dates of birth.
  - PII-Tracer takes a trailing full stop into a role mailbox (`facilities@...example.`), which then escapes the
    allow-list's whole-value match.
  - The `au` rule pack's USI checksum matches ordinary 10-letter words about one time in 32 ("Breakwater",
    "just repeat").
- **The organisation's own ABN is never allow-listed** when it passes its checksum: `allowlist.trim` keeps any
  checksum-valid identifier before it looks at literals.
- These are listed under "Next" in the project README. They were not changed here: they are not prompt or label
  work, and the kit is frozen.

## 7. Timings and measured speeds

Setup: one laptop with an 8 GB Blackwell laptop GPU and 31.5 GB RAM, llama.cpp b11760, flash attention on, one model
resident at a time, PII-Tracer on the CPU in the same process. A typical case has 320 to 330 characters; each split
also has six long documents of 9K to 13K characters.

| Model (role) | Weights / args | Generation tok/s (median) | Prompt tok/s (uncached, ~2.3K-token prompts) | Seconds per document (median / long doc) |
|---|---|---|---|---|
| Gemma 4 E4B (finder-fast) | Q4_K_M, `-ngl 99`, ctx 16384 | 61.6 | ~2,750 | 3.1 / 5 to 7 |
| Gemma 4 26B-A4B (finder-thorough) | UD-IQ4_XS, `-ngl 99 -ncmoe 20`, ctx 16384 | 23.8 | ~680 | 10.0 / 23 to 25 |
| Gemma 4 26B-A4B (finder-thorough) | **UD-IQ3_XXS**, `-ngl 99 -ncmoe 18`, ctx 16384 | 36.3 | ~750 | 6.6 / 14 to 17 (45 when it listed 1,500 tokens) |
| Tier 1 (rules + PII-Tracer, CPU) | | | | 0.3 / 6 to 9 |
| **Whole gate, thorough** (E4B + 26B IQ3_XXS) | | | | median 10.2, mean 14.1 per document; 296 cases in 70 min |

- Load times: E4B 4 to 6 s, 26B 8 to 9 s.
- Under the scheduler's memory budget the 26B loads in full mode when about 18 GB of RAM is free, and otherwise in
  mmap mode (same weights, same output).
- With the v2 prefix cached, each finder call processes only the document: about 150 new tokens for a typical case.

## 8. Prompt-cache reuse: how it was confirmed

1. **Per request, from the server's own timings.** Every llama-server response carries `timings.cache_n`; the client
   now keeps it, and the runner writes it to `llm_calls.jsonl`.
   - In `dev-v2-it1b`, the first E4B call processed 1,828 prompt tokens with `cache_n = 0`. Every later call reused
     1,663 to 1,711 tokens and processed about 150.
   - On test, 494K of 562K E4B prompt tokens and 494K of 563K 26B prompt tokens came from the cache.
   - Every call in a run had the same masked template hash (`templates_per_model = 1`), so the prefix was
     byte-identical.
2. **In the server log.** The gate keeps llama-server's log at verbosity 0 and drains it through a fixed-code filter.
   So we ran the same E4B weights once by hand with `--verbose` and sent two v2 requests with two invented one-line
   notes (log kept locally). For the second request the server logged
   `cached n_tokens = 1713` and `prompt eval time = 65.75 ms / 35 tokens`. The first request logged
   `827.57 ms / 1747 tokens`.

llama.cpp notes that a reused cache can change logits slightly because batch sizes differ, so runs are not
bit-for-bit reproducible. The v1 control on test also used the cache; the dev baselines did not.

## 9. Crashes and fixes (recorded as required)

- **`dev-v2-it1` (dev).** In 5 of 6 chunks the 26B never loaded. The scheduler waited 5 seconds for memory, raised
  `NotEnoughMemory`, and the gate failed closed: BLOCK on 185 cases. The run's "0 released leaks" therefore means
  nothing.
  - First fix (`a6bdc03`): the harness gives the scheduler 60 s for memory to settle, and stops before writing any
    chunk in which a stage FAILED. Infrastructure faults are then fixed and resumed instead of scored. Malformed
    output and timeouts still count.
- **`dev-v2-it1-rerun`** stopped at chunk 1 for the same fault. It was diagnosed by reproducing it by hand.
  - The 26B full mode needs 16 GB of available RAM plus a 2 GB margin. With PII-Tracer's heap in the same process,
    the laptop had about 18.4 GB.
  - The shipped IQ4_XS entry falls back to its mmap variant; the IQ3_XXS patch had dropped that variant.
  - Fix (`2ebf2cc`): the patch now carries the same variant, and the recording client logs why a model could not be
    made resident.
- Neither fix touched the prompt. No test run crashed, and neither test arm was re-run.

## 10. Label mapping

`eval/label_map.py` maps gate labels to gold labels; one gate label may stand for several. Notable entries:

- STUDENT_ID → any identifier, because it also receives PII-Tracer's coarse `account_number`.
- DATE_OF_BIRTH → DOB or OTHER_DATE, because of the tracer's `private_date`.
- NATIONAL_ID → NATIONAL_ID, TFN, MEDICARE, ABN.
- URL_PERSONAL → URL_PERSONAL, USERNAME.
- INDIGENOUS → ETHNICITY.

Gold labels prompt v1 cannot name:
- USERNAME, except as a "handle";
- INTERNAL_ID beyond student numbers;
- NATIONAL_ID;
- PAYMENT_CARD;
- ETHNICITY beyond Indigenous status;
- UNION.

The headline numbers are label-agnostic. Typed recall on test was 0.987 for v2 and 0.933 for v1.

## 11. Limitations

1. **Synthetic text.** Sentences are grammatical, PII sits in predictable slots, and identifiers are mostly in wide
   tables. Every identifier label scored 1.000 under both prompts, so this set does not separate identifier
   detection, and real recall will be lower. Treat the numbers as a ceiling and a regression test.
2. **One author family.** The eval set's two generators, the prompt v2 examples and this harness were all written by
   the same kind of model, and correlated habits can flatter the result. The 8-gram check rules out copied text, not
   shared style.
3. **One machine, one run per configuration.** There are no repeats, so no variance estimate. The prompt cache makes
   outputs not bit-for-bit reproducible. The speeds hold for this laptop only.
4. **Small denominators.** There are 24 clean cases in dev and 36 in test, so one case moves friction by 0.028. Each
   sensitive label has 30 to 49 test spans. All released leaks on test come from one table template.
5. **The pass-rule margin was set from dev,** where the empty-finder rule fired once; on test it fired 11 times. The
   rule did its job: it caught a friction cost that dev hid.
6. **Thorough mode only.** Every document went through both finders. The fast path (tier 1 only, for short,
   low-sensitivity text) was not evaluated.
7. **R_full counts partial coverage as a leak,** even when only a verb or pronoun is uncovered ("he" in "he has a
   migraine again"). R_any is the safety-relevant figure; both are reported.
