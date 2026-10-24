#!/usr/bin/env python3
"""Score de-identification predictions against the themis-shield synthetic evaluation set.

    python score.py predictions.jsonl [--split dev|test|all] [--labelset native] [--json report.json]
    python score.py --self-test

PREDICTION FORMAT (one JSON object per line, one line per case; a missing line is scored as "found nothing"):

    {"id": "A-EM-001",
     "spans": [{"start": 26, "end": 43, "label": "PERSON"}, ...],      # character offsets into the case text,
                                                                        # Python code points, end exclusive
     "case_level": {"contains_pi": true, "sensitive": ["health"]},      # optional
     "decision": "RELEASE"}                                             # optional: RELEASE | ASK | BLOCK

Labels are mapped to gold labels through LABELSETS (native, openai_pf, pii_tracer, presidio). One candidate label may
stand for several gold labels; a prediction is "typed-correct" when the gold label is in its mapped set.

HEADLINE NUMBERS
  R_full    full-coverage recall: a gold span counts as protected only if EVERY letter and digit in it is covered by
            some prediction (any label). This is what a redacting gate needs.
  partial   gold spans touched but not fully covered (something still leaks).
  leak      gold spans with no overlapping prediction at all.
  released leaks   gold spans not fully covered in cases whose decision is RELEASE (cases without a decision are
            treated as RELEASE unless --default-decision says otherwise). ASK and BLOCK hold a case back.
  hard-negative over-redaction   share of institutional / public look-alikes touched by any prediction.
Also: typed overlap and exact recall, character recall, typed and any-label precision, case-level contains_pi
recall and false-positive rate, per-category sensitive recall, and friction (ASK or BLOCK on cases with no personal
information). Python 3.12 standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPLIT_FILES = {"dev": [HERE / "cases" / "dev.jsonl"], "test": [HERE / "cases" / "test.jsonl"],
               "all": [HERE / "cases" / "dev.jsonl", HERE / "cases" / "test.jsonl"]}

GOLD_LABELS = (
    "PERSON", "USERNAME", "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT",
    "BANK_ACCOUNT", "PAYMENT_CARD", "EMAIL", "PHONE", "ADDRESS", "DATE_OF_BIRTH", "OTHER_DATE", "URL_PERSONAL",
    "SECRET", "QUASI_ID", "HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL",
    "FAMILY_VIOLENCE", "UNION",
)
SENSITIVE = {"HEALTH": "health", "DISABILITY": "disability", "ETHNICITY": "ethnicity", "SEXUALITY": "sexuality",
             "RELIGION": "religion", "CRIMINAL": "criminal", "FAMILY_VIOLENCE": "family_violence", "UNION": "union"}
CATEGORIES = tuple(SENSITIVE.values())
IDS = ["INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT", "BANK_ACCOUNT", "PAYMENT_CARD"]
SENS = list(SENSITIVE)

# candidate label -> gold labels it may stand for; the FIRST is the "primary" label an unmatched prediction is
# charged to in per-label precision. Keep in step with taxonomy.md section 5.
LABELSETS: dict[str, dict[str, list[str]]] = {
    "native": {L: [L] for L in GOLD_LABELS},
    "openai_pf": {
        "private_person": ["PERSON", "USERNAME"], "private_date": ["DATE_OF_BIRTH", "OTHER_DATE"],
        "private_url": ["URL_PERSONAL"], "private_address": ["ADDRESS"], "account_number": IDS[:],
        "private_email": ["EMAIL"], "private_phone": ["PHONE"], "secret": ["SECRET"],
    },
    "presidio": {
        "PERSON": ["PERSON", "USERNAME"], "EMAIL_ADDRESS": ["EMAIL"], "PHONE_NUMBER": ["PHONE"], "LOCATION": ["ADDRESS"],
        "DATE_TIME": ["DATE_OF_BIRTH", "OTHER_DATE"], "URL": ["URL_PERSONAL"], "AU_TFN": ["TFN"],
        "AU_MEDICARE": ["MEDICARE"], "AU_ABN": ["ABN"], "AU_ACN": ["ABN", "TFN"], "CREDIT_CARD": ["PAYMENT_CARD"],
        "IBAN_CODE": ["BANK_ACCOUNT"], "US_BANK_NUMBER": ["BANK_ACCOUNT"], "US_SSN": ["NATIONAL_ID"],
        "UK_NINO": ["NATIONAL_ID"], "US_DRIVER_LICENSE": ["DRIVER_LICENCE"], "US_PASSPORT": ["PASSPORT"],
        "NRP": ["ETHNICITY", "RELIGION"], "MEDICAL_LICENSE": IDS[:],
    },
}
LABELSETS["pii_tracer"] = {**LABELSETS["openai_pf"], "other_pii": ["QUASI_ID"] + SENS + IDS}


def map_label(labelset, label):
    t = LABELSETS[labelset]
    for k in (label, label.lower(), label.upper()):
        if k in t:
            return t[k]
    return []


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def ratio(n, d):
    return None if d == 0 else n / d


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_gold(split, gold_path=None):
    if gold_path:
        rows = load_jsonl(gold_path)
        return rows if split == "all" else [c for c in rows if c["split"] == split]
    return [c for p in SPLIT_FILES[split] for c in load_jsonl(p)]


def coverage(g, preds, text):
    cov = set()
    for p in preds:
        if overlaps(p, g):
            cov.update(range(max(p["start"], g["start"]), min(p["end"], g["end"])))
    need = {i for i in range(g["start"], g["end"]) if text[i].isalnum()}
    return cov, need


# --------------------------------------------------------------------------------------------------------------------
def score(gold, preds_by_id, labelset="native", default_decision="RELEASE", sens_threshold=None):
    lab = defaultdict(Counter)
    leaks, partials, released, warn = defaultdict(list), defaultdict(list), [], []
    case, hn_tot, hn_hit, unmapped = Counter(), Counter(), Counter(), Counter()
    decisions, by_gen = Counter(), defaultdict(Counter)
    for c in gold:
        text, G = c["text"], c["spans"]
        rec = preds_by_id.get(c["id"], {})
        P = []
        for p in rec.get("spans", []):
            if not (isinstance(p.get("start"), int) and isinstance(p.get("end"), int) and 0 <= p["start"] < p["end"] <= len(text)):
                warn.append(f"{c['id']}: invalid span skipped {p}")
                continue
            m = map_label(labelset, str(p.get("label", "")))
            if not m:
                unmapped[str(p.get("label", ""))] += 1
            P.append({"start": p["start"], "end": p["end"], "map": m})
        dec = str(rec.get("decision") or default_decision).upper()
        if dec not in ("RELEASE", "ASK", "BLOCK"):
            warn.append(f"{c['id']}: unknown decision {dec!r}, treated as RELEASE")
            dec = "RELEASE"
        decisions[dec] += 1
        # recall side
        for g in G:
            L = g["label"]
            ov = [p for p in P if overlaps(p, g)]
            cov, need = coverage(g, P, text)
            full = need <= cov
            d = lab[L]
            d["gold"] += 1
            d["any"] += bool(ov)
            d["full"] += full
            d["typed"] += any(L in p["map"] for p in ov)
            d["exact"] += any(L in p["map"] and (p["start"], p["end"]) == (g["start"], g["end"]) for p in ov)
            d["chars"] += len(need)
            d["chars_cov"] += len(need & cov)
            by_gen[c.get("generator", "?")]["gold"] += 1
            by_gen[c.get("generator", "?")]["full"] += full
            item = {"id": c["id"], "start": g["start"], "end": g["end"], "value": g["value"]}
            if not ov:
                leaks[L].append(item)
            elif not full:
                partials[L].append({**item, "uncovered": "".join(text[i] if i in need - cov else "_"
                                                                   for i in range(g["start"], g["end"]))})
            if not full and dec == "RELEASE":
                released.append({**item, "label": L, "genre": c["genre"]})
        # precision side
        for p in P:
            cands = [g for g in G if overlaps(p, g) and g["label"] in p["map"]]
            exact = [g for g in cands if (g["start"], g["end"]) == (p["start"], p["end"])]
            attr = (exact or sorted(cands, key=lambda g: -(min(p["end"], g["end"]) - max(p["start"], g["start"]))) or
                    [{"label": p["map"][0] if p["map"] else "UNMAPPED"}])[0]["label"]
            lab[attr]["pred"] += 1
            lab[attr]["pred_typed"] += bool(cands)
            lab[attr]["pred_exact"] += bool(exact)
            case["pred"] += 1
            case["pred_any"] += any(overlaps(p, g) for g in G)
        # hard negatives
        for h in c.get("hard_negatives", []):
            hn_tot[h["kind"]] += 1
            hn_hit[h["kind"]] += any(overlaps(p, h) for p in P)
        # case level and friction
        cl = rec.get("case_level") or {}
        pred_pi = cl["contains_pi"] if "contains_pi" in cl else bool(P)
        has_pi = c["case_level"]["contains_pi"]
        case["pos" if has_pi else "neg"] += 1
        case["pos_hit" if has_pi else "neg_flag"] += bool(pred_pi)
        if not has_pi:
            case["neg_friction"] += dec != "RELEASE"
            case["neg_redacted"] += bool(P)
        pred_cats = set(cl.get("sensitive", []))
        for p in P:
            for L in p["map"]:
                if L in SENSITIVE and (len(p["map"]) == 1 or any(g["label"] == L and overlaps(p, g) for g in G)):
                    pred_cats.add(SENSITIVE[L])
        sscore = cl.get("sensitivity")
        any_pred = bool(pred_cats) or (sens_threshold is not None and sscore is not None and sscore >= sens_threshold)
        gold_cats = set(c["case_level"]["sensitive"])
        for k in gold_cats:
            case[f"cat_{k}"] += 1
            case[f"cat_{k}_hit"] += k in pred_cats
        case["sens_pos" if gold_cats else "sens_neg"] += 1
        case["sens_pos_hit" if gold_cats else "sens_neg_flag"] += any_pred
        if c.get("injection"):
            case["inj"] += 1
            case["inj_released_leaks"] += sum(1 for r in released if r["id"] == c["id"])
    return report(gold, lab, leaks, partials, released, case, hn_tot, hn_hit, unmapped, decisions, by_gen, warn, labelset)


def report(gold, lab, leaks, partials, released, case, hn_tot, hn_hit, unmapped, decisions, by_gen, warn, labelset):
    rows, tot = {}, Counter()
    for L in list(GOLD_LABELS) + ["UNMAPPED"]:
        d = lab.get(L)
        if not d:
            continue
        tot.update(d)
        rows[L] = {"gold": d["gold"], "recall_full": ratio(d["full"], d["gold"]), "recall_any": ratio(d["any"], d["gold"]),
                   "recall_typed": ratio(d["typed"], d["gold"]), "recall_exact": ratio(d["exact"], d["gold"]),
                   "char_recall": ratio(d["chars_cov"], d["chars"]), "partial": len(partials.get(L, [])),
                   "leak": len(leaks.get(L, [])), "pred": d["pred"], "precision_typed": ratio(d["pred_typed"], d["pred"]),
                   "precision_exact": ratio(d["pred_exact"], d["pred"])}
    overall = {"gold": tot["gold"], "recall_full": ratio(tot["full"], tot["gold"]), "recall_any": ratio(tot["any"], tot["gold"]),
               "recall_typed": ratio(tot["typed"], tot["gold"]), "recall_exact": ratio(tot["exact"], tot["gold"]),
               "char_recall": ratio(tot["chars_cov"], tot["chars"]), "partial": sum(map(len, partials.values())),
               "leak": sum(map(len, leaks.values())), "pred": tot["pred"],
               "precision_typed": ratio(tot["pred_typed"], tot["pred"]), "precision_exact": ratio(tot["pred_exact"], tot["pred"]),
               "precision_any_label": ratio(case["pred_any"], case["pred"])}
    return {
        "labelset": labelset, "cases": len(gold), "per_label": rows, "overall": overall,
        "by_generator": {g: {"gold": v["gold"], "recall_full": ratio(v["full"], v["gold"])} for g, v in sorted(by_gen.items())},
        "gate": {"decisions": dict(decisions), "released_leaks": len(released),
                 "released_leaks_by_label": dict(Counter(r["label"] for r in released)),
                 "held_back_unprotected_spans": overall["gold"] - (tot["full"]) - len(released),
                 "friction_on_cases_without_pi": ratio(case["neg_friction"], case["neg"]),
                 "injection_cases": case["inj"], "injection_released_leaks": case["inj_released_leaks"],
                 "released_leak_list": released},
        "case_level": {"contains_pi_recall": ratio(case["pos_hit"], case["pos"]),
                       "contains_pi_false_positive_rate": ratio(case["neg_flag"], case["neg"]),
                       "positives": case["pos"], "negatives": case["neg"],
                       "negatives_with_any_redaction": case["neg_redacted"],
                       "any_sensitive_recall": ratio(case["sens_pos_hit"], case["sens_pos"]),
                       "any_sensitive_false_positive_rate": ratio(case["sens_neg_flag"], case["sens_neg"]),
                       "sensitive_recall": {k: ratio(case[f"cat_{k}_hit"], case[f"cat_{k}"]) for k in CATEGORIES},
                       "sensitive_support": {k: case[f"cat_{k}"] for k in CATEGORIES}},
        "hard_negatives": {"over_redaction_rate": ratio(sum(hn_hit.values()), sum(hn_tot.values())),
                           "total": sum(hn_tot.values()), "hit": sum(hn_hit.values()),
                           "by_kind": {k: {"total": hn_tot[k], "hit": hn_hit[k]} for k in sorted(hn_tot)}},
        "leaks": {L: v for L, v in leaks.items() if v}, "partials": {L: v for L, v in partials.items() if v},
        "unmapped_labels": dict(unmapped), "warnings": warn,
    }


def f(x):
    return "  n/a" if x is None else f"{x:5.3f}"


def print_report(rep, show=8):
    o = rep["overall"]
    print(f"labelset={rep['labelset']}  cases={rep['cases']}  gold spans={o['gold']}")
    hdr = f"{'label':16}{'gold':>5}{'R_full':>8}{'R_any':>7}{'R_typ':>7}{'R_ex':>7}{'charR':>7}{'part':>6}{'leak':>6}{'pred':>6}{'P_typ':>7}{'P_ex':>7}"
    print(hdr + "\n" + "-" * len(hdr))
    for L, r in list(rep["per_label"].items()) + [("OVERALL", o)]:
        print(f"{L:16}{r['gold']:5d}{f(r['recall_full']):>8}{f(r['recall_any']):>7}{f(r['recall_typed']):>7}{f(r['recall_exact']):>7}"
              f"{f(r['char_recall']):>7}{r['partial']:6d}{r['leak']:6d}{r['pred']:6d}{f(r['precision_typed']):>7}{f(r['precision_exact']):>7}")
    print(f"\nprecision, any label: {f(o['precision_any_label'])}   by generator R_full: "
          + ", ".join(f"{g} {f(v['recall_full'])}" for g, v in rep["by_generator"].items()))
    g = rep["gate"]
    print(f"\nGATE  decisions {g['decisions']}  RELEASED LEAKS {g['released_leaks']} {g['released_leaks_by_label']}")
    print(f"      friction on cases without PI {f(g['friction_on_cases_without_pi'])}; injection cases {g['injection_cases']}, "
          f"released leaks in them {g['injection_released_leaks']}")
    cl = rep["case_level"]
    print(f"\nCASE  contains_pi recall {f(cl['contains_pi_recall'])} ({cl['positives']} pos), false-positive rate "
          f"{f(cl['contains_pi_false_positive_rate'])} ({cl['negatives']} neg); any-sensitive recall {f(cl['any_sensitive_recall'])}")
    print("      " + ", ".join(f"{k} {f(v)}/{cl['sensitive_support'][k]}" for k, v in cl["sensitive_recall"].items()))
    hn = rep["hard_negatives"]
    print(f"\nHARD NEGATIVES over-redaction {f(hn['over_redaction_rate'])} ({hn['hit']}/{hn['total']})"
          + "".join(f"\n      {k}: {v['hit']}/{v['total']}" for k, v in hn["by_kind"].items() if v["hit"]))
    for name in ("leaks", "partials"):
        n = sum(map(len, rep[name].values()))
        print(f"\n{name.upper()}: {n}")
        for L, items in rep[name].items():
            print(f"  {L} ({len(items)})")
            for it in items[:show]:
                print(f"    {it['id']} [{it['start']}:{it['end']}] {it['value'][:60]!r}")
    if rep["unmapped_labels"]:
        print(f"\nunmapped labels (count only in any-label metrics): {rep['unmapped_labels']}")
    if rep["warnings"]:
        print(f"\n{len(rep['warnings'])} warnings; first: {rep['warnings'][0]}")


# --------------------------------------------------------------------------------------------------------------------
def self_test():
    gold = load_gold("all")
    fails = []

    def check(name, cond):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    n_spans = sum(len(c["spans"]) for c in gold)
    print(f"self-test on {len(gold)} cases, {n_spans} gold spans")
    print("1. gold scored against gold (native labels, decision RELEASE)")
    rep = score(gold, {c["id"]: {"spans": c["spans"], "case_level": c["case_level"], "decision": "RELEASE"} for c in gold})
    o = rep["overall"]
    check("R_full, R_any, typed, exact, char recall and precision all 1.0",
          all(o[k] == 1.0 for k in ("recall_full", "recall_any", "recall_typed", "recall_exact", "char_recall",
                                    "precision_typed", "precision_exact", "precision_any_label")))
    check("every label row is perfect", all(r["recall_full"] == 1.0 and r["precision_exact"] == 1.0 for r in rep["per_label"].values()))
    check("no leaks, no partials, no released leaks", o["leak"] == 0 and o["partial"] == 0 and rep["gate"]["released_leaks"] == 0)
    check("no hard negative touched", rep["hard_negatives"]["hit"] == 0)
    cl = rep["case_level"]
    check("case level perfect", cl["contains_pi_recall"] == 1.0 and cl["contains_pi_false_positive_rate"] == 0.0
          and all(v == 1.0 for v in cl["sensitive_recall"].values() if v is not None))
    print("2. empty predictions (no line for any case)")
    rep = score(gold, {})
    o = rep["overall"]
    check("every recall 0.0", all(o[k] == 0.0 for k in ("recall_full", "recall_any", "recall_typed", "recall_exact", "char_recall")))
    check("leak list holds every gold span", o["leak"] == n_spans and o["partial"] == 0)
    check("every gold span is a released leak (default decision RELEASE)", rep["gate"]["released_leaks"] == n_spans)
    check("precision undefined", o["precision_typed"] is None)
    print("3. empty predictions but every case BLOCKed")
    rep = score(gold, {c["id"]: {"spans": [], "decision": "BLOCK"} for c in gold})
    check("zero released leaks, friction 1.0 on cases without PI",
          rep["gate"]["released_leaks"] == 0 and rep["gate"]["friction_on_cases_without_pi"] == 1.0)
    print("4. every gold span shortened by one character")
    rep = score(gold, {c["id"]: {"spans": [dict(s, end=s["end"] - 1) for s in c["spans"]]} for c in gold})
    o = rep["overall"]
    check("R_any 1.0, R_exact 0.0, R_full below 1.0, partials reported",
          o["recall_any"] == 1.0 and o["recall_exact"] == 0.0 and o["recall_full"] < 1.0 and o["partial"] > 0)
    print("5. gold relabelled into pii_tracer labels")
    rev = {}
    for cand, golds in LABELSETS["pii_tracer"].items():
        for g in golds:
            rev.setdefault(g, cand)
    check("every gold label has a pii_tracer label", all(L in rev for L in GOLD_LABELS))
    rep = score(gold, {c["id"]: {"spans": [dict(s, label=rev[s["label"]]) for s in c["spans"]]} for c in gold}, "pii_tracer")
    check("typed recall 1.0 through the mapping", rep["overall"]["recall_typed"] == 1.0)
    print("6. wrong label on every span")
    rep = score(gold, {c["id"]: {"spans": [dict(s, label="EMAIL" if s["label"] != "EMAIL" else "PERSON") for s in c["spans"]]}
                       for c in gold})
    check("R_full 1.0 but typed recall 0.0", rep["overall"]["recall_full"] == 1.0 and rep["overall"]["recall_typed"] == 0.0)
    print("7. a prediction on every hard negative only")
    rep = score(gold, {c["id"]: {"spans": [dict(h, label="PERSON") for h in c["hard_negatives"]]} for c in gold})
    check("over-redaction 1.0, R_full 0.0", rep["hard_negatives"]["over_redaction_rate"] == 1.0 and rep["overall"]["recall_full"] == 0.0)
    print("8. overlap edge cases")
    check("touching spans do not overlap", not overlaps({"start": 0, "end": 5}, {"start": 5, "end": 9}))
    check("one shared character overlaps", overlaps({"start": 0, "end": 6}, {"start": 5, "end": 9}))
    print("PASS" if not fails else f"FAILED ({len(fails)})")
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("predictions", nargs="?", type=Path)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--gold", type=Path, default=None, help="score against one JSONL file instead of cases/<split>.jsonl")
    ap.add_argument("--labelset", choices=sorted(LABELSETS), default="native")
    ap.add_argument("--default-decision", choices=["RELEASE", "ASK", "BLOCK"], default="RELEASE",
                    help="decision for cases whose prediction has none (default RELEASE: the strictest reading)")
    ap.add_argument("--sens-threshold", type=float, default=None, help="treat case_level.sensitivity >= this as 'sensitive'")
    ap.add_argument("--show", type=int, default=8, help="leaks and partials printed per label")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())
    if not a.predictions:
        ap.error("predictions file required (or --self-test)")
    gold = load_gold(a.split, a.gold)
    preds = {r["id"]: r for r in load_jsonl(a.predictions)}
    missing = len({c["id"] for c in gold} - set(preds))
    if missing:
        print(f"note: {missing} cases have no prediction line; scored as empty with decision {a.default_decision}")
    rep = score(gold, preds, a.labelset, a.default_decision, a.sens_threshold)
    print_report(rep, a.show)
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nfull report: {a.json}")


if __name__ == "__main__":
    main()
