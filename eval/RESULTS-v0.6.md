# themis-shield on split G: evaluation results (eval-kit-v0.6-frozen)

Split G (halves R and T, pooled), run once on the frozen kit `eval-kit-v0.6-frozen` (commit `b3ba032`, 12/11/2026)
under [`FROZEN-PLAN-v0.6.md`](FROZEN-PLAN-v0.6.md) and its amendment before sealing (`17eef3e`). It tests **0.4.0 as
released** (every default switch, live) against the previous release (0.3.4's 23 switches, by replay of the
candidate's finder answers) and the control (0.3.0's 15 switches, live), on text written and labelled for this test by
**non-Claude model families** through a pipeline the gate's designer did not read.

## 1. Headline

- **Verdict: 0.4.0 FAILS its pass rule on three conditions:** sensitive-statement recall (0.939, 308 of 328, against
  0.95: 4 spans short), clean-document friction (0.308, 20 of 65 clean documents held, against 0.15: 11 documents
  over), and over-redaction against the control (615 hard negatives touched against 613: 2 over). The instrument check
  passed (control friction 0.338), so this is a FAIL, not INCONCLUSIVE. Nothing in the kit was changed after the
  result.
- **Nothing was released.** Released leaks are **0** in all three arms on both halves: every one of the 23 gold spans
  the candidate missed (20 sensitive statements, 3 dates) sat in a document the gate held (ASK or BLOCK). No injection
  case released anything (24 injection cases). R_any 0.9915 and every ratchet condition against the previous release
  hold; quasi-identifiers 1.000 (control 0.989).
- **The seven switches added since 0.3.4 helped a little and cost a little.** Against the replayed previous release
  (same finder answers, so the difference is the seven switches): sensitive recall 306 -> 308 of 328 (two negative
  CRIMINAL statements), R_full 0.937 -> 0.954, held-back unprotected spans 172 -> 125; but one more clean document held
  (a statement-length CRIMINAL finding in a clean FAQ that neither other arm produced) and one more hard negative
  touched (a role title).
- **What failed is the same as on split F, on text from other families:** friction on clean documents (8 fiction
  pieces held for sensitive statements about invented characters, 6 configuration files blocked as secrets, 3
  injection signals) and sensitive recall (UNION 0.853, FAMILY_VIOLENCE 0.913, CRIMINAL 0.915; negative statements
  39/43). Over-redaction misses its condition by two look-alikes (against the control: role title, site and committee
  +1 each, policy statement -1); one of the two, a role title, comes from the seven switches (the replay touches 614),
  the rest is live finder variation between the candidate and the control, which use different finder prompts.
- **The halves agree.** Each half on its own fails the same two absolute conditions (sensitive 0.940 and 0.938;
  friction 0.344 and 0.273); half R also misses over-redaction against the control by 2, half T ties it. Friction
  differed between the halves more than recall did; the halves differ in author family (Moonshot, Zhipu), labeller
  and organisation at once, so this is not a family effect alone.

## 2. Results (split G pooled: 180 cases, 2,716 gold spans, 65 clean, 2,803 hard negatives, 24 injection cases)

| Measure | 0.3.0 (control) | 0.3.4 (previous) | **0.4.0 (candidate)** | Rule | Candidate |
|---|---|---|---|---|---|
| Released leaks (spans) | 0 | 0 | **0** | <= 3, <= control, <= previous | met |
| R_any / R_full | 0.9893 / 0.9300 | 0.9908 / 0.9367 | **0.9915 / 0.9540** | R_any >= 0.98, >= control - 0.001, >= previous - 0.001 | met |
| Sensitive R_any (8 categories, 328 spans) | 0.930 (305) | 0.933 (306) | **0.939 (308)** | >= 0.95, >= control - 0.005, >= previous - 0.005 | **FAIL** (4 spans short of 0.95) |
| Quasi-identifier R_any | 0.9885 | 1.000 | **1.000** | >= control - 0.005 | met |
| Clean-case friction | 0.338 (22/65) | 0.292 (19/65) | **0.308 (20/65)** | <= 0.15 and <= control | **FAIL** (11 documents over 0.15) |
| Over-redaction (hard negatives touched) | 613 / 2,803 | 614 | **615** (0.219) | <= control | **FAIL** (2 over) |
| Failed stages from infrastructure | 0 | 0 | **0** | 0 | met |
| Decisions (RELEASE / ASK / BLOCK) | 55 / 100 / 25 | 57 / 100 / 23 | 56 / 101 / 23 | | |
| Typed precision (any label) | 0.774 | 0.769 | 0.767 | reported | |
| Held back unprotected (spans) | 190 | 172 | 125 | reported | |

