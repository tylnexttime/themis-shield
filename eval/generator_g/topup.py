"""Top-up (at most one round per half): analyse the quota status of the current valid cases and write new plan entries
(plan.topup.jsonl, ids continuing the half's numbering) only for what is short. Counts only."""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

import common as C
import finalize as F
import safevalues as sv
from plan_data import (BANDS, COMPAT, HN_OTHER, HN_PROFILED, INJ_STYLES, LANGS, OTHERS_BY_HALF, OTHER_ONLY, SENS_GENRES,
                       SENSITIVE_CATS, TECHNIQUES, CLEAN_FOCUS, CLEAN_KINDS)
from store import load_entries, load_profile, load_state

sys.path.insert(0, str(C.ROOT / "validator"))
import check_cases as V  # noqa: E402

# targets are the validator's quotas plus a small margin for top-up drops
T = {"cases": 92, "clean": 32, "label": 10, "sens": 14, "indirect": 0.68, "tables": 9, "hard": 7, "inj_lo": 12, "inj_hi": 14,
     "inj_clean": 4, "long": 4, "others": 6, "scripts": 7}
CHECKSUMMED = ("TFN", "MEDICARE", "ABN", "BANK_ACCOUNT", "PAYMENT_CARD", "PASSPORT", "DRIVER_LICENCE")
NON_SENS = [l for l in V.LABELS if l not in V.SENSITIVE and l != "PERSON"]


def analyse(h: C.Half, cases: list[dict], profile: dict) -> dict:
    per = Counter(s["label"] for c in cases for s in c["spans"])
    sens = [s for c in cases for s in c["spans"] if s["label"] in V.SENSITIVE]
    ind = sum(1 for s in sens if s.get("indirect"))
    neg = {s["label"] for s in sens if s.get("negative")}
    inj = [c for c in cases if c.get("injection")]
    pos = Counter(c["injection"]["position"] for c in inj)
    others = {c.get("organisation") for c in cases if c.get("organisation") != profile["organisation"]}
    scripts = {s for s, rx in V.SCRIPT_RES.items() if any(rx.search(c["text"]) for c in cases)}
    return {"cases": len(cases), "clean": sum(1 for c in cases if not c["spans"]), "labels": per, "sens": len(sens), "ind": ind,
            "neg": neg, "tables": sum(1 for c in cases if V.looks_tabular(c) and any(s["label"] == "QUASI_ID" for s in c["spans"])),
            "hard": sum(1 for c in cases if c.get("hard_to_spot")), "inj": len(inj), "inj_pos": pos,
            "inj_clean": sum(1 for c in inj if not c["spans"]), "long": sum(1 for c in cases if 9000 <= len(c["text"]) <= 24000),
            "others": len(others), "scripts": scripts, "profiled": sum(1 for c in cases if c.get("organisation") == profile["organisation"]),
            "inj_subtypes": {c["injection"]["subtype"] for c in inj}}


def shortfall_counts(a: dict) -> dict:
    s = {}
    if a["cases"] < T["cases"]: s["cases"] = T["cases"] - a["cases"]
    if a["clean"] < T["clean"]: s["clean"] = T["clean"] - a["clean"]
    for l in NON_SENS:
        if a["labels"].get(l, 0) < T["label"]: s["label " + l] = T["label"] - a["labels"].get(l, 0)
    if a["labels"].get("PERSON", 0) < T["label"]: s["label PERSON"] = T["label"] - a["labels"].get("PERSON", 0)
    for l in V.SENSITIVE:
        if a["labels"].get(l, 0) < T["sens"]: s["sens " + l] = T["sens"] - a["labels"].get(l, 0)
    if a["sens"] and a["ind"] / a["sens"] < T["indirect"]: s["indirect share"] = 1
    for l in V.SENSITIVE:
        if l not in a["neg"]: s["negative " + l] = 1
    if a["tables"] < T["tables"]: s["tables"] = T["tables"] - a["tables"]
    if a["hard"] < T["hard"]: s["hard"] = T["hard"] - a["hard"]
    if a["inj"] < T["inj_lo"]: s["injections"] = T["inj_lo"] - a["inj"]
    if a["inj_clean"] < T["inj_clean"]: s["injections without pi"] = T["inj_clean"] - a["inj_clean"]
    if a["long"] < T["long"]: s["long"] = T["long"] - a["long"]
    if a["others"] < T["others"]: s["other workplaces"] = T["others"] - a["others"]
    if len(a["scripts"]) < T["scripts"]: s["scripts"] = T["scripts"] - len(a["scripts"])
    n = max(1, a["cases"])
    if not 0.43 <= a["profiled"] / n <= 0.57: s["profiled share"] = 1
    return s


