# themis-shield v0.2 evaluation results

25/10/2026. The v0.2 detection and policy changes, tuned on development data only
([`V02-DEV-LOG.md`](V02-DEV-LOG.md)), then tested once on a fresh held-out split under a plan committed before the
split was opened ([`FROZEN-PLAN-v0.2.md`](FROZEN-PLAN-v0.2.md)). Per-run outputs were kept outside the repository;
the commands are in the plan. The v0.1 results are in [`RESULTS.md`](RESULTS.md).

## 1. Headline

- **Frozen kit:** tag `eval-kit-v0.2-frozen`. Test split C: `eval/cases/test-c.jsonl`, sha256
  `39ea2f9595c959f19f2d15607003159f42349ce9ca485f0cf2777d674f2e9ab9`, 371 cases, 3,686 gold spans, 95 clean cases,
  42 injection cases, 2,983 hard negatives. Written by an independent author (another model family) who saw only
  the taxonomy, the scorer's format and the Harbourline profile; its generator is in `eval/generator_c/`.
- **Arms:** v0.2 (the candidate, run live with every finder response captured) and a **v0.1 negative control**
  (the same tag with `--gate-version v0.1`, replaying v0.2's capture), so both arms saw identical finder output and
  differ only in the v0.2 detection and policy code.
- **v0.2 on test:** **0 released leaks**, R_any 0.990, R_full 0.979, sensitive R_any 0.954, over-redaction 0.203.
  **Clean-case friction 0.200** (19 of 95). Control: 0 released leaks, R_any 0.982, friction 0.358 (34 of 95),
  over-redaction 0.214.
- **Verdict: v0.2 FAILS its pass rule on friction** (0.200 > 0.15). Every other condition is met, and the instrument
  check passed (control friction 0.358 ≥ 0.10), so the verdict is FAIL, not inconclusive. The kit was not changed
  afterwards.
- **What v0.2 did fix:** quasi-identifiers in tables (QUASI_ID R_any 0.738 → 0.976), empty-finder holds on clean
  documents (21 → 6), and tier-1 over-redaction of the organisation's own ABN, public dates and building names.
  **What failed the rule:** clean documents held for reasons outside v0.2's changes (§4).

## 2. Development (521 cases, the whole v0.1 set)

One live capture, every row replayed from it (details in [`V02-DEV-LOG.md`](V02-DEV-LOG.md)). Over-redaction is out
of 1,096 hard negatives; look-alike out of 106; clean friction out of 60.

| Run | R_full | R_any | Precision | Sens. R_any | Released leaks | Inj. leaks | ASK | BLOCK | Clean friction | Over-redaction | Look-alike |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline (v0.1 behaviour) | 0.986 | 0.998 | 0.922 | 0.997 | 1 | 0 | 350 | 43 | 0.233 | 0.332 (364) | 9 |
| iteration 1, QUASI_ID-ASK off | 0.987 | 0.999 | 0.935 | 0.997 | 0 | 0 | 340 | 43 | 0.067 | 0.287 (315) | 9 |
| iteration 1, QUASI_ID-ASK on | 0.987 | 0.999 | 0.935 | 0.997 | 0 | 0 | 354 | 43 | 0.067 | 0.287 (315) | 9 |
| **iteration 2 (frozen)** | 0.987 | 0.999 | 0.937 | 0.997 | 0 | 0 | 339 | 43 | 0.050 | 0.279 (306) | 9 |
| iteration 3 (not adopted) | 0.988 | 0.999 | 0.933 | 0.997 | 0 | 0 | 339 | 43 | 0.050 | 0.283 (310) | 9 |

- Replay was shown exact first: a live run and its replay gave byte-identical predictions and decisions, and replay
  refuses any finder call whose prompt hash or model identity differs from the capture.
- Quasi-identifiers do not trigger ASK: both arms tied on the criteria, and ASK held 14 more documents.
- Iteration 3 (date exemptions never override a finder) was not adopted under the criteria written first; it is a
  v0.3 candidate.

## 3. Test results (frozen kit, each arm run once)

| Metric | v0.2 | v0.1 control |
|---|---|---|
| **Released leaks** | **0** | 0 |
| R_any / R_full | **0.990** / 0.979 | 0.982 / 0.970 |
| Sensitive R_any / R_full | 0.954 / 0.897 | 0.955 / 0.897 |
| Any-label / typed precision | 0.761 / 0.649 | 0.769 / 0.651 |
| Untouched / partial spans (all in held cases) | 36 / 43 | 65 / 46 |
| Decisions RELEASE / ASK / BLOCK | 97 / 211 / 63 | 82 / 226 / 63 |
| **Clean-case friction** | **0.200 (19/95)** | 0.358 (34/95) |
| **Over-redaction** | **0.203 (605/2983)** | 0.214 (638/2983) |
| Sensitive look-alikes touched | 32/391 | 33/391 |
| Released leaks in the 42 injection cases | 0 | 0 |
| Empty-finder disagreement (cases) | 6 | 21 |
| FAILED stages | 0 | 0 |

| Label (R_full / R_any) | Gold | v0.2 | v0.1 control |
|---|---|---|---|
| QUASI_ID | 126 | **0.952 / 0.976** | 0.698 / 0.738 |
| HEALTH | 151 | 0.934 / 1.000 | 0.934 / 1.000 |
| DISABILITY | 108 | 0.935 / 0.991 | 0.935 / 0.991 |
| ETHNICITY | 59 | 0.847 / 0.915 | 0.847 / 0.915 |
| SEXUALITY | 57 | 0.912 / 1.000 | 0.912 / 1.000 |
| RELIGION | 143 | 0.958 / 1.000 | 0.958 / 1.000 |
| CRIMINAL | 75 | 0.840 / 0.920 | 0.840 / 0.920 |
| FAMILY_VIOLENCE | 45 | 0.956 / 0.978 | 0.956 / 0.978 |
| UNION | 74 | 0.703 / 0.730 | 0.703 / 0.743 |
| Every other label | 2,848 | 1.000 / 1.000 | 1.000 / 1.000 |

### Pass rule (from `eval/FROZEN-PLAN-v0.2.md`)

- Instrument check: met (control friction 0.358 ≥ 0.10).
- v0.2 conditions:
  - released leaks 0 ≤ 3 and ≤ control (0): met;
  - R_any 0.990 ≥ 0.98 and ≥ control − 0.001: met;
  - sensitive R_any 0.954 ≥ 0.95 and ≥ control − 0.005: met;
  - **clean friction 0.200 > 0.15: not met** (it is below the control's 0.358);
  - over-redaction 0.203 ≤ control's 0.214: met;
  - no failed stage: met.

**Verdict: FAIL.**

### Released leaks

None in either arm. Every unprotected gold span was in a document the gate held. The 36 untouched spans in v0.2 are
finder misses of indirect statements (UNION 20, CRIMINAL 6, ETHNICITY 5, QUASI_ID 3, DISABILITY 1,
FAMILY_VIOLENCE 1); the control had 65 (30 more QUASI_ID, one fewer UNION).

## 4. What the test run says

- **Friction comes from four places, and only one is v0.2's.**
  - Empty-finder disagreement, 6 cases (control 21): tracer findings the allow-list cannot know about. Mailboxes and
    numbers of organisations no profile describes, two public figures named in news items, "24 hours" called a
    date of birth, and a tracer phone span that dropped its opening bracket.
  - Sensitive, 7 cases: the 26B finder tagged look-alikes as statements (five fiction excerpts, "Ramadan" in a
    holiday notice, a workplace-policy FAQ).
  - Secret, 4 cases (BLOCK): the core secret-assignment rule matched vault placeholders (`${VAULT:…}`, `<set …>`)
    in clean configuration files.
  - Injection signal, 2 cases: by design.

  Without the injection holds friction would still be 0.179. Fiction look-alikes, vault placeholders and
  public-figure news never reached a clean dev case, so the dev margin did not see them, the same failure mode as in
  v0.1.
- **The table pass works and costs precision.** It fired in 50 tables: 70 cells match gold quasi-identifiers, 203
  are common job titles the author did not label, and 10 touch site names (two of them the institute's own sites,
  where PII-Tracer's ADDRESS on the same cell blocked the whole-value exemption).
- **The narrowed empty-finder rule** fired on no case with personal information in either arm, so its purpose,
  catching a double finder miss, is still unshown on real misses.
- **One cost on a gold span:** the bare-weekday exemption cancelled a finder's "Tuesday", which was the only touch on
  one UNION statement (held for ASK, so not released). That is why sensitive R_any is 0.0014 below the control. The
  not-adopted iteration 3 would have kept it.

## 5. Timings

Same laptop as v0.1 (8 GB laptop GPU, llama.cpp b11760). v0.2 live: 371 cases in 88 minutes, median 12.6 s and mean
14.2 s per document. E4B 60.8 tok/s, 26B UD-IQ3_XXS 35.6 tok/s. 85 % of finder prompt tokens came from the cache.
The control replay took 4 minutes.

## 6. Kit change after the plan (recorded as required)

Before the runs, the prompt-overlap test (`tests/test_finder_prompt.py`) failed on one 8-word phrase shared by the
rendered prompt and split C: a course name of the fictional institute that is in the profile's `finder_do_not_flag`
list (so in the prompt) and that split C's author used as a hard negative. The test now exempts n-grams taken
verbatim from do-not-flag items; every other shared 8-gram still fails it. No detector, policy, prompt, profile or
scorer change.

## 7. Limitations

1. One run per arm on one machine. Finder output is not bit-for-bit reproducible between live runs; the control's
   replay removes that noise from the comparison, not from the absolute numbers.
2. Split C is synthetic. Its author is independent of the v0.1 generators and the v0.2 code, but it labels
   differently: any-label precision is 0.76 here against 0.94 on dev, largely unlabelled values the gate redacts.
3. 95 clean cases: one case moves friction by 0.011.
4. The empty-finder rule's safety purpose remains untested on real misses.
5. Not cleared for real personal data: `[gate] synthetic_only = true` still applies.