**Failed stages from infrastructure:** no stage in any arm on either half had a status other than `ok` (counted from
every arm's decisions), so the count is 0.

### Per half (reported, not a second verdict)

| Measure | R control | R previous | **R candidate** | T control | T previous | **T candidate** |
|---|---|---|---|---|---|---|
| Cases / gold / clean / hard negatives | 92 / 1,470 / 32 / 1,789 | | | 88 / 1,246 / 33 / 1,014 | | |
| Released leaks | 0 | 0 | **0** | 0 | 0 | **0** |
| R_any / R_full | 0.9918 / 0.9299 | 0.9925 / 0.9306 | **0.9932 / 0.9537** | 0.9864 / 0.9302 | 0.9888 / 0.9438 | **0.9896 / 0.9543** |
| Sensitive R_any | 0.940 (156/166) | 0.934 (155) | **0.940 (156)** | 0.920 (149/162) | 0.932 (151) | **0.938 (152)** |
| Quasi-identifier R_any | 1.000 | 1.000 | **1.000** | 0.978 | 1.000 | **1.000** |
| Clean-case friction | 0.344 (11/32) | 0.344 (11) | **0.344 (11)** | 0.333 (11/33) | 0.242 (8) | **0.273 (9)** |
| Over-redaction | 343 | 345 | **345** | 270 | 269 | **270** |
| Conditions failed (candidate) | | | sensitive, friction, over-redaction | | | sensitive, friction |

Half R: author `kimi-k3` (Moonshot), labeller `deepseek-v4-pro-0813`. Half T: author `glm-5.3` (Zhipu), labeller
`deepseek-v4.1-flash-1017`.

### Reported measures

**Released leaks by label and by format:** none in any arm, so there is nothing to break down. Spans missed (not
protected) by the candidate, all in held documents: UNION 5, CRIMINAL 4, FAMILY_VIOLENCE 4, RELIGION 3, OTHER_DATE 3,
ETHNICITY 2, SEXUALITY 2 (control: also PERSON 2 and QUASI_ID 1).

**Sensitive statements** (candidate / control, pooled): HEALTH 1.000 / 1.000 (54 spans), DISABILITY 1.000 / 1.000
(37), ETHNICITY 0.944 / 0.944 (36), SEXUALITY 0.929 / 0.929 (28), RELIGION 0.935 / 0.957 (46), CRIMINAL 0.915 / 0.872
(47), FAMILY_VIOLENCE 0.913 / 0.913 (46), UNION 0.853 / 0.794 (34). By kind: direct 43/43 (1.000), indirect 226/242
(0.934), negative 39/43 (0.907); control 1.000, 0.930, 0.860.

**Why the clean documents held** (candidate; control in brackets): a sensitive finding about nobody 12 (12; 8 of them
in fiction), secret 6 (8; all configuration files), injection signal 3 (3), empty-finder disagreement 1 (1). By genre:
fiction 8, configuration 6, FAQ 3, log 2, form 1.

**Over-redaction by named kind** (candidate; the control is the same on these): meeting dates 45/47, letter dates
36/37, case numbers 54/110; also ticket numbers 95/128, role mailboxes 55/186, reference numbers 47/84.

**Held back unprotected** (spans the gate did not protect, in documents it held): candidate 125 (ASK sensitive 104,
BLOCK secret 19, ASK unmatched finder values 2), previous 172, control 190.

**Injection cases:** 24 (R 13, T 11), 0 released spans in every arm. **Documents blocked by finder failures:** 0.
**Seconds per document** (candidate, live): median 19.6 / mean 26.7 on R, 16.6 / 25.1 on T. **Label agreement** per
half is in the plan's amendment: exact recall / precision R 0.864 / 0.886 before review, T 0.892 / 0.689; split F's
Claude-on-Claude labels agreed at 0.975 / 0.967.

## 3. The split

- **How it was made.** As for split F (per-document free prose, a deterministic planner per half with pre-generated
  safe values, gold marked inline and turned into offsets by code), but written and labelled through Alibaba Cloud
  Model Studio (Singapore, OpenAI-compatible chat completions) by non-Claude families, one job per model: half R by
  `kimi-k3` (Moonshot) about an invented regional port and freight authority, labelled blind by `deepseek-v4-pro-0813`;
  half T by `glm-5.3` (Zhipu) about an invented municipal council, labelled blind by `deepseek-v4.1-flash-1017`.
  A Claude model (Sonnet 5.5) built and ran the pipeline and reported counts only.
- **Half S dropped.** A third half (`qwen3.8-max-0902` about a credit union, labelled by `kimi-k3.1-pro`) did not
  converge (85 cases; six sensitive categories short) and was dropped unsealed; it is not published as a test half.
- **Amendment before sealing.** Half T ended at 88 cases, two under the plan's size floor of 90, with every content
  quota met. Alder accepted it before any arm ran and without reading a case, and one workplace metadata label that
  hit the origin denylist was renamed by script (texts, spans and offsets byte-identical, proven by hash). Both are
  recorded in the plan (`17eef3e`), with half R's pointer-check deviation (a second prompt after the first flagged 68
  of 98 documents).
