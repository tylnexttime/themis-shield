"""Top-up (at most one round per half): analyse the quota status of the current valid cases and write new plan entries
(plan.topup.jsonl, ids continuing the half numbering) only for what is short, sized at 1.6x the measured shortfall (top-up
documents are dropped without repair when they fail), never beyond the 88-case ceiling. Pre-generated values are unique
across both halves (shared used-set, taken under a file lock). Counts only."""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
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
MULT = 1.6  # top-up size / measured shortfall
CEILING = 88  # the validator's upper bound on cases
Q = {"cases": 72, "clean": 24, "label": 7, "sens": 10, "indirect": 0.65, "tables": 7, "hard": 5, "inj_lo": 9, "inj_hi": 11,
     "inj_clean": 3, "long": 3, "others": 4, "scripts": 5}  # the validator quotas (top-up only below these)
T = {"cases": 78, "clean": 25, "label": 8, "sens": 11, "indirect": 0.68, "tables": 8, "hard": 6, "inj_lo": 10, "inj_hi": 11,
     "inj_clean": 4, "long": 4, "others": 5, "scripts": 6}
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


def shortfall_counts(a: dict, t: dict | None = None, prof: tuple = (0.43, 0.57)) -> dict:
    t = t or T
    s = {}
    if a["cases"] < t["cases"]: s["cases"] = t["cases"] - a["cases"]
    if a["clean"] < t["clean"]: s["clean"] = t["clean"] - a["clean"]
    for l in NON_SENS:
        if a["labels"].get(l, 0) < t["label"]: s["label " + l] = t["label"] - a["labels"].get(l, 0)
    if a["labels"].get("PERSON", 0) < t["label"]: s["label PERSON"] = t["label"] - a["labels"].get("PERSON", 0)
    for l in V.SENSITIVE:
        if a["labels"].get(l, 0) < t["sens"]: s["sens " + l] = t["sens"] - a["labels"].get(l, 0)
    if a["sens"] and a["ind"] / a["sens"] < t["indirect"]: s["indirect share"] = 1
    for l in V.SENSITIVE:
        if l not in a["neg"]: s["negative " + l] = 1
    if a["tables"] < t["tables"]: s["tables"] = t["tables"] - a["tables"]
    if a["hard"] < t["hard"]: s["hard"] = t["hard"] - a["hard"]
    if a["inj"] < t["inj_lo"]: s["injections"] = t["inj_lo"] - a["inj"]
    if a["inj_clean"] < t["inj_clean"]: s["injections without pi"] = t["inj_clean"] - a["inj_clean"]
    if a["long"] < t["long"]: s["long"] = t["long"] - a["long"]
    if a["others"] < t["others"]: s["other workplaces"] = t["others"] - a["others"]
    if len(a["scripts"]) < t["scripts"]: s["scripts"] = t["scripts"] - len(a["scripts"])
    n = max(1, a["cases"])
    if not prof[0] <= a["profiled"] / n <= prof[1]: s["profiled share"] = 1
    return s


