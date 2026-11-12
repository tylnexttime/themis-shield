# Split G: adjudication of the disputed gold (13/11/2026)

Split G's gold was written by non-Claude authors (`kimi-k3`, `glm-5.3`) and labelled blind by DeepSeek models, with
cross-family agreement of only 0.86-0.92 exact ([plan amendment](../../FROZEN-PLAN-v0.6.md)). After the frozen result
([RESULTS-v0.6.md](../../RESULTS-v0.6.md)) split G became development data, so its disputed gold was adjudicated. The
sealed files `eval/cases/test-g-r.jsonl` and `test-g-t.jsonl` are **not edited**; the corrections are in separate files
beside them, `test-g-r.adjudicated.jsonl` (no change: byte-identical to the sealed file) and
`test-g-t.adjudicated.jsonl` (two spans removed). **The frozen verdict stands as it was.**

## Scope (`build_items.py`)

Every gold span that 0.4.0's candidate arm missed (no prediction overlapped it: 23), every clean case it held (20),
and every blind-labeller span touching either (86): 43 items in all. Nothing outside that scope was changed, even
where the adjudicator noted something (below).

## Method

One adjudicator, Claude Opus 5.5 at high effort (cost US$1.68), decided each item against `eval/taxonomy.md` alone:
gate error, gold error (with a machine-readable change), or grey (the taxonomy does not decide; gold kept). Verdicts
with a one-line reason each: [`decisions.jsonl`](decisions.jsonl). The orchestrator (Claude Opus 5.5) checked every
item marked as a gold error and a random 20% of the rest (seed 20261113: I001, I002, I005, I009, I012, I024, I031,
I034, I040) against the taxonomy and the texts, and agreed with all eleven. `apply_adjudication.py` applies the gold
errors' changes, checks each against the text, and writes the adjudicated files.

## Result

| | gate error | gold error | grey |
|---|---|---|---|
| Missed spans (23) | 19 | 2 | 2 |
| Held clean cases (20) | 19 | 0 | 1 |

**Gold errors (both removed from half T):**
- GT-018, SEXUALITY "her partner": the case never states the partner's gender, so the phrase does not reveal
  orientation (taxonomy section 3: a relationship fact *that reveals it*). The case keeps another SEXUALITY span.
- GT-089, UNION "circulated a signature sheet among colleagues before raising the matter formally": a petition is
  neither union membership nor a union role; the delegate role beside it stays gold.

**Grey (gold kept):** GR-065 UNION (an employee representative in consultative meetings, with no union wording);
GR-092 ETHNICITY (leave over Matariki to visit whānau); GR-063, a held "clean" fiction piece written as a notebook with
no fiction marker in the text.

**Held clean cases:** none gains a span. Every blind-labeller span in them is a story character or a statement about
one (8 fiction pieces), a public date, or a secret-manager reference path (6 configuration files), all hard negatives
by taxonomy section 4. The 19 holds are gate errors against the taxonomy; the two held only for an injection signal
are gate errors against a taxonomy that does not cover injection (whether such holds are wanted is a policy choice).

## Noted, not applied (outside the scope)

- GT-018 has a second SEXUALITY span on the same statement (offsets 1011-1109) that fails for the same reason, and
  one more (1306-1418) that deserves a second look; the gate covered both, so they were outside the scope.
- Several sensitive gold spans include the subject ("he leaves early ...") where the span rule says the predicate;
  this matters only for full-coverage recall and looks like a split-wide convention to settle once.
- GT-070 and GT-075 have no `hard_negatives`; some site addresses in GT-050 and GR-086 are not annotated.
