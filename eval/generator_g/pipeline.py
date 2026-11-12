"""Phases of one half (split G): author, pointer check, repair, top-up, blind labelling, review. Every phase is a set of
chat-completions calls through runner.run_calls (resumable: a call whose result file exists is not repeated) followed
by an ingest step that rebuilds the half's state from the files on disk. Prints and logs counts only."""
from __future__ import annotations

import json
import re
from collections import Counter

import agreement as A
import ali
import common as C
import finalize as F
import prompts
import runner
import validate as VAL
from markup import parse_markup, to_markup
from store import load_entries, load_profile, load_state, save_state


def author_max_tokens(e: dict, h_model_thinking) -> int:
    base = int(e["chars"][1] / 1.3) + 2000
    if h_model_thinking is not False:
        base += 4000
    return max(4000, min(30000, base))


def sysmsg(text: str) -> dict:
    return {"role": "system", "content": text}


def usermsg(text: str) -> dict:
    return {"role": "user", "content": text}


def _thinking_of(h: C.Half, role: str):
    return C.settings(h.models[role])["thinking"]


# ------------------------------------------------------------------ author, repair, top-up
def ingest_round(h: C.Half, round_name: str, ids: list[str]) -> dict:
    entries, profile, st = load_entries(h), load_profile(h), load_state(h)
    new_raw, meta = {}, Counter()
    for cid in ids:
        r = ali.load_result(h.L, round_name, cid)
        ok = bool(r and r.get("ok") and r.get("finish_reason") != "length" and r.get("content", "").strip())
        new_raw[cid] = r["content"] if ok else ""
        if not r:
            meta["no_result"] += 1
        elif not r.get("ok"):
            meta["call_failed"] += 1
            meta["call_failed:" + str((r.get("error") or {}).get("code") or "?")] += 1
        elif r.get("finish_reason") == "length":
            meta["truncated_at_max_tokens"] += 1
        meta["model:" + str(r.get("model") if r else "?")] += 1
    rows = {cid: rec["raw"] for cid, rec in st.items() if rec["status"] != "dropped"}
    if round_name == "author":
        rows = {}
    rows.update(new_raw)
    cases, issues, parsed = VAL.all_issues(entries, {k: v for k, v in rows.items() if v}, profile, h.L)
    counts = Counter()
    for cid, raw in new_raw.items():
        iss = issues.get(cid, []) if raw else [("P_NOOUTPUT", "no usable output (error, empty or cut off)")]
        codes = sorted({c for c, _ in iss})
        if round_name == "author":
            st[cid] = {"raw": raw, "round": round_name, "issues": iss, "status": "invalid" if iss else "valid",
                       "pointer": None, "history": [{"round": round_name, "codes": codes}]}
        else:
            rec = st.get(cid)
            if cid not in st and round_name == "topup":
                rec = {"raw": raw, "round": round_name, "issues": iss, "status": "invalid" if iss else "valid",
                       "pointer": None, "history": [{"round": round_name, "codes": codes}]}
                if iss:
                    rec["status"] = "dropped"
                    rec["dropped_after"] = round_name
                st[cid] = rec
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
    save_state(h, st)
    parsed_new = [parsed[c] for c in new_raw if new_raw[c] and c in parsed]
    summary = {"round": round_name, "requests": len(ids), "call_stats": dict(meta), "cases_in_round": len(new_raw),
               "cases_with_issues": sum(1 for c in new_raw if issues.get(c) or not new_raw[c]),
               "cases_clean_of_issues": sum(1 for c in new_raw if new_raw[c] and not issues.get(c)),
               "issue_case_counts_by_code": dict(counts), "trimmed_spans": sum(p["trimmed"] for p in parsed_new),
               "fence_stripped": sum(p["fence_stripped"] for p in parsed_new),
               "cases_with_wrong_closer_accepted": sum(1 for p in parsed_new if p["closer_fixed"]),
               "wrong_closers_accepted": sum(p["closer_fixed"] for p in parsed_new),
               "unknown_hn_kind_warnings": sum(len(p["warnings"]) for p in parsed_new),
               "status_totals": dict(Counter(r["status"] for r in st.values()))}
    h.round_log(f"ingest-{round_name}", summary)
    return summary


