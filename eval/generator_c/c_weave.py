"""Weave: a flat list of parts rendered to text plus offsets, and the validators that gate generator C's build.

A template returns a list whose items are plain strings, S(label, value) gold spans, N(kind, value) hard negatives,
INJ(subtype, position, text) prompt-injection payloads, or nested lists of those. Nothing else is needed to produce
offsets, so no template ever counts characters by hand. Python 3.12 standard library only.
"""
from __future__ import annotations

import re
import unicodedata

from c_pools import (HARBOURLINE, TEST_CARDS, WORD_DIGITS, abn_ok, au_1300_fiction, au_landline_fiction, au_mobile_fiction,
                     email_domain_ok, iban_ok, luhn_ok, medicare_ok, nino_ok, ssn_ok, tfn_ok, uk_fiction, us_fiction)

GOLD_LABELS = (
    "PERSON", "USERNAME", "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT",
    "BANK_ACCOUNT", "PAYMENT_CARD", "EMAIL", "PHONE", "ADDRESS", "DATE_OF_BIRTH", "OTHER_DATE", "URL_PERSONAL",
    "SECRET", "QUASI_ID", "HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL",
    "FAMILY_VIOLENCE", "UNION",
)
SENSITIVE = {"HEALTH": "health", "DISABILITY": "disability", "ETHNICITY": "ethnicity", "SEXUALITY": "sexuality",
             "RELIGION": "religion", "CRIMINAL": "criminal", "FAMILY_VIOLENCE": "family_violence", "UNION": "union"}
# labels whose every repeat in a text must itself be labelled (sensitive phrases and quasi-ids are prose, exempt)
REPEAT_LABELS = {"PERSON", "USERNAME", "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT",
                 "BANK_ACCOUNT", "PAYMENT_CARD", "EMAIL", "PHONE", "ADDRESS", "DATE_OF_BIRTH", "OTHER_DATE", "URL_PERSONAL", "SECRET"}
LONG_MIN, LONG_MAX = 6000, 12500


class BuildError(Exception):
    pass


def S(label: str, value: str):
    if label not in GOLD_LABELS:
        raise BuildError(f"unknown label {label}")
    return ("S", label, value)


def N(kind: str, value: str):
    return ("N", kind, value)


def INJ(subtype: str, position: str, text: str):
    return ("I", subtype, position, text)


def flatten(parts):
    for p in parts:
        if isinstance(p, (list, tuple)) and not (isinstance(p, tuple) and p and p[0] in ("S", "N", "I")):
            yield from flatten(p)
        else:
            yield p


def render(parts):
    """Return text, spans, hard_negatives, injection (or None)."""
    buf, spans, negs, inj = [], [], [], None
    pos = 0
    for p in flatten(parts):
        if isinstance(p, str):
            buf.append(p)
            pos += len(p)
        elif p[0] == "S":
            _, label, value = p
            if not value or value != value.strip():
                raise BuildError(f"span value with outer whitespace or empty: {value!r}")
            spans.append({"start": pos, "end": pos + len(value), "label": label, "value": value})
            buf.append(value)
            pos += len(value)
        elif p[0] == "N":
            _, kind, value = p
            if not value or value != value.strip():
                raise BuildError(f"hard negative with outer whitespace or empty: {value!r}")
            negs.append({"start": pos, "end": pos + len(value), "kind": kind, "value": value})
            buf.append(value)
            pos += len(value)
        elif p[0] == "I":
            _, subtype, position, text = p
            if inj is not None:
                raise BuildError("two injections in one case")
            inj = {"start": pos, "end": pos + len(text), "subtype": subtype, "position": position}
            buf.append(text)
            pos += len(text)
        else:
            raise BuildError(f"bad part {p!r}")
    text = "".join(buf)
    assert len(text) == pos
    return text, spans, negs, inj


# ----------------------------------------------------------------------------------------------------------------------
# Harbourline allow-list: every hard negative in a Harbourline case must come from the profile
# ----------------------------------------------------------------------------------------------------------------------
def harbourline_allowed():
    h = HARBOURLINE
    vals = set(h["forms"]) | set(h["abn_forms"]) | set(h["buildings"]) | set(h["mailboxes"]) | set(h["roles"]) | set(h["committees"]) | set(h["initiatives"])
    for s in h["sites"]:
        vals.update(s["forms"])
        vals.add(s["reception"])
        vals.add(s["address"])
    for purpose, num in h["lines"]:
        vals.add(num)
    for code, title in h["products"] + h["projects"]:
        vals.update((code, title))
    for event, date in h["public_dates"]:
        vals.update((event, date))
    regexes = [re.compile(r) for r in h["refs"].values()] + [re.compile(r"HLP-\d{4}"), re.compile(r"RP-20\d{2}-\d{3}")]
    return vals, regexes