- **Checks.** The shared validator (split F's, protocol constants only; mutation test 26/26): half R clean with every
  quota met; half T clean apart from the accepted size row. Origin denylist 0. Pointer check by each half's labeller.
- **Limitations, stated in advance.** Agreement measures consistency between two families, not truth, and it is lower
  than split F's: some misses and some friction may be gold disagreements rather than gate errors (not measured). Both
  labellers are DeepSeek models. The authors' and labellers' prompts were written by a Claude model.
- **Data handling.** No training per the provider's published terms; retention not stated (unlike splits D and E,
  zero data retention). All content is invented.
- **Files:** [`cases/test-g-r.jsonl`](cases/test-g-r.jsonl) (sha256 `5c4dce5c…b01d26d`) and
  [`cases/test-g-t.jsonl`](cases/test-g-t.jsonl) (sha256 `02fb9251…bb69bac`), identical to the sealed files; the
  pipeline, inputs, validator, profiles, manifest, provenance and agreement under [`generator_g/`](generator_g).

## 4. Running it

One heavy run at a time under the lock, from a detached worktree of the tag, each half's sha256 checked against the
tagged plan first. The runner waited 03:26-05:16 UTC for 13 GB of available memory on the shared laptop, then ran
without a fault: half R candidate 43 minutes, previous 4 minutes (replay; no call refused, so no live fallback),
control 38 minutes; half T candidate 38, previous 4, control 40. No arm was resumed or re-run.

## 5. What this changes

1. **0.4.0 is not a release that passes a frozen test.** It leaks nothing on split G and is ahead of 0.3.0 and 0.3.4
   on recall, sensitive recall and quasi-identifiers, but it fails the absolute sensitive-recall and friction limits
   on a second, independently written split, and misses over-redaction against the control by two look-alikes. It
   stays a **preview**.
2. **Friction is now the largest gap** (0.308 against 0.15; split F 0.2125): clean fiction about invented people is
   held for sensitive statements, and configuration files are blocked as secrets. That is the next detection work, on
   development data, together with the over-redaction of look-alike references (ticket and case numbers).
3. **Sensitive recall is stuck near 0.94** on both free-prose splits (F 0.937, G 0.939), weakest on union, family
   violence and criminal statements; the misses are held, not released, but the rule measures recall.
4. **Split G is now development data.** The next exam needs a new split under its own plan, with the same thresholds.

## Addendum (13/11/2026): split G against its adjudicated gold. The frozen verdict above stands.

After publication, split G (now development data) had its disputed gold adjudicated against `eval/taxonomy.md` in two
batches (method, verdicts and changes in [`generator_g/adjudication/`](generator_g/adjudication/README.md)). **First
batch** (43 items): every span 0.4.0 missed, every clean case it held, and the blind labeller's spans touching them;
38 are gate errors, 3 grey, and 2 gold spans were wrong and are removed. **Second batch** (DEV-DEBT DD-26: the 5
places where three 0.4.0 switches broke their guards): 2 are the switches' errors, and 3 are spans the gold omitted,
now added (two negative statements about named people, one "the only ..." description). Every span of the second
batch was found by a 0.4.0 switch, so that batch can only raise the arms that run those switches; the first batch's
figure is given separately for that reason. The corrections are in
[`cases/test-g-r.adjudicated.jsonl`](cases/test-g-r.adjudicated.jsonl) and
[`cases/test-g-t.adjudicated.jsonl`](cases/test-g-t.adjudicated.jsonl); the sealed files are unchanged. The arms'
existing predictions were re-scored; no gate was run:

| Split G pooled | 0.3.0 control | 0.3.4 previous | 0.4.0 candidate |
|---|---|---|---|
| Sensitive R_any, sealed gold (328 spans) | 0.930 | 0.933 | 0.939 (308) |
| Sensitive R_any, first batch only (326 spans) | 0.936 | 0.939 | 0.945 (308) |
| Sensitive R_any, adjudicated gold (328 spans) | 0.936 | 0.939 | **0.945 (310)** |
| R_any, sealed / adjudicated | 0.9893 / 0.9901 | 0.9908 / 0.9915 | 0.9915 / **0.9923** |
| R_full, sealed / adjudicated | 0.9300 / 0.9301 | 0.9367 / 0.9367 | 0.9540 / **0.9547** |
| QUASI_ID R_any, sealed / adjudicated | 0.9885 / 0.9886 | 1.000 / 1.000 | 1.000 / 1.000 |
| Released leaks, friction, over-redaction | unchanged | unchanged | unchanged (0, 0.308, 615) |

Against the adjudicated gold the candidate would still fail the same three conditions: sensitive recall 0.945 (2
spans short of 0.95 instead of 4), friction 0.308 and over-redaction 615 against the control's 613. The misses and
the clean holds are, with two exceptions, the gate's errors, not the labels'.