def phase_author(h: C.Half, round_name: str = "author") -> tuple[dict, str | None]:
    """round_name author: every entry of plan.jsonl; topup: the ids in work/topup_ids.json."""
    entries, profile = load_entries(h), load_profile(h)
    if round_name == "author":
        ids = sorted(e["id"] for e in C.read_jsonl(h.plan))
    else:
        ids = json.loads((h.dir / "work" / "topup_ids.json").read_text(encoding="utf-8"))
    system = sysmsg(prompts.author_system(profile))
    think = _thinking_of(h, "author")
    jobs = [{"case_id": i, "messages": [system, usermsg(prompts.user_spec(entries[i], profile))],
             "max_tokens": author_max_tokens(entries[i], think)} for i in ids]
    res, stop = runner.run_calls(h, "author", round_name, jobs)
    summ = ingest_round(h, round_name, ids)
    return summ, stop


def pointer_line(rec: dict) -> list[str]:
    p = rec.get("pointer")
    if p and p.get("flag"):
        return ["[P_POINTER] a reviewer flagged this document: it may name a real organisation, real product or real "
                "public figure together with an invented private person (" + p.get("reason", "").strip()[:300] +
                ")  -> rewrite so that no real organisation, product or public figure is named in connection with "
                "an invented private person or an organisation's staff; use invented names"]
    return []


def phase_repair(h: C.Half) -> tuple[dict, str | None]:
    entries, profile, st = load_entries(h), load_profile(h), load_state(h)
    system = sysmsg(prompts.repair_system())
    think = _thinking_of(h, "author")
    jobs = []
    for cid in sorted(st):
        rec = st[cid]
        if rec["status"] == "dropped" or rec.get("round") == "repair":
            continue
        probs = []
        if rec["issues"]:
            case, _ = VAL.parse_output(entries[cid], rec["raw"], profile, h.L) if rec["raw"] else ({"text": ""}, None)
            probs += VAL.problem_lines(case, [tuple(x) for x in rec["issues"]])
        probs += pointer_line(rec)
        if not probs:
            continue
        prev = rec["raw"] or "(unavailable: the previous output was empty or cut off; write the document afresh)"
        jobs.append({"case_id": cid, "messages": [system, usermsg(prompts.user_repair(entries[cid], profile, prev, probs))],
                     "max_tokens": author_max_tokens(entries[cid], think)})
    if not jobs:
        print(h.L, "nothing to repair")
        h.round_log("ingest-repair", {"round": "repair", "requests": 0})
        return {"requests": 0}, None
    ids = [j["case_id"] for j in jobs]
    (h.dir / "work" / "repair_ids.json").write_text(json.dumps(ids), encoding="utf-8")
    res, stop = runner.run_calls(h, "author", "repair", jobs)
    summ = ingest_round(h, "repair", ids)
    return summ, stop


# ------------------------------------------------------------------ pointer check
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


def pointer_ids(mode: str, st: dict) -> list[str]:
    if mode == "all":
        return sorted(i for i, r in st.items() if r["raw"] and r["status"] != "dropped")
    return sorted(i for i, r in st.items() if r["status"] == "valid" and
                  (r.get("pointer_recheck") or (r["round"] == "topup" and not r.get("pointer"))))


def phase_pointer(h: C.Half, name: str, mode: str) -> tuple[dict, str | None]:
    """name pointer (mode all) or pointer2 (mode recheck: repaired-and-flagged cases and top-up cases)."""
    st = load_state(h)
    ids = pointer_ids(mode, st)
    if not ids:
        print(h.L, name, "nothing to check")
        return {"checked": 0}, None
    psys = prompts.POINTER_SYSTEM_V2 if h.L in prompts.POINTER_V2_HALVES else prompts.POINTER_SYSTEM
    jobs = [{"case_id": i, "messages": [sysmsg(psys), usermsg(prompts.pointer_user(
        parse_markup(st[i]["raw"], lenient=True)["plain"]))], "max_tokens": 600} for i in ids]
    res, stop = runner.run_calls(h, "labeller", name, jobs, temp_key="label_temperature", temp_default=0)
    out = ingest_pointer(h, name, ids)
    return out, stop


