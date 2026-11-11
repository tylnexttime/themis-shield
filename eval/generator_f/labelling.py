"""Steps 6 and 7: blind labelling batch + agreement; one review round (author decides, script applies).

    python labelling.py label-submit | label-ingest | review-submit | review-ingest
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

import agreement as A
import common as C
import finalize as F
import prompts
import validate as VAL
from markup import parse_markup, to_markup
from store import WORK, load_entries, load_profile, load_state, save_state

LABEL, REVIEW = "f-label-1", "f-review-1"


def cmd_label_submit() -> int:
    cases = F.valid_cases()
    system = prompts.taxonomy_text()
    reqs = [C.batch_request(c["id"], C.CHECK_MODEL, system, prompts.label_user(c["text"]), 64000, "xhigh")
            for c in cases]
    exp = [int(len(c["text"]) / 3 * 1.5) + 15000 for c in cases]
    C.estimate_and_guard(LABEL, C.CHECK_MODEL, [r["params"] for r in reqs], exp)
    (WORK / "label_set.json").write_text(json.dumps([c["id"] for c in cases]))
    C.submit(LABEL, reqs, C.CHECK_MODEL, "xhigh", "split F blind labelling: taxonomy-only labeller, one request per case",
             record="label-batch-1")
    return 0


def _dump_metrics(m: dict, extra: dict, path) -> None:
    path.write_text(json.dumps({"per": {k: dict(v) for k, v in m["per"].items()}, "total": dict(m["total"]),
                                "extra": extra}), encoding="utf-8")


def _load_metrics(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {"per": {k: Counter(v) for k, v in d["per"].items()}, "total": Counter(d["total"])}, d["extra"]


def cmd_label_ingest() -> int:
    ids = set(json.loads((WORK / "label_set.json").read_text()))
    cases = [c for c in F.valid_cases(statuses=("valid", "dropped")) if c["id"] in ids]
    rows = {r["custom_id"]: r for r in C.read_jsonl(C.ROOT / "results" / f"{LABEL}.jsonl")}
    lab, c = {}, Counter()
    for case in cases:
        r = rows.get(case["id"])
        if not r or r["type"] != "succeeded" or not r.get("text", "").strip():
            c["no_labeller_output"] += 1
            continue
        d = A.labeller_spans(case["text"], r["text"])
        c["truncated_output"] += r.get("stop_reason") == "max_tokens"
        c["documents_with_text_drift"] += d["drift"]
        c["labeller_spans_dropped_in_alignment"] += d["dropped"]
        c["labeller_markup_errors"] += d["errors"]
        lab[case["id"]] = d
    (WORK / "labeller.json").write_text(json.dumps(lab), encoding="utf-8")
    m = A.metrics(cases, lab)
    extra = {"documents compared": len(lab), "documents with no labeller output": c["no_labeller_output"],
             "documents where the labeller's echo differed from the text (aligned by diff)": c["documents_with_text_drift"],
             "labeller spans dropped in alignment": c["labeller_spans_dropped_in_alignment"],
             "labeller markup errors (lenient parse)": c["labeller_markup_errors"],
             "labeller outputs cut off at max_tokens": c["truncated_output"],
             "documents with at least one disagreement item": sum(1 for x in cases if A.items_for(
                 x, lab.get(x["id"], {}).get("spans", [])))}
    _dump_metrics(m, extra, WORK / "metrics_before.json")
    A.write_md(C.ROOT / "AGREEMENT.md", [("Before review", m, extra)])
    C.log_round("agreement-before", {**extra, **{k: int(v) for k, v in m["total"].items()}})
    print(json.dumps(extra))
    print({k: int(v) for k, v in m["total"].items()})
    return 0


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


def cmd_review_submit() -> int:
    entries, profile = load_entries(), load_profile()
    lab = json.loads((WORK / "labeller.json").read_text(encoding="utf-8"))
    system = prompts.author_system(profile)
    reqs, ents, store = [], [], {}
    for c in F.valid_cases():
        L = lab.get(c["id"], {}).get("spans", [])
        items = A.items_for(c, L)
        if not items:
            continue
        lab_case = {"text": c["text"], "hard_negatives": [], "spans": [
            {**l, "indirect": l["kind"] == "indirect", "negative": l["kind"] == "negative"} for l in L]}
        texts = [item_text(c, it, i) for i, it in enumerate(items, 1)]
        reqs.append(C.batch_request(c["id"], C.AUTHOR_MODEL, system,
                                    prompts.user_review(entries[c["id"]], profile, to_markup(c), to_markup(lab_case, False, False),
                                                        texts), 64000, "high"))
        ents.append(entries[c["id"]])
        store[c["id"]] = {"n_items": len(items), "types": dict(Counter(i["type"] for i in items))}
    (WORK / "review_items.json").write_text(json.dumps(store), encoding="utf-8")
    exp = [min(64000, int(e["chars"][1] / 3 * 1.4) + 9000) for e in ents]
    C.estimate_and_guard(REVIEW, C.AUTHOR_MODEL, [r["params"] for r in reqs], exp)
    C.submit(REVIEW, reqs, C.AUTHOR_MODEL, "high", "split F review round: author decides keep/fix per disagreement",
             record="review-batch-1")
    print("review requests:", len(reqs))
    return 0


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


def cmd_review_ingest() -> int:
    entries, profile, st = load_entries(), load_profile(), load_state()
    rows = {r["custom_id"]: r for r in C.read_jsonl(C.ROOT / "results" / f"{REVIEW}.jsonl")}
    items = json.loads((WORK / "review_items.json").read_text(encoding="utf-8"))
    cnt = Counter()
    for cid, meta in items.items():
        r = rows.get(cid)
        if not r or r["type"] != "succeeded":
            cnt["review_request_failed"] += 1
            continue
        dec, doc = parse_review(r["text"])
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
        case = VAL.build_case(e, parsed, profile)
        old = VAL.build_case(e, parse_markup(rec["raw"]), profile)
        if parsed["plain"] != old["text"]:
            cnt["fix_rejected_text_changed"] += 1
            rec["review"]["rejected"] = "text changed"
            continue
        iss = VAL.entry_issues(e, case, parsed) + VAL.validator_issues([case], profile).get(cid, [])
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
    save_state(st)
    C.log_round("review-1", dict(cnt))
    print(json.dumps(dict(cnt)))
    # agreement after review (same labeller output against the reviewed gold)
    ids = set(json.loads((WORK / "label_set.json").read_text()))
    cases = [c for c in F.valid_cases() if c["id"] in ids]
    lab = json.loads((WORK / "labeller.json").read_text(encoding="utf-8"))
    m2 = A.metrics(cases, lab)
    before, extra_b = _load_metrics(WORK / "metrics_before.json")
    extra2 = {"documents compared": len(lab), "review: cases with a disagreement item": len(items),
              "review: items": cnt["items_total"], "review: items decided keep": cnt["items_keep"],
              "review: items decided fix": cnt["items_fix"], "review: cases changed by script": cnt["fix_applied"],
              "review: fixes rejected by the script (text changed / validator / no document)":
                  cnt["fix_rejected_text_changed"] + cnt["fix_rejected_validator"] + cnt["fix_without_document"],
              "documents with at least one disagreement item left": sum(1 for x in cases if A.items_for(
                  x, lab.get(x["id"], {}).get("spans", [])))}
    A.write_md(C.ROOT / "AGREEMENT.md", [("Before review", before, extra_b), ("After review", m2, extra2)])
    print({k: int(v) for k, v in m2["total"].items()})
    return 0


if __name__ == "__main__":
    sys.exit({"label-submit": cmd_label_submit, "label-ingest": cmd_label_ingest, "review-submit": cmd_review_submit,
              "review-ingest": cmd_review_ingest}[sys.argv[1]]())