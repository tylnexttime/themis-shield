"""Driver for the second, targeted top-up of one half (amendment of 13/11/2026). Resumable: a phase marked done
(work/done-t2-<phase>.json) is skipped, and inside a phase a request with a result file is not repeated. Counts only.
Exit code 5 = a token guard stopped the phase (state kept); 9 = unexpected error.

    python run_second.py A          plan, author, pointer, repair, pointer recheck, check, label, review, agreement, final check
    python run_second.py A --only plan
"""
from __future__ import annotations

import sys
import traceback

import common as C
import llm
import runner
import second_topup as S
import seal
from run_half import short

ORDER = ["plan", "author", "pointer", "repair", "pointer2", "check1", "label", "review", "agreement", "check"]


def step_plan(h):
    if (h.dir / "work" / "topup2_ids.json").exists():
        return 0, {"note": "plan exists", **C.jload(h.dir / "rounds" / "topup2-plan.json", {})}
    info = S.plan_topup2(h)
    return 0, info


def step_check1(h):
    cases, ok, out = seal.build_and_check(h)
    (h.dir / "rounds" / "check-after-topup2.txt").write_text(out, encoding="utf-8")
    return 0, {"cases": len(cases), "checks_pass": ok}


def step_agreement(h):
    return 0, S.final_agreement(h)


def step_check(h):
    cases, ok, out = seal.build_and_check(h)
    (h.dir / "rounds" / "final-check-2.txt").write_text(out, encoding="utf-8")
    return 0, {"cases": len(cases), "checks_pass": ok}


STEPS = {"plan": step_plan, "author": S.step_author, "pointer": S.step_pointer, "repair": S.step_repair,
         "pointer2": S.step_pointer2, "check1": step_check1, "label": S.step_label, "review": S.step_review,
         "agreement": step_agreement, "check": step_check}


def main(argv):
    h = C.Half(argv[0])
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    for name in ORDER:
        if only and name != only:
            continue
        marker = h.dir / "work" / f"done-t2-{name}.json"
        if marker.exists():
            print(h.L, name, "already done")
            continue
        try:
            rc, info = STEPS[name](h)
        except (llm.GuardStop, llm.NotAllowed) as exc:
            rc, info = 5, {"stop": str(exc)}
        except SystemExit as exc:
            rc, info = 4, {"note": f"exit {exc}"}
        except Exception as exc:  # noqa: BLE001
            rc, info = 9, {"note": f"{type(exc).__name__}: {llm.scrub(exc)[:200]}"}
            traceback.print_exc()
        runner.write_usage_summary()
        brief = {k: info[k] for k in ("entries", "shortfalls", "planned_spans_or_documents", "profiled_entries", "cases", "checks_pass",
                                      "documents compared", "no_labeller_output") if k in info}
        line = f"T2 phase={name} half={h.L} rc={rc} {short(info)} {brief} | {runner.cumulative_line()}"
        if info.get("stop"):
            line += " | GUARD STOP: " + str(info["stop"])[:160]
        if info.get("note"):
            line += " | " + str(info["note"])[:160]
        C.status_line(line)
        print(line)
        if rc != 0:
            print(h.L, f"phase {name} did not complete (rc {rc}); resume with: python run_second.py {h.L}")
            return rc
        marker.write_text("{}", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))