# Development debt

Things that are wrong, fragile or half-done in what already exists. New features live in [ROADMAP.md](ROADMAP.md);
this page is for debt. **The rule: keep it short.** Anything new is recorded here the day it is found and fixed as
soon as it can be. An item leaves the open table only when it is fixed and tested, or moved to the roadmap with a
reason. Dates are day-first.

## Open

| ID | Found | What | Why it matters | Plan |
|---|---|---|---|---|
| DD-04 | 27/10/2026 | Re-acquiring the 26B finder while PII-Tracer is loaded can fail the scheduler's memory check on an 8 GB GPU / 31.5 GB RAM laptop, and the stage then fails closed (split D, arm 2, half C). | A long-running gate can BLOCK on memory alone. Fail-closed is safe; the friction it adds is not. | Fix the re-acquire check (release, then retry once under the lock) and test it with a fake runtime that reports memory pressure. |
| DD-05 | 27/10/2026 | A long table can overflow the finder's 8,192-token answer; the stage fails and the document is BLOCKed. | **Raised 30/10/2026:** on split D, 274 of the 489 unprotected spans in held documents were held *only* by this failure (`BLOCK:stage-failed`), so these holds were luck, not detection. Fixing the overflow alone would turn some of them into released leaks. | Do it together with column-aware identifier propagation (ROADMAP v0.4), never before it: finders list each distinct value once, then propagate by column. Re-measure on development data with the held-by-reason split. |
| DD-06 | 27/10/2026 | The LLM finders and PII-Tracer read the raw text, not the Unicode-hardened detection view that markings and rules read (v0.2.1). | A zero-width character inside a name can still hide it from the finders. | Needs a new finder capture (the prompts are hashed for replay). Schedule with the next capture, i.e. with DD-05. |
| DD-03 | before v0.1 | Two contract tests are `xfail`: the conservative offset map and the add-only ledger (`tests/test_contracts.py`, "M2 deferred"). | The contracts are written down but not enforced. | Either implement both or move them to the roadmap with a reason; decide at the next release. |

## Fixed

| ID | Found | Fixed | What | Fix |
|---|---|---|---|---|
| DD-01 | 28/10/2026 | 30/10/2026 | Split D's pooled scores were computed with the `native` label set, so 1,424 gate predictions (union labels such as `DRIVER_LICENCE\|STUDENT_ID`, plus `STUDENT_ID`, `USI`, `INDIGENOUS`) counted as unmapped. Label-blind measures (released leaks, R_any, R_full, sensitive R_any, friction, over-redaction), and so the verdict, were unaffected. Typed recall was understated (0.690, really 0.899), as were typed precision (0.637-0.640, really 0.772-0.775) and case-level any-sensitive recall (0.935, really 0.974). | `score.py --labelset themis` maps the gate's labels and every union through `label_map.py`; the report prints the label table's fingerprint, warns when union labels go unmapped, and separates *leak* (touched by nothing: a finder miss) from *touched-untyped* (touched, but no label maps to it: the price of the taxonomy). Self-test cases 9-10. |
| DD-02 | 30/10/2026 | 30/10/2026 | `test_rejected_flag_lets_the_same_package_go_without_a_second_trip_wire` started failing on 29/10/2026 with no code change: the harness approves at a fixed date for seven days, and `themis tripwire review` read the wall clock. | Every register method that takes `now` defaults to the harness clock (pinned by signature, so a method added later is covered too). DD-08 tracks the wider check. |
| DD-07 | 28/10/2026 | 30/10/2026 | A held document's unprotected spans could not be told apart by *why* it was held, so "caught" and "held incidentally" read as one number. | The scorer reports `held_back_unprotected_by_reason` (by the hold's first reason, from `decisions.jsonl`). It produced DD-05's new priority on its first run. |
| DD-09 | 30/10/2026 | 30/10/2026 | The roadmap still described v0.3 as future work, and the README said every test author was a Claude model (split D had a Qwen author and a DeepSeek labeller). | Roadmap status updated; README corrected. |
| DD-08 | 30/10/2026 | 30/10/2026 | Only the job register's clock was pinned in the test harness; other modules read the wall clock in tests, so a second date bomb like DD-02 could be waiting. | `pytest --shift-clock-days N` runs the whole suite with the wall clock N days ahead (`time-machine`, `dev` extra) and asserts the clock really moved. At +400 days: 1,669 passed, no other date bomb. Run once per release. |
| DD-10 | 30/10/2026 | 30/10/2026 | (Found while building `themis vet`.) The chat vault expires a conversation after four idle hours, so a codebook kept there for a vetted release would stop restoring the same day. | Each vetted release gets its own codebook vault file under the deployment's key protector, with a long idle life; retention and disposal are the custodian's rule (vetting record, part 5). |
