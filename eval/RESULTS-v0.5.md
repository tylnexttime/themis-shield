# themis-shield on split F: evaluation results (eval-kit-v0.5-frozen)

Split F (half O), run once on the frozen kit `eval-kit-v0.5-frozen` (commit `baf5350`, 11/11/2026) under
[`FROZEN-PLAN-v0.5.md`](FROZEN-PLAN-v0.5.md). It tests the candidate (0.3.4 plus `joined-terms`, the DD-17 fix written
from split E's misses) against the previous release (0.3.4: the same code with `joined-terms` off) and the control
(0.3.0's switch list), on text written for this test by a pipeline the gate's designer did not read.

## 1. Headline

- **Verdict: the candidate FAILS its pass rule on four conditions:** released leaks (12, against a limit of 3),
  sensitive-statement recall (0.937, against 0.95), quasi-identifier recall against the control (104 of 112 against
  105, one span short of the 0.005 margin), and clean-document friction (0.2125, 17 of 80, against 0.15). The
  instrument check passed (control: friction 0.250 and 17 released leaks), so this is a FAIL, not INCONCLUSIVE.
  Nothing in the kit was changed after the result.
- **The fix under test worked, on text it had never seen.** The previous-release arm is the candidate's own finder
  answers replayed with `joined-terms` off, so the two differ by that switch alone. With it, every one of the 235
  organisation-issued identifiers is protected whole (INTERNAL_ID R_full 0.553 -> **1.000**), overall R_full rises
  0.945 -> 0.969, held-back unprotected spans fall 230 -> 126, and the one staff number the previous release left
  half in clear (`KPCN-` beside a substituted number) is no longer released. No measure is worse than the previous
  release: the new ratchet condition holds.
- **What leaked is narrow and mostly one table.** 9 of the 12 released spans are the `residence_postcode` column of
  one "anonymised" leavers table (bare four-digit postcodes, gold `ADDRESS`); the other three are two
  quasi-identifying role descriptions and one date, in two tables. No name, contact, identifier, number or sensitive
  statement was released. No injection case released anything (the control released 4 in injection cases).
- **What failed is detection on harder text, and friction.** Split F is free prose written document by document
  (every earlier split was a template generator), over-weighted on sensitive statements (458 spans, 70% indirect,
  60 negative) and on hard look-alikes. Every sensitive statement the candidate missed (29 of 458) sat in a document
  that was held anyway, so none was released; but the rule measures recall, and negative statements ("has never been
  charged") were found least (47 of 60). Clean documents held: 10 for sensitive look-alikes about nobody, 6 code or
  configuration files blocked as secrets.

## 2. Results (split F, half O: 240 cases, 4,414 gold spans, 80 clean, 4,877 hard negatives, 28 injection cases)

| Measure | 0.3.0 (control) | 0.3.4 (previous) | **Candidate** | Rule | Candidate |
|---|---|---|---|---|---|
| Released leaks (spans) | 17 (ADDRESS 9, INTERNAL_ID 5, QUASI_ID 2, OTHER_DATE 1) | 13 (ADDRESS 9, QUASI_ID 2, INTERNAL_ID 1, OTHER_DATE 1) | **12** (ADDRESS 9, QUASI_ID 2, OTHER_DATE 1) | <= 3, <= control, <= previous | **FAIL** (12 > 3) |
| R_any / R_full | 0.9850 / 0.9327 | 0.9889 / 0.9449 | **0.9889 / 0.9687** | R_any >= 0.98, >= control - 0.001, >= previous - 0.001 | met |
| Sensitive R_any (8 categories, 458 spans) | 0.906 (415) | 0.937 (429) | **0.937 (429)** | >= 0.95, >= control - 0.005, >= previous - 0.005 | **FAIL** (7 spans short of 0.95) |
| Quasi-identifier R_any (112 spans) | 0.9375 (105) | 0.9286 (104) | **0.9286 (104)** | >= control - 0.005 | **FAIL** (one span) |
| Clean-case friction | 0.250 (20/80) | 0.2125 (17/80) | **0.2125 (17/80)** | <= 0.15 and <= control | **FAIL** (5 documents over) |
| Over-redaction (hard negatives touched) | 1,153 / 4,877 (0.236) | 1,118 | **1,118** (0.229) | <= control | met |
| Failed stages from infrastructure | 0 | 0 | **0** | 0 | met |
| Finder stage failures (results) | 0 | 0 | 0 | reported | |
| Decisions (RELEASE / ASK / BLOCK) | 79 / 120 / 41 | 82 / 119 / 39 | 82 / 119 / 39 | | |
| Typed precision (any label) | 0.721 | 0.713 | 0.713 | reported | |
| INTERNAL_ID R_full (235 spans) | 0.426 | 0.553 | **1.000** | reported | |

**Failed stages from infrastructure** were established from the stage error codes in every arm's decisions
(`stage-failed:not-enough-memory`, `target-unavailable`, `transport`, `os`, `out-of-memory`): no arm had a single
stage that was not `ok`, so the count is 0 without having to tell an infrastructure fault from a truncated answer
(the two share a code; DEV-DEBT DD-21).

**The quasi-identifier condition** fails by one span between two live runs: the control found two role descriptions
the candidate missed, the candidate found one the control missed. The candidate and the control use different finder
prompts (two of the control's switches change them), so this is finder variation between arms, not a change the
candidate made to quasi-identifier handling; the rule counts it all the same.

**Sensitive statements** (reported, not gating), candidate / control: HEALTH 0.916 / 0.916 (95 spans), DISABILITY
0.980 / 0.980 (49), ETHNICITY 0.942 / 0.942 (52), SEXUALITY 0.891 / 0.891 (46), RELIGION 1.000 / 0.980 (49), CRIMINAL
0.905 / 0.857 (63), FAMILY_VIOLENCE 0.962 / 0.904 (52), UNION 0.923 / 0.788 (52). By kind: direct 74/78 (0.949),
indirect 308/320 (0.963), negative 47/60 (0.783); control 0.923, 0.922, 0.800.

**Why the clean documents held** (candidate; control in brackets): a sensitive finding about nobody 10 (11), secret
6 (8; all six candidate holds are code or configuration files), empty-finder disagreement 1 (1), unmatched finder
values 1 (1).

**Over-redaction by named kind** (candidate / control): meeting dates 51/77 / 52/77, letter dates 29/33 / 28/33,
case numbers 87/130 / 87/130. As on split E, most of these look-alikes are touched in both arms.

**Held back unprotected** (spans the gate did not protect, in documents it held): candidate 126 (ASK sensitive 95,
BLOCK secret 27, ASK unmatched values 4), previous 230, control 280.

## 3. The split

- **How it was made.** A deterministic planner (seed 20261111) wrote 240 case specifications: genre, length band,
  labels and counts, sensitive categories and whether each statement is direct, indirect or negative, hard-negative
  kinds, injection position, and pre-generated safe values for every checksummed or range-restricted identifier,
  phone and email. Each document was then written whole by `claude-opus-5-5` at effort high (adaptive thinking)
  through the Message Batches API, with gold marked inline and turned into offsets by code. The profile is one
  invented regional public health and community-care network (a new sector for these splits), with five
  organisation-issued identifier formats carrying its short form; 120 cases are about it and 120 about twelve other
  invented workplaces.
- **Checks.** The shared validator (split E's, protocol constants only changed; mutation test 26/26), every quota
  met, the origin denylist (0 hits), and a pointer check by `claude-sonnet-5-5` (one case flagged and repaired). 30
  cases failed a rule on the first pass and were repaired in one batch; none was dropped and no top-up was needed.
- **Labels.** Blind re-labelling by `claude-sonnet-5-5` at effort xhigh: exact-offset recall / precision of gold
  against the labeller 0.975 / 0.967 before review, 0.984 / 0.988 after the author decided 168 disagreements (97
  fixed, 71 kept). The after-review figures are partly circular; the before-review ones are the cleaner measure.
- **Limitation, stated in the plan before the split existed:** author and labeller are one vendor's models, so label
  agreement may overstate label quality. The author is independent of the gate (Gemma, a Qwen-based PII-Tracer and
  rules), which is what the verdict rests on.
- **Blindness.** The gate's designer saw counts, hashes and costs until all three arms had run, then only each arm's
  score.json numbers before scoring. One deviation, recorded at sealing: the gate-side orchestrator placed the run
  script in the authoring workspace while authoring was running, and the authoring orchestrator read its header
  comment (kit tag and arm names).
- **Cost** of authoring: US$22.50 on the Anthropic API (every batch at the Batch API's half price).
- **Files:** [`cases/test-f-o.jsonl`](cases/test-f-o.jsonl) (sha256 `c8ef6854…06afd`, identical to the sealed file),
  the pipeline, inputs, validator, profile, manifest, provenance and agreement under [`generator_f/`](generator_f).

## 4. Running it

The candidate arm's first start (09:33 UTC) stopped at the detector canary before any document: the 26B finder was
refused for memory (the laptop was busy with other work). Under the crash policy this is an infrastructure fault:
nothing was processed or scored, the runner was made to wait for 13 GB of available memory and no competing jobs, and
the arm resumed at 10:55, canary passed. Candidate 105 minutes and control 101 minutes (live, an 8 GB laptop GPU, both
Gemma finders; median 20 s per document, mean 26 s); the previous-release replay took 13 minutes and refused no call.

## 5. What this changes

1. **`joined-terms` (DD-17) is confirmed on unseen text:** the arm that differs by that switch alone protects every
   organisation-issued identifier whole and is no worse on any measure. It stays on by default.
2. **The candidate is not a release that passes a frozen test.** It is better than 0.3.0 on released leaks (12 vs
   17), recall, sensitive recall (0.937 vs 0.906), friction and over-redaction, and worse on quasi-identifiers by one
   span. It fails the absolute limits on free-prose text by a margin larger than split E's.
3. **Next detection work, now that split F is development data:** location and quasi-identifier columns in
   "anonymised" tables (a `residence_postcode` column was released whole: the same column-aware idea as 0.3.2's
   identifier columns); negative sensitive statements; and friction on clean documents (sensitive look-alikes about
   nobody, and configuration files read as secrets, which the review fix to `secret-references` made after the kit
   tag may raise; it is measured on split F as development data next).
4. **The next exam** needs a new split under its own plan, with an author and a labeller from different families,
   and the same thresholds.

The candidate remains a **preview**, like every release before it.