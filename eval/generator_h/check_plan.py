"""Plan-level checks before any authoring (counts only): the origin denylist over every string of every plan entry (the
workplace labels included) and over the half's organisation kind and workplace sectors; cross-half uniqueness of the
pre-generated values (plan and top-up files of both halves); the plan files' sha256.

    python check_plan.py            exit code 0 = clean
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import common as C
import safevalues as sv
from plan_data import OTHERS_BY_HALF
from validate import PATS


def strings(o):
    if isinstance(o, dict):
        return [x for v in o.values() for x in strings(v)]
    if isinstance(o, list):
        return [x for v in o for x in strings(v)]
    return [o] if isinstance(o, str) else []


def hits(text: str) -> int:
    return sum(len(p.findall(text)) for p in PATS)


def main() -> int:
    bad = 0
    print(f"denylist patterns loaded: {len(PATS)}")
    for L in sorted(C.HALVES):
        h = C.Half(L)
        n_hit = hits(C.HALVES[L]["org_kind"])
        n_sector = sum(1 for k, sector, where in OTHERS_BY_HALF[L] if hits(sector) or hits(where))
        rows = C.read_jsonl(h.plan) + C.read_jsonl(h.plan_topup) + C.read_jsonl(h.plan_topup2)
        cases_hit = [e["id"] for e in rows if any(hits(t) for t in strings(e))]
        # metadata as it will appear in a case: the organisation label of other workplaces
        labels_hit = sum(1 for e in rows if e["org"]["kind"] == "other"
                         and hits(f"Other workplace {e['org']['k']}: {e['org']['sector']}"))
        print(f"half {L}: org_kind hit {n_hit}; workplace sector/place entries with a hit {n_sector}; "
              f"plan entries {len(rows)}, with a hit {len(cases_hit)}, workplace labels with a hit {labels_hit}")
        bad += n_hit + n_sector + len(cases_hit) + labels_hit
    # uniqueness of the values across both halves, plan and top-up
    seen: Counter = Counter()
    where: dict = {}
    for L in sorted(C.HALVES):
        h = C.Half(L)
        for e in C.read_jsonl(h.plan) + C.read_jsonl(h.plan_topup) + C.read_jsonl(h.plan_topup2):
            for kind, lst in (e.get("values") or {}).items():
                for x in lst:
                    k = sv.vkey(x["value"] if isinstance(x, dict) else x)
                    seen[k] += 1
    dup = sum(1 for k, n in seen.items() if n > 1)
    print(f"pre-generated values: {sum(seen.values())} drawn, {len(seen)} distinct, {dup} drawn more than once across both halves")
    bad += dup
    for L in sorted(C.HALVES):
        h = C.Half(L)
        for p in (h.plan, h.plan_topup, h.plan_topup2):
            if p.exists():
                print(f"  {p.name} half {L}: sha256 {C.sha256_file(p)}")
    print("PLAN CHECK:", "PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
