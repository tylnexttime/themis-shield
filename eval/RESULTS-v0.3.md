# themis-shield v0.3 evaluation results

Split D, run once on the frozen kit `eval-kit-v0.3-frozen` (27/10/2026) under
[`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md). Development work: [`V03-DEV-LOG.md`](V03-DEV-LOG.md).

## 1. Headline

- **Verdict: v0.3 FAILS its pass rule**, clearly and on several conditions at once (released leaks, recall,
  sensitive-statement recall, friction). The instrument check passed (control friction 0.320 ≥ 0.10), so this is a
  FAIL, not INCONCLUSIVE. Arm 1 fails too, so the pre-declared fallback does not apply. Nothing in the kit was changed
  after the result.
- **What v0.3's changes did:** they work where they were aimed. Clean-document friction fell from 0.320 (control) to
  0.304 (deterministic fixes) to **0.248** (with the typed tier), over-redaction fell slightly, and no measure got
  worse.
- **What failed:** detection on text from new authors. Every arm released the same 28 personal spans and reached the
  same recall, because every v0.3 change acts after the finders. **Sensitive-statement recall fell to 0.740**
  (split C: 0.954), and **21 of the 28 released spans are driver licence numbers in table columns.**
- **The two halves differ more than any arm does:** sensitive-statement recall 0.984 on the Qwen-written half and
  0.665 on the Claude-written half, whose author wrote far more, and more indirect, sensitive statements.

## 2. Results (D pooled: 404 cases, 5,841 gold spans, 125 clean, 48 injection cases)

| Measure | v0.2.1 control | Arm 1: v0.3 | **Arm 2: v0.3 + typed tier (candidate)** | Rule | Arm 2 |
|---|---|---|---|---|---|
| Released leaks (spans) | 28 | 28 | **28** | ≤ 3 and ≤ control | FAIL |
| R_any / R_full | 0.937 / 0.911 | 0.937 / 0.911 | **0.937 / 0.911** | R_any ≥ 0.98 and ≥ control − 0.001 | FAIL |
| Sensitive R_any (8 categories, 816 spans) | 0.740 | 0.740 | **0.740** | ≥ 0.95 and ≥ control − 0.005 | FAIL |
| Quasi-identifier R_any | 1.000 | 1.000 | **1.000** | ≥ control − 0.005 | met |
| Clean-case friction | 0.320 (40/125) | 0.304 (38/125) | **0.248 (31/125)** | ≤ 0.15 and ≤ control | FAIL (but ≤ control) |
| Over-redaction (hard negatives touched) | 0.242 (560/2,312) | 0.239 (552) | **0.239 (552)** | ≤ control | met |
| Any-label precision | 0.884 | 0.888 | **0.888** | reported | |
| Released leaks in injection cases | 0 | 0 | **0** | reported | |
| Decisions RELEASE / ASK / BLOCK | 188 / 168 / 48 | 190 / 166 / 48 | **197 / 159 / 48** | reported | |
| Infrastructure stage failures (scored) | 0 | 0 | **0** (after re-run, §4) | 0 | met |

### Per half (every arm; detection is identical across arms)

| | Half Q (Qwen 3.8, 199 cases) | Half C (Claude Sonnet, 205 cases) |
|---|---|---|
| Gold spans / sensitive spans | 1,259 / 191 | 4,582 / 625 |
| R_any / R_full | 0.981 / 0.980 | 0.925 / 0.893 |
| Sensitive R_any | **0.984** | **0.665** |
| Union / criminal / family violence / sexuality R_any | 0.967 / 1.000 / 1.000 / 0.905 | 0.453 / 0.506 / 0.545 / 0.559 |
| Released leaks | 2 | 26 |
| Friction: control / arm 1 / arm 2 | 0.317 / 0.286 / 0.222 | 0.323 / 0.323 / 0.274 |

**The family effect cannot be separated from difficulty.** We wrote D in two halves to measure whether the author's
model family matters. The halves do differ, but the Claude-written half is the *harder* one: it carries 3.6 times the
gold spans and far more indirect sensitive statements (72% indirect by its author's own marking). This split does not
show that Claude-written tests flatter a Claude-built gate; it shows that the difficulty an author chooses matters
more than any difference we could attribute to family.

## 3. What leaked

28 spans in 10 released documents, none in an injection case:

- **21 driver licence numbers** in the identifier columns of three tables (8, 6 and 7 per table). In every row the
  gate covered the name, date of birth, passport and national-ID columns, but not the bare 8-digit licence number:
  no rule matches it, the finders skipped the column, and the table pass only looks for quasi-identifiers.
- one sole trader's ABN in an offer letter, one account number in a CSV row, one member number in prose, two
  quasi-identifiers and two dates.

## 4. Infrastructure re-run (arm 2, half C)

In arm 2's first run the typed-decision stage could not start in two chunks of 40 documents, so those chunks failed
closed (BLOCK). Every typed question that did run succeeded; the same 40 documents run alone passed the stage
cleanly, so the fault depends on the machine's state, not on the documents: on an 8 GB laptop GPU with 31.5 GB of
RAM, re-acquiring the 26B while PII-Tracer is loaded can fail the memory check. Under the plan's crash policy this
is an infrastructure fault: half C was re-run once (still failing one chunk), and that chunk's 40 documents were
re-run on their own; their span predictions were byte-identical to the re-run's, so only their decisions changed.
The first run is kept: there, arm 2's friction was 0.464, entirely from the 70 fail-closed blocks.

Finder-output failures (a finder answer that overflows its budget) are results under the plan: 3 documents were
blocked this way in half C, in every arm.

## 5. Timings

Arm 1 (live finders) took 45 minutes for half Q (about 14 s per document) and 2 h 20 min for half C (about 41 s per
document; longer documents and dense tables). Arms 2 and the control replay the finder answers and took minutes.

## 6. What this changes

- **Column-aware identifier propagation:** when a table column's header names a personal identifier (licence,
  member, account, ID), every cell in it is protected.
- **Indirect sensitive statements remain the main gap.** Asking the 26B about each untouched sentence (the Q-I
  family) raised friction on development data without finding these; the statements sit in sentences a detector had
  already touched. A sentence-level classifier, or a Q-I that also asks about sentences with only a name in them, is
  the next thing to measure, on a fresh split.
- **The typed tier is worth keeping:** it lowered friction by 0.056 with no cost on any safety measure, and the
  families it was not trusted with were rejected on development data before the test.
- **Memory on an 8 GB laptop is a real constraint for a running gate**, not just for evaluation: the scheduler's
  re-acquire check needs a fix so that a long-running gate does not fail closed on memory alone.
