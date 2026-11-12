"""Driver for one half of split G: runs the phases in order, resumable (a phase marked done is skipped; inside a phase a
request whose result file exists is not repeated). Counts only on the console. Exit code 5 = a token guard stopped the
phase (state written, nothing lost); 4 = profile/phase problem; 0 = ran to the end of the requested phases.

    python run_half.py R                 all phases of half R
    python run_half.py R --until repair  stop after that phase
    python run_half.py R --only label    run only that phase
"""
from __future__ import annotations

import json
import sys
import traceback

import ali
import common as C
import prompts
import runner

ORDER = ["profile", "author", "pointer", "repair", "pointer2", "topup", "label", "review", "seal"]


def step_profile(h):
    import profile
    return profile.run(h), {}


def step_author(h):
    import pipeline
    summ, stop = pipeline.phase_author(h, "author")
    st = summ["status_totals"]
    nout = summ["issue_case_counts_by_code"].get("P_NOOUTPUT", 0)
    if nout > 0.3 * max(1, summ["requests"]) and not stop:
        return 6, {"note": f"{nout} of {summ['requests']} author requests gave no usable output: halting", **summ}
    return (5 if stop else 0), {"stop": stop, **summ}


def step_pointer(h):
    import pipeline
    summ, stop = pipeline.phase_pointer(h, "pointerb" if h.L in prompts.POINTER_V2_HALVES else "pointer", "all")
    return (5 if stop else 0), {"stop": stop, **summ}


def step_repair(h):
    import pipeline
    summ, stop = pipeline.phase_repair(h)
    return (5 if stop else 0), {"stop": stop, **summ}


def step_pointer2(h):
    import pipeline
    summ, stop = pipeline.phase_pointer(h, "pointer2", "recheck")
    return (5 if stop else 0), {"stop": stop, **summ}


def step_topup(h):
    import pipeline
    import topup
    if not (h.dir / "work" / "topup_ids.json").exists():
        info = topup.plan_topup(h)
    else:
        info = json.loads((h.dir / "rounds" / "topup-plan.json").read_text(encoding="utf-8"))
    if not info["entries"]:
        return 0, {"topup": "none needed", **info}
    summ, stop = pipeline.phase_author(h, "topup")
    if stop:
        return 5, {"stop": stop, **summ}
    s2, stop = pipeline.phase_pointer(h, "pointer2", "recheck")  # top-up cases get their pointer check
    return (5 if stop else 0), {"stop": stop, "topup_entries": info["entries"], **summ, "pointer": s2}


def step_label(h):
    import pipeline
    summ, stop = pipeline.phase_label(h)
    return (5 if stop else 0), {"stop": stop, **summ}


def step_review(h):
    import pipeline
    summ, stop = pipeline.phase_review(h)
    return (5 if stop else 0), {"stop": stop, **summ}


def step_seal(h):
    import seal
    return seal.seal_half(h), {}


STEPS = {"profile": step_profile, "author": step_author, "pointer": step_pointer, "repair": step_repair,
         "pointer2": step_pointer2, "topup": step_topup, "label": step_label, "review": step_review, "seal": step_seal}


def short(info: dict) -> str:
    keys = ("requests", "cases_in_round", "cases_clean_of_issues", "cases_with_issues", "checked", "flagged",
            "dropped_after_recheck", "documents compared", "items_total", "items_keep", "items_fix", "fix_applied", "entries",
            "topup_entries", "status_totals")
    return " ".join(f"{k}={info[k]}" for k in keys if k in info)


def main(argv: list[str]) -> int:
    h = C.Half(argv[0])
    until = argv[argv.index("--until") + 1] if "--until" in argv else None
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    for name in ORDER:
        if only and name != only:
            continue
        if h.is_done(name):
            print(h.L, name, "already done")
        else:
            try:
                rc, info = STEPS[name](h)
            except ali.GuardStop as exc:
                rc, info = 5, {"stop": str(exc)}
            except SystemExit as exc:
                rc, info = 4, {"note": f"exit {exc}"}
            except Exception as exc:  # noqa: BLE001
                rc, info = 9, {"note": f"{type(exc).__name__}: {ali.scrub(exc)[:200]}"}
                traceback.print_exc()
            runner.write_usage_summary()
            line = f"phase={name} half={h.L} rc={rc} {short(info)} | {runner.cumulative_line()}"
            if info.get("stop"):
                line += " | GUARD STOP: " + str(info["stop"])[:160]
            if info.get("note"):
                line += " | " + str(info["note"])[:160]
            C.status_line(line)
            print(line)
            if rc != 0:
                print(h.L, f"phase {name} did not complete (rc {rc}); resume with: python run_half.py {h.L}")
                return rc
            h.mark_done(name, {k: v for k, v in info.items() if isinstance(v, (int, str, float, dict, list)) and k != "stop"})
        if until and name == until:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))