"""Pilot: 3 specifications through the real prompts (non-batch, streamed), to debug parser and prompts. Output is
discarded (not part of the set); the spend is booked as f-pilot. Prints counts and rule codes only."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import common as C
import prompts
import validate as VAL
from store import load_entries, load_profile, WORK

entries, profile = load_entries(), load_profile()
system = prompts.author_system(profile)
pick = []
for pred in (lambda e: not e["clean"] and e["org"]["kind"] == "profiled" and e["hard"] and e["injection"] and not e["long"],
             lambda e: e["quasi_table"] and e["org"]["kind"] == "other" and not e["long"],
             lambda e: e["clean"] and e["genre"] == "config" and e["org"]["kind"] == "profiled" and not e["long"]):
    pick.append(next(e for e in entries.values() if pred(e)))
(WORK / "pilot").mkdir(exist_ok=True)


def one(e):
    c = C.client()
    params = {"model": C.AUTHOR_MODEL, "max_tokens": 64000, "thinking": {"type": "adaptive"},
              "output_config": {"effort": "high"},
              "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": prompts.user_spec(e, profile)}]}
    with c.messages.stream(**params) as s:
        m = s.get_final_message()
    text = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
    u = {k: int(getattr(m.usage, k, 0) or 0) for k in C.USAGE_KEYS}
    return e, text, m.stop_reason, u


if C.ledger_total() + 5 > C.BUDGET_USD:
    sys.exit("over cap")
with ThreadPoolExecutor(3) as ex:
    res = list(ex.map(one, pick))
usage = C.sum_usage([r[3] for r in res])
usage["note"] = f"pilot, {len(res)} succeeded, 0 errored (streamed, output discarded)"
(C.ROOT / "requests" / "pilot.usage.json").write_text(json.dumps(usage), encoding="utf-8")
C.book("f-pilot", C.AUTHOR_MODEL, "high", "split F pilot of 3 specifications to debug prompts and parser (discarded)",
       C.ROOT / "requests" / "pilot.usage.json", batch=False)
for e, text, stop, u in res:
    (WORK / "pilot" / f"{e['id']}.txt").write_text(text, encoding="utf-8")
    case, parsed = VAL.parse_output(e, text, profile)
    iss = VAL.entry_issues(e, case, parsed) + VAL.validator_issues([case], profile).get(e["id"], [])
    print(e["id"], e["genre"], "stop", stop, "out_tokens", u["output_tokens"], "chars", len(case["text"]),
          "spans", len(case["spans"]), "hns", len(case["hard_negatives"]), "inj", bool(case.get("injection")),
          "issues", sorted({c for c, _ in iss}))