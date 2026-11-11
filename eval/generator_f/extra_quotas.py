"""extra_quotas.py: the split-F plan's stricter targets, which the shared validator does not check.

    python extra_quotas.py cases.jsonl [--profile profile.json]

Rows are counts only. Exit code 1 if a hard target is missed (info rows never fail)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "validator"))
import check_cases as V  # noqa: E402

DATE_L = ("OTHER_DATE", "DATE_OF_BIRTH")
IDENT_L = ("INTERNAL_ID", "TFN", "MEDICARE", "PASSPORT", "DRIVER_LICENCE", "BANK_ACCOUNT", "NATIONAL_ID", "ABN")
LOOKALIKE = {"committee", "initiative", "policy_statement", "programme_title", "public_event", "fiction_statement",
             "public_news"}


def rows_for(cases: list[dict], profile: dict | None) -> list[tuple[str, object, str, bool | None]]:
    R = []

    def row(name, got, target, ok):
        R.append((name, got, target, ok))

    n = len(cases)
    per = Counter(s["label"] for c in cases for s in c["spans"])
    clean = [c for c in cases if not c["spans"]]
    row("clean documents", len(clean), ">= 70", len(clean) >= 70)
    row("every label >= 20 gold spans (min over 27)", min(per.get(l, 0) for l in V.LABELS), ">= 20",
        all(per.get(l, 0) >= 20 for l in V.LABELS))
    sens_min = min(per.get(l, 0) for l in V.SENSITIVE)
    row("every sensitive category >= 30 gold spans (min over 8)", sens_min, ">= 30", sens_min >= 30)
    sens = [s for c in cases for s in c["spans"] if s["label"] in V.SENSITIVE]
    ind = sum(1 for s in sens if s.get("indirect"))
    row("indirect share of sensitive spans", f"{ind}/{len(sens)} = {ind / max(1, len(sens)):.0%}", ">= 65%",
        len(sens) > 0 and ind / len(sens) >= 0.65)
    neg_cats = {V.SENSITIVE[s["label"]] for s in sens if s.get("negative")}
    row("categories with negative statements", f"{len(neg_cats)}/8", "8/8", len(neg_cats) == 8)
    tabs = [c for c in cases if V.looks_tabular(c) and any(s["label"] == "QUASI_ID" for s in c["spans"])]
    row("tables with a QUASI_ID span", len(tabs), ">= 20", len(tabs) >= 20)
    hard = sum(1 for c in cases if c.get("hard_to_spot"))
    row("hard-to-spot cases", hard, ">= 15", hard >= 15)
    inj = [c for c in cases if c.get("injection")]
    pos = Counter(c["injection"]["position"] for c in inj)
    row("injected cases", len(inj), "25-30", 25 <= len(inj) <= 30)
    row("injection positions", dict(pos), "each >= 25% of injections",
        bool(inj) and all(pos.get(p, 0) >= round(len(inj) * 0.25) for p in ("before", "inside", "after")))
    row("injected cases without personal information", sum(1 for c in inj if not c["spans"]), ">= 6",
        sum(1 for c in inj if not c["spans"]) >= 6)
    row("distinct injection subtypes", len({c["injection"]["subtype"] for c in inj}), ">= 6",
        len({c["injection"]["subtype"] for c in inj}) >= 6)
    longs = [c for c in cases if 9000 <= len(c["text"]) <= 24000]
    row("long documents (9,000-24,000 characters)", len(longs), ">= 6", len(longs) >= 6)
    scripts = [s for s, rx in V.SCRIPT_RES.items() if any(rx.search(c["text"]) for c in cases)]
    row("scripts/languages present (validator's detector)", len(scripts), ">= 6", len(scripts) >= 6)
    others = {c.get("organisation") for c in cases if profile and c.get("organisation") != profile["organisation"]}
    row("other invented workplaces", len(others), ">= 8", len(others) >= 8)
    if profile:
        prof = [c for c in cases if c.get("organisation") == profile["organisation"]]
        row("profiled-organisation share", f"{len(prof)}/{n} = {len(prof) / max(1, n):.0%}", "40-60%",
            0.4 <= len(prof) / max(1, n) <= 0.6)
        prefixes = sorted({re.match(r"[A-Za-z]+", str(p)).group(0) for p in
                           profile["personal_identifier_formats_issued"].values() if re.match(r"[A-Za-z]+", str(p))})
        issued = sum(1 for c in prof for s in c["spans"] if s["label"] == "INTERNAL_ID"
                     and any(s["value"].startswith(p) for p in prefixes))
        row("INTERNAL_ID spans written with the organisation's prefix (profiled cases)", issued, "info", None)
    trap = [c for c in cases if c["spans"] and any(s["label"] in DATE_L for s in c["spans"])
            and any(s["label"] in IDENT_L for s in c["spans"])]
    md = sum(1 for c in trap if any(h["kind"] in ("meeting_date", "letter_date") for h in c["hard_negatives"]))
    rn = sum(1 for c in trap if any(h["kind"] in ("ticket_number", "case_number", "reference_number")
                                    for h in c["hard_negatives"]))
    row("documents with personal dates and identifiers: also a meeting/letter-date hard negative", md, "info", None)
    row("documents with personal dates and identifiers: also a ticket/case/reference-number hard negative", rn,
        "info", None)
    nb = sum(1 for c in cases if any(s["label"] in V.SENSITIVE for s in c["spans"])
             and any(h["kind"] in LOOKALIKE for h in c["hard_negatives"]))
    row("documents with a sensitive statement next to a sensitive look-alike hard negative", nb, "info", None)
    row("sensitive statements by label (min)", sens_min, "info", None)
    return R


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--profile", default=str(Path(__file__).resolve().parent / "profile.json"))
    a = ap.parse_args(argv)
    cases = [json.loads(l) for l in Path(a.cases).read_text(encoding="utf-8").split("\n") if l.strip()]
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8")) if Path(a.profile).exists() else None
    bad = False
    print("extra quotas (split-F plan):")
    for name, got, target, ok in rows_for(cases, profile):
        mark = "ok  " if ok else ("info" if ok is None else "SHORT")
        bad |= ok is False
        print(f"  {mark} {name}: {got} (target {target})")
    print("EXTRA VERDICT:", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())