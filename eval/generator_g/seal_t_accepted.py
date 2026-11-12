"""Seal half T under plan v0.6's amendment (12/11/2026, commit 17eef3e): the same three checks as seal.py, accepted
only if the validator has 0 failures and its single non-ok quota row is `cases: 88`, the denylist passes and the
extra quotas pass. Anything else refuses to seal. Counts only on the console."""
import json
import re
import sys

import common as C
import seal

orig = seal.build_and_check


def accepted(h):
    cases, ok, out = orig(h)
    short = [l.strip() for l in out.splitlines() if l.strip().startswith("SHORT")]
    fails = re.search(r"failures: (\d+)", out)
    cond = (h.L == "T" and len(cases) == 88 and fails and fails.group(1) == "0"
            and short == ["SHORT cases: 88 (target 90-110 (about 100))"]
            and "DENYLIST VERDICT: PASS" in out and "EXTRA VERDICT: PASS" in out
            and (h.dir / "work" / "org_label_override.json").exists())
    print("accepted under the plan amendment:", bool(cond), "| short rows:", len(short))
    return cases, bool(cond), out


seal.build_and_check = accepted
rc = seal.seal_half(C.Half("T"))
if rc == 0:
    p = C.Half("T").dir / "work" / "seal-result.json"
    r = json.loads(p.read_text(encoding="utf-8"))
    r["accepted_shortfall"] = "cases 88 < 90, accepted by FROZEN-PLAN-v0.6 amendment (commit 17eef3e)"
    p.write_text(json.dumps(r), encoding="utf-8")
sys.exit(rc)