class Builder:
    def __init__(self, h: C.Half, entries: dict, a: dict, profile: dict):
        self.h, self.a, self.profile = h, a, profile
        self.rng = random.Random(C.HALVES[h.L]["seed"] + 3)
        self.vals = sv.Values(C.HALVES[h.L]["seed"] + 2)
        self.used = {v for e in entries.values() for lst in (e.get("values") or {}).values() for v in lst if isinstance(v, str)}
        self.next = max(int(i.split("-")[1]) for i in entries) + 1
        self.out: list[dict] = []
        self.others = OTHERS_BY_HALF[h.L]
        self.k_next = 1
        self.prof_n, self.tot_n = a["profiled"], a["cases"]
        self.positions = Counter(a["inj_pos"])

    def org(self, genre: str, want: str | None = None) -> dict:
        prof_ok = genre not in OTHER_ONLY
        if want is None:
            want = "profiled" if (self.prof_n + 0.0) / max(1, self.tot_n) < 0.5 and prof_ok else "other"
        if want == "profiled" and prof_ok:
            self.prof_n += 1
            self.tot_n += 1
            return {"kind": "profiled"}
        self.tot_n += 1
        k, sector, where = self.others[(self.k_next - 1) % len(self.others)]
        self.k_next += 1
        return {"kind": "other", "k": k, "sector": sector, "where": where}

    def blank(self, genre: str, band: str = "medium", clean: bool = False, org: dict | None = None) -> dict:
        e = {"id": f"G{self.h.L}-{self.next:03d}", "genre": genre, "clean": clean, "band": band, "chars": list(BANDS[band]),
             "long": band == "long", "org": org or self.org(genre), "labels": {}, "sensitive": [], "hn_kinds": [], "lookalike": False,
             "trap": False, "duration_trap": False, "quasi_table": False, "roster_table": False, "injection": None, "hard": None,
             "scripts": [], "clean_focus": "", "values": {}, "topup": True}
        self.next += 1
        prof = e["org"]["kind"] == "profiled"
        if clean:
            e["clean_focus"] = CLEAN_FOCUS.get(genre, CLEAN_FOCUS["other"])
            e["duration_trap"] = self.rng.random() < 0.45
            if genre in CLEAN_KINDS:
                pk, ok = CLEAN_KINDS[genre]
                pool = pk if prof else ok
                e["hn_kinds"] = self.rng.sample(pool, min(len(pool), self.rng.randint(5, 7)))
            else:
                pool = [k for k, _ in (HN_PROFILED if prof else HN_OTHER)]
                e["hn_kinds"] = self.rng.sample(pool, 5)
        else:
            pool = [k for k, _ in (HN_PROFILED if prof else HN_OTHER)]
            e["hn_kinds"] = self.rng.sample(pool, 3)
            e["labels"]["PERSON"] = self.rng.randint(2, 4)
        self.out.append(e)
        return e

    def fill_values(self, e: dict) -> None:
        L, v = e["labels"], e["values"]
        fns = {"TFN": self.vals.tfn, "MEDICARE": self.vals.medicare, "ABN": self.vals.abn, "BANK_ACCOUNT": self.vals.bank,
               "PAYMENT_CARD": self.vals.card, "PASSPORT": self.vals.passport, "DRIVER_LICENCE": self.vals.licence}

        def fresh(fn):
            for _ in range(50):
                x = fn()
                if x not in self.used:
                    self.used.add(x)
                    return x
            return fn()

        if L.get("PHONE"):
            kinds = [None] * L["PHONE"]
            if e["hard"] and e["hard"]["label"] == "PHONE":
                kinds[0] = "mobile"
            v["PHONE"] = [fresh(lambda k=k: self.vals.phone(k)) for k in kinds]
        if L.get("EMAIL"):
            v["EMAIL"] = [fresh(lambda: self.vals.email("{ORG_DOMAIN}" if e["org"]["kind"] == "profiled" and self.rng.random() < 0.6 else None))
                          for _ in range(L["EMAIL"])]
        for k, fn in fns.items():
            if L.get(k):
                v[k] = [fresh(fn) for _ in range(L[k])]
        if L.get("NATIONAL_ID"):
            v["NATIONAL_ID"] = [fresh(self.vals.national_id) for _ in range(L["NATIONAL_ID"])]
        if L.get("SECRET"):
            kinds = ["api key", "password", "access token", "client secret", "database password", "PIN"]
            v["SECRET"] = [self.vals.secret(self.rng.choice(kinds)) for _ in range(L["SECRET"])]

    def finish(self) -> list[dict]:
        for e in self.out:
            self.fill_values(e)
            if e["org"]["kind"] == "other" and any(k in e["hn_kinds"] for k in ("business_phone", "reception_phone")):
                e["values"]["BUSINESS_PHONE"] = [self.vals.business_phone()]
            e["labels"] = {k: v for k, v in sorted(e["labels"].items()) if v}
        return self.out


