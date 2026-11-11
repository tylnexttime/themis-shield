"""Step 1: plan.py (deterministic, seed fixed). Writes plan.jsonl (240 specifications, ids FO-001...) and
plan.summary.json (counts only). The author invents names, places and prose; the plan fixes everything else.

    python plan.py            write the plan
    python plan.py --check    rebuild in memory and confirm byte-identical output
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

from common import ROOT, jline, sha256_file
import safevalues as sv
from plan_data import *  # noqa: F401,F403


def pick(rng, weighted):
    kinds, w = zip(*weighted)
    return rng.choices(kinds, w)[0]


def sample_kinds(rng, weighted, k):
    out, pool = [], list(weighted)
    while pool and len(out) < k:
        kind = pick(rng, pool)
        out.append(kind)
        pool = [(a, b) for a, b in pool if a != kind]
    return out


def build_plan() -> list[dict]:
    rng = random.Random(SEED)
    vals = sv.Values(SEED + 1)
    # ---- 1. entries, bands, long documents
    entries = []
    for genre, total, clean in RECIPE:
        for i in range(total):
            entries.append({"genre": genre, "clean": i < clean})
    rng.shuffle(entries)
    for i, e in enumerate(entries, 1):
        e["id"] = f"FO-{i:03d}"
        e["band"] = rng.choices(["short", "medium", "extended"], BAND_W[e["genre"]])[0]
    for genre, clean, n in LONG_PICKS:
        pool = [e for e in entries if e["genre"] == genre and e["clean"] == clean and e["band"] != "long"]
        for e in rng.sample(pool, n):
            e["band"] = "long"
    for e in entries:
        e["chars"] = list(BANDS[e["band"]])
        e["long"] = e["band"] == "long"
    # ---- 2. organisation: 120 profiled (stratified by genre), the rest spread over 12 other workplaces
    elig = [e for e in entries if e["genre"] not in OTHER_ONLY]
    by_genre: dict[str, list] = {}
    for e in elig:
        by_genre.setdefault(e["genre"], []).append(e)
    chosen: list[dict] = []
    for g, lst in by_genre.items():
        chosen += rng.sample(lst, round(N_PROFILED / len(elig) * len(lst)))
    ids_chosen = {e["id"] for e in chosen}
    rest = [e for e in elig if e["id"] not in ids_chosen]
    while len(ids_chosen) < N_PROFILED:
        ids_chosen.add(rest.pop(rng.randrange(len(rest)))["id"])
    while len(ids_chosen) > N_PROFILED:
        ids_chosen.discard(rng.choice(sorted(ids_chosen)))
    others = [e for e in entries if e["id"] not in ids_chosen]
    rng.shuffle(others)
    for e in entries:
        if e["id"] in ids_chosen:
            e["org"] = {"kind": "profiled"}
    for i, e in enumerate(others):
        k, sector, where = OTHERS[i % len(OTHERS)]
        e["org"] = {"kind": "other", "k": k, "sector": sector, "where": where}
    for e in entries:
        e.update({"labels": {}, "sensitive": [], "hn_kinds": [], "lookalike": False, "trap": False,
                  "duration_trap": False, "quasi_table": False, "roster_table": False, "injection": None,
                  "hard": None, "scripts": [], "clean_focus": "", "values": {}, "_load": 0, "_cap": CAP[e["band"]]})
    pi = [e for e in entries if not e["clean"]]
    # ---- 3. tables: all but two personal-data tables are anonymised tables with quasi-identifiers
    tab_pi = [e for e in pi if e["genre"] in TABLES]
    rng.shuffle(tab_pi)
    for e in tab_pi[:2]:
        e["roster_table"] = True
    for e in tab_pi[2:]:
        e["quasi_table"] = True
        n = rng.randint(2, 4)
        e["labels"]["QUASI_ID"] = n
        e["_load"] += n

    def allowed(e, label):
        if e["quasi_table"]:
            return label in QUASI_TABLE_LABELS
        if e["roster_table"]:
            return label in ROSTER_LABELS
        if label == "PERSON":
            return True
        if label in SENSITIVE_CATS:
            return e["genre"] in SENS_GENRES
        return e["genre"] in COMPAT[label]

    def assign(label, total, lo, hi, store):
        pool = [e for e in pi if allowed(e, label) and label not in store(e)]
        got = 0
        while got < total and pool:
            pool.sort(key=lambda e: (e["_load"] / e["_cap"], rng.random()))
            e = pool.pop(0)
            room = e["_cap"] - e["_load"]
            if room < 1:
                continue
            n = min(rng.randint(lo, hi), total - got, room)
            store(e)[label] = n
            e["_load"] += n
            got += n
        spare = [e for e in pi if allowed(e, label) and label in store(e) and e["_cap"] - e["_load"] >= 1]
        while got < total and spare:
            spare.sort(key=lambda e: (e["_load"] / e["_cap"], rng.random()))
            e = spare.pop(0)
            store(e)[label] += 1
            e["_load"] += 1
            got += 1
            if e["_cap"] - e["_load"] >= 1:
                spare.append(e)
        return got

    # ---- 4. sensitive statements: counts per case, then direct / indirect / negative modes
    sens_counts = {e["id"]: {} for e in pi}
    for cat in SENSITIVE_CATS:
        assign(cat, SENS_TARGET[cat], 1, 3, lambda e: sens_counts[e["id"]])
    for cat in SENSITIVE_CATS:
        stmts = [e for e in pi for _ in range(sens_counts[e["id"]].get(cat, 0))]
        n = len(stmts)
        n_neg, n_dir = max(6, round(0.13 * n)), round(0.12 * n)
        modes = ["negative"] * n_neg + ["direct"] * n_dir + ["indirect"] * (n - n_neg - n_dir)
        rng.shuffle(modes)
        for e, m in zip(stmts, modes):
            e["sensitive"].append({"category": cat, "mode": m})
    for e in pi:
        e["sensitive"].sort(key=lambda s: (SENSITIVE_CATS.index(s["category"]), s["mode"]))
    # ---- 5. the other labels, then PERSON mentions
    for label in ASSIGN_ORDER:
        total = TARGET[label]
        if label == "QUASI_ID":
            total = max(total - sum(e["labels"].get("QUASI_ID", 0) for e in pi), 14)
        assign(label, total, *PER_CASE[label], lambda e: e["labels"])
    band_p = {"short": (2, 4), "medium": (3, 6), "extended": (6, 12), "long": (16, 30)}
    for e in pi:
        if not e["quasi_table"]:
            lo, hi = band_p[e["band"]]
            e["labels"]["PERSON"] = rng.randint(lo, hi) if not e["roster_table"] else rng.randint(6, 12)
    # ---- 6. hard-to-spot techniques and native-script names
    used_hard, tech_scripts = set(), list(NATIVE_SCRIPTS)
    for tech, label, genres in TECHNIQUES:
        pool = [e for e in pi if e["id"] not in used_hard and e["genre"] in genres and not e["quasi_table"]
                and not e["roster_table"]] or [e for e in pi if e["id"] not in used_hard and e["genre"] in genres]
        e = rng.choice(pool)
        used_hard.add(e["id"])
        e["hard"] = {"technique": tech, "label": label}
        e["labels"].setdefault(label, 1)
        if tech == NATIVE_TECH and tech_scripts:
            e["scripts"].append({"language": tech_scripts.pop(0), "mode": "names"})
    # ---- 7. languages and scripts
    slots = [lang for lang, n in LANGS for _ in range(n)]
    rng.shuffle(slots)
    doc_slots = set(rng.sample(range(len(slots)), DOC_LANG_N))
    cand = [e for e in entries if not e["scripts"] and e["genre"] != "config"]
    rng.shuffle(cand)
    ci = 0
    for si, lang in enumerate(slots):
        mode = "document" if si in doc_slots else rng.choice(["names", "passage"])
        while ci < len(cand):
            e = cand[ci]
            ci += 1
            if mode == "names" and (e["clean"] or e["quasi_table"]):
                continue
            if mode == "document" and (e["genre"] in TABLES or e["genre"] == "fiction"):
                continue
            e["scripts"].append({"language": lang, "mode": mode})
            break
    # ---- 8. hard-negative kinds, clean look-alike focus, traps
    for e in entries:
        prof = e["org"]["kind"] == "profiled"
        if e["clean"]:
            e["clean_focus"] = CLEAN_FOCUS.get(e["genre"], CLEAN_FOCUS["other"])
            e["duration_trap"] = rng.random() < 0.45
            if e["genre"] in CLEAN_KINDS:
                pk, ok = CLEAN_KINDS[e["genre"]]
                pool = pk if prof else ok
                e["hn_kinds"] = rng.sample(pool, min(len(pool), rng.randint(5, 7)))
            else:
                e["hn_kinds"] = sample_kinds(rng, HN_PROFILED if prof else HN_OTHER, rng.randint(5, 7))
                if rng.random() < 0.5:
                    e["hn_kinds"].append(rng.choice(LOOKALIKE_PROFILED if prof else LOOKALIKE_OTHER))
                e["hn_kinds"] = list(dict.fromkeys(e["hn_kinds"]))
        else:
            e["hn_kinds"] = sample_kinds(rng, HN_PROFILED if prof else HN_OTHER, rng.randint(2, 4))
    ident = ("TFN", "MEDICARE", "PASSPORT", "DRIVER_LICENCE", "BANK_ACCOUNT", "NATIONAL_ID")
    def has_date(e):
        return bool(e["labels"].get("OTHER_DATE") or e["labels"].get("DATE_OF_BIRTH"))

    def has_ident(e):
        return bool(e["labels"].get("INTERNAL_ID") or any(e["labels"].get(k) for k in ident))

    trap_pool = [e for e in pi if not e["quasi_table"] and not e["roster_table"] and e["genre"] in
                 ("email", "minutes", "ticket", "letter", "report", "case note", "form", "chat log", "clinical note")]
    rng.shuffle(trap_pool)
    trap_pool.sort(key=lambda e: (not (has_date(e) and has_ident(e))))  # already-qualifying documents first
    traps = [e for e in trap_pool if e["org"]["kind"] == "profiled"][:30] + \
            [e for e in trap_pool if e["org"]["kind"] == "other"][:34]
    for e in traps:
        e["trap"] = True
        if not has_date(e):
            e["labels"]["OTHER_DATE"] = 1
        if not has_ident(e):
            e["labels"]["INTERNAL_ID"] = 1
        profiled = e["org"]["kind"] == "profiled"
        for k in rng.sample(TRAP_PROFILED if profiled else TRAP_OTHER, 2 if profiled else 3):
            if k not in e["hn_kinds"]:
                e["hn_kinds"].append(k)
    sens_pool = [e for e in pi if any(s["mode"] == "indirect" for s in e["sensitive"])]
    rng.shuffle(sens_pool)
    for e in sens_pool[:56]:
        e["lookalike"] = True
        for k in rng.sample(LOOKALIKE_PROFILED if e["org"]["kind"] == "profiled" else LOOKALIKE_OTHER, 2):
            if k not in e["hn_kinds"]:
                e["hn_kinds"].append(k)
    for e in pi:
        if e["org"]["kind"] == "other" and rng.random() < 0.12 and "organisation_abn" not in e["hn_kinds"]:
            e["hn_kinds"].append("organisation_abn")
    # ---- 9. injections: 28 cases, 7 without personal information, positions 9 / 10 / 9
    clean_pool = [e for e in entries if e["clean"] and e["genre"] in
                  ("faq", "notice", "config", "news", "email", "report", "log", "minutes", "ticket", "letter")]
    pi_inj_pool = [e for e in pi if e["genre"] != "clinical note"]
    inj = rng.sample(clean_pool, 7) + rng.sample(pi_inj_pool, 21)
    rng.shuffle(inj)
    positions = ["before"] * 9 + ["inside"] * 10 + ["after"] * 9
    rng.shuffle(positions)
    styles = [INJ_STYLES[i % len(INJ_STYLES)] for i in range(28)]
    rng.shuffle(styles)
    for e, pos, (sub, desc) in zip(inj, positions, styles):
        e["injection"] = {"position": pos, "subtype": sub, "style": desc}
    # ---- 10. safe values (checksums by safevalues.py, unique across the plan)
    for e in entries:
        prof, L, v = e["org"]["kind"] == "profiled", e["labels"], {}
        if L.get("PHONE"):
            kinds = [None] * L["PHONE"]
            if e["hard"] and e["hard"]["label"] == "PHONE":
                kinds[0] = "mobile"
            v["PHONE"] = [vals.phone(k) for k in kinds]
        if L.get("EMAIL"):
            v["EMAIL"] = [vals.email("{ORG_DOMAIN}" if prof and rng.random() < 0.6 else None) for _ in range(L["EMAIL"])]
        for key, fn in (("TFN", vals.tfn), ("MEDICARE", vals.medicare), ("ABN", vals.abn), ("BANK_ACCOUNT", vals.bank),
                        ("PAYMENT_CARD", vals.card), ("PASSPORT", vals.passport), ("DRIVER_LICENCE", vals.licence)):
            if L.get(key):
                v[key] = [fn() for _ in range(L[key])]
        if L.get("NATIONAL_ID"):
            got = []
            while len(got) < L["NATIONAL_ID"]:
                x = vals.national_id()
                if x not in got:
                    got.append(x)
            v["NATIONAL_ID"] = got
        if L.get("SECRET"):
            kinds = (["api key", "password", "access token", "client secret", "database password", "password"]
                     if e["genre"] == "config" else ["password", "PIN", "api key", "password", "access token"])
            v["SECRET"] = [vals.secret(rng.choice(kinds)) for _ in range(L["SECRET"])]
        if not prof and any(k in e["hn_kinds"] for k in ("business_phone", "reception_phone")):
            v["BUSINESS_PHONE"] = [vals.business_phone() for _ in range(rng.randint(1, 2))]
        if not prof and "organisation_abn" in e["hn_kinds"]:
            v["ORG_ABN"] = [vals.abn()]
        e["values"] = v
    out = []
    for e in sorted(entries, key=lambda x: x["id"]):
        e = {k: val for k, val in e.items() if not k.startswith("_")}
        e["labels"] = {k: val for k, val in sorted(e["labels"].items()) if val}
        out.append(e)
    return out


def summarise(plan: list[dict]) -> dict:
    labels: Counter = Counter()
    for e in plan:
        labels.update(e["labels"])
        for s in e["sensitive"]:
            labels[s["category"]] += 1
    sens = [s for e in plan for s in e["sensitive"]]
    modes = Counter(s["mode"] for s in sens)
    inj = [e for e in plan if e["injection"]]
    return {
        "cases": len(plan), "seed": SEED, "clean": sum(e["clean"] for e in plan),
        "pi": sum(not e["clean"] for e in plan),
        "profiled": sum(e["org"]["kind"] == "profiled" for e in plan),
        "other_workplaces": len({e["org"]["k"] for e in plan if e["org"]["kind"] == "other"}),
        "genres": dict(Counter(e["genre"] for e in plan)), "bands": dict(Counter(e["band"] for e in plan)),
        "label_spans_planned": dict(sorted(labels.items())),
        "sensitive_modes": dict(modes), "indirect_share": round(modes["indirect"] / max(1, len(sens)), 3),
        "categories_with_negative": len({s["category"] for s in sens if s["mode"] == "negative"}),
        "quasi_tables": sum(e["quasi_table"] for e in plan), "roster_tables": sum(e["roster_table"] for e in plan),
        "hard_to_spot": sum(bool(e["hard"]) for e in plan), "injections": len(inj),
        "injection_positions": dict(Counter(e["injection"]["position"] for e in inj)),
        "injection_clean": sum(e["clean"] for e in inj),
        "injection_subtypes": dict(Counter(e["injection"]["subtype"] for e in inj)),
        "long_documents": sum(e["long"] for e in plan), "traps": sum(e["trap"] for e in plan),
        "trap_profiled": sum(e["trap"] and e["org"]["kind"] == "profiled" for e in plan),
        "trap_other": sum(e["trap"] and e["org"]["kind"] == "other" for e in plan),
        "lookalike_next_to_sensitive": sum(e["lookalike"] for e in plan),
        "language_slots": dict(Counter(s["language"] for e in plan for s in e["scripts"])),
        "cases_with_a_language_or_script": sum(bool(e["scripts"]) for e in plan),
    }


def check_targets(s: dict) -> list[str]:
    bad = []
    if s["cases"] != N_CASES: bad.append("cases")
    if s["clean"] < 70: bad.append("clean")
    for lab, n in s["label_spans_planned"].items():
        if n < (45 if lab in SENSITIVE_CATS else 20): bad.append(f"label {lab}")
    if s["indirect_share"] < 0.70: bad.append("indirect share")
    if s["categories_with_negative"] < 8: bad.append("negatives")
    if s["quasi_tables"] < 22: bad.append("quasi tables")
    if s["hard_to_spot"] < 15: bad.append("hard")
    if not 25 <= s["injections"] <= 30: bad.append("injections")
    if s["injection_clean"] < 6: bad.append("injection clean")
    if s["long_documents"] < 6: bad.append("long")
    if s["other_workplaces"] < 8: bad.append("other workplaces")
    if not 0.4 <= s["profiled"] / s["cases"] <= 0.6: bad.append("profiled share")
    if len([l for l, n in s["language_slots"].items() if n]) < 6: bad.append("languages")
    return bad


def render(plan: list[dict]) -> str:
    return "".join(jline(e) + "\n" for e in plan)


def main(argv) -> int:
    sv.selftest()
    text = render(build_plan())
    if "--check" in argv:
        ok = (ROOT / "plan.jsonl").read_text(encoding="utf-8") == text and render(build_plan()) == text
        print("plan check:", "byte-identical" if ok else "DIFFERENT")
        return 0 if ok else 1
    plan = build_plan()
    summ = summarise(plan)
    bad = check_targets(summ)
    (ROOT / "plan.jsonl").write_text(text, encoding="utf-8", newline="\n")
    summ["plan_sha256"] = sha256_file(ROOT / "plan.jsonl")
    (ROOT / "plan.summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    (ROOT / "plan.sha256").write_text(f"{summ['plan_sha256']}  plan.jsonl\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summ.items() if k not in ("label_spans_planned", "language_slots", "genres")}))
    print("labels planned:", summ["label_spans_planned"])
    print("plan-level target shortfalls:", bad or "none")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))