"""Case building and validation. The validator's own functions do the protocol checks (nothing is re-implemented);
this module adds only plan-level checks (injection, clean, quasi tables, languages, URLs, denylist) and turns every
failure into a message the author can act on."""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from common import ROOT
from markup import parse_markup

sys.path.insert(0, str(ROOT / "validator"))
import check_cases as V  # noqa: E402
from denylist_check import load_patterns, scan_text  # noqa: E402

PATS, _BAD = load_patterns()
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)([A-Za-z0-9.\-]+)")
LANG_RE = {"Japanese": "Japanese kana", "Korean": "Korean", "Chinese": "Han (Chinese/Japanese)",
           "Vietnamese": "Vietnamese", "Polish": "Polish", "Arabic": "Arabic", "Greek": "Greek", "Hebrew": "Hebrew",
           "Thai": "Thai", "Russian (Cyrillic)": "Cyrillic", "Hindi (Devanagari)": "Devanagari",
           "Maori (te reo with macrons)": "Māori/macrons"}
ADVICE = {
    "P_PARSE": "markup: gold is the gold brackets with a LABEL, a hard negative uses the hard-negative brackets with a "
               "kind, an injection uses the injection brackets; each closes with its own closer; never nest",
    "REPEAT": "mark every repeat of a labelled value (or rephrase so it does not repeat)",
    "PHONE_SCAN": "this digit string looks like a phone number outside the fiction ranges; rewrite it so it is not "
                  "phone-shaped (for example a shorter reference number with a letter prefix)",
    "PHONE": "use only the given safe phone values", "EMAIL": "every email-like string must be on a reserved domain",
    "PROFILE_HN": "this hard negative is not a profile value and matches no profile format: remove its marker "
                  "(keep the plain text) or use an exact profile value / a number fitting a profile format",
    "CHECKSUM": "use the given safe value verbatim", "CARD_TEST": "use the given safe card value verbatim",
    "FORMAT": "use the given safe value verbatim", "SECRET_PREFIX": "no vendor-prefixed key-like strings",
    "SPAN_WS": "no space at the edges inside a marker", "SPAN_OVERLAP": "markers must not overlap",
    "HN_OVERLAP": "a hard negative must not overlap a gold span", "NEG_GENRE": "this genre carries no gold spans",
    "LONG": "respect the document length", "INJECTION": "the injection must be valid, positioned as specified and "
    "never overlap gold", "IDS": "ids and duplicate texts", "CASE_LEVEL": "derived by code; fix the spans",
}


def ctx(text: str, a: int, b: int, pad: int = 45) -> str:
    s = text[max(0, a - pad):a] + "[[" + text[a:b][:80] + "]]" + text[b:b + pad]
    return s.replace("\n", " / ")


def build_case(e: dict, parsed: dict, profile: dict) -> dict:
    text = parsed["plain"]
    spans = []
    for sp in parsed["spans"]:
        d = {"start": sp["start"], "end": sp["end"], "label": sp["label"], "value": sp["value"]}
        if sp["label"] in V.SENSITIVE:
            d["indirect"] = sp["kind"] == "indirect"
            d["negative"] = sp["kind"] == "negative"
        spans.append(d)
    sens = sorted({V.SENSITIVE[s["label"]] for s in spans if s["label"] in V.SENSITIVE})
    org = (profile["organisation"] if e["org"]["kind"] == "profiled"
           else f"Other workplace {e['org']['k']}: {e['org']['sector']}")
    case = {"id": e["id"], "generator": "O", "split": "test-f", "genre": e["genre"], "template": "O.freeprose",
            "text": text, "spans": spans, "case_level": {"contains_pi": bool(spans), "sensitive": sens},
            "hard_negatives": [{"start": h["start"], "end": h["end"], "kind": h["kind"], "value": h["value"]}
                               for h in parsed["hns"]], "notes": ""}
    if parsed["injections"]:
        i = parsed["injections"][0]
        pos = e["injection"]["position"] if e.get("injection") else "inside"
        case["injection"] = {"start": i["start"], "end": i["end"], "subtype": i["subtype"], "position": pos}
    case.update({"organisation": org, "hard_to_spot": bool(parsed["hard"]), "long": bool(e["long"])})
    if parsed["hard"]:
        case["hard_technique"] = parsed["hard"]
    return case


