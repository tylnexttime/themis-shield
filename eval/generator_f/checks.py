"""Step 5: real-world pointer check (claude-sonnet-5-5, effort high), one request per case.

    python checks.py submit f-pointer-1 all          every case with usable text
    python checks.py submit f-pointer-2 recheck      repaired-and-flagged cases and top-up cases
    python checks.py ingest f-pointer-1
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

import common as C
import prompts
from markup import parse_markup
from store import load_state, save_state


def ids_for(mode: str, st: dict) -> list[str]:
    if mode == "all":
        return sorted(i for i, r in st.items() if r["raw"] and r["status"] != "dropped")
    return sorted(i for i, r in st.items() if r["status"] == "valid" and
                  (r.get("pointer_recheck") or (r["round"] == "topup-1" and not r.get("pointer"))))


def cmd_submit(name: str, mode: str) -> int:
    st = load_state()
    ids = ids_for(mode, st)
    if not ids:
        print("nothing to check")
        return 0
    reqs = [C.batch_request(i, C.CHECK_MODEL, prompts.POINTER_SYSTEM,
                            prompts.pointer_user(parse_markup(st[i]["raw"], lenient=True)["plain"]), 16000, "high",
                            cache_system=False) for i in ids]
    exp = [3000] * len(reqs)
    C.estimate_and_guard(name, C.CHECK_MODEL, [r["params"] for r in reqs], exp)
    C.submit(name, reqs, C.CHECK_MODEL, "high", "split F real-world pointer check: one request per case",
             record=name.replace("f-pointer-", "pointer-batch-"))
    return 0


def parse_answer(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d.get("flag"), bool):
                return d["flag"], str(d.get("reason", ""))
        except json.JSONDecodeError:
            pass
    m = re.search(r'"flag"\s*:\s*(true|false)', text)
    if m:
        return m.group(1) == "true", ""
    return True, "unparseable answer (treated as flagged)"


def cmd_ingest(name: str) -> int:
    st = load_state()
    rows = {r["custom_id"]: r for r in C.read_jsonl(C.ROOT / "results" / f"{name}.jsonl")}
    second = name.endswith("2")
    c = Counter()
    for cid, r in rows.items():
        if r["type"] != "succeeded":
            flag, reason = True, "pointer request errored (treated as flagged)"
            c["errored_treated_as_flagged"] += 1
        else:
            flag, reason = parse_answer(r["text"])
        c["checked"] += 1
        c["flagged"] += flag
        rec = st[cid]
        if second:
            rec["pointer2"] = {"flag": flag, "reason": reason}
            rec["pointer_recheck"] = False
            if flag:
                rec["status"], rec["dropped_after"] = "dropped", name
                c["dropped_after_recheck"] += 1
        else:
            rec["pointer"] = {"flag": flag, "reason": reason}
    save_state(st)
    out = dict(c)
    out["name"] = name
    C.log_round(f"ingest-{name}", out)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "submit":
        sys.exit(cmd_submit(sys.argv[2], sys.argv[3]))
    sys.exit(cmd_ingest(sys.argv[2]))