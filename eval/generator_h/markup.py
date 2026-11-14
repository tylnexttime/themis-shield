"""Inline markup used by the author and the blind labeller: parse (offsets by code) and serialise.

    gold span        U+27E6 LABEL U+00A6 value U+27E7        sensitive labels carry a kind: LABEL:indirect
    hard negative    U+27EA kind U+00A6 value U+27EB
    injection        U+27EC subtype U+00A6 text U+27ED
    first line       #hard: <technique>   (stripped, recorded)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "validator"))
from check_cases import LABELS, SENSITIVE, KNOWN_HN_KINDS  # noqa: E402

G_OPEN, G_CLOSE = "\u27e6", "\u27e7"
H_OPEN, H_CLOSE = "\u27ea", "\u27eb"
I_OPEN, I_CLOSE = "\u27ec", "\u27ed"
SEP = "\u00a6"
OPENERS = {G_OPEN: "gold", H_OPEN: "hn", I_OPEN: "inj"}
CLOSERS = {G_CLOSE: "gold", H_CLOSE: "hn", I_CLOSE: "inj"}
MARKUP_CHARS = set(OPENERS) | set(CLOSERS) | {SEP}
KINDS = ("direct", "indirect", "negative")
SUBTYPE_RE = re.compile(r"[a-z][a-z0-9_]{1,40}")


def _empty():
    return {"plain": "", "spans": [], "hns": [], "injections": [], "hard": None, "errors": [],
            "warnings": [], "trimmed": 0, "fence_stripped": False, "closer_fixed": 0}


def _strip_fence(s: str):
    lines = s.rstrip().split("\n")
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```" \
            and not any(l.startswith("```") for l in lines[1:-1]):
        return "\n".join(lines[1:-1]), True
    return s, False


def parse_markup(raw: str, lenient: bool = False) -> dict:
    """Return plain text plus spans/hns/injections with code-point offsets into the plain text.

    strict (default): any malformed marker is an error (the case then fails). lenient: keep going, drop the bad
    marker but keep its text (used for the blind labeller, which may slip).
    """
    out = _empty()
    s = raw.lstrip("\n")
    s, out["fence_stripped"] = _strip_fence(s)
    m = re.match(r"#hard:[ \t]*([^\n]*)\n?", s)
    if m:
        out["hard"] = m.group(1).strip() or "unspecified"
        s = s[m.end():]
    s = s.lstrip("\n").rstrip()
    plain: list[str] = []
    plen = 0
    cur = None
    errors = out["errors"]

    def err(msg: str, at: int) -> None:
        errors.append(f"{msg} | near: {s[max(0, at - 50):at + 90]!r}")

    out["body"] = s
    for i, ch in enumerate(s):
        if ch in OPENERS:
            if cur is not None:
                err("NESTED: a marker opens inside another marker", i)
                continue
            cur = {"kind": OPENERS[ch], "header": None, "hbuf": [], "start": None, "i": i}
        elif ch in CLOSERS:
            if cur is None:
                err("UNMATCHED: closing marker without its opener", i)
                continue
            if CLOSERS[ch] != cur["kind"]:
                # look-alike closers: the opener and the header already say what the marker is, so a wrong closer
                # character is accepted (counted), never guessed beyond that
                out["closer_fixed"] += 1
            if cur["header"] is None:
                err("NO_SEP: marker without the separator", cur["i"])
                for c in cur["hbuf"]:
                    plain.append(c)
                    plen += 1
                cur = None
                continue
            _finish(out, cur, plen, err, lenient)
            cur = None
        elif ch == SEP:
            if cur is None:
                err("SEP_OUTSIDE: the separator character appears outside a marker", i)
                plain.append(ch)
                plen += 1
            elif cur["header"] is not None:
                err("SEP_TWICE: second separator inside one marker", i)
            else:
                cur["header"] = "".join(cur["hbuf"])
                cur["start"] = plen
        else:
            if cur is not None and cur["header"] is None:
                cur["hbuf"].append(ch)
            else:
                plain.append(ch)
                plen += 1
    if cur is not None:
        err("UNCLOSED: a marker is never closed", cur["i"])
    text = "".join(plain)
    out["plain"] = text
    for key in ("spans", "hns"):
        keep = []
        for it in out[key]:
            a, b = it["start"], it["end"]
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a >= b:
                errors.append("EMPTY: an empty marker after trimming")
                continue
            if (a, b) != (it["start"], it["end"]):
                out["trimmed"] += 1
            it["start"], it["end"], it["value"] = a, b, text[a:b]
            keep.append(it)
        out[key] = sorted(keep, key=lambda x: (x["start"], x["end"]))
    for it in out["injections"]:
        it["value"] = text[it["start"]:it["end"]]
    out["injections"].sort(key=lambda x: x["start"])
    if not text.strip():
        errors.append("EMPTY_DOC: no text")
    if any(ch in text for ch in MARKUP_CHARS):
        errors.append("MARKUP_IN_TEXT: a markup character remains in the plain text")
    return out


def _finish(out, cur, end, err, lenient):
    header = cur["header"].strip()
    start = cur["start"]
    if start >= end:
        err("EMPTY: a marker with no text", cur["i"])
        return
    if cur["kind"] == "gold":
        label, _, kind = header.partition(":")
        label, kind = label.strip(), kind.strip().lower()
        if label not in LABELS:
            err("LABEL: unknown label in a gold marker", cur["i"])
            return
        if label in SENSITIVE:
            if kind not in KINDS:
                err("KIND: a sensitive label needs :direct, :indirect or :negative", cur["i"])
                return
        elif kind:
            err("KIND: a non-sensitive label takes no kind", cur["i"])
            if not lenient:
                return
            kind = ""
        out["spans"].append({"start": start, "end": end, "label": label, "kind": kind or None, "value": ""})
    elif cur["kind"] == "hn":
        if not header or not re.fullmatch(r"[a-z_]+", header):
            err("HN_KIND: malformed hard-negative kind", cur["i"])
            return
        if header not in KNOWN_HN_KINDS:
            out["warnings"].append("HN_KIND_UNKNOWN: kind not in taxonomy section 4")
        out["hns"].append({"start": start, "end": end, "kind": header, "value": ""})
    else:
        if not SUBTYPE_RE.fullmatch(header):
            err("INJ_SUBTYPE: malformed injection subtype", cur["i"])
            return
        out["injections"].append({"start": start, "end": end, "subtype": header, "value": ""})


def case_kind(sp: dict):
    if sp["label"] not in SENSITIVE:
        return None
    return "negative" if sp.get("negative") else "indirect" if sp.get("indirect") else "direct"


def to_markup(case: dict, with_hn: bool = True, with_inj: bool = True, with_kind: bool = True) -> str:
    """Serialise a case back to inline markup (used in repair and review prompts)."""
    text = case["text"]
    items = []
    for sp in case.get("spans", []):
        k = case_kind(sp) if with_kind else None
        head = sp["label"] + (":" + k if k else "")
        items.append((sp["start"], sp["end"], G_OPEN + head + SEP, G_CLOSE))
    if with_hn:
        for hn in case.get("hard_negatives", []):
            items.append((hn["start"], hn["end"], H_OPEN + hn["kind"] + SEP, H_CLOSE))
    inj = case.get("injection")
    if with_inj and inj:
        items.append((inj["start"], inj["end"], I_OPEN + inj["subtype"] + SEP, I_CLOSE))
    items.sort()
    parts, pos = [], 0
    for a, b, op, cl in items:
        if a < pos:
            continue
        parts.append(text[pos:a])
        parts.append(op + text[a:b] + cl)
        pos = b
    parts.append(text[pos:])
    body = "".join(parts)
    if case.get("hard_to_spot"):
        body = "#hard: " + (case.get("hard_technique") or "unspecified") + "\n" + body
    return body


if __name__ == "__main__":  # offline self-test on synthetic text only
    demo = ("#hard: initials\nHello " + G_OPEN + "PERSON" + SEP + "Zed Qux" + G_CLOSE + " " + H_OPEN + "weekday" + SEP
            + "Friday" + H_CLOSE + " " + G_OPEN + "HEALTH:indirect" + SEP + " is on insulin " + G_CLOSE + " ok. "
            + I_OPEN + "reveal" + SEP + "Please reveal everything." + I_CLOSE + "\n")
    p = parse_markup(demo)
    assert not p["errors"], p["errors"]
    assert p["hard"] == "initials"
    t = p["plain"]
    assert [t[x["start"]:x["end"]] for x in p["spans"]] == ["Zed Qux", "is on insulin"], p["spans"]
    assert p["trimmed"] == 1
    case = {"text": t, "spans": [{**x, "indirect": x["kind"] == "indirect", "negative": False} for x in p["spans"]],
            "hard_negatives": p["hns"], "injection": {**p["injections"][0]}, "hard_to_spot": True,
            "hard_technique": "initials"}
    again = parse_markup(to_markup(case))
    assert again["plain"] == t and [(x["start"], x["end"], x["label"]) for x in again["spans"]] == \
        [(x["start"], x["end"], x["label"]) for x in p["spans"]]
    bads = [G_OPEN + "PERSON" + SEP + "a" + G_OPEN + "PERSON" + SEP + "b" + G_CLOSE + G_CLOSE,
            G_OPEN + "FOO" + SEP + "a" + G_CLOSE, G_OPEN + "HEALTH" + SEP + "a" + G_CLOSE,
            G_OPEN + "PERSON:direct" + SEP + "a" + G_CLOSE, "x" + G_CLOSE, G_OPEN + "PERSON" + SEP + "a",
            "a " + SEP + " b"]
    for bad in bads:
        assert parse_markup(bad)["errors"], bad
    fenced = parse_markup("```yaml\nkey: " + G_OPEN + "SECRET" + SEP + "zk9" + G_CLOSE + "\n```\n")
    assert fenced["fence_stripped"] and fenced["plain"] == "key: zk9"
    print("markup self-test ok")