def build(h: C.Half, a: dict, short: dict, entries: dict, profile: dict) -> list[dict]:
    b = Builder(h, entries, a, profile)
    rng = b.rng
    # 1. plain labels
    for l in NON_SENS + ["PERSON"]:
        need = short.get("label " + l, 0)
        while need > 0:
            genre = rng.choice([g for g in COMPAT.get(l, SENS_GENRES) if g not in ("csv", "tsv", "markdown table", "clinical note")] or ["report"])
            e = b.blank(genre, rng.choice(["short", "medium"]))
            n = min(3, need)
            e["labels"][l] = e["labels"].get(l, 0) + n if l != "PERSON" else max(e["labels"]["PERSON"], n + 2)
            need -= n
    # 2. sensitive categories: mostly indirect; one negative where a category has none
    mode_pool = ["indirect"] * 8 + ["direct"]
    for l in V.SENSITIVE:
        cat = V.SENSITIVE[l] if l in V.SENSITIVE else l
        need = short.get("sens " + l, 0) + (1 if ("negative " + l) in short else 0)
        neg_needed = ("negative " + l) in short
        while need > 0:
            e = b.blank(rng.choice([g for g in SENS_GENRES if g not in ("csv", "tsv", "markdown table")]), "medium")
            n = min(3, need)
            for _ in range(n):
                mode = "negative" if neg_needed else rng.choice(mode_pool)
                neg_needed = False
                e["sensitive"].append({"category": l, "mode": mode})
            need -= n
    if "indirect share" in short:
        for l in rng.sample(list(V.SENSITIVE), 3):
            e = b.blank("case note", "medium")
            e["sensitive"] += [{"category": l, "mode": "indirect"}] * 3
    # 3. clean documents
    for _ in range(short.get("clean", 0)):
        genre = rng.choice(["faq", "notice", "config", "log", "news", "fiction", "report", "email"])
        b.blank(genre, "short" if genre in ("notice", "faq") else "medium", clean=True)
    # 4. anonymised tables
    for _ in range(short.get("tables", 0)):
        e = b.blank(rng.choice(["csv", "markdown table", "tsv"]), "medium")
        e["quasi_table"] = True
        e["labels"] = {"QUASI_ID": 3, "ADDRESS": 3, "DATE_OF_BIRTH": 3}
    # 5. hard-to-spot
    for _ in range(short.get("hard", 0)):
        tech, label, genres = rng.choice(TECHNIQUES)
        e = b.blank(rng.choice(genres), "medium")
        e["hard"] = {"technique": tech, "label": label}
        e["labels"].setdefault(label, 1)
    # 6. injections (restore up to 12-13; clean ones first when short of them)
    n_inj = max(short.get("injections", 0), short.get("injections without pi", 0))
    clean_inj = short.get("injections without pi", 0)
    for i in range(n_inj):
        is_clean = i < clean_inj
        genre = rng.choice(["faq", "notice", "config", "email", "report", "ticket"]) if is_clean else rng.choice(["email", "minutes", "ticket", "letter", "report"])
        e = b.blank(genre, "short" if is_clean else "medium", clean=is_clean)
        pos = min(("before", "inside", "after"), key=lambda p: b.positions[p])
        b.positions[pos] += 1
        sub, desc = INJ_STYLES[(len(b.a["inj_subtypes"]) + i) % len(INJ_STYLES)]
        e["injection"] = {"position": pos, "subtype": sub, "style": desc}
    # 7. long documents
    for _ in range(short.get("long", 0)):
        b.blank(rng.choice(["report", "minutes", "case note", "chat log"]), "long")
    # 8. other workplaces / scripts / cases
    for _ in range(short.get("other workplaces", 0)):
        b.blank(rng.choice(["email", "ticket", "report"]), "medium", org=b.org("email", "other"))
    present = a["scripts"]
    langs = [l for l, _ in LANGS if l in ("Japanese", "Korean", "Chinese", "Arabic", "Greek", "Hebrew", "Thai", "Russian (Cyrillic)",
                                           "Hindi (Devanagari)", "Vietnamese", "Polish")]
    for _ in range(short.get("scripts", 0)):
        e = b.blank(rng.choice(["email", "minutes", "ticket"]), "medium")
        e["scripts"].append({"language": rng.choice(langs), "mode": "passage"})
    while len(b.out) + a["cases"] < T["cases"]:
        b.blank(rng.choice(["email", "ticket", "report", "form"]), "medium")
    # a profiled-share shortfall alone is fixed by the org choice of the entries above; one extra entry if nothing was added
    if not b.out and "profiled share" in short:
        want = "profiled" if a["profiled"] / max(1, a["cases"]) < 0.5 else "other"
        b.blank("email", "medium", org=b.org("email", want))
    return b.finish()


def plan_topup(h: C.Half) -> dict:
    """Returns {"shortfalls": {...}, "entries": n}. Writes plan.topup.jsonl and work/topup_ids.json when entries > 0."""
    profile = load_profile(h)
    cases = F.valid_cases(h)
    a = analyse(h, cases, profile)
    short = shortfall_counts(a)
    entries = load_entries(h)
    new = build(h, a, short, entries, profile) if short else []
    if new:
        C.write_jsonl(h.plan_topup, new)
        (h.dir / "work" / "topup_ids.json").write_text(json.dumps([e["id"] for e in new]), encoding="utf-8")
    info = {"shortfalls": short, "entries": len(new), "valid_cases_before": a["cases"]}
    h.round_log("topup-plan", info)
    return info


if __name__ == "__main__":
    print(json.dumps(plan_topup(C.Half(sys.argv[1]))))