HL_VALUES, HL_REGEXES = harbourline_allowed()


def hl_allowed(value: str) -> bool:
    return value in HL_VALUES or any(r.fullmatch(value) for r in HL_REGEXES)


# ----------------------------------------------------------------------------------------------------------------------
# Value checks per label
# ----------------------------------------------------------------------------------------------------------------------
ZW = "​‌‍⁠﻿"


def digits_of(v: str) -> str:
    v = unicodedata.normalize("NFKC", v)
    return re.sub(r"[^\d]", "", v)


def phone_fiction(value: str) -> bool:
    v = value.lower()
    if re.search(r"[a-z]{3}", v):  # spelled out
        words = re.findall(r"[a-z]+", v)
        out = []
        for i, w in enumerate(words):
            if w == "double":
                continue
            d = WORD_DIGITS.get(w)
            if d is None:
                return False
            out.append(d)
            if i > 0 and words[i - 1] == "double":
                out.append(d)
        digits = "".join(out)
    else:
        v2 = re.sub(r"\(0\)", "", unicodedata.normalize("NFKC", value))
        digits = re.sub(r"\D", "", v2)
    for pre, repl in (("61", "0"), ("44", "0"), ("1", "")):
        if value.strip().startswith("+") and digits.startswith(pre):
            cand = repl + digits[len(pre):]
            if pre == "1" and len(digits) == 11:
                return us_fiction(cand)
            if pre == "61" and len(digits) in (11,):
                return au_mobile_fiction(cand) or au_landline_fiction(cand)
            if pre == "44" and len(digits) == 12:
                return uk_fiction(cand)
    return au_mobile_fiction(digits) or au_landline_fiction(digits) or uk_fiction(digits) or us_fiction(digits)


def check_value(label: str, value: str) -> str | None:
    """Return an error string or None."""
    d = digits_of(value)
    if label == "TFN":
        return None if tfn_ok(d) else f"TFN checksum fails: {value!r}"
    if label == "MEDICARE":
        return None if medicare_ok(d) else f"Medicare checksum fails: {value!r}"
    if label == "ABN":
        return None if abn_ok(d) else f"ABN checksum fails: {value!r}"
    if label == "NATIONAL_ID":
        return None if (ssn_ok(value) or nino_ok(value)) else f"NATIONAL_ID outside reserved ranges: {value!r}"
    if label == "DRIVER_LICENCE":
        return None if re.fullmatch(r"\d{8,10}", d) and not re.search(r"[A-Za-z]", value) else f"DRIVER_LICENCE format: {value!r}"
    if label == "PASSPORT":
        return None if re.fullmatch(r"[A-Z]{1,2}\d{7}", value.replace(" ", "")) else f"PASSPORT format: {value!r}"
    if label == "BANK_ACCOUNT":
        if iban_ok(value):
            return None
        if re.fullmatch(r"\d{3}-\d{3}(?:[ /]\d{6,10})?", value) or re.fullmatch(r"\d{6,10}", value):
            return None
        return f"BANK_ACCOUNT format: {value!r}"
    if label == "PAYMENT_CARD":
        return None if (d in TEST_CARDS and luhn_ok(d)) else f"PAYMENT_CARD not a published Luhn-valid test number: {value!r}"
    if label == "SECRET":
        if len(value) < 3 or re.match(r"(sk-|AKIA|ghp_|gho_|xox[abp]-|AIza|SG\.|glpat-)", value):
            return f"SECRET looks like a vendor key or is too short: {value!r}"
        return None
    if label == "EMAIL":
        if "@" in value:
            return None if email_domain_ok(value) else f"EMAIL not on a reserved domain: {value!r}"
        return None if "example" in value.lower() else f"obfuscated EMAIL not on example domain: {value!r}"
    if label == "PHONE":
        return None if phone_fiction(value) else f"PHONE outside fiction ranges: {value!r}"
    if label == "URL_PERSONAL":
        m = re.match(r"(?:https?://)?([^/\s]+)", value)
        return None if (m and email_domain_ok("x@" + m.group(1))) else f"URL_PERSONAL not on reserved domain: {value!r}"
    if label in ("DATE_OF_BIRTH", "OTHER_DATE"):
        return None if re.search(r"\d", value) else f"date without digits: {value!r}"
    if label == "INTERNAL_ID":
        return None if re.search(r"\d", value) else f"INTERNAL_ID without digits: {value!r}"
    return None