def ingest_pointer(h: C.Half, name: str, ids: list[str]) -> dict:
    st = load_state(h)
    second = name == "pointer2"
    c = Counter()
    for cid in ids:
        r = ali.load_result(h.L, name, cid)
        if not r:
            c["no_result"] += 1
            continue
        if not r.get("ok"):
            flag, reason = True, "pointer request errored (treated as flagged)"
            c["errored_treated_as_flagged"] += 1
        else:
            flag, reason = parse_answer(r["content"])
            c["unparseable_treated_as_flagged"] += reason.startswith("unparseable")
        c["checked"] += 1
        c["flagged"] += flag
        c["model:" + str(r.get("model"))] += 1
        rec = st[cid]
        if second:
            rec["pointer2"] = {"flag": flag, "reason": reason}
            rec["pointer_recheck"] = False
            if flag:
                rec["status"], rec["dropped_after"] = "dropped", name
                c["dropped_after_recheck"] += 1
        else:
            rec["pointer"] = {"flag": flag, "reason": reason}
    save_state(h, st)
    out = dict(c)
    out["name"] = name
    h.round_log(f"ingest-{name}", out)
    return out


# ------------------------------------------------------------------ blind labelling
def label_max_tokens(text: str) -> int:
    return max(4000, min(30000, int(len(text) / 1.2) + 3000))


def _dump_metrics(m: dict, extra: dict, path) -> None:
    path.write_text(json.dumps({"per": {k: dict(v) for k, v in m["per"].items()}, "total": dict(m["total"]),
                                "extra": extra}), encoding="utf-8")


