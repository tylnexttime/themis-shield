"""Per-half sealing under the amended rule of 13/11/2026 (FROZEN-PLAN-v0.7, "Amendment ... one targeted top-up").

    python seal_h.py evaluate     both halves: run the checks and print the decision (counts only); seals nothing
    python seal_h.py seal         seal every half whose decision is PASS or ACCEPT_SHORT, convert the profiles, write
                                  manifest.json and PROVENANCE.md; a DROP half is not sealed

Decision per half:
  PASS          validator (--quotas), denylist and extra quotas all pass.
  ACCEPT_SHORT  validator failures 0, denylist clean, and the only failing rows are coverage rows (a minimum number of gold
                spans per label or sensitive category, or of documents of one kind), at most two of them, each short by at
                most two units (a gold span; a document for the long-documents row). The shortfall is recorded.
  DROP          anything else.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

import common as C
import pipeline
import seal
import topup as T
from store import load_profile

COVERAGE_ROW = re.compile(r"^(gold spans |every label|every sensitive|clean documents|tables|hard-to-spot|long documents|scripts|other invented)")
COVERAGE_KEYS = ("clean", "tables", "hard", "long", "scripts", "others")
MAX_ROWS, MAX_UNITS = 2, 2


def evaluate(h) -> dict:
    cases, ok, out = seal.build_and_check(h)
    profile = load_profile(h)
    m = re.search(r"cases: (\d+)\s+failures: (\d+)", out)
    failures = int(m.group(2)) if m else None
    denylist_ok = "DENYLIST VERDICT: PASS" in out
    a = T.analyse(h, cases, profile)
    short = T.shortfall_counts(a, T.Q, (0.40, 0.60))
    coverage = {k: v for k, v in short.items() if k.startswith(("label ", "sens ")) or k in COVERAGE_KEYS}
    other_short = {k: v for k, v in short.items() if k not in coverage}
    other_failing = sorted(n for n in pipeline.failing_rows(cases, profile) if not COVERAGE_ROW.match(n))
    if ok:
        decision = "PASS"
    elif (failures == 0 and denylist_ok and coverage and not other_short and not other_failing
          and len(coverage) <= MAX_ROWS and all(v <= MAX_UNITS for v in coverage.values())):
        decision = "ACCEPT_SHORT"
    else:
        decision = "DROP"
    return {"half": h.L, "cases": len(cases), "validator_failures": failures, "denylist_ok": denylist_ok, "all_checks_pass": ok,
            "coverage_short": coverage, "other_short": other_short, "other_failing_rows": other_failing, "decision": decision}


def main(argv: list[str]) -> int:
    evs = {L: evaluate(C.Half(L)) for L in sorted(C.HALVES)}
    for L, ev in evs.items():
        print(json.dumps(ev, sort_keys=True))
    if argv and argv[0] == "seal":
        sealed = []
        for L, ev in evs.items():
            if ev["decision"] == "DROP":
                print(f"half {L}: DROP, not sealed")
                continue
            h = C.Half(L)
            digest = seal.seal_one(h)
            p = h.dir / "work" / "seal-result.json"
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec.update({"decision": ev["decision"], "short_rows": ev["coverage_short"]})
            p.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
            sealed.append(L.lower())
            print(f"half {L}: sealed {digest} ({ev['cases']} cases, {ev['decision']}, short rows {ev['coverage_short']})")
        if sealed:
            r = subprocess.run([C.PY, str(C.ROOT / "prep_profiles.py")] + sealed, capture_output=True, text=True, encoding="utf-8")
            print(r.stdout.strip(), (r.stderr or "")[-300:])
        return seal.final()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))