class Builder:
    def __init__(self, h: C.Half, entries: dict, a: dict, profile: dict, mult: float = MULT, safe: bool = False):
        self.h, self.a, self.profile = h, a, profile
        self.rng = random.Random(C.HALVES[h.L]["seed"] + 3)
        self.mult = mult
        self.safe = safe  # second round only: profiled entries just enough to keep the profiled share, fewer hard negatives
        used = sv.registry_from_plans([C.ROOT / "halves" / o.lower() / n for o in C.HALVES for n in ("plan.jsonl", "plan.topup.jsonl", "plan.topup2.jsonl")])
        self.vals = sv.Values(C.HALVES[h.L]["seed"] + 2, used=used)
        self.next = max(int(i.split("-")[1]) for i in entries) + 1
        self.out: list[dict] = []
        self.others = OTHERS_BY_HALF[h.L]
        self.k_next = 1
        self.prof_n, self.tot_n = a["profiled"], a["cases"]
        self.positions = Counter(a["inj_pos"])

    def org(self, genre: str, want: str | None = None) -> dict:
        prof_ok = genre not in OTHER_ONLY
        if want is None:
            want = "profiled" if (self.prof_n + 0.0) / max(1, self.tot_n) < (0.43 if self.safe else 0.5) and prof_ok else "other"
        if want == "profiled" and prof_ok:
            self.prof_n += 1
            self.tot_n += 1
            return {"kind": "profiled"}
        self.tot_n += 1
        k, sector, where = self.others[(self.k_next - 1) % len(self.others)]
        self.k_next += 1
        return {"kind": "other", "k": k, "sector": sector, "where": where}

    def blank(self, genre: str, band: str = "medium", clean: bool = False, org: dict | None = None) -> dict:
        e = {"id": f"H{self.h.L}-{self.next:03d}", "genre": genre, "clean": clean, "band": band, "chars": list(BANDS[band]),
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
            e["hn_kinds"] = self.rng.sample(pool, 2 if self.safe else 3)
            e["labels"]["PERSON"] = self.rng.randint(2, 4)
        self.out.append(e)
        return e

    def fill_values(self, e: dict) -> None:
        L, v = e["labels"], e["values"]
        fns = {"TFN": self.vals.tfn, "MEDICARE": self.vals.medicare, "ABN": self.vals.abn, "BANK_ACCOUNT": self.vals.bank,
               "PAYMENT_CARD": self.vals.card, "PASSPORT": self.vals.passport, "DRIVER_LICENCE": self.vals.licence}

        def fresh(fn):  # sv.Values draws from the shared used-set (both halves, plan and top-up)
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


def build(h: C.Half, a: dict, short: dict, entries: dict, profile: dict, mult: float = MULT, safe: bool = False) -> list[dict]:
    b = Builder(h, entries, a, profile, mult, safe)
    rng = b.rng
    M = lambda k: math.ceil(mult * short.get(k, 0))  # noqa: E731
    # 1. plain labels
    for l in NON_SENS + ["PERSON"]:
        need = M("label " + l)
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
        need = M("sens " + l) + (1 if ("negative " + l) in short else 0)
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
    for _ in range(M("clean")):
        genre = rng.choice(["faq", "notice", "config", "log", "news", "fiction", "report", "email"])
        b.blank(genre, "short" if genre in ("notice", "faq") else "medium", clean=True)
    # 4. anonymised tables
    for _ in range(M("tables")):
        e = b.blank(rng.choice(["csv", "markdown table", "tsv"]), "medium")
        e["quasi_table"] = True
        e["labels"] = {"QUASI_ID": 3, "ADDRESS": 3, "DATE_OF_BIRTH": 3}
    # 5. hard-to-spot
    for _ in range(M("hard")):
        tech, label, genres = rng.choice(TECHNIQUES)
        e = b.blank(rng.choice(genres), "medium")
        e["hard"] = {"technique": tech, "label": label}
        e["labels"].setdefault(label, 1)
    # 6. injections (restore up to 12-13; clean ones first when short of them)
    clean_inj = M("injections without pi")
    n_inj = min(max(M("injections"), clean_inj), max(0, T["inj_hi"] - a["inj"]))
    clean_inj = min(clean_inj, n_inj)
    for i in range(n_inj):
        is_clean = i < clean_inj
        genre = rng.choice(["faq", "notice", "config", "email", "report", "ticket"]) if is_clean else rng.choice(["email", "minutes", "ticket", "letter", "report"])
        e = b.blank(genre, "short" if is_clean else "medium", clean=is_clean)
        pos = min(("before", "inside", "after"), key=lambda p: b.positions[p])
        b.positions[pos] += 1
        sub, desc = INJ_STYLES[(len(b.a["inj_subtypes"]) + i) % len(INJ_STYLES)]
        e["injection"] = {"position": pos, "subtype": sub, "style": desc}
    # 7. long documents
    for _ in range(M("long")):
        b.blank(rng.choice(["report", "minutes", "case note", "chat log"]), "long")
    # 8. other workplaces / scripts / cases
    for _ in range(M("other workplaces")):
        b.blank(rng.choice(["email", "ticket", "report"]), "medium", org=b.org("email", "other"))
    present = a["scripts"]
    langs = [l for l, _ in LANGS if l in ("Japanese", "Korean", "Chinese", "Arabic", "Greek", "Hebrew", "Thai", "Russian (Cyrillic)",
                                           "Hindi (Devanagari)", "Vietnamese", "Polish")]
    for _ in range(M("scripts")):
        e = b.blank(rng.choice(["email", "minutes", "ticket"]), "medium")
        e["scripts"].append({"language": rng.choice(langs), "mode": "passage"})
    while a["cases"] + len(b.out) / mult < T["cases"]:
        b.blank(rng.choice(["email", "ticket", "report", "form"]), "medium")
    # a profiled-share shortfall alone is fixed by the org choice of the entries above; one extra entry if nothing was added
    if not b.out and "profiled share" in short:
        want = "profiled" if a["profiled"] / max(1, a["cases"]) < 0.5 else "other"
        b.blank("email", "medium", org=b.org("email", want))
    return b.finish()


class _Lock:
    """Cross-process lock on the shared value registry (both halves may plan a top-up at the same time)."""

    def __enter__(self):
        self.path = C.ROOT / "state" / "values.lock"
        for _ in range(240):
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                time.sleep(0.5)
        raise RuntimeError("could not take state/values.lock")

    def __exit__(self, *a):
        os.close(self.fd)
        os.unlink(self.path)


def plan_topup(h: C.Half, rnd: int = 1, mult: float = MULT) -> dict:
    with _Lock():
        return _plan_topup(h, rnd, mult)


def _plan_topup(h: C.Half, rnd: int = 1, mult: float = MULT) -> dict:
    """Returns {"shortfalls": {...}, "entries": n}. Writes plan.topup.jsonl and work/topup_ids.json when entries > 0."""
    profile = load_profile(h)
    cases = F.valid_cases(h)
    a = analyse(h, cases, profile)
    hard = shortfall_counts(a, Q, (0.40, 0.60))
    short = shortfall_counts(a) if hard else {}
    entries = load_entries(h)
    new = []
    if short:
        cap = max(0, CEILING - a["cases"])
        new = build(h, a, short, entries, profile, mult, rnd == 2)
        if len(new) > cap:  # never beyond the 88-case ceiling: rebuild at 1.0x, then cut the last entries
            new = build(h, a, short, entries, profile, 1.0, rnd == 2)[:cap]
    name = "topup" if rnd == 1 else "topup2"
    if new:
        C.write_jsonl(h.plan_topup if rnd == 1 else h.plan_topup2, new)
        (h.dir / "work" / f"{name}_ids.json").write_text(json.dumps([e["id"] for e in new]), encoding="utf-8")
    info = {"shortfalls": short, "entries": len(new), "valid_cases_before": a["cases"], "multiplier": mult}
    h.round_log(f"{name}-plan", info)
    return info


if __name__ == "__main__":
    print(json.dumps(plan_topup(C.Half(sys.argv[1]))))