def _load_metrics(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {"per": {k: Counter(v) for k, v in d["per"].items()}, "total": Counter(d["total"])}, d["extra"]


def phase_label(h: C.Half) -> tuple[dict, str | None]:
    cases = F.valid_cases(h)
    system = sysmsg(prompts.taxonomy_text())
    jobs = [{"case_id": c["id"], "messages": [system, usermsg(prompts.label_user(c["text"]))],
             "max_tokens": label_max_tokens(c["text"])} for c in cases]
    (h.dir / "work" / "label_set.json").write_text(json.dumps([c["id"] for c in cases]), encoding="utf-8")
    res, stop = runner.run_calls(h, "labeller", "label", jobs, temp_key="label_temperature", temp_default=0)
    return ingest_label(h), stop


def ingest_label(h: C.Half) -> dict:
    ids = set(json.loads((h.dir / "work" / "label_set.json").read_text(encoding="utf-8")))
    cases = [c for c in F.valid_cases(h, statuses=("valid", "dropped")) if c["id"] in ids]
    lab, c = {}, Counter()
    for case in cases:
        r = ali.load_result(h.L, "label", case["id"])
        if not r or not r.get("ok") or not r.get("content", "").strip():
            c["no_labeller_output"] += 1
            continue
        d = A.labeller_spans(case["text"], r["content"])
        c["truncated_output"] += r.get("finish_reason") == "length"
        c["documents_with_text_drift"] += d["drift"]
        c["labeller_spans_dropped_in_alignment"] += d["dropped"]
        c["labeller_markup_errors"] += d["errors"]
        c["model:" + str(r.get("model"))] += 1
        lab[case["id"]] = d
    (h.dir / "work" / "labeller.json").write_text(json.dumps(lab), encoding="utf-8")
    m = A.metrics(cases, lab)
    extra = {"documents compared": len(lab), "documents with no labeller output": c["no_labeller_output"],
             "documents where the labeller's echo differed from the text (aligned by diff)": c["documents_with_text_drift"],
             "labeller spans dropped in alignment": c["labeller_spans_dropped_in_alignment"],
             "labeller markup errors (lenient parse)": c["labeller_markup_errors"],
             "labeller outputs cut off at max_tokens": c["truncated_output"],
             "documents with at least one disagreement item": sum(1 for x in cases if A.items_for(
                 x, lab.get(x["id"], {}).get("spans", [])))}
    for k, v in c.items():
        if k.startswith("model:"):
            extra["labeller " + k] = v
    _dump_metrics(m, extra, h.dir / "work" / "metrics_before.json")
    A.write_md(h.agreement_path, h, [("Before review", m, extra)])
    h.round_log("agreement-before", {**extra, **{k: int(v) for k, v in m["total"].items()}})
    return {**extra, **{k: int(v) for k, v in m["total"].items()}}


# ------------------------------------------------------------------ review
def item_text(c: dict, it: dict, n: int) -> str:
    t, g, l = c["text"], it["gold"], it["lab"]
    if it["type"] == "missed":
        return (f'{n}. MISSED BY LABELLER: you marked {g["label"]} "{g["value"][:100]}" (characters {g["start"]}-{g["end"]}; '
                f'context: {VAL.ctx(t, g["start"], g["end"])}); the labeller marked nothing there.')
    if it["type"] == "conflict":
        return (f'{n}. LABEL CONFLICT: you marked {g["label"]} "{g["value"][:100]}" (characters {g["start"]}-{g["end"]}; '
                f'context: {VAL.ctx(t, g["start"], g["end"])}); the labeller marked {l["label"]} "{t[l["start"]:l["end"]][:100]}" there.')
    if it["type"] == "boundary":
        return (f'{n}. BOUNDARY: you marked {g["label"]} "{g["value"][:100]}" (characters {g["start"]}-{g["end"]}; context: '
                f'{VAL.ctx(t, g["start"], g["end"])}); the labeller marked the same label with other boundaries: '
                f'"{t[l["start"]:l["end"]][:100]}" ({l["start"]}-{l["end"]}).')
    hn = it.get("hn")
    return (f'{n}. LABELLER-ONLY: the labeller marked {l["label"]} "{t[l["start"]:l["end"]][:100]}" (characters '
            f'{l["start"]}-{l["end"]}; context: {VAL.ctx(t, l["start"], l["end"])}) where you marked no gold'
            + (f' (it overlaps your hard negative of kind {hn["kind"]})' if hn else "") + ".")


def phase_review(h: C.Half) -> tuple[dict, str | None]:
    entries, profile = load_entries(h), load_profile(h)
    lab = json.loads((h.dir / "work" / "labeller.json").read_text(encoding="utf-8"))
    system = sysmsg(prompts.review_system())
    think = _thinking_of(h, "author")
    jobs, store = [], {}
    for c in F.valid_cases(h):
        L = lab.get(c["id"], {}).get("spans", [])
        items = A.items_for(c, L)
        if not items:
            continue
        texts = [item_text(c, it, i) for i, it in enumerate(items, 1)]
        jobs.append({"case_id": c["id"], "messages": [system, usermsg(prompts.user_review(entries[c["id"]], profile, to_markup(c), texts))],
                     "max_tokens": author_max_tokens(entries[c["id"]], think)})
        store[c["id"]] = {"n_items": len(items), "types": dict(Counter(i["type"] for i in items))}
    (h.dir / "work" / "review_items.json").write_text(json.dumps(store), encoding="utf-8")
    res, stop = runner.run_calls(h, "author", "review", jobs)
    return ingest_review(h), stop


def parse_review(text: str) -> tuple[dict, str | None]:
    dec = {}
    head = text.split("DOCUMENT", 1)[0]
    for line in head.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.)\-]\s*(keep|fix)\b", line, re.I)
        if m:
            dec[int(m.group(1))] = m.group(2).lower()
    doc = None
    if "DOCUMENT" in text:
        tail = text.split("DOCUMENT", 1)[1]
        a = tail.find("<<<")
        b = tail.rfind(">>>")
        if a >= 0 and b > a:
            body = tail[a + 3:b].strip("\n")
            doc = None if body.strip().upper() == "NONE" else body
    return dec, doc


