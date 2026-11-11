"""Span agreement between the author's gold and the blind labeller: alignment, metrics, disagreement items,
AGREEMENT.md (numbers only)."""
from __future__ import annotations

import difflib
from collections import Counter

from markup import parse_markup
from check_cases import LABELS


def align_map(orig: str, other: str) -> list:
    """Position in `other` -> position in `orig` (None where the text was inserted or changed)."""
    m = [None] * (len(other) + 1)
    al, bl = orig.splitlines(True), other.splitlines(True)
    ao, bo = [0], [0]
    for l in al:
        ao.append(ao[-1] + len(l))
    for l in bl:
        bo.append(bo[-1] + len(l))
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, al, bl, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(bo[j2] - bo[j1]):
                m[bo[j1] + k] = ao[i1] + k
        elif tag == "replace":
            a, b = orig[ao[i1]:ao[i2]], other[bo[j1]:bo[j2]]
            for t, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if t == "equal":
                    for k in range(b2 - b1):
                        m[bo[j1] + b1 + k] = ao[i1] + a1 + k
    m[len(other)] = len(orig)
    return m


def labeller_spans(orig: str, raw: str) -> dict:
    """Parse the labeller's output (lenient) and map its spans onto the original text."""
    p = parse_markup(raw, lenient=True)
    plain = p["plain"]
    out = {"spans": [], "drift": False, "dropped": 0, "errors": len(p["errors"])}
    if plain == orig:
        mapping = None
    else:
        out["drift"] = True
        mapping = align_map(orig, plain)
    for sp in p["spans"]:
        a, b = sp["start"], sp["end"]
        if mapping is not None:
            pos = [mapping[k] for k in range(a, b) if mapping[k] is not None]
            if len(pos) < 0.6 * (b - a):
                out["dropped"] += 1
                continue
            a, b = min(pos), max(pos) + 1
        out["spans"].append({"start": a, "end": b, "label": sp["label"], "kind": sp["kind"]})
    return out


def _ov(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def metrics(cases: list[dict], lab: dict) -> dict:
    """Per-label and overall agreement of gold (cases) against the labeller (lab: id -> spans)."""
    per = {l: Counter() for l in LABELS}
    tot = Counter()
    for c in cases:
        G, L = c["spans"], lab.get(c["id"], {}).get("spans", [])
        ex_g = {(l["start"], l["end"], l["label"]) for l in L}
        ex_l = {(g["start"], g["end"], g["label"]) for g in G}
        for g in G:
            k = per[g["label"]]
            k["gold"] += 1
            k["ex_r"] += (g["start"], g["end"], g["label"]) in ex_g
            k["ov_r"] += any(_ov(g, l) and l["label"] == g["label"] for l in L)
            k["any_r"] += any(_ov(g, l) for l in L)
        for l in L:
            k = per[l["label"]]
            k["lab"] += 1
            k["ex_p"] += (l["start"], l["end"], l["label"]) in ex_l
            k["ov_p"] += any(_ov(g, l) and l["label"] == g["label"] for g in G)
    for k in per.values():
        for f in ("gold", "lab", "ex_r", "ex_p", "ov_r", "ov_p", "any_r"):
            tot[f] += k[f]
    return {"per": per, "total": tot}


def items_for(case: dict, L: list[dict]) -> list[dict]:
    """Disagreement items between one case's gold and the labeller's spans."""
    G = case["spans"]
    gset = {(g["start"], g["end"], g["label"]) for g in G}
    lset = {(l["start"], l["end"], l["label"]) for l in L}
    items = []
    for g in G:
        if (g["start"], g["end"], g["label"]) in lset:
            continue
        ov = [l for l in L if _ov(g, l)]
        same = [l for l in ov if l["label"] == g["label"]]
        if not ov:
            items.append({"type": "missed", "gold": g, "lab": None})
        elif same:
            items.append({"type": "boundary", "gold": g, "lab": same[0]})
        else:
            items.append({"type": "conflict", "gold": g, "lab": ov[0]})
    for l in L:
        if (l["start"], l["end"], l["label"]) in gset or any(_ov(l, g) for g in G):
            continue
        hn = [h for h in case["hard_negatives"] if _ov(l, h)]
        items.append({"type": "labeller_only", "gold": None, "lab": l, "hn": hn[0] if hn else None})
    return items


def _pct(a: int, b: int) -> str:
    return f"{a / b:.3f}" if b else "n/a"


def table(m: dict) -> list[str]:
    rows = ["| label | gold | labeller | exact recall | exact precision | overlap recall | overlap precision |",
            "|---|---|---|---|---|---|---|"]
    for lab in LABELS + ["ALL"]:
        k = m["total"] if lab == "ALL" else m["per"][lab]
        rows.append(f"| {lab} | {k['gold']} | {k['lab']} | {_pct(k['ex_r'], k['gold'])} | {_pct(k['ex_p'], k['lab'])} | "
                    f"{_pct(k['ov_r'], k['gold'])} | {_pct(k['ov_p'], k['lab'])} |")
    return rows


def write_md(path, sections: list[tuple[str, dict, dict]]) -> None:
    """sections: (title, metrics, extra counts). Numbers only."""
    out = ["# AGREEMENT: split F, gold (author, claude-opus-5-5) against blind labelling (claude-sonnet-5-5, "
           "effort xhigh)", "",
           "Numbers only. Definitions: *exact* = same start, end and label; *overlap* = same label and at least one "
           "shared character. Recall = share of gold spans matched; precision = share of labeller spans matched. "
           "Sensitive spans are compared on label and offsets only (the direct / indirect / negative kind is not "
           "compared). Rates are 0-1.", ""]
    for title, m, extra in sections:
        out += [f"## {title}", ""]
        out += [f"- {k}: {v}" for k, v in extra.items()]
        k = m["total"]
        out += [f"- any-label overlap recall (a gold span touched by any labeller span): {_pct(k['any_r'], k['gold'])}",
                ""]
        out += table(m) + [""]
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")