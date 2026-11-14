# themis-shield on split H: evaluation results (eval-kit-v0.7-frozen)

Split H (halves A and B, pooled), run once on the frozen kit `eval-kit-v0.7-frozen` (commit `6d4d0c2`, 14/11/2026)
under [`FROZEN-PLAN-v0.7.md`](FROZEN-PLAN-v0.7.md), its amendment before sealing (`9d30c7f`), its sealing (`c8b02b1`)
and the commit that named the kit before any arm ran (`6d4d0c2`). It tests **release 0.5.0** (tag `v0.5.0`, every
default switch, live) against the previous release (**0.4.0 as released**: the `v0.4.0` tag's own code, by replay of
the candidate's finder answers) and the control (0.3.0's 15 switches on the kit's code, live), on text written and
labelled for this test by **Mistral, DeepSeek, Moonshot and OpenAI models** through a pipeline the gate's designer did
not read.

## 1. Headline

- **Verdict: 0.5.0 FAILS its pass rule on three conditions:** sensitive-statement recall (0.913, 316 of 346, against
  0.95: 13 spans short), clean-document friction (0.278, 15 of 54 clean documents held, against 0.15: 7 documents
  over), and over-redaction against the control (621 hard negatives touched against 594: 27 over). The instrument
  check passed (control friction 0.296), so this is a FAIL, not INCONCLUSIVE. Nothing in the kit was changed after the
  result. These are the same three conditions 0.4.0 failed on split G.
- **Nothing was released.** Released leaks are **0** in all three arms on both halves: every one of the 47 gold spans
  the candidate missed (30 sensitive statements, 17 identifiers, names, contacts and dates) sat in a document the gate
  held (ASK or BLOCK). No injection case released anything (19 injection cases). R_any 0.9818 meets its limit, every
  ratchet condition against 0.4.0 holds, and the candidate is ahead of the control on R_any (0.9764), R_full
  (0.951 against 0.915) and sensitive recall (0.873).
- **On split H, 0.5.0 detects exactly what 0.4.0 detects, and holds two more clean documents.** The previous-release
  arm replayed the candidate's finder answers through 0.4.0's own code (no call refused), so the difference between
  the two arms is the code since 0.4.0 alone. Gold-span measures are identical (R_any, R_full, sensitive 316/346,
  quasi-identifiers, over-redaction 621, released 0): the `indirect-statements` switch and DD-26's change found
  nothing on split H that 0.4.0's code missed. Friction rose from 13 to 15 clean documents: both extra holds are the
  wider injection signal (HL-04's "already de-identified" claims), a clean FAQ that says it "contains no personal
  information" and a clean log with "no personal details in" it. The development replay had predicted this cost
  (split G 20 -> 21 clean documents).
- **What failed is what failed on splits F and G, on text from two more families.** Friction: fiction about invented
  people held for sensitive statements (4), configuration files blocked as secrets (4), injection signals (5, two of
  them the new pattern), empty-finder disagreements (3). Sensitive recall: union 0.761, criminal 0.857, religion
  0.884; negative statements 35/41. Over-redaction: 27 look-alikes more than the control, all of them already in
  0.4.0 (the replay touches the same 621): role titles +9, sites +7, reference numbers +4, programme titles +3.
- **The halves disagree on sensitive recall.** Half A (Mistral's airline text) is at 0.888 (191/215), half B
  (DeepSeek's school text) at 0.954 (125/131), which on its own would pass the absolute limit. Both halves fail
  friction (0.222 and 0.333) and over-redaction against the control (+9 and +18). The halves differ in author,
  labeller, organisation and sector at once, so this is not a family effect alone.

## 2. Results (split H pooled: 158 cases, 2,588 gold spans, 54 clean, 2,570 hard negatives, 19 injection cases)

| Measure | 0.3.0 (control) | 0.4.0 (previous) | **0.5.0 (candidate)** | Rule | Candidate |
|---|---|---|---|---|---|
| Released leaks (spans) | 0 | 0 | **0** | <= 3, <= control, <= previous | met |
| R_any / R_full | 0.9764 / 0.9150 | 0.9818 / 0.9513 | **0.9818 / 0.9513** | R_any >= 0.98, >= control - 0.001, >= previous - 0.001 | met |
| Sensitive R_any (8 categories, 346 spans) | 0.873 (302) | 0.913 (316) | **0.913 (316)** | >= 0.95, >= control - 0.005, >= previous - 0.005 | **FAIL** (13 spans short of 0.95) |
| Quasi-identifier R_any (78 spans) | 0.9615 | 0.9615 | **0.9615** | >= control - 0.005 | met |
| Clean-case friction | 0.296 (16/54) | 0.241 (13/54) | **0.278 (15/54)** | <= 0.15 and <= control | **FAIL** (7 documents over 0.15) |
| Over-redaction (hard negatives touched) | 594 / 2,570 | 621 | **621** (0.242) | <= control | **FAIL** (27 over) |
| Failed stages from infrastructure | 0 | 0 | **0** | 0 | met |
| Decisions (RELEASE / ASK / BLOCK) | 47 / 91 / 20 | 49 / 91 / 18 | 47 / 93 / 18 | | |
| Typed precision (any label) | 0.725 | 0.687 | 0.687 | reported | |
| Held back unprotected (spans) | 220 | 126 | 126 | reported | |

**Failed stages from infrastructure:** no stage in any arm on either half had a status other than `ok` (counted from
every arm's decisions), so the count is 0.

### Per half (reported, not a second verdict)

| Measure | A control | A previous | **A candidate** | B control | B previous | **B candidate** |
|---|---|---|---|---|---|---|
| Cases / gold / clean / hard negatives | 76 / 1,514 / 27 / 1,468 | | | 82 / 1,074 / 27 / 1,102 | | |
| Released leaks | 0 | 0 | **0** | 0 | 0 | **0** |
| R_any / R_full | 0.9742 / 0.9036 | 0.9815 / 0.9465 | **0.9815 / 0.9465** | 0.9795 / 0.9311 | 0.9823 / 0.9581 | **0.9823 / 0.9581** |
| Sensitive R_any | 0.837 (180/215) | 0.888 (191) | **0.888 (191)** | 0.931 (122/131) | 0.954 (125) | **0.954 (125)** |
| Quasi-identifier R_any | 0.977 | 0.977 | **0.977** | 0.941 | 0.941 | **0.941** |
| Clean-case friction | 0.222 (6/27) | 0.222 (6) | **0.222 (6)** | 0.370 (10/27) | 0.259 (7) | **0.333 (9)** |
| Over-redaction | 338 | 347 | **347** | 256 | 274 | **274** |
| Conditions failed (candidate) | | | sensitive, friction, over-redaction | | | friction, over-redaction |

Half A: author `mistralai/mistral-large-2512` (Mistral), labeller `kimi-k3.1-pro` (Moonshot), a regional airline and
airport operator. Half B: author `deepseek-v4.1-flash` (DeepSeek), labeller `openai/gpt-oss-120b` (OpenAI), an
independent school group.

### Reported measures

**Released leaks by label and by format:** none in any arm, so there is nothing to break down. Spans missed (not
protected) by the candidate, all in held documents: UNION 11, RELIGION 5, EMAIL 5, CRIMINAL 4, HEALTH 4, PERSON 3,
PHONE 3, QUASI_ID 3, FAMILY_VIOLENCE 2, ETHNICITY 2, DISABILITY 1, SEXUALITY 1, INTERNAL_ID 1, OTHER_DATE 1,
ADDRESS 1 (47; the previous release misses the same 47). Control: 61, mostly sensitive statements (44).

**Sensitive statements** (candidate / control, pooled): DISABILITY 0.980 / 0.939 (49 spans), SEXUALITY 0.966 / 0.897
(29), HEALTH 0.958 / 0.927 (96), ETHNICITY 0.929 / 0.929 (28), FAMILY_VIOLENCE 0.926 / 0.889 (27), RELIGION 0.884 /
0.907 (43), CRIMINAL 0.857 / 0.714 (28), UNION 0.761 / 0.696 (46). By kind: direct 69/75 (0.920), indirect 212/230
(0.922), negative 35/41 (0.854); control 0.920, 0.878, 0.756. Half A carries most misses: 24 of the 30 (UNION 8 of
28, CRIMINAL 4 of 13, RELIGION 5 of 26).

**Why the clean documents held** (candidate; control in brackets): a sensitive finding about nobody 6 (6; 4 of them in
fiction, 2 in FAQs), secret 4 (5; all configuration files, 2 of them also with an injection signal), injection signal
5 (5: the control runs the kit's code, so it has the wider signal too; 0.4.0: 3), empty-finder disagreement 3 (3). A
document can be held for more than one reason. By genre: configuration 5, fiction 4, FAQ 3, report
2, log 1.

**Over-redaction by named kind** (candidate; control): meeting dates 56/63 (55), letter dates 17/18 (17), case
numbers 40/46 (39); also ticket numbers 79/84 (79), reference numbers 76/97 (72), role mailboxes 58/184 (58), sites
40/428 (33), role titles 22/370 (13).

**Held back unprotected** (spans the gate did not protect, in documents it held): candidate 126 (ASK sensitive 100,
BLOCK secret 14, ASK unmatched finder values 12), previous 126, control 220.

**Injection cases:** 19 (A 9, B 10), 0 released spans in every arm. **Documents blocked by finder failures:** 0.
**Seconds per document** (candidate, live): median 27.7 / mean 41.4 on A, 18.0 / 25.8 on B.

**Label agreement** (as recorded at sealing, `generator_h/AGREEMENT-A.md`, `AGREEMENT-B.md`; exact-offset recall /
precision of the blind labeller against the author's gold): half A 0.795 / 0.639 before review, 0.880 / 0.863 after;
half B 0.736 / 0.631 before, 0.794 / 0.739 after (overlap after review A 0.904 / 0.887, B 0.830 / 0.771). Half B's
agreement is the lowest of any split so far (split G 0.86-0.89 exact recall before review), so some of half B's misses
and holds may be gold disagreements rather than gate errors (not measured).

**Half A's sealed shortfall:** half A was sealed under the amendment with one coverage row short, gold spans PASSPORT 6
of the required 7. Pooled, split H has 16 PASSPORT spans; every arm protects all 16 (R_any 1.000), so the missing span
would not have changed any measure's direction.

**Tokens and cost per authoring model** (cumulative for split H, `generator_h/usage-summary.md`):
`mistral-large-2512` 1,413,513 tokens (US$0.7472, OpenRouter), `deepseek-v4.1-flash` 924,169 (free quota),
`kimi-k3.1-pro` 285,248 (free quota), `gpt-oss-120b` 544,829 (US$0.0655, OpenRouter); OpenRouter US$0.8127 in all.

## 3. The split

- **How it was made.** As for splits F and G (per-document free prose, a deterministic planner per half with
  pre-generated safe values, gold marked inline and turned into offsets by code), with each half's author, labeller
  and pointer check from different families: half A by Mistral about an invented regional airline and airport
  operator, half B by DeepSeek about an invented independent school group; sectors not used before. A Claude model
  (Sonnet 5.5) built and ran the pipeline and reported counts only.
- **Amendment before sealing.** Neither half converged under the original rule (one repair round and one top-up): the
  top-up documents had no repair round. Alder allowed one more targeted top-up per half, with the repair round, and
  sealing with at most two short coverage rows of at most two units each, before sealing and without reading a case
  (`9d30c7f`). Half B then passed every row; half A was sealed short by one PASSPORT span (`c8b02b1`).
- **Checks.** The shared validator (split G's, protocol constants only; mutation test 26/26): validator failures 0 in
  both halves; half A short only in the PASSPORT row, half B every row. Origin denylist 0 over every field of every
  case. Pointer check by `openai/gpt-oss-120b` on both halves.
- **Limitations, stated in advance.** Agreement measures consistency between two families, not truth. One model
  (`gpt-oss-120b`) labels half B and runs the pointer check on both halves. The authors' and labellers' prompts were
  written by a Claude model. Half B's profile needed a code step before conversion (three lists of one-key objects
  unwrapped to strings; recorded in the plan).
- **Data handling.** OpenRouter calls forced zero-data-retention routing; Alibaba Cloud Model Studio calls: no training
  per the provider's terms, retention not stated. All content is invented.
- **Files:** [`cases/test-h-a.jsonl`](cases/test-h-a.jsonl) and [`cases/test-h-b.jsonl`](cases/test-h-b.jsonl), identical to the sealed files: sha256
  `3f29c5735b4a5113396a961cde10d110ac6eebda21af4c9f960b7cdbca97fe0f` (A) and
  `5544671eb45323179b45588461b6b96e266bb668a62cd6b1c33c504991c20069` (B); no denylist replacement was needed, so the
  sealed and published hashes are the same. The pipeline, inputs, validator, profiles, plans, manifest, provenance,
  agreement and usage are under [`generator_h/`](generator_h).

## 4. Running it

One heavy run at a time under the lock, each half's sha256 checked against the tagged plan first; the candidate and
the control from a detached worktree of the kit tag, the previous release from a detached worktree of `v0.4.0` (its
own `--frozen-tag v0.4.0` check). Available memory was 15 GB at the start, above the 13 GB guard, so no arm waited.
**Why 0.4.0's own code:** the plan's arm 2 names it, and the tagged code with 0.4.0's switch list would not be 0.4.0:
the wider injection signal (HL-04's de-identified claims) is not behind a switch. The replay was possible because
0.5.0's finder prompts are 0.4.0's: every call was served from the candidate's capture (188 on A, 196 on B) and none
was refused, so the previous-release arm did not need its live fallback. Durations: half A candidate 54 minutes,
previous 5 (replay), control 88; half B candidate 36, previous 3, control 29 (08:32-12:09 UTC). No arm faulted, was
resumed or was re-run.

## 5. What this changes

1. **0.5.0 is not a release that passes a frozen test.** It releases nothing on split H and is ahead of the control
   on recall and sensitive recall, but it fails the same three conditions as 0.4.0 on split G, by more: sensitive
   recall 0.913 (split G 0.939), friction 0.278 (0.308), over-redaction 27 above the control (2). It stays a
   **preview**.
2. **The work since 0.4.0 did not move detection on fresh text.** Split G's replay had 0.5.0's default build at
   sensitive recall 0.9665 against 0.4.0's 0.939; on split H the two are identical (0.913). The gains written from
   split G were split G's. What the new code did change on split H is friction: two more clean documents held by the
   wider injection signal, the cost the release notes stated.
3. **Sensitive recall is the largest gap again, and it is a finder problem.** Union, criminal and religious statements
   in half A's text are missed by both finders and by every rule pass; every miss was held, not released, but the rule
   measures recall. Friction (fiction held for statements about invented characters, configuration files blocked as
   secrets) and the over-redaction of role titles, sites and references are unchanged gaps.
4. **Split H is now development data.** The next exam needs a new split under its own plan, with the same thresholds.

**Development guards and split H.** The suite's development guards read every case file under `eval/cases`. With
split H published, seven of them fail on its halves, as development guards do when a new set arrives: the location,
negative-statement and unique-role passes touch hard negatives in it (4, 2 and 1 spans), the wider injection signal
newly flags 4 of its documents (2 clean, 1 injection case, 1 with personal information), the indirect-statement guard
has no floor for it, and two guards of opt-in switches (`policy-statements`, `empty-finder-substitute`) have no entry
for its cases. Those seven guards now leave split H out (`test-h-*`) so that they keep recording the sets they were
written on; split H joins them, with its own figures, when development work starts from it. No gate code changed.