# ----------------------------------------------------------------------------------------------------------------------
# Whole-text scans
# ----------------------------------------------------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBFUSCATED_RE = re.compile(r"\b[\w.]+(?: dot \w+)* at (\w+(?: dot \w+)+)", re.I)


def scan_emails(text: str) -> list[str]:
    errs = []
    for m in EMAIL_RE.finditer(text):
        if not email_domain_ok(m.group(0)):
            errs.append(f"email-like string outside reserved domains: {m.group(0)!r}")
    for m in OBFUSCATED_RE.finditer(text):
        if "example" not in m.group(1).lower():
            errs.append(f"obfuscated email outside example domain: {m.group(0)!r}")
    return errs


def scan_phones(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\(0\)", "", t)
    t = re.sub(r"(?<=\d)[ .\- ]+(?=\d)", "", t)  # join digit groups
    t = re.sub(r"\((\d+)\)", r"\1", t)
    t = re.sub(r"(?<=\d)[ .\-]+(?=\d)", "", t)
    errs = []
    for m in re.finditer(r"(?<![\d])(?:\+?61|0)([2-478])(\d{8})(?![\d])", t):
        nat = "0" + m.group(1) + m.group(2)
        if nat.startswith("04"):
            if not au_mobile_fiction(nat):
                errs.append(f"non-fiction AU mobile-like string: {m.group(0)!r}")
        elif not au_landline_fiction(nat):
            errs.append(f"non-fiction AU landline-like string: {m.group(0)!r}")
    for m in re.finditer(r"(?<!\d)1[38]00\d{6}(?!\d)", t):
        if not au_1300_fiction(m.group(0)):
            errs.append(f"non-fiction 1300/1800 string: {m.group(0)!r}")
    for m in re.finditer(r"(?<!\d)(?:\+?44|0)7(\d{9})(?!\d)", t):
        if not uk_fiction("07" + m.group(1)):
            errs.append(f"non-fiction UK mobile-like string: {m.group(0)!r}")
    for m in re.finditer(r"(?<!\d)(?:\+?1)?(\d{3}555\d{4})(?!\d)", t):
        if not us_fiction(m.group(1)):
            errs.append(f"non-fiction US 555 string: {m.group(0)!r}")
    return errs


def word_pattern(value: str) -> re.Pattern:
    pat = re.escape(value)
    if value[0].isalnum():
        pat = r"(?<!\w)" + pat
    if value[-1].isalnum():
        pat = pat + r"(?!\w)"
    return re.compile(pat)


# ----------------------------------------------------------------------------------------------------------------------
# Case validation
# ----------------------------------------------------------------------------------------------------------------------
def validate_case(c: dict, *, is_hl: bool, clean: bool, long: bool) -> list[str]:
    errs = []
    text, spans, negs = c["text"], c["spans"], c["hard_negatives"]
    if " " in text or " " in text:
        errs.append("text contains U+2028/U+2029")
    # spans
    last = -1
    for s in spans:
        if not (0 <= s["start"] < s["end"] <= len(text)):
            errs.append(f"span outside text {s}")
            continue
        if text[s["start"]:s["end"]] != s["value"]:
            errs.append(f"span value mismatch {s} vs {text[s['start']:s['end']]!r}")
        if s["value"] != s["value"].strip():
            errs.append(f"span has outer whitespace {s}")
        if s["start"] < last:
            errs.append(f"spans not sorted or overlapping at {s}")
        last = s["end"]
        if s["label"] not in GOLD_LABELS:
            errs.append(f"unknown label {s['label']}")
        e = check_value(s["label"], s["value"])
        if e:
            errs.append(e)
    # hard negatives
    for h in negs:
        if not (0 <= h["start"] < h["end"] <= len(text)) or text[h["start"]:h["end"]] != h["value"]:
            errs.append(f"hard negative mismatch {h}")
        for s in spans:
            if s["start"] < h["end"] and h["start"] < s["end"]:
                errs.append(f"hard negative overlaps gold: {h} / {s}")
        if is_hl and not hl_allowed(h["value"]):
            errs.append(f"Harbourline hard negative not in profile: {h['value']!r}")
    for i, a in enumerate(negs):
        for b in negs[i + 1:]:
            if a["start"] < b["end"] and b["start"] < a["end"]:
                errs.append(f"hard negatives overlap: {a} / {b}")
    # injection
    inj = c.get("injection")
    if inj:
        if not (0 <= inj["start"] < inj["end"] <= len(text)):
            errs.append("injection outside text")
        for s in spans:
            if s["start"] < inj["end"] and inj["start"] < s["end"]:
                errs.append(f"injection overlaps gold {s}")
        if inj["position"] not in ("before", "inside", "after"):
            errs.append(f"bad injection position {inj['position']}")
    # case level
    cl = c["case_level"]
    if cl["contains_pi"] != bool(spans):
        errs.append("contains_pi disagrees with spans")
    cats = sorted({SENSITIVE[s["label"]] for s in spans if s["label"] in SENSITIVE})
    if cl["sensitive"] != cats:
        errs.append(f"sensitive categories {cl['sensitive']} != {cats}")
    if clean and spans:
        errs.append("clean genre has spans")
    if long and not (LONG_MIN <= len(text) <= LONG_MAX):
        errs.append(f"long document length {len(text)} outside {LONG_MIN}..{LONG_MAX}")
    # repeats
    covered = [(x["start"], x["end"]) for x in spans] + [(x["start"], x["end"]) for x in negs]
    seen = set()
    for s in spans:
        if s["label"] not in REPEAT_LABELS or s["value"] in seen or len(s["value"]) < 2:
            continue
        seen.add(s["value"])
        for m in word_pattern(s["value"]).finditer(text):
            if not any(a <= m.start() and m.end() <= b for a, b in covered):
                errs.append(f"unlabelled repeat of {s['label']} value {s['value']!r} at {m.start()}")
    errs += scan_emails(text)
    errs += scan_phones(text)
    return errs


def validate_set(cases: list[dict], *, label_min: int, clean_min: int, table_min: int, inj_min: int) -> list[str]:
    errs = []
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        errs.append("duplicate ids")
    texts = [c["text"] for c in cases]
    if len(set(texts)) != len(texts):
        errs.append("duplicate texts")
    counts = {L: 0 for L in GOLD_LABELS}
    for c in cases:
        for s in c["spans"]:
            counts[s["label"]] += 1
    for L, n in counts.items():
        if n < label_min:
            errs.append(f"label {L} has {n} gold spans, below {label_min}")
    n_clean = sum(1 for c in cases if not c["spans"])
    if n_clean < clean_min:
        errs.append(f"only {n_clean} clean cases, below {clean_min}")
    n_tab = sum(1 for c in cases if c["genre"] == "table")
    if n_tab < table_min:
        errs.append(f"only {n_tab} table cases, below {table_min}")
    n_inj = sum(1 for c in cases if c.get("injection"))
    if n_inj < inj_min:
        errs.append(f"only {n_inj} injection cases, below {inj_min}")
    return errs


def known_answer_tests():
    """Fail loudly if the checksum code is wrong."""
    assert tfn_ok("123456782"), "TFN KAT"
    assert not tfn_ok("123456789"), "TFN KAT neg"
    assert abn_ok(HARBOURLINE["abn"].replace(" ", "")), "profile ABN"
    assert not abn_ok("96444148270"), "ABN KAT neg"
    assert medicare_ok("2123456701"), "Medicare KAT"
    assert not medicare_ok("2123456711"), "Medicare KAT neg"
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112"), "Luhn KAT"
    assert iban_ok("GB82WEST12345698765432"), "IBAN KAT"
    assert not iban_ok("GB82WEST12345698765433"), "IBAN KAT neg"
    assert phone_fiction("0491 570 006") and phone_fiction("+61 (0) 491 570 006") and phone_fiction("(02) 5550 0141")
    assert phone_fiction("07700 900123") and phone_fiction("(212) 555-0142") and phone_fiction("+44 7700 900 456")
    assert phone_fiction("zero four nine one five seven zero zero zero six")
    assert not phone_fiction("0412 345 678") and not phone_fiction("(02) 9876 5432")
    assert scan_phones("call 0412 345 678") and not scan_phones("call 0491 570 006 or (02) 5550 0141 or 1300 975 707")
    assert scan_emails("x@gmail.com") and not scan_emails("x@example.com y@learner.harbourline.example")
    assert email_domain_ok("a@b.example") and not email_domain_ok("a@example.co")