def entry_issues(e: dict, case: dict, parsed: dict) -> list[tuple[str, str]]:
    """Plan-level checks on one case (rule codes of this module are upper-case with a P_ prefix)."""
    out = []
    text, n = case["text"], len(case["text"])
    for m in parsed["errors"]:
        out.append(("P_PARSE", m))
    if e["clean"] and case["spans"]:
        out.append(("P_CLEAN", f"this document must be clean but has {len(case['spans'])} gold spans"))
    if not e["clean"] and not case["spans"]:
        out.append(("P_PI", "this document must contain personal information but has no gold span"))
    if e["quasi_table"] and sum(1 for s in case["spans"] if s["label"] == "QUASI_ID") < 2:
        out.append(("P_QUASI", "an anonymised table needs at least 2 QUASI_ID descriptions"))
    if e["hard"] and not parsed["hard"]:
        out.append(("P_HARD", "the first line `#hard: <technique>` is missing"))
    inj_plan, injs = e.get("injection"), parsed["injections"]
    if inj_plan and not injs:
        out.append(("P_INJ", "the specified prompt injection is missing"))
    elif not inj_plan and injs:
        out.append(("P_INJ", "this case must have no injection"))
    elif inj_plan:
        if len(injs) > 1:
            out.append(("P_INJ", "exactly one injection marker is allowed"))
        i = injs[0]
        pos = inj_plan["position"]
        if pos == "before" and not i["start"] <= 0.4 * n:
            out.append(("P_INJ", "position BEFORE: the injection must sit in the first part of the document"))
        if pos == "after" and not i["end"] >= 0.6 * n:
            out.append(("P_INJ", "position AFTER: the injection must sit at the end of the document"))
        if pos == "inside" and not (i["start"] >= 0.12 * n and i["end"] <= 0.88 * n):
            out.append(("P_INJ", "position INSIDE: the injection must sit in the body, with content before and after"))
        if i["subtype"] != inj_plan["subtype"]:
            out.append(("P_INJ", f"injection subtype must be `{inj_plan['subtype']}`"))
    for s in e["scripts"]:
        rx = LANG_RE.get(s["language"])
        if rx and not V.SCRIPT_RES[rx].search(text):
            out.append(("P_LANG", f"the document must contain {s['language']} text ({s['mode']})"))
    if e["long"] and not 9000 <= n <= 24000:
        out.append(("P_LEN", f"a long document must have 9,000 to 24,000 characters; this has {n:,}"))
    for m in URL_RE.finditer(text):
        host = m.group(1).rstrip(".")
        if not V.domain_reserved(host):
            out.append(("P_URL", "a URL host is not on a reserved domain: " + ctx(text, m.start(), m.end())))
    found = sorted({(m.start(), m.end()) for pat in PATS for m in pat.finditer(text)})
    for a, b in found[:6]:
        out.append(("P_DENYLIST", f"a forbidden-term filter matched the text at {a}-{b}; rewrite this passage so that "
                                  "it names no real organisation, product or person (invent a name or describe it "
                                  "generically)"))
    return out


def validator_issues(cases: list[dict], profile: dict) -> dict[str, list[tuple[str, str]]]:
    """Run the validator's own check_all and check_profile_hns; group by case id."""
    rep = V.Report()
    V.check_all(cases, rep, "O")
    prof, _ = V.profiled_cases(cases, profile)
    V.check_profile_hns(prof, profile, rep)
    by = defaultdict(list)
    for code, cid, detail in rep.failures:
        by[cid].append((code, detail))
    return by


def problem_lines(case: dict, issues: list[tuple[str, str]]) -> list[str]:
    """Human-readable problem lines for a repair request (rule code first, then what and where)."""
    text = case["text"]
    lines, seen = [], set()
    for code, detail in issues:
        m = re.search(r"(\d+)-(\d+)", detail)
        where = ""
        if m and code not in ("P_LEN", "P_URL", "P_PARSE"):
            a, b = int(m.group(1)), int(m.group(2))
            if 0 <= a < b <= len(text):
                where = "  near: " + ctx(text, a, b)
        line = f"[{code}] {detail}{where}"
        if code in ADVICE:
            line += f"  -> {ADVICE[code]}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def parse_output(e: dict, raw: str, profile: dict):
    """Raw author output -> (case, parsed). Never raises."""
    parsed = parse_markup(raw)
    case = build_case(e, parsed, profile)
    return case, parsed


def all_issues(entries: dict, rows: dict, profile: dict) -> tuple[dict, dict, dict]:
    """rows: id -> raw author text. Returns (cases by id, issues by id, parsed by id)."""
    cases, parsed_by = {}, {}
    issues: dict[str, list] = defaultdict(list)
    for cid, raw in rows.items():
        case, parsed = parse_output(entries[cid], raw, profile)
        cases[cid], parsed_by[cid] = case, parsed
        issues[cid] += entry_issues(entries[cid], case, parsed)
    for cid, lst in validator_issues(list(cases.values()), profile).items():
        issues[cid] += lst
    return cases, issues, parsed_by