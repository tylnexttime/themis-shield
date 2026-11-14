"""The second, targeted top-up of one half (the amendment of 13/11/2026 to FROZEN-PLAN-v0.7). Counts only on the console.

Rule (see the amendment): measure the half's failing coverage rows on its current valid cases; plan ceil(2.5 x shortfall) gold
spans of that label or category (at most three per entry), or that many long documents; nothing for rows that pass, no margin
rows; never beyond 88 cases. The new documents get the same single repair round as the planned entries (pointer check ->
repair on validator codes and pointer flags -> pointer recheck of what was repaired); a document still failing is dropped,
never hand-edited. Labelling and one review round touch the new documents only; the first round's results stay as they are.

Phase names in results/, requests/ and usage.jsonl: topup2 (author), t2-pointer, t2-repair, t2-pointer2, t2-label, t2-review.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter

import agreement as A
import common as C
import finalize as F
import llm
import pipeline
import prompts
import runner
import safevalues as sv
import topup as T
import validate as VAL
from markup import parse_markup
from plan_data import COMPAT, SENS_GENRES
from store import load_entries, load_profile, load_state
import check_cases as V  # noqa: E402

MULT2 = 2.5
CEILING = 88
PER_ENTRY = 3  # at most three planned spans of the short row per entry
P_POINTER, P_REPAIR, P_POINTER2, P_LABEL, P_REVIEW = "t2-pointer", "t2-repair", "t2-pointer2", "t2-label", "t2-review"


def work(h, name):
    return h.dir / "work" / name


def new_ids(h) -> list[str]:
    p = work(h, "topup2_ids.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


# ------------------------------------------------------------------ plan
def measure(h):
    profile = load_profile(h)
    a = T.analyse(h, F.valid_cases(h), profile)
    return a, T.shortfall_counts(a, T.Q, (0.40, 0.60))  # the validator's minimums, not the planner's margin targets


def plan_topup2(h) -> dict:
    with T._Lock():  # the shared value registry, as for every plan
        return _plan(h)


def _plan(h) -> dict:
    profile = load_profile(h)
    a, short = measure(h)
    other = {k: v for k, v in short.items() if not (k.startswith(("label ", "sens ")) or k == "long")}
    if other:
        raise SystemExit(f"rows short that this top-up does not cover: {sorted(other)}")
    entries = load_entries(h)
    seed = C.HALVES[h.L]["seed"]
    b = T.Builder(h, entries, a, profile, MULT2, safe=True)
    b.rng = random.Random(seed + 5)
    b.vals = sv.Values(seed + 6, used=b.vals.used)  # a fresh stream over the shared used-set of both halves
    rng = b.rng
    M = lambda k: math.ceil(MULT2 * short.get(k, 0))  # noqa: E731
    planned = {}
    for l in T.NON_SENS + ["PERSON"]:
        need = planned["label " + l] = M("label " + l)
        while need > 0:
            genre = rng.choice([g for g in COMPAT.get(l, SENS_GENRES) if g not in ("csv", "tsv", "markdown table", "clinical note")] or ["report"])
            e = b.blank(genre, rng.choice(["short", "medium"]))
            n = min(PER_ENTRY, need)
            e["labels"][l] = e["labels"].get(l, 0) + n if l != "PERSON" else max(e["labels"]["PERSON"], n + 2)
            need -= n
    mode_pool = ["indirect"] * 8 + ["direct"]
    for l in V.SENSITIVE:
        need = planned["sens " + l] = M("sens " + l)
        while need > 0:
            e = b.blank(rng.choice([g for g in SENS_GENRES if g not in ("csv", "tsv", "markdown table")]), "medium")
            n = min(PER_ENTRY, need)
            for _ in range(n):
                e["sensitive"].append({"category": l, "mode": rng.choice(mode_pool)})
            need -= n
    planned["long documents"] = M("long")
    for _ in range(M("long")):
        b.blank(rng.choice(["report", "minutes", "case note", "chat log"]), "long")
    new = b.finish()
    if a["cases"] + len(new) > CEILING:
        raise SystemExit(f"{a['cases']} valid + {len(new)} entries would pass the {CEILING}-case ceiling")
    planned = {k: v for k, v in planned.items() if v}
    info = {"shortfalls": short, "planned_spans_or_documents": planned, "entries": len(new), "valid_cases_before": a["cases"],
            "multiplier": MULT2, "profiled_entries": sum(1 for e in new if e["org"]["kind"] == "profiled"),
            "seed_choices": seed + 5, "seed_values": seed + 6}
    if new:
        C.write_jsonl(h.plan_topup2, new)
        work(h, "topup2_ids.json").write_text(json.dumps([e["id"] for e in new]), encoding="utf-8")
    h.round_log("topup2-plan", info)
    return info


# ------------------------------------------------------------------ author (pipeline.phase_author, round "topup2")
def step_author(h):
    summ, stop = pipeline.phase_author(h, "topup2")
    return (5 if stop else 0), {"stop": stop, **summ}


# ------------------------------------------------------------------ pointer check and the one repair round
def _pointer(h, name, ids, second):
    st = load_state(h)
    if not ids:
        return {"checked": 0}, None
    jobs = [{"case_id": i, "messages": [pipeline.sysmsg(prompts.POINTER_SYSTEM), pipeline.usermsg(prompts.pointer_user(
        parse_markup(st[i]["raw"], lenient=True)["plain"]))], "max_tokens": 1500} for i in ids]
    res, stop = runner.run_calls(h, "pointer", name, jobs, temp_key="label_temperature", temp_default=0)
    return pipeline.ingest_pointer(h, name, ids, second=second), stop


def step_pointer(h):
    st = load_state(h)
    ids = sorted(i for i in new_ids(h) if st[i]["raw"] and st[i]["status"] != "dropped")
    out, stop = _pointer(h, P_POINTER, ids, False)
    return (5 if stop else 0), {"stop": stop, **out}


def step_repair(h):
    entries, profile, st = load_entries(h), load_profile(h), load_state(h)
    sys_prof, sys_oth = pipeline.sysmsg(prompts.repair_system(profile)), pipeline.sysmsg(prompts.repair_system())
    think = pipeline._thinking_of(h, "author")
    jobs = []
    for cid in sorted(new_ids(h)):
        rec = st[cid]
        if rec["status"] == "dropped" or rec.get("round") == P_REPAIR:
            continue
        probs = []
        if rec["issues"]:
            case, _ = VAL.parse_output(entries[cid], rec["raw"], profile, h.L) if rec["raw"] else ({"text": ""}, None)
            probs += VAL.problem_lines(case, [tuple(x) for x in rec["issues"]])
        probs += pipeline.pointer_line(rec)
        if not probs:
            continue
        prev = rec["raw"] or "(unavailable: the previous output was empty or cut off; write the document afresh)"
        needs_profile = entries[cid]["org"]["kind"] == "profiled" and any(c == "PROFILE_HN" for c, _ in rec["issues"])
        jobs.append({"case_id": cid, "messages": [sys_prof if needs_profile else sys_oth,
                                                  pipeline.usermsg(prompts.user_repair(entries[cid], profile, prev, probs))],
                     "max_tokens": pipeline.author_max_tokens(entries[cid], think)})
    if not jobs:
        h.round_log("ingest-" + P_REPAIR, {"round": P_REPAIR, "requests": 0})
        return 0, {"requests": 0}
    ids = [j["case_id"] for j in jobs]
    work(h, "t2_repair_ids.json").write_text(json.dumps(ids), encoding="utf-8")
    res, stop = runner.run_calls(h, "author", P_REPAIR, jobs)
    summ = pipeline.ingest_round(h, P_REPAIR, ids)
    return (5 if stop else 0), {"stop": stop, **summ}


def step_pointer2(h):
    st = load_state(h)
    ids = sorted(i for i in new_ids(h) if st[i]["status"] == "valid" and
                 (st[i].get("round") == P_REPAIR or (st[i].get("pointer") or {}).get("flag")))
    out, stop = _pointer(h, P_POINTER2, ids, True)
    return (5 if stop else 0), {"stop": stop, **out}


def valid_new(h) -> list[str]:
    st = load_state(h)
    return sorted(i for i in new_ids(h) if st[i]["status"] == "valid")


# ------------------------------------------------------------------ blind labelling of the new documents only
def step_label(h):
    cases = {c["id"]: c for c in F.valid_cases(h)}
    ids = valid_new(h)
    work(h, "label_set_t2.json").write_text(json.dumps(ids), encoding="utf-8")
    system = pipeline.sysmsg(prompts.label_system())
    jobs = [{"case_id": i, "messages": [system, pipeline.usermsg(prompts.label_user(cases[i]["text"]))],
             "max_tokens": pipeline.label_max_tokens(cases[i]["text"])} for i in ids]
    res, stop = runner.run_calls(h, "labeller", P_LABEL, jobs, temp_key="label_temperature", temp_default=0)
    return (5 if stop else 0), {"stop": stop, **ingest_label_new(h, ids)}


def ingest_label_new(h, ids) -> dict:
    cases = {c["id"]: c for c in F.valid_cases(h)}
    lab_new, c = {}, Counter()
    for i in ids:
        r = llm.load_result(h.L, P_LABEL, i)
        if not r or not r.get("ok") or not r.get("content", "").strip():
            c["no_labeller_output"] += 1
            continue
        d = A.labeller_spans(cases[i]["text"], r["content"])
        c["truncated_output"] += r.get("finish_reason") == "length"
        c["documents_with_text_drift"] += d["drift"]
        c["labeller_spans_dropped_in_alignment"] += d["dropped"]
        c["labeller_markup_errors"] += d["errors"]
        c["model:" + str(r.get("model"))] += 1
        lab_new[i] = d
    lab = json.loads(work(h, "labeller.json").read_text(encoding="utf-8"))
    lab.update(lab_new)  # first-round entries are not touched (their ids are not in lab_new)
    work(h, "labeller.json").write_text(json.dumps(lab), encoding="utf-8")
    if not work(h, "metrics_before_t2.json").exists():  # before review: written once, never recomputed after a review edit
        sub = [cases[i] for i in ids]
        m = A.metrics(sub, lab_new)
        extra = {"documents compared": len(lab_new), "documents with no labeller output": c["no_labeller_output"],
                 "documents where the labeller's echo differed from the text (aligned by diff)": c["documents_with_text_drift"],
                 "labeller spans dropped in alignment": c["labeller_spans_dropped_in_alignment"],
                 "labeller markup errors (lenient parse)": c["labeller_markup_errors"],
                 "labeller outputs cut off at max_tokens": c["truncated_output"],
                 "documents with at least one disagreement item": sum(1 for x in sub if A.items_for(
                     x, lab_new.get(x["id"], {}).get("spans", [])))}
        for k, v in c.items():
            if k.startswith("model:"):
                extra["labeller " + k] = v
        pipeline._dump_metrics(m, extra, work(h, "metrics_before_t2.json"))
    return {"documents compared": len(lab_new), "no_labeller_output": c["no_labeller_output"],
            "markup_errors": c["labeller_markup_errors"], "spans_dropped_in_alignment": c["labeller_spans_dropped_in_alignment"]}


# ------------------------------------------------------------------ one review round of the new documents only
def step_review(h):
    entries = load_entries(h)
    lab = json.loads(work(h, "labeller.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in F.valid_cases(h)}
    ids = json.loads(work(h, "label_set_t2.json").read_text(encoding="utf-8"))
    system = pipeline.sysmsg(prompts.review_system())
    jobs, store = [], {}
    for i in ids:
        c, L = cases[i], lab.get(i, {}).get("spans", [])
        items = A.items_for(c, L)
        if not items:
            continue
        texts = [pipeline.item_text(c, it, n) for n, it in enumerate(items, 1)]
        jobs.append({"case_id": i, "messages": [system, pipeline.usermsg(prompts.user_review(entries[i], texts))],
                     "max_tokens": max(600, 40 * len(items) + 300)})
        store[i] = {"n_items": len(items), "types": dict(Counter(it["type"] for it in items))}
    cap = C.ALI_CAPS.get(h.models["author"])
    skipped = 0
    if cap:  # budget governor as in the first round
        room = max(0, cap - llm.totals()[0].get(h.models["author"], 0) - 8000)
        keep = max(0, room // 2900)
        if len(jobs) > keep:
            order = sorted(jobs, key=lambda j: (-store[j["case_id"]]["n_items"], j["case_id"]))[:keep]
            kept = {j["case_id"] for j in order}
            skipped = len(jobs) - len(kept)
            jobs = [j for j in jobs if j["case_id"] in kept]
            store = {k: v for k, v in store.items() if k in kept}
    h.round_log("review-2-budget", {"review_requests": len(jobs), "skipped_for_budget": skipped})
    work(h, "review_items_t2.json").write_text(json.dumps(store), encoding="utf-8")
    res, stop = runner.run_calls(h, "author", P_REVIEW, jobs)
    out = pipeline.ingest_review(h, P_REVIEW, "review_items_t2.json", "review-2", agreement=False)
    return (5 if stop else 0), {"stop": stop, **out}


# ------------------------------------------------------------------ agreement over the whole half
def final_agreement(h) -> dict:
    """AGREEMENT-<X>.md over every labelled document of the half: before review = the first round's counts plus the new
    documents' counts (both measured before their review); after review = the final gold of all of them. Numbers only."""
    lab = json.loads(work(h, "labeller.json").read_text(encoding="utf-8"))
    ids = set(json.loads(work(h, "label_set.json").read_text(encoding="utf-8"))) | set(json.loads(work(h, "label_set_t2.json").read_text(encoding="utf-8")))
    cases = [c for c in F.valid_cases(h) if c["id"] in ids]
    m_after = A.metrics(cases, lab)
    mb1, eb1 = pipeline._load_metrics(work(h, "metrics_before.json"))
    mb2, eb2 = pipeline._load_metrics(work(h, "metrics_before_t2.json"))
    m_before = {"per": {l: Counter(mb1["per"].get(l, {})) + Counter(mb2["per"].get(l, {})) for l in A.LABELS},
                "total": Counter(mb1["total"]) + Counter(mb2["total"])}
    extra_b = {k: (eb1.get(k, 0) + eb2.get(k, 0)) for k in eb1} | {k: eb2[k] for k in eb2 if k not in eb1}
    a1 = C.jload(h.dir / "rounds" / "agreement-after.json", {})  # first round's review counts (not rewritten by this round)
    r2 = C.jload(h.dir / "rounds" / "review-2.json", {})
    items2 = json.loads(work(h, "review_items_t2.json").read_text(encoding="utf-8"))
    g = lambda k: a1.get(k, 0)  # noqa: E731
    extra_a = {"documents compared": len(lab),
               "review: cases with a disagreement item": g("review: cases with a disagreement item") + len(items2),
               "review: items": g("review: items") + r2.get("items_total", 0),
               "review: items decided keep": g("review: items decided keep") + r2.get("items_keep", 0),
               "review: items decided fix": g("review: items decided fix") + r2.get("items_fix", 0),
               "review: cases changed by script": g("review: cases changed by script") + r2.get("fix_applied", 0),
               "review: fixes rejected by the script (overlap / text changed / validator / quota)":
                   g("review: fixes rejected by the script (overlap / text changed / validator / quota)") + sum(
                       r2.get(k, 0) for k in ("fix_rejected_overlap", "fix_rejected_text_changed", "fix_rejected_validator", "fix_rejected_quota")),
               "review: requests that failed": g("review: requests that failed") + r2.get("review_request_failed", 0),
               "documents with at least one disagreement item left": sum(1 for x in cases if A.items_for(
                   x, lab.get(x["id"], {}).get("spans", [])))}
    A.write_md(h.agreement_path, h, [("Before review (first round and second top-up documents, each before its own review)", m_before, extra_b),
                                     ("After review (final gold of every labelled document)", m_after, extra_a)])
    out = {"before": {k: int(v) for k, v in m_before["total"].items()}, "after": {k: int(v) for k, v in m_after["total"].items()},
           "extra_after": extra_a}
    h.round_log("agreement-final", out)
    return out