def ingest_review(h: C.Half) -> dict:
    entries, profile, st = load_entries(h), load_profile(h), load_state(h)
    items = json.loads((h.dir / "work" / "review_items.json").read_text(encoding="utf-8"))
    cnt = Counter()
    for cid, meta in items.items():
        r = ali.load_result(h.L, "review", cid)
        if not r or not r.get("ok"):
            cnt["review_request_failed"] += 1
            continue
        if st[cid].get("review", {}).get("applied"):
            cnt["already_applied"] += 1
            continue
        dec, doc = parse_review(r["content"])
        cnt["items_total"] += meta["n_items"]
        cnt["items_decided"] += len(dec)
        cnt["items_keep"] += sum(1 for v in dec.values() if v == "keep")
        cnt["items_fix"] += sum(1 for v in dec.values() if v == "fix")
        rec = st[cid]
        rec["review"] = {"keep": sum(1 for v in dec.values() if v == "keep"), "fix": sum(1 for v in dec.values() if v == "fix"),
                         "applied": False}
        if not any(v == "fix" for v in dec.values()):
            cnt["cases_all_keep"] += 1
            continue
        cnt["cases_with_a_fix"] += 1
        if not doc:
            cnt["fix_without_document"] += 1
            rec["review"]["rejected"] = "no document"
            continue
        parsed = parse_markup(doc)
        e = entries[cid]
        case = VAL.build_case(e, parsed, profile, h.L)
        old = VAL.build_case(e, parse_markup(rec["raw"]), profile, h.L)
        if parsed["plain"] != old["text"]:
            cnt["fix_rejected_text_changed"] += 1
            rec["review"]["rejected"] = "text changed"
            continue
        iss = VAL.entry_issues(e, case, parsed) + VAL.validator_issues([case], profile, h.L).get(cid, [])
        if iss:
            cnt["fix_rejected_validator"] += 1
            rec["review"]["rejected"] = "validator: " + ",".join(sorted({c for c, _ in iss}))
            continue
        same = (case["spans"] == old["spans"] and case["hard_negatives"] == old["hard_negatives"])
        rec["raw_before_review"] = rec["raw"]
        rec["raw"] = doc
        rec["review"]["applied"] = True
        cnt["fix_applied"] += 1
        cnt["fix_applied_no_net_change"] += same
    save_state(h, st)
    h.round_log("review-1", dict(cnt))
    ids = set(json.loads((h.dir / "work" / "label_set.json").read_text(encoding="utf-8")))
    cases = [c for c in F.valid_cases(h) if c["id"] in ids]
    lab = json.loads((h.dir / "work" / "labeller.json").read_text(encoding="utf-8"))
    m2 = A.metrics(cases, lab)
    before, extra_b = _load_metrics(h.dir / "work" / "metrics_before.json")
    extra2 = {"documents compared": len(lab), "review: cases with a disagreement item": len(items),
              "review: items": cnt["items_total"], "review: items decided keep": cnt["items_keep"],
              "review: items decided fix": cnt["items_fix"], "review: cases changed by script": cnt["fix_applied"],
              "review: fixes rejected by the script (text changed / validator / no document)":
                  cnt["fix_rejected_text_changed"] + cnt["fix_rejected_validator"] + cnt["fix_without_document"],
              "review: requests that failed": cnt["review_request_failed"],
              "documents with at least one disagreement item left": sum(1 for x in cases if A.items_for(
                  x, lab.get(x["id"], {}).get("spans", [])))}
    A.write_md(h.agreement_path, h, [("Before review", before, extra_b), ("After review", m2, extra2)])
    out = {**dict(cnt), **{"after_" + k: int(v) for k, v in m2["total"].items()}}
    h.round_log("agreement-after", {**extra2, **{k: int(v) for k, v in m2["total"].items()}})
    return out