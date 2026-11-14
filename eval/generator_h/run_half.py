"""Driver for one half of split H: runs the phases in order, resumable (a phase marked done is skipped; inside a phase a
request whose result file exists is not repeated). Counts only on the console. Exit code 5 = a token guard stopped the
phase (state written, nothing lost); 4 = profile/phase problem; 0 = ran to the end of the requested phases.

    python run_half.py A                 all phases of half A (the last one only checks; sealing is done for both halves together)
    python run_half.py A --until repair  stop after that phase
    python run_half.py A --only label    run only that phase
"""
from __future__ import annotations

import json
import sys
import traceback

import llm
import common as C
import prompts
import runner

ORDER = ["profile", "author", "pointer", "repair", "pointer2", "topup", "topup2", "converge", "label", "review", "check"]
ALLOW2 = "--allow-second-topup" in sys.argv


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
    summ, stop = pipeline.phase_pointer(h, "pointer", "all")
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


def step_topup2(h):
    """A SECOND top-up: not allowed by the frozen plan (at most one). It runs only with --allow-second-topup, which Alder or
    Chris must authorise explicitly; without the flag the step is skipped. Entries use multiplier 2.5 and the safe builder."""
    import pipeline
    import topup
    if not ALLOW2:
        return 0, {"topup2": "skipped: not authorised (--allow-second-topup)"}
    if not (h.dir / "work" / "topup2_ids.json").exists():
        info = topup.plan_topup(h, 2, 2.5)
    else:
        info = json.loads((h.dir / "rounds" / "topup2-plan.json").read_text(encoding="utf-8"))
    if not info["entries"]:
        return 0, {"topup2": "none needed", **info}
    summ, stop = pipeline.phase_author(h, "topup2")
    if stop:
        return 5, {"stop": stop, **summ}
    s2, stop = pipeline.phase_pointer(h, "pointer2", "recheck")
    return (5 if stop else 0), {"stop": stop, "topup_entries": info["entries"], **summ, "pointer": s2}


def step_converge(h):
    """After one repair round and at most one top-up: is the half clean with every quota met (validator, denylist, extra
    quotas)? If not, the half is dropped (not hand-edited) and nothing more is spent on it. More than 88 cases is trimmed by
    script only (never a hand edit)."""
    import finalize
    import seal
    trimmed = finalize.trim_to_ceiling(h)
    cases, ok, out = seal.build_and_check(h)
    info = {"cases": len(cases), "converged": ok, "trimmed_by_script": trimmed}
    (h.dir / "rounds" / "convergence-check.txt").write_text(out, encoding="utf-8")
    h.round_log("convergence", info)
    if not ok:
        info["note"] = "NOT CONVERGED after one repair round and one top-up: half dropped (see rounds/convergence-check.txt)"
        return 6, info
    return 0, info


def step_check(h):
    """Final check on the half after review (validator, denylist, extra quotas); sealing happens for both halves together."""
    import seal
    cases, ok, out = seal.build_and_check(h)
    info = {"cases": len(cases), "checks_pass": ok}
    h.round_log("final-check", info)
    return (0 if ok else 6), info


STEPS = {"profile": step_profile, "author": step_author, "pointer": step_pointer, "repair": step_repair,
         "pointer2": step_pointer2, "topup": step_topup, "topup2": step_topup2, "converge": step_converge, "label": step_label,
         "review": step_review, "check": step_check}


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
        if name == "topup2" and not ALLOW2:
            continue  # the plan allows one top-up; a second needs an explicit, recorded authorisation
        if h.is_done(name):
            print(h.L, name, "already done")
        else:
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