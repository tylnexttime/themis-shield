"""Author, repair and top-up batches: build requests, estimate, guard, submit; ingest results into the state.

    python author.py submit author-1        (all plan entries)
    python author.py ingest author-1        (parse + validate + round counts)
    python author.py repair-submit          (one repair batch: rule failures and pointer flags)
    python author.py ingest repair-1
    python author.py topup-submit           (reads work/topup_ids.json; entries are in plan.topup.jsonl)
    python author.py ingest topup-1
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import common as C
import prompts
import validate as VAL
from store import load_entries, load_profile, load_state, save_state

NAMES = {"author-1": "f-author-1", "repair-1": "f-repair-1", "topup-1": "f-topup-1"}
PURPOSE = {"author-1": "split F author batch: 240 free-prose documents with inline gold",
           "repair-1": "split F repair batch: one round, rule failures and real-world pointer flags",
           "topup-1": "split F top-up batch: new plan entries for a quota shortfall only"}


def expected_out(e: dict) -> int:
    return min(64000, int(e["chars"][1] / 3 * 1.3) + 18000)


def submit_requests(round_name: str, reqs: list[dict], entries: list[dict]) -> None:
    name = NAMES[round_name]
    est = C.estimate_and_guard(name, C.AUTHOR_MODEL, [r["params"] for r in reqs], [expected_out(e) for e in entries])
    C.submit(name, reqs, C.AUTHOR_MODEL, "high", PURPOSE[round_name], record=round_name.replace("-", "-batch-"))


def cmd_submit(round_name: str) -> int:
    entries, profile = load_entries(), load_profile()
    system = prompts.author_system(profile)
    ids = sorted(i for i in entries if i.startswith("FO-") and int(i[3:]) <= 240)
    reqs = [C.batch_request(i, C.AUTHOR_MODEL, system, prompts.user_spec(entries[i], profile), 64000, "high")
            for i in ids]
    submit_requests(round_name, reqs, [entries[i] for i in ids])
    return 0


def cmd_topup_submit() -> int:
    entries, profile = load_entries(), load_profile()
    ids = json.loads((C.ROOT / "work" / "topup_ids.json").read_text())
    system = prompts.author_system(profile)
    reqs = [C.batch_request(i, C.AUTHOR_MODEL, system, prompts.user_spec(entries[i], profile), 64000, "high")
            for i in ids]
    submit_requests("topup-1", reqs, [entries[i] for i in ids])
    return 0


def pointer_line(st_rec: dict) -> list[str]:
    p = st_rec.get("pointer")
    if p and p.get("flag"):
        return ["[P_POINTER] a reviewer flagged this document: it may name a real organisation, real product or real "
                "public figure together with an invented private person (" + p.get("reason", "").strip()[:300] +
                ")  -> rewrite so that no real organisation, product or public figure is named in connection with "
                "an invented private person or an organisation's staff; use invented names"]
    return []


def cmd_repair_submit() -> int:
    entries, profile, st = load_entries(), load_profile(), load_state()
    system = prompts.author_system(profile)
    reqs, ents = [], []
    for cid in sorted(st):
        rec = st[cid]
        if rec["status"] == "dropped":
            continue
        probs = []
        if rec["issues"]:
            case, _ = VAL.parse_output(entries[cid], rec["raw"], profile) if rec["raw"] else ({"text": ""}, None)
            probs += VAL.problem_lines(case, [tuple(x) for x in rec["issues"]])
        probs += pointer_line(rec)
        if not probs:
            continue
        prev = rec["raw"] or "(unavailable: the previous output was empty or cut off; write the document afresh)"
        reqs.append(C.batch_request(cid, C.AUTHOR_MODEL, system, prompts.user_repair(entries[cid], profile, prev, probs),
                                    64000, "high"))
        ents.append(entries[cid])
        rec["repair_requested"] = True
    if not reqs:
        print("nothing to repair")
        return 0
    save_state(st)
    submit_requests("repair-1", reqs, ents)
    print(f"repair requests: {len(reqs)}")
    return 0


def read_results(name: str) -> dict:
    return {r["custom_id"]: r for r in C.read_jsonl(C.ROOT / "results" / f"{name}.jsonl")}


def cmd_ingest(round_name: str) -> int:
    entries, profile, st = load_entries(), load_profile(), load_state()
    res = read_results(NAMES[round_name])
    bst = C.load_state(NAMES[round_name])
    # candidate raws
    new_raw = {}
    for cid, r in res.items():
        ok = r["type"] == "succeeded" and r.get("stop_reason") != "max_tokens" and r.get("text", "").strip()
        new_raw[cid] = r["text"] if ok else ""
    rows = {cid: (rec["raw"]) for cid, rec in st.items() if rec["status"] != "dropped"}
    if round_name == "author-1":
        rows = {}
    rows.update({cid: raw for cid, raw in new_raw.items()})
    cases, issues, parsed = VAL.all_issues(entries, {k: v for k, v in rows.items() if v}, profile)
    counts = Counter()
    for cid, raw in new_raw.items():
        iss = issues.get(cid, []) if raw else [("P_NOOUTPUT", "no usable output (error, empty or cut off)")]
        codes = sorted({c for c, _ in iss})
        if round_name == "author-1":
            st[cid] = {"raw": raw, "round": round_name, "issues": iss, "status": "invalid" if iss else "valid",
                       "pointer": None, "history": [{"round": round_name, "codes": codes}]}
        else:
            rec = st.get(cid) or {"raw": "", "history": [], "pointer": None, "issues": []}
            if cid not in st and round_name == "topup-1":
                rec = {"raw": raw, "round": round_name, "issues": iss, "status": "invalid" if iss else "valid",
                       "pointer": None, "history": []}
                rec["history"].append({"round": round_name, "codes": codes})
                st[cid] = rec
                if iss:
                    rec["status"] = "dropped"
            else:
                rec["history"].append({"round": round_name, "codes": codes})
                if iss:
                    rec["status"], rec["dropped_after"] = "dropped", round_name
                else:
                    rec["raw_before_repair"] = rec["raw"]
                    rec.update({"raw": raw, "round": round_name, "issues": [], "status": "valid",
                                "pointer_recheck": bool(rec.get("pointer") and rec["pointer"].get("flag"))})
        for c in codes:
            counts[c] += 1
    # cases that were valid earlier must still be valid in the whole set (duplicate texts etc.)
    save_state(st)
    parsed_new = [parsed[c] for c in new_raw if new_raw[c] and c in parsed]
    summary = {"round": round_name, "requests": bst.get("n"), "succeeded": bst.get("succeeded"),
               "errored": bst.get("errored"), "truncated": bst.get("truncated"),
               "cases_in_round": len(new_raw), "cases_with_issues": sum(1 for c in new_raw if issues.get(c) or not new_raw[c]),
               "cases_clean_of_issues": sum(1 for c in new_raw if new_raw[c] and not issues.get(c)),
               "issue_case_counts_by_code": dict(counts), "trimmed_spans": sum(p["trimmed"] for p in parsed_new),
               "fence_stripped": sum(p["fence_stripped"] for p in parsed_new),
               "cases_with_wrong_closer_accepted": sum(1 for p in parsed_new if p["closer_fixed"]),
               "wrong_closers_accepted": sum(p["closer_fixed"] for p in parsed_new),
               "unknown_hn_kind_warnings": sum(len(p["warnings"]) for p in parsed_new),
               "status_totals": dict(Counter(r["status"] for r in st.values()))}
    C.log_round(f"ingest-{round_name}", summary)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "submit":
        sys.exit(cmd_submit(sys.argv[2]))
    if cmd == "repair-submit":
        sys.exit(cmd_repair_submit())
    if cmd == "topup-submit":
        sys.exit(cmd_topup_submit())
    if cmd == "ingest":
        sys.exit(cmd_ingest(sys.argv[2]))
    sys.exit("unknown command")