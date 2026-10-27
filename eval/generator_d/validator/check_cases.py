#!/usr/bin/env python3
"""check_cases.py: independent validator for one half of the split-d held-out test set.

Protocol, not content. Python 3.12 standard library only. It re-checks every rule in
inputs/format.md "What the build must check" and the brief's "Safety of values" with its own
checksum code (a third implementation, independent of both generators), and optionally reports
quota counts against inputs/brief.md.

Usage:
    python -I check_cases.py <cases.jsonl> [--profile profile.json] [--quotas] [--half Q]
                             [--json-out report.json] [--max-report 60]
    python -I check_cases.py --kat              # known-answer tests only
    python -I check_cases.py --mutation-test    # prove that the checks reject known breaches

Exit codes: 0 = clean; 1 = at least one rule breach (or quota shortfall with --quotas);
2 = known-answer tests failed (nothing else is trusted then); 3 = usage / unreadable input.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------------------------
# Taxonomy constants (from inputs/taxonomy.md)
# --------------------------------------------------------------------------------------------
LABELS = [
    "PERSON", "USERNAME", "EMAIL", "PHONE", "ADDRESS", "URL_PERSONAL",
    "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT",
    "BANK_ACCOUNT", "PAYMENT_CARD", "SECRET",
    "DATE_OF_BIRTH", "OTHER_DATE", "QUASI_ID",
    "HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL", "FAMILY_VIOLENCE", "UNION",
]
assert len(LABELS) == 27
SENSITIVE = {
    "HEALTH": "health", "DISABILITY": "disability", "ETHNICITY": "ethnicity", "SEXUALITY": "sexuality",
    "RELIGION": "religion", "CRIMINAL": "criminal", "FAMILY_VIOLENCE": "family_violence", "UNION": "union",
}
KNOWN_HN_KINDS = {
    "organisation", "organisation_abn", "business_identifier", "site", "building", "room", "site_code",
    "business_address", "site_address", "role_mailbox", "business_phone", "reception_phone", "role_title",
    "programme_code", "programme_title", "module_code", "project_code", "project_title", "product_code",
    "service_name", "event_name", "reference_number", "ticket_number", "case_number", "order_number",
    "invoice_number", "asset_id", "cost_centre", "build_number", "software_version", "form_number",
    "payroll_group", "provider_number", "client_account", "public_date", "meeting_date", "letter_date",
    "release_date", "weekday", "public_figure", "public_place", "fictional_character", "fiction_statement",
    "public_news", "committee", "initiative", "public_event", "policy_statement",
}
PHONE_HN_KINDS = {"business_phone", "reception_phone"}
EMAIL_HN_KINDS = {"role_mailbox"}
# Labels whose exact repeats must be labelled too (sensitive predicates and QUASI_ID are phrases).
REPEAT_LABELS = set(LABELS) - set(SENSITIVE) - {"QUASI_ID"}
NEGATIVE_GENRE_TOKENS = {"fiction", "news", "faq", "negative", "clean"}
LONG_MIN, LONG_MAX = 8000, 26000
REQUIRED_CASE_KEYS = ["id", "generator", "split", "genre", "template", "text", "spans", "case_level",
                      "hard_negatives"]

RULES = {  # rule code -> the rule text it enforces (quoted from format.md / brief.md / taxonomy.md)
    "JSONL": "no characters that break line-based JSONL readers",
    "SCHEMA": "case format (format.md 'Case format')",
    "IDS": "no duplicate ids or texts; ids prefixed with the half letter; generator/split fields",
    "SPAN_VALUE": "text[start:end] == value for every span and hard negative",
    "SPAN_BOUNDS": "spans ... inside the text",
    "SPAN_SORT": "spans sorted",
    "SPAN_WS": "spans ... without outer whitespace",
    "SPAN_OVERLAP": "spans ... never overlapping",
    "LABEL": "labels are the 27 taxonomy labels",
    "HN_OVERLAP": "hard negatives never overlap gold",
    "CHECKSUM": "Checksums: TFN (mod 11), Medicare (mod 10 + issue number), ABN (mod 89), payment cards "
                "(Luhn), IBAN (mod 97); SSNs only in 987-65-4320..4329; NINOs only with the QQ prefix",
    "CARD_TEST": "payment cards only from card networks' published test numbers",
    "FORMAT": "taxonomy section 2 format rules (PASSPORT one or two letters + 7 digits; DRIVER_LICENCE 8 to 10 "
              "digits; TFN 9 digits; MEDICARE 10 digits; ABN 11 digits)",
    "SECRET_PREFIX": "Secrets are random strings with no vendor prefix",
    "PHONE": "Every PHONE span, and every institutional phone, is in a fiction range",
    "PHONE_SCAN": "A whole-text scan also rejects any mobile-like string outside the list",
    "EMAIL": "Every email span and every email-like string anywhere in the text is on a reserved domain",
    "REPEAT": "Every repeat of a labelled value in the same case is itself labelled (or institutional)",
    "CASE_LEVEL": "case_level agrees with the spans (contains_pi exactly when a gold span exists; sensitive lists "
                  "the categories of its sensitive spans)",
    "INJECTION": "injection records are valid and never overlap gold",
    "NEG_GENRE": "negative genres have no spans",
    "LONG": "long documents are 8,000 to 26,000 characters",
    "PROFILE": "profile.json per profile-schema.md and the brief's safety rules",
    "PROFILE_HN": "every hard negative in [profiled-organisation] cases is a value from your profile.json or "
                  "matches one of its reference formats",
    "MANIFEST": "manifest.json: ... sha256 of cases.jsonl",
}

# --------------------------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------------------------
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­᠎"), None)
NUM_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "nil": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
REPEATERS = {"double": 2, "triple": 3}


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s).translate(ZERO_WIDTH)


def digits_of(value: str) -> str:
    """Digits of a numeric value written in any form: separators, full-width, zero-width,
    spelled-out words ('zero four nine one'), 'double five'."""
    s = nfkc(value).lower()
    out: list[str] = []
    pending = 1
    for tok in re.findall(r"[a-z]+|\d", s):
        if tok.isdigit():
            out.append(tok * pending)
            pending = 1
        elif tok in REPEATERS:
            pending = REPEATERS[tok]
        elif tok in NUM_WORDS:
            out.append(NUM_WORDS[tok] * pending)
            pending = 1
    return "".join(out)


def alnum_upper(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", nfkc(value)).upper()


# --------------------------------------------------------------------------------------------
# Checksums: own implementations
# --------------------------------------------------------------------------------------------
def abn_detail(d: str) -> str:
    if not re.fullmatch(r"\d{11}", d):
        return f"{len(d)} digits"
    w = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    ds = [int(c) for c in d]
    ds[0] -= 1
    t = sum(a * b for a, b in zip(ds, w))
    return (f"validator computes: first digit minus 1, weights (10,1,3,5,7,9,11,13,15,17,19), weighted sum {t}, "
            f"{t} mod 89 = {t % 89} (must be 0)")


def tfn_detail(d: str) -> str:
    t = sum(int(c) * k for c, k in zip(d, (1, 4, 3, 7, 5, 8, 6, 9, 10)))
    return f"validator computes: weights (1,4,3,7,5,8,6,9,10), weighted sum {t}, {t} mod 11 = {t % 11} (must be 0)"


def medicare_detail(d: str) -> str:
    t = sum(int(c) * k for c, k in zip(d[:8], (1, 3, 7, 9, 1, 3, 7, 9)))
    return (f"validator computes: weights (1,3,7,9,1,3,7,9) on digits 1-8, sum {t}, {t} mod 10 = {t % 10} must equal "
            f"digit 9 ({d[8]}); issue number digit 10 = {d[9]} must be 1-9")


def tfn_ok(d: str) -> bool:
    if not re.fullmatch(r"\d{9}", d):
        return False
    w = (1, 4, 3, 7, 5, 8, 6, 9, 10)
    return sum(int(c) * k for c, k in zip(d, w)) % 11 == 0


def medicare_ok(d: str) -> bool:
    if not re.fullmatch(r"\d{10}", d):
        return False
    w = (1, 3, 7, 9, 1, 3, 7, 9)
    check = sum(int(c) * k for c, k in zip(d[:8], w)) % 10
    return check == int(d[8]) and 1 <= int(d[9]) <= 9


REAL_ABNS = {"51824753556"}  # the ATO's own ABN (widely used as a documentation example): real, never "invented"


def abn_ok(d: str) -> bool:
    if not re.fullmatch(r"\d{11}", d) or d[0] == "0":
        return False
    w = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    ds = [int(c) for c in d]
    ds[0] -= 1
    return sum(a * b for a, b in zip(ds, w)) % 89 == 0


def luhn_ok(d: str) -> bool:
    if not re.fullmatch(r"\d{2,}", d):
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = alnum_upper(s)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    r = s[4:] + s[:4]
    num = "".join(str(int(ch, 36)) for ch in r)
    rem = 0
    for i in range(0, len(num), 9):  # piecewise mod, no big-int shortcut
        rem = int(str(rem) + num[i:i + 9]) % 97
    return rem == 1


def ssn_ok(d: str) -> bool:
    return re.fullmatch(r"98765432\d", d) is not None


def nino_ok(s: str) -> bool:
    return re.fullmatch(r"QQ\d{6}[A-D]?", alnum_upper(s)) is not None


# Card networks' / payment providers' published test numbers (Stripe, Braintree, Adyen, Worldpay,
# eWAY and network documentation). Entries that fail Luhn are dropped at load (guard against typos).
_TEST_CARDS_RAW = """
4242424242424242 4000056655665556 4111111111111111 4012888888881881 4222222222222 4000000000000002
4000000000009995 4000000000000127 4000000000000069 4000000000000119 4000002500003155 4000003560000008
4000000360000006 4000000000000077 4000000000000093 4000000000003220 4000000000003063 4000008400001629
4005519200000004 4009348888881881 4012000033330026 4012000077777777 4217651111111119 4500600000000061
4444333322221111 4917610000000000 4988438843884305 4111111145551142 4035501000000008 4000020000000000
5555555555554444 2223003122003222 5200828282828210 5105105105105100 2221000000000009 2223000048400011
5555341244441115 5454545454545454 5431111111111111 5200000000000007
378282246310005 371449635398431 378734493671000 370000000000002 340000000000009
6011111111111117 6011000990139424 6011981111111113 6011000000000004
3056930009020004 36227206271667 30569309025904 38520000023237 36000000000008
3566002020360505 3530111333300000 3566111111111113 3528000000000007
6200000000000005 6304000000000000 6759649826438453
"""
TEST_CARDS = {c for c in _TEST_CARDS_RAW.split() if luhn_ok(c)}

VENDOR_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|[sprk]k_(?:live|test)_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_\-]{20,}|xox[abposr]-[A-Za-z0-9\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|glpat-[A-Za-z0-9_\-]{16,}|SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"
    r"|hf_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_\-]{20,}|shpat_[A-Fa-f0-9]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

# --------------------------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------------------------
FICTION_PHONE_RES = [
    re.compile(r"049157\d{4}"),                       # ACMA fiction mobiles 0491 570 000..0491 579 999
    re.compile(r"0[2378](?:5550|7010)\d{4}"),         # (0x) 5550 xxxx, (0x) 7010 xxxx
    re.compile(r"(?:5550|7010)\d{4}"),                # same without the area code
    re.compile(r"1[38]00975(?:70[7-9]|71[01])"),      # 1300/1800 975 707..711
    re.compile(r"07700900\d{3}"),                     # Ofcom 07700 900xxx
    re.compile(r"(?:\d{3})?55501\d{2}"),              # NANP 555-01xx (optional area code)
]
EXT_RE = re.compile(r"(?i)[\s,]*(?:ext|extn|extension|x)\.?\s*\d{1,5}\s*$")


def phone_ok(value: str) -> bool:
    v = EXT_RE.sub("", nfkc(value))
    d = digits_of(v)
    if not d:
        return False
    cands = {d}
    if d.startswith("61"):
        rest = d[2:]
        cands.add("0" + (rest[1:] if rest.startswith("0") else rest))
    if d.startswith("44"):
        rest = d[2:]
        cands.add("0" + (rest[1:] if rest.startswith("0") else rest))
    if d.startswith("1") and len(d) == 11:
        cands.add(d[1:])
    if d.startswith("0011"):
        cands.add(d[4:])
    return any(rx.fullmatch(c) for rx in FICTION_PHONE_RES for c in cands)


SEP = r"[ \t\r\n.\- ]?"
PHONE_SCAN_RES = [
    ("AU mobile", re.compile(r"(?:\+61" + SEP + r"(?:\(0\)" + SEP + r")?|(?<![\w+])0)4\d{2}(?:" + SEP + r"\d){6}(?!\d)")),
    ("AU landline", re.compile(r"(?:\+61" + SEP + r"(?:\(0\)" + SEP + r")?[2378]|\(0[2378]\)|(?<![\w+])0[2378])"
                               + SEP + r"\d{4}" + SEP + r"\d{4}(?!\d)")),
    ("AU 1300/1800", re.compile(r"(?<![\w+])1[38]00" + SEP + r"\d{3}" + SEP + r"\d{3}(?!\d)")),
    ("UK mobile", re.compile(r"(?:\+44" + SEP + r"(?:\(0\)" + SEP + r")?|(?<![\w+])0)7\d{3}" + SEP + r"\d{3}"
                             + SEP + r"\d{3}(?!\d)")),
    ("NANP", re.compile(r"(?:\+?1[ .\-])?(?:\(\d{3}\)\s?|(?<![\w+])\d{3}[ .\-])\d{3}[ .\-]\d{4}(?!\d)")),
]

# --------------------------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------------------------
EMAIL_SCAN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
RESERVED_EMAIL_BASES = ("example.com", "example.net", "example.org")


def domain_reserved(domain: str) -> bool:
    d = domain.lower().rstrip(".")
    if d in RESERVED_EMAIL_BASES or any(d.endswith("." + b) for b in RESERVED_EMAIL_BASES):
        return True
    return d.endswith(".example") or d == "example"


def email_domain_of(value: str) -> str | None:
    """Domain of a plain, wrapped or obfuscated ('name dot family at example dot com') address."""
    s = nfkc(value).lower()
    s = re.sub(r"[\[\(\{<]\s*(at|dot)\s*[\]\)\}>]", r" \1 ", s)
    if "@" not in s:
        s = re.sub(r"(?<![a-z0-9])at(?![a-z0-9])", "@", s)
    s = re.sub(r"(?<![a-z0-9])dot(?![a-z0-9])", ".", s)
    s = re.sub(r"\s+", "", s)
    s = s.strip("<>()[]{}\"'.,;:")
    if s.startswith("mailto:"):
        s = s[7:]
    if s.count("@") < 1:
        return None
    dom = s.rsplit("@", 1)[1]
    return dom or None


# --------------------------------------------------------------------------------------------
# Scripts (for the languages report)
# --------------------------------------------------------------------------------------------
SCRIPT_RES = {
    "Japanese kana": re.compile(r"[぀-ヿ]"),
    "Korean": re.compile(r"[가-힯ᄀ-ᇿ]"),
    "Han (Chinese/Japanese)": re.compile(r"[一-鿿]"),
    "Vietnamese": re.compile(r"[ơƠưƯđĐạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]"),
    "Polish": re.compile(r"[łŁśŚżŻźŹąĄęĘńŃćĆ]"),
    "Arabic": re.compile(r"[؀-ۿ]"),
    "Greek": re.compile(r"[Ͱ-Ͽ]"),
    "Hebrew": re.compile(r"[֐-׿]"),
    "Thai": re.compile(r"[฀-๿]"),
    "Cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "Devanagari": re.compile(r"[ऀ-ॿ]"),
    "Māori/macrons": re.compile(r"[āēīōūĀĒĪŌŪ]"),
}
NOSPACE_SCRIPT_RE = re.compile(r"[぀-ヿ一-鿿가-힯฀-๿]")

GENRE_FAMILIES = {
    "emails": ("email", "e-mail", "mail"),
    "meeting minutes": ("minutes", "meeting"),
    "tickets": ("ticket", "helpdesk", "incident"),
    "chat logs": ("chat", "slack", "teams", "im"),
    "forms": ("form",),
    "letters": ("letter",),
    "reports": ("report",),
    "CSV/TSV": ("csv", "tsv"),
    "markdown tables": ("markdown", "md", "table"),
    "code and config": ("code", "config", "yaml", "yml", "json", "ini", "env", "toml", "script", "sql",
                        "python", "dockerfile", "terraform"),
    "logs": ("log", "logs", "syslog", "audit"),
    "news": ("news",),
    "fiction": ("fiction", "story", "novel"),
    "FAQs": ("faq", "faqs"),
}
TABLE_TOKENS = {"csv", "tsv", "markdown", "table", "md", "spreadsheet"}


def genre_tokens(genre: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(genre).lower()) if t}


# --------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str, str]] = []
        self.warnings: list[tuple[str, str, str]] = []
        self.quota_rows: list[tuple[str, str, str, bool | None]] = []
        self.info: dict = {}

    def fail(self, code: str, cid: str, detail: str) -> None:
        self.failures.append((code, cid, detail))

    def warn(self, code: str, cid: str, detail: str) -> None:
        self.warnings.append((code, cid, detail))

    def quota(self, name: str, got, target: str, ok: bool | None) -> None:
        self.quota_rows.append((name, str(got), target, ok))

    def codes(self) -> set[str]:
        return {c for c, _, _ in self.failures}


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def _short(s: str, n: int = 60) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + "..."


# --------------------------------------------------------------------------------------------
# Per-case checks
# --------------------------------------------------------------------------------------------
def check_identifier(rep: Report, cid: str, sp: dict) -> None:
    label, value = sp["label"], sp["value"]
    where = f"span {sp['start']}-{sp['end']} {label}"
    if label == "TFN":
        d = digits_of(value)
        if len(d) != 9:
            rep.fail("FORMAT", cid, f"{where}: TFN must have 9 digits, got {len(d)}")
        elif not tfn_ok(d):
            rep.fail("CHECKSUM", cid, f"{where}: TFN fails weighted mod-11; {tfn_detail(d)}")
    elif label == "MEDICARE":
        d = digits_of(value)
        if len(d) != 10:
            rep.fail("FORMAT", cid, f"{where}: Medicare must have 10 digits, got {len(d)}")
        elif not medicare_ok(d):
            rep.fail("CHECKSUM", cid, f"{where}: Medicare fails mod-10 check digit or issue number 1-9; "
                                      f"{medicare_detail(d)}")
    elif label == "ABN":
        d = digits_of(value)
        if len(d) != 11:
            rep.fail("FORMAT", cid, f"{where}: ABN must have 11 digits, got {len(d)}")
        elif not abn_ok(d):
            rep.fail("CHECKSUM", cid, f"{where}: ABN fails mod-89; {abn_detail(d)}")
        elif d in REAL_ABNS:
            rep.fail("CHECKSUM", cid, f"{where}: a real organisation's ABN, not invented")
    elif label == "PAYMENT_CARD":
        d = digits_of(value)
        if not luhn_ok(d) or not 12 <= len(d) <= 19:
            rep.fail("CHECKSUM", cid, f"{where}: payment card fails Luhn (or not 12-19 digits)")
        elif d not in TEST_CARDS:
            rep.fail("CARD_TEST", cid, f"{where}: Luhn-valid but not in the validator's list of published "
                                       f"test card numbers")
    elif label == "NATIONAL_ID":
        a = alnum_upper(value)
        if re.search(r"[A-Z]", a):
            if not nino_ok(value):
                rep.fail("CHECKSUM", cid, f"{where}: NINO must be QQ + 6 digits (+ optional A-D suffix)")
        else:
            d = digits_of(value)
            if not ssn_ok(d):
                rep.fail("CHECKSUM", cid, f"{where}: SSN outside 987-65-4320..4329")
    elif label == "BANK_ACCOUNT":
        a = alnum_upper(value)
        if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", a):
            if not iban_ok(a):
                rep.fail("CHECKSUM", cid, f"{where}: IBAN fails mod-97")
        else:
            d = digits_of(value)
            if not 5 <= len(d) <= 16:
                rep.warn("FORMAT", cid, f"{where}: BSB/account has {len(d)} digits")
    elif label == "PASSPORT":
        if not re.fullmatch(r"[A-Z]{1,2}\d{7}", alnum_upper(value)):
            rep.fail("FORMAT", cid, f"{where}: passport must be one or two letters + 7 digits")
    elif label == "DRIVER_LICENCE":
        a = alnum_upper(value)
        d = digits_of(value)
        if re.search(r"[A-Z]", a) and not re.fullmatch(r"\d{8,10}", d):
            rep.fail("FORMAT", cid, f"{where}: driver licence must be 8 to 10 digits")
        elif not 8 <= len(d) <= 10:
            rep.fail("FORMAT", cid, f"{where}: driver licence must be 8 to 10 digits, got {len(d)}")
    elif label == "SECRET":
        if VENDOR_SECRET_RE.search(nfkc(value)):
            rep.fail("SECRET_PREFIX", cid, f"{where}: secret carries a real vendor prefix")


def check_case(rep: Report, case: dict, half: str | None) -> None:
    cid = str(case.get("id", "?"))
    missing = [k for k in REQUIRED_CASE_KEYS if k not in case]
    if missing:
        rep.fail("SCHEMA", cid, f"missing keys {missing}")
        return
    text = case["text"]
    if not isinstance(text, str) or not text:
        rep.fail("SCHEMA", cid, "text must be a non-empty string")
        return
    if half:
        if not cid.startswith(f"D{half}-"):
            rep.fail("IDS", cid, f"id must start with D{half}-")
        if case["generator"] != half:
            rep.fail("IDS", cid, f"generator must be {half!r}")
        if case["split"] != "test-d":
            rep.fail("IDS", cid, "split must be 'test-d'")
    if not isinstance(case["template"], str) or not isinstance(case["genre"], str):
        rep.fail("SCHEMA", cid, "genre and template must be strings")
    if "\x00" in text:
        rep.fail("JSONL", cid, "text contains NUL")
    n = len(text)
    spans = case["spans"]
    hns = case["hard_negatives"]
    if not isinstance(spans, list) or not isinstance(hns, list):
        rep.fail("SCHEMA", cid, "spans and hard_negatives must be lists")
        return

    good_spans: list[dict] = []
    for i, sp in enumerate(spans):
        if not isinstance(sp, dict) or not all(k in sp for k in ("start", "end", "label", "value")):
            rep.fail("SCHEMA", cid, f"span #{i} needs start, end, label, value")
            continue
        s, e, lab, val = sp["start"], sp["end"], sp["label"], sp["value"]
        if not (isinstance(s, int) and isinstance(e, int) and not isinstance(s, bool)):
            rep.fail("SCHEMA", cid, f"span #{i} offsets must be integers")
            continue
        if lab not in LABELS:
            rep.fail("LABEL", cid, f"span #{i} unknown label {lab!r}")
        if not (0 <= s < e <= n):
            rep.fail("SPAN_BOUNDS", cid, f"span #{i} {s}-{e} outside text of length {n} or empty")
            continue
        if text[s:e] != val:
            rep.fail("SPAN_VALUE", cid, f"span #{i} {lab} {s}-{e}: text[start:end] is "
                                        f"{_short(text[s:e])!r}, value is {_short(str(val))!r}")
            continue
        if val != val.strip():
            rep.fail("SPAN_WS", cid, f"span #{i} {lab} {s}-{e} has outer whitespace")
        good_spans.append(sp)
    # sorted and non-overlapping
    for i in range(1, len(spans)):
        a, b = spans[i - 1], spans[i]
        try:
            if (a["start"], a["end"]) > (b["start"], b["end"]):
                rep.fail("SPAN_SORT", cid, f"spans #{i-1} and #{i} out of order")
            if b["start"] < a["end"]:
                rep.fail("SPAN_OVERLAP", cid, f"spans #{i-1} ({a['start']}-{a['end']} {a['label']}) and "
                                              f"#{i} ({b['start']}-{b['end']} {b['label']}) overlap")
        except (KeyError, TypeError):
            pass

    good_hns: list[dict] = []
    for i, hn in enumerate(hns):
        if not isinstance(hn, dict) or not all(k in hn for k in ("start", "end", "kind", "value")):
            rep.fail("SCHEMA", cid, f"hard negative #{i} needs start, end, kind, value")
            continue
        s, e = hn["start"], hn["end"]
        if not (isinstance(s, int) and isinstance(e, int)) or not (0 <= s < e <= n):
            rep.fail("SPAN_BOUNDS", cid, f"hard negative #{i} {s}-{e} outside text or empty")
            continue
        if text[s:e] != hn["value"]:
            rep.fail("SPAN_VALUE", cid, f"hard negative #{i} {hn['kind']} {s}-{e}: text[start:end] is "
                                        f"{_short(text[s:e])!r}, value is {_short(str(hn['value']))!r}")
            continue
        if hn["kind"] not in KNOWN_HN_KINDS:
            rep.warn("SCHEMA", cid, f"hard negative #{i} kind {hn['kind']!r} not in taxonomy section 4 list")
        for sp in good_spans:
            if _overlap(s, e, sp["start"], sp["end"]):
                rep.fail("HN_OVERLAP", cid, f"hard negative #{i} {hn['kind']} {s}-{e} overlaps gold "
                                            f"{sp['label']} {sp['start']}-{sp['end']}")
        good_hns.append(hn)

    # identifiers, phones, emails on gold
    for sp in good_spans:
        lab = sp["label"]
        check_identifier(rep, cid, sp)
        if lab == "PHONE" and not phone_ok(sp["value"]):
            rep.fail("PHONE", cid, f"PHONE span {sp['start']}-{sp['end']} not in a fiction range")
        if lab == "EMAIL":
            dom = email_domain_of(sp["value"])
            if dom is None:
                rep.fail("EMAIL", cid, f"EMAIL span {sp['start']}-{sp['end']}: cannot read an address")
            elif not domain_reserved(dom):
                rep.fail("EMAIL", cid, f"EMAIL span {sp['start']}-{sp['end']}: domain {dom!r} is not reserved")
    for hn in good_hns:
        if hn["kind"] in PHONE_HN_KINDS and not phone_ok(hn["value"]):
            rep.fail("PHONE", cid, f"institutional phone ({hn['kind']}) {hn['start']}-{hn['end']} not in a "
                                   f"fiction range")
        if hn["kind"] in EMAIL_HN_KINDS:
            dom = email_domain_of(hn["value"])
            if dom is None or not domain_reserved(dom):
                rep.fail("EMAIL", cid, f"role mailbox {hn['start']}-{hn['end']}: domain {dom!r} not reserved")
        if hn["kind"] == "organisation_abn":
            if not abn_ok(digits_of(hn["value"])):
                rep.fail("CHECKSUM", cid, f"organisation_abn {hn['start']}-{hn['end']} fails mod-89; "
                                          f"{abn_detail(digits_of(hn['value']))}")

    # whole-text scans
    spans_hn = [(sp["start"], sp["end"], "gold:" + sp["label"]) for sp in good_spans] + \
               [(hn["start"], hn["end"], "hn:" + hn["kind"]) for hn in good_hns]
    for name, rx in PHONE_SCAN_RES:
        for m in rx.finditer(text):
            ms, me = m.start(), m.end()
            covered_by = [t for (s, e, t) in spans_hn if _overlap(ms, me, s, e)]
            if any(t == "gold:PHONE" or t[3:] in PHONE_HN_KINDS for t in covered_by):
                continue  # checked as a span above
            if covered_by:
                continue  # part of another identifier or institutional value
            if not phone_ok(m.group(0)):
                rep.fail("PHONE_SCAN", cid, f"{name}-like string at {ms}-{me} outside the fiction ranges")
    for m in EMAIL_SCAN_RE.finditer(text):
        dom = m.group(0).rsplit("@", 1)[1]
        if not domain_reserved(dom):
            rep.fail("EMAIL", cid, f"email-like string at {m.start()}-{m.end()} on non-reserved domain {dom!r}")
    for m in VENDOR_SECRET_RE.finditer(nfkc(text) if len(nfkc(text)) == n else text):
        rep.fail("SECRET_PREFIX", cid, f"vendor-prefixed key-like string at {m.start()}-{m.end()}")

    # repeats
    covers = [(s, e) for (s, e, _t) in spans_hn]
    for sp in good_spans:
        if sp["label"] not in REPEAT_LABELS:
            continue
        v = sp["value"]
        if len(v.strip()) < 2:
            continue
        pat = re.escape(v) if NOSPACE_SCRIPT_RE.search(v) else r"(?<!\w)" + re.escape(v) + r"(?!\w)"
        for m in re.finditer(pat, text):
            if not any(s <= m.start() and m.end() <= e for (s, e) in covers):
                rep.fail("REPEAT", cid, f"{sp['label']} value repeated unlabelled at {m.start()}-{m.end()}")
    # PERSON tokens (warning: mentions such as a given name alone)
    seen_tok: set[str] = set()
    for sp in good_spans:
        if sp["label"] != "PERSON":
            continue
        for tok in re.findall(r"[^\W\d_][\w'\-]*", sp["value"]):
            if tok in seen_tok or len(tok) < 3 or tok == sp["value"]:
                continue
            seen_tok.add(tok)
            if not (tok[0].isupper() or not tok[0].isascii()):
                continue
            pat = re.escape(tok) if NOSPACE_SCRIPT_RE.search(tok) else r"(?<!\w)" + re.escape(tok) + r"(?!\w)"
            for m in re.finditer(pat, text):
                if not any(s <= m.start() and m.end() <= e for (s, e) in covers):
                    rep.warn("REPEAT", cid, f"PERSON token of a labelled name appears unlabelled at "
                                            f"{m.start()}-{m.end()}")
                    break

    # case level
    cl = case["case_level"]
    if not isinstance(cl, dict) or "contains_pi" not in cl or "sensitive" not in cl:
        rep.fail("CASE_LEVEL", cid, "case_level needs contains_pi and sensitive")
    else:
        if cl["contains_pi"] is not bool(spans):
            rep.fail("CASE_LEVEL", cid, f"contains_pi={cl['contains_pi']} but {len(spans)} gold spans")
        want = {SENSITIVE[sp["label"]] for sp in spans if isinstance(sp, dict) and sp.get("label") in SENSITIVE}
        got = cl["sensitive"]
        if not isinstance(got, list) or set(got) != want or len(got) != len(set(got)):
            rep.fail("CASE_LEVEL", cid, f"sensitive={got} but spans imply {sorted(want)}")

    # injection
    inj = case.get("injection")
    if inj is not None:
        ok = isinstance(inj, dict) and all(k in inj for k in ("start", "end", "subtype", "position"))
        if not ok:
            rep.fail("INJECTION", cid, "injection needs start, end, subtype, position")
        else:
            s, e = inj["start"], inj["end"]
            if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= n):
                rep.fail("INJECTION", cid, f"injection {s}-{e} outside text or empty")
            elif inj["position"] not in ("before", "inside", "after"):
                rep.fail("INJECTION", cid, f"injection position {inj['position']!r} not before/inside/after")
            elif not isinstance(inj["subtype"], str) or not inj["subtype"]:
                rep.fail("INJECTION", cid, "injection subtype must be a non-empty string")
            else:
                for sp in good_spans:
                    if _overlap(s, e, sp["start"], sp["end"]):
                        rep.fail("INJECTION", cid, f"injection {s}-{e} overlaps gold {sp['label']} "
                                                   f"{sp['start']}-{sp['end']}")
                if not text[s:e].strip():
                    rep.fail("INJECTION", cid, "injection region is blank")
                pos = inj["position"]
                if pos == "inside" and (not text[:s].strip() or not text[e:].strip()):
                    rep.warn("INJECTION", cid, "position 'inside' but no content on one side")
                if pos == "before" and text[:s].strip() and s > n // 2:
                    rep.warn("INJECTION", cid, "position 'before' but injection is in the second half")
                if pos == "after" and text[e:].strip() and e < n // 2:
                    rep.warn("INJECTION", cid, "position 'after' but injection is in the first half")

    # negative genres
    if genre_tokens(case["genre"]) & NEGATIVE_GENRE_TOKENS and spans:
        rep.fail("NEG_GENRE", cid, f"genre {case['genre']!r} is a negative genre but has {len(spans)} spans")
    # long docs
    is_long_marked = bool(case.get("long")) or "long" in genre_tokens(case["genre"]) or \
        "long" in genre_tokens(case.get("template", ""))
    if n > LONG_MAX:
        rep.fail("LONG", cid, f"text has {n} characters (> {LONG_MAX})")
    if is_long_marked and n < LONG_MIN:
        rep.fail("LONG", cid, f"marked long but text has {n} characters (< {LONG_MIN})")


# --------------------------------------------------------------------------------------------
# File-level checks
# --------------------------------------------------------------------------------------------
BAD_LINE_CHARS = "\r\x0b\x0c\x1c\x1d\x1e\x85  "


def load_cases(path: Path, rep: Report) -> list[dict]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        rep.fail("JSONL", "-", "file starts with a UTF-8 BOM")
        raw = raw[3:]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.fail("JSONL", "-", f"file is not valid UTF-8: {exc}")
        content = raw.decode("utf-8", errors="replace")
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    else:
        rep.warn("JSONL", "-", "file does not end with a newline")
    if len(content.replace(chr(13) + chr(10), chr(10)).splitlines()) != len(lines):
        rep.fail("JSONL", "-", f"str.splitlines() sees {len(content.splitlines())} lines, '\\n' split sees "
                               f"{len(lines)}")
    cases: list[dict] = []
    crlf = 0
    for ln, line in enumerate(lines, 1):
        if line.endswith(chr(13)):  # a CRLF line terminator is read correctly by line-based readers
            line = line[:-1]
            crlf += 1
        bad = [hex(ord(c)) for c in BAD_LINE_CHARS if c in line]
        if bad:
            rep.fail("JSONL", f"line {ln}", f"raw line contains line-breaking characters {bad}")
        if not line.strip():
            rep.fail("JSONL", f"line {ln}", "blank line")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            rep.fail("JSONL", f"line {ln}", f"not JSON: {exc}")
            continue
        if not isinstance(obj, dict):
            rep.fail("JSONL", f"line {ln}", "line is not a JSON object")
            continue
        cases.append(obj)
    if crlf:
        rep.warn("JSONL", "-", f"{crlf} lines end in CRLF (accepted as a line terminator)")
    return cases


def check_all(cases: list[dict], rep: Report, half: str | None) -> None:
    ids = Counter(str(c.get("id")) for c in cases)
    for i, k in ids.items():
        if k > 1:
            rep.fail("IDS", i, f"duplicate id ({k} times)")
    texts = Counter(c.get("text") for c in cases if isinstance(c.get("text"), str))
    dup_texts = sum(1 for k in texts.values() if k > 1)
    if dup_texts:
        for c in cases:
            if texts.get(c.get("text"), 0) > 1:
                rep.fail("IDS", str(c.get("id")), "duplicate text")
    for c in cases:
        check_case(rep, c, half)


# --------------------------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------------------------
PROFILE_KEYS = {
    "_about": str, "organisation": str, "organisation_forms": list, "description": str,
    "business_identifier": dict, "email_domain": str, "sites": list, "site_note": str, "buildings": list,
    "role_mailboxes": list, "main_lines": list, "reception_numbers": list, "role_titles": list,
    "programmes": list, "research_projects": list, "committees": list, "initiatives": list,
    "committee_note": str, "code_formats": dict, "reference_number_formats": dict, "public_dates": list,
    "personal_identifier_formats_issued": dict,
}


def _leaves(obj, skip_keys=()) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            out += _leaves(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            out += _leaves(v, skip_keys)
    elif isinstance(obj, str):
        out.append(obj)
    return out


def _norm_txt(s: str) -> str:
    return re.sub(r"\s+", " ", nfkc(s)).strip().casefold()


def _pattern_regexes(patterns: list[str]) -> list[re.Pattern]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error:
            pass
        mask = "".join(r"\d" if ch == "#" else "[A-Z]" if ch == "@" else re.escape(ch) for ch in p)
        out.append(re.compile(mask))
    return out


def check_profile(profile: dict, rep: Report) -> None:
    for k, t in PROFILE_KEYS.items():
        if k not in profile:
            rep.fail("PROFILE", "profile", f"missing key {k!r}")
        elif not isinstance(profile[k], t):
            rep.fail("PROFILE", "profile", f"key {k!r} should be {t.__name__}")
    dom = profile.get("email_domain", "")
    if isinstance(dom, str) and not domain_reserved(dom):
        rep.fail("PROFILE", "profile", f"email_domain {dom!r} is not reserved")
    for mb in profile.get("role_mailboxes", []) or []:
        d = email_domain_of(str(mb))
        if d is None or not domain_reserved(d):
            rep.fail("PROFILE", "profile", f"role mailbox {mb!r} not on a reserved domain")
    for key in ("main_lines", "reception_numbers"):
        for ph in profile.get(key, []) or []:
            if not phone_ok(str(ph)):
                rep.fail("PROFILE", "profile", f"{key} entry {ph!r} not in a fiction range")
    bi = profile.get("business_identifier") or {}
    if isinstance(bi, dict) and str(bi.get("scheme", "")).upper() == "ABN":
        for v in [bi.get("value", "")] + list(bi.get("forms", []) or []):
            if digits_of(str(v)) in REAL_ABNS:
                rep.fail("PROFILE", "profile", f"business identifier {v!r} is a real organisation's ABN, not invented")
            if not abn_ok(digits_of(str(v))):
                rep.fail("PROFILE", "profile", f"business identifier form {v!r} fails ABN mod-89; "
                                              f"{abn_detail(digits_of(str(v)))}")
    for s in _leaves(profile):
        for m in EMAIL_SCAN_RE.finditer(s):
            if not domain_reserved(m.group(0).rsplit("@", 1)[1]):
                rep.fail("PROFILE", "profile", f"email-like string {m.group(0)!r} on a non-reserved domain")
        for name, rx in PHONE_SCAN_RES:
            for m in rx.finditer(s):
                if not phone_ok(m.group(0)):
                    rep.fail("PROFILE", "profile", f"{name}-like string outside the fiction ranges")


def profiled_cases(cases: list[dict], profile: dict) -> tuple[list[dict], str]:
    names = {_norm_txt(profile.get("organisation", ""))} | {_norm_txt(x) for x in
                                                             profile.get("organisation_forms", []) or []}
    names.discard("")
    org_keys = [k for k in ("organisation", "org", "organization", "workplace") if any(k in c for c in cases)]
    if org_keys:
        k = org_keys[0]
        return [c for c in cases if _norm_txt(str(c.get(k, ""))) in names], f"case field {k!r}"
    out = []
    for c in cases:
        t = _norm_txt(c.get("text", ""))
        if any(len(nm) >= 4 and nm in t for nm in names):
            out.append(c)
    return out, "text mentions the organisation name or a form (no organisation field in cases)"


def check_profile_hns(cases: list[dict], profile: dict, rep: Report) -> None:
    values = {_norm_txt(s) for s in _leaves(profile, skip_keys=("personal_identifier_formats_issued", "_about"))}
    phone_digits = {digits_of(str(p)) for p in (profile.get("main_lines", []) or []) +
                    (profile.get("reception_numbers", []) or [])}
    pats = _pattern_regexes([str(v) for v in (profile.get("code_formats") or {}).values()] +
                            [str(v) for v in (profile.get("reference_number_formats") or {}).values()])
    for c in cases:
        for hn in c.get("hard_negatives", []) or []:
            v = str(hn.get("value", ""))
            if _norm_txt(v) in values:
                continue
            if digits_of(v) and digits_of(v) in phone_digits and hn.get("kind") in PHONE_HN_KINDS:
                continue
            if any(p.fullmatch(v) for p in pats):
                continue
            rep.fail("PROFILE_HN", str(c.get("id")), f"hard negative {hn.get('kind')} {hn.get('start')}-"
                                                     f"{hn.get('end')} is not a profile value and matches no "
                                                     f"profile format")


# --------------------------------------------------------------------------------------------
# Quotas (inputs/brief.md)
# --------------------------------------------------------------------------------------------
def looks_tabular(c: dict) -> bool:
    if genre_tokens(c.get("genre", "")) & TABLE_TOKENS:
        return True
    lines = [l for l in c.get("text", "").splitlines() if l.strip()]
    for sep in ("|", "\t", ","):
        rows = [l for l in lines if l.count(sep) >= 2]
        if len(rows) >= 4 and len({r.count(sep) for r in rows}) <= 2:
            return True
    return False


def quotas(cases: list[dict], rep: Report, profile: dict | None) -> None:
    n = len(cases)
    rep.quota("cases", n, "190-210 (about 200)", 190 <= n <= 210)
    clean = [c for c in cases if not c.get("spans")]
    rep.quota("clean documents (no spans)", len(clean), ">= 60", len(clean) >= 60)
    per = Counter(sp.get("label") for c in cases for sp in c.get("spans", []) if isinstance(sp, dict))
    for lab in LABELS:
        need = 30 if lab in ("UNION", "CRIMINAL", "ETHNICITY") else 20
        rep.quota(f"gold spans {lab}", per.get(lab, 0), f">= {need}", per.get(lab, 0) >= need)
    rep.info["label_counts"] = {lab: per.get(lab, 0) for lab in LABELS}
    # indirect / negative statements (only if the cases mark them)
    sens = [sp for c in cases for sp in c.get("spans", []) if isinstance(sp, dict) and sp.get("label") in SENSITIVE]
    ind_keys = [k for k in ("indirect", "directness", "statement", "style", "mode") if any(k in sp for sp in sens)]
    if ind_keys:
        k = ind_keys[0]
        n_ind = sum(1 for sp in sens if sp.get(k) is True or str(sp.get(k)).lower() == "indirect")
        share = n_ind / len(sens) if sens else 0.0
        rep.quota(f"indirect share of sensitive spans (span field {k!r})", f"{n_ind}/{len(sens)} = {share:.0%}",
                  ">= 60%", share >= 0.60)
    else:
        rep.quota("indirect share of sensitive spans", "not marked in cases", ">= 60%", None)
    neg_keys = [k for k in ("negative", "polarity", "negated") if any(k in sp for sp in sens)]
    if neg_keys:
        k = neg_keys[0]
        cats = {SENSITIVE[sp["label"]] for sp in sens
                if sp.get(k) is True or str(sp.get(k)).lower() in ("negative", "negated")}
        missing = sorted(set(SENSITIVE.values()) - cats)
        rep.quota(f"sensitive categories with a negative statement (span field {k!r})",
                  f"{len(cats)}/8" + (f" missing {missing}" if missing else ""), "8/8", not missing)
    else:
        rep.quota("sensitive categories with a negative statement", "not marked in cases", "8/8", None)
    # tables with QUASI_ID
    qt = [c for c in cases if looks_tabular(c) and any(sp.get("label") == "QUASI_ID" for sp in c.get("spans", []))]
    rep.quota("tables (CSV/TSV/markdown) with a QUASI_ID span", len(qt), ">= 20", len(qt) >= 20)
    # hard-to-spot (only if marked)
    hk = [c for c in cases if c.get("hard_to_spot") or any("hard" in str(t).lower() for t in
                                                          (c.get("tags") or c.get("features") or []))]
    if any(("hard_to_spot" in c) or ("tags" in c) or ("features" in c) for c in cases):
        rep.quota("hard-to-spot format cases (as marked)", len(hk), ">= 15", len(hk) >= 15)
    else:
        rep.quota("hard-to-spot format cases", "not marked in cases", ">= 15", None)
    # injections
    inj = [c for c in cases if c.get("injection")]
    pos = Counter(c["injection"].get("position") for c in inj if isinstance(c["injection"], dict))
    rep.quota("injected cases", len(inj), "about 12% (about 24; 20-30)", 20 <= len(inj) <= 30)
    bal = bool(inj) and all(pos.get(p, 0) >= max(1, round(len(inj) * 0.25)) for p in ("before", "inside", "after"))
    rep.quota("injection positions before/inside/after", dict(pos), "balanced (each >= 25% of injections)", bal)
    inj_clean = sum(1 for c in inj if not c.get("spans"))
    rep.quota("injected cases with no personal information", inj_clean, ">= 5", inj_clean >= 5)
    rep.info["injection_subtypes"] = dict(Counter(c["injection"].get("subtype") for c in inj
                                                  if isinstance(c["injection"], dict)))
    # long docs
    longs = [c for c in cases if LONG_MIN <= len(c.get("text", "")) <= LONG_MAX]
    rep.quota("long documents (8,000-26,000 chars)", len(longs), ">= 4", len(longs) >= 4)
    # genres
    gen = Counter(c.get("genre") for c in cases)
    rep.info["genres"] = dict(gen)
    fam_hits = {}
    for fam, keys in GENRE_FAMILIES.items():
        fam_hits[fam] = sum(k for g, k in gen.items() if genre_tokens(g) & set(keys))
    missing = [f for f, k in fam_hits.items() if k == 0]
    rep.quota("genre families present", f"{len(GENRE_FAMILIES) - len(missing)}/{len(GENRE_FAMILIES)}"
              + (f" missing {missing}" if missing else ""), "all 14", not missing)
    rep.info["genre_families"] = fam_hits
    # clean docs content
    clean_kinds = Counter(hn.get("kind") for c in clean for hn in c.get("hard_negatives", []))
    need_kinds = ["fiction_statement", "fictional_character", "public_news", "public_figure", "policy_statement",
                  "public_event", "public_date", "role_mailbox", "business_phone"]
    miss_k = [k for k in need_kinds if not clean_kinds.get(k)]
    rep.quota("clean docs carry the brief's look-alike kinds", f"{len(need_kinds) - len(miss_k)}/{len(need_kinds)}"
              + (f" missing {miss_k}" if miss_k else ""), f"all {len(need_kinds)}", not miss_k)
    code_clean = sum(1 for c in clean if genre_tokens(c.get("genre", "")) & set(GENRE_FAMILIES["code and config"]))
    rep.quota("clean code/config documents", code_clean, ">= 1", code_clean >= 1)
    # scripts
    scripts = {name: sum(1 for c in cases if rx.search(c.get("text", ""))) for name, rx in SCRIPT_RES.items()}
    rep.info["scripts"] = scripts
    present = [s for s, k in scripts.items() if k]
    rep.quota("scripts/languages present", f"{len(present)}: {present}", ">= 5 ('several')", len(present) >= 5)
    # organisations
    if profile is not None:
        prof, method = profiled_cases(cases, profile)
        share = len(prof) / n if n else 0
        rep.quota(f"profiled-organisation cases ({method})", f"{len(prof)} = {share:.0%}", "about half (40-60%)",
                  0.40 <= share <= 0.60)
        org_keys = [k for k in ("organisation", "org", "organization", "workplace") if any(k in c for c in cases)]
        if org_keys:
            pid = {id(c) for c in prof}
            others = {str(c.get(org_keys[0])) for c in cases if id(c) not in pid and c.get(org_keys[0])}
            rep.quota("other invented workplaces", len(others), ">= 6", len(others) >= 6)
            rep.info["organisations"] = dict(Counter(str(c.get(org_keys[0])) for c in cases))
        else:
            rep.quota("other invented workplaces", "no organisation field in cases", ">= 6", None)


# --------------------------------------------------------------------------------------------
# Known-answer tests
# --------------------------------------------------------------------------------------------
def kat() -> list[str]:
    errs: list[str] = []

    def t(cond: bool, msg: str) -> None:
        if not cond:
            errs.append(msg)

    # TFN: 123 456 782 -> 1*1+2*4+3*3+4*7+5*5+6*8+7*6+8*9+2*10 = 253 = 23*11
    t(tfn_ok("123456782"), "TFN 123456782 should pass")
    t(not tfn_ok("123456789"), "TFN 123456789 should fail (sum 323, mod 11 = 4)")
    t(digits_of("123 456 782") == "123456782" and tfn_ok(digits_of("１２３.４５６.７８２")), "TFN full-width")
    t(tfn_ok(digits_of("123​456​782")), "TFN zero-width separators")
    # Medicare: 2123 4567 -> 2+3+14+27+4+15+42+63 = 170 -> check 0; issue 1
    t(medicare_ok("2123456701"), "Medicare 2123456701 should pass")
    t(not medicare_ok("2123456711"), "Medicare wrong check digit should fail")
    t(not medicare_ok("2123456700"), "Medicare issue number 0 should fail")
    # ABN: 51 824 753 556 (ATO's own ABN): (5-1)*10+1+24+10+28+63+55+39+75+85+114 = 534 = 6*89
    t(abn_ok("51824753556"), "ABN 51824753556 should pass")
    t(not abn_ok("51824753557"), "ABN 51824753557 should fail")
    # Luhn
    t(luhn_ok("79927398713"), "Luhn 79927398713 should pass")
    t(not luhn_ok("79927398710"), "Luhn 79927398710 should fail")
    t(luhn_ok("4111111111111111") and not luhn_ok("4111111111111112"), "Luhn 4111...")
    for c in ("4111111111111111", "4242424242424242", "5555555555554444", "378282246310005"):
        t(c in TEST_CARDS, f"test card {c} should be in the list")
    t(len(TEST_CARDS) >= 40, f"test-card list too short after Luhn filter ({len(TEST_CARDS)})")
    # IBAN: GB82 WEST 1234 5698 7654 32 (the standard documentation example)
    t(iban_ok("GB82 WEST 1234 5698 7654 32"), "IBAN GB82WEST12345698765432 should pass")
    t(not iban_ok("GB82 WEST 1234 5698 7654 33"), "IBAN with changed digit should fail")
    t(not iban_ok("GB83 WEST 1234 5698 7654 32"), "IBAN with changed check digits should fail")
    # SSN / NINO
    t(ssn_ok(digits_of("987-65-4320")) and ssn_ok(digits_of("987 65 4329")), "SSN 4320/4329 should pass")
    t(not ssn_ok(digits_of("987-65-4330")) and not ssn_ok(digits_of("123-45-6789")), "SSN outside block fails")
    t(nino_ok("QQ 12 34 56 C") and nino_ok("QQ123456"), "NINO QQ should pass")
    t(not nino_ok("AB123456C"), "NINO AB should fail")
    # phones
    for ok in ("0491 570 156", "+61 491 570 110", "+61 (0)491 579 999", "0491-570-000", "(02) 5550 1234",
               "03 7010 9876", "1800 975 709", "1300 975 711", "07700 900123", "+44 7700 900 001", "555-0199",
               "(212) 555-0123", "+1 212 555 0100", "zero four nine one five seven zero one one zero",
               "0491 570 156 ext 12", "04 9157 0156", "oh four nine one, five seven three, double two eight"):
        t(phone_ok(ok), f"phone {ok!r} should be fiction")
    for bad in ("0412 345 678", "(02) 9555 1234", "1800 975 712", "07700 901123", "555-0299", "+61 412 345 678",
                "0491 580 000"):
        t(not phone_ok(bad), f"phone {bad!r} should be rejected")
    # scan finds a mobile-like string
    t(any(rx.search("call 0412 345 678 now") for _, rx in PHONE_SCAN_RES), "scan should see 0412 345 678")
    t(any(rx.search("ring +61 412 345 678") for _, rx in PHONE_SCAN_RES), "scan should see +61 412 345 678")
    # emails
    t(domain_reserved("example.com") and domain_reserved("harbour.example") and domain_reserved("mail.example.org"),
      "reserved domains")
    t(not domain_reserved("gmail.com") and not domain_reserved("example.com.au"), "non-reserved domains")
    t(email_domain_of("jo dot bloggs at example dot com") == "example.com", "obfuscated email")
    t(email_domain_of("jo.bloggs [at] harbour [dot] example") == "harbour.example", "bracket-obfuscated email")
    t(email_domain_of("jo.bloggs@exam\nple.com") == "example.com", "wrapped email")
    # secrets
    t(VENDOR_SECRET_RE.search("AKIA" + "ABCDEFGHIJKLMNOP") is not None, "AWS-like prefix detected")  # split so scanners do not flag the fixture
    t(VENDOR_SECRET_RE.search("ghp_" + "a1" * 15) is not None, "GitHub-like prefix detected")
    t(VENDOR_SECRET_RE.search("Zq8vT2mP9xLk4Rw7") is None, "random string not flagged")
    # spelled digits
    t(digits_of("double five, triple zero") == "55000", "double/triple")
    return errs


# --------------------------------------------------------------------------------------------
# Mutation test (fixtures are protocol scaffolding, not test content)
# --------------------------------------------------------------------------------------------
def _build(pieces: list[tuple[str, str | None]], cid: str = "DQ-FIX-001", hns_kinds: dict | None = None) -> dict:
    text, spans, hns = "", [], []
    for chunk, lab in pieces:
        s = len(text)
        text += chunk
        if lab and lab.startswith("hn:"):
            hns.append({"start": s, "end": len(text), "kind": lab[3:], "value": chunk})
        elif lab:
            spans.append({"start": s, "end": len(text), "label": lab, "value": chunk})
    sens = sorted({SENSITIVE[sp["label"]] for sp in spans if sp["label"] in SENSITIVE})
    return {"id": cid, "generator": "Q", "split": "test-d", "genre": "email", "template": "fixture",
            "text": text, "spans": spans, "case_level": {"contains_pi": bool(spans), "sensitive": sens},
            "hard_negatives": hns, "notes": ""}


def _base_pieces(**over) -> list[tuple[str, str | None]]:
    p = {"tfn": "123 456 782", "phone": "0491 570 156", "email": "zx.qv@example.com", "name2": "Zxq Vortel"}
    p.update(over)
    return [
        ("Subject: fixture\n\nHello ", None), ("Zxq Vortel", "PERSON"), (", TFN ", None), (p["tfn"], "TFN"),
        (". Mobile ", None), (p["phone"], "PHONE"), (", mail ", None), (p["email"], "EMAIL"), (". ", None),
        (p["name2"], "PERSON"), (" ", None), ("has epilepsy", "HEALTH"), (". Desk: ", None),
        ("desk@example.org", "hn:role_mailbox"), (" or ", None), ("(02) 5550 0000", "hn:business_phone"),
        (", ", None), ("Friday", "hn:weekday"), (". Card ", None), ("4111 1111 1111 1111", "PAYMENT_CARD"),
        (", Medicare ", None), ("2123 45670 1", "MEDICARE"), (", ABN ", None), ("99 000 000 000", "ABN"),
        (", IBAN ", None), ("GB82 WEST 1234 5698 7654 32", "BANK_ACCOUNT"), (", SSN ", None),
        ("987-65-4321", "NATIONAL_ID"), (", NI ", None), ("QQ 12 34 56 C", "NATIONAL_ID"), (".\n", None),
    ]


def mutation_test() -> int:
    def run(cases: list[dict]) -> Report:
        rep = Report()
        check_all(cases, rep, "Q")
        return rep

    base = _build(_base_pieces())
    r0 = run([base])
    if r0.failures:
        print("MUTATION TEST: the unmutated fixture fails:", r0.failures)
        return 2
    muts: list[tuple[str, str, list[dict]]] = []

    def m(name: str, code: str, cases: list[dict]) -> None:
        muts.append((name, code, cases))

    m("broken TFN check digit", "CHECKSUM", [_build(_base_pieces(tfn="123 456 783"))])
    c = copy.deepcopy(base); c["spans"][0]["end"] += 1
    m("off-by-one span end", "SPAN_VALUE", [c])
    c = copy.deepcopy(base); c["spans"][0]["start"] -= 1; c["spans"][0]["value"] = c["text"][c["spans"][0]["start"]:c["spans"][0]["end"]]
    m("off-by-one span start (value consistent, outer whitespace)", "SPAN_WS", [c])
    c = copy.deepcopy(base); c["spans"].insert(1, copy.deepcopy(c["spans"][1]))
    m("duplicated span", "SPAN_OVERLAP", [c])
    c = copy.deepcopy(base); sp = c["spans"][0]
    c["hard_negatives"].append({"start": sp["start"], "end": sp["end"], "kind": "role_title", "value": sp["value"]})
    m("hard negative over gold", "HN_OVERLAP", [c])
    m("real-domain email span", "EMAIL", [_build(_base_pieces(email="zx.qv@gmail.com"))])
    c = _build(_base_pieces()); c["text"] = c["text"][:-2] + " cc zx@realmail.com.\n"
    m("real-domain email in free text", "EMAIL", [c])
    m("non-fiction mobile span", "PHONE", [_build(_base_pieces(phone="0412 345 678"))])
    c = _build(_base_pieces()); c["text"] = c["text"][:-2] + " Alt 0412 345 678.\n"
    m("unlabelled non-fiction mobile in text", "PHONE_SCAN", [c])
    c = copy.deepcopy(base); c["case_level"]["sensitive"] = []
    m("dropped sensitive category", "CASE_LEVEL", [c])
    c = copy.deepcopy(base); c["case_level"]["contains_pi"] = False
    m("contains_pi false with spans", "CASE_LEVEL", [c])
    c = copy.deepcopy(base); c["spans"] = [s for s in c["spans"] if not (s["label"] == "PERSON" and s["start"] > 30)]
    m("unlabelled repeat of a name", "REPEAT", [c])
    m("SSN outside advertising block", "CHECKSUM", [_build([(x, ("NATIONAL_ID" if x == "987-65-4321" else l))
                                                            if x != "987-65-4321" else ("123-45-6789", "NATIONAL_ID")
                                                            for x, l in _base_pieces()])])
    m("NINO without QQ", "CHECKSUM", [_build([(("AB 12 34 56 C" if x == "QQ 12 34 56 C" else x), l)
                                              for x, l in _base_pieces()])])
    m("Luhn-invalid card", "CHECKSUM", [_build([(("4111 1111 1111 1112" if l == "PAYMENT_CARD" else x), l)
                                                for x, l in _base_pieces()])])
    m("Luhn-valid non-test card", "CARD_TEST", [_build([(("4111 1111 1111 1129" if l == "PAYMENT_CARD" else x), l)
                                                        for x, l in _base_pieces()])])
    m("Medicare issue 0", "CHECKSUM", [_build([(("2123 45670 0" if l == "MEDICARE" else x), l)
                                               for x, l in _base_pieces()])])
    m("ABN bad", "CHECKSUM", [_build([(("99 000 000 001" if l == "ABN" else x), l) for x, l in _base_pieces()])])
    m("IBAN bad", "CHECKSUM", [_build([(("GB82 WEST 1234 5698 7654 33" if l == "BANK_ACCOUNT" else x), l)
                                       for x, l in _base_pieces()])])
    c = copy.deepcopy(base); c["injection"] = {"start": c["spans"][0]["start"] - 2, "end": c["spans"][0]["end"],
                                                "subtype": "x", "position": "inside"}
    m("injection over gold", "INJECTION", [c])
    c = copy.deepcopy(base); c2 = copy.deepcopy(base); c2["text"] += " "
    m("duplicate id", "IDS", [c, c2])
    c = copy.deepcopy(base); c["genre"] = "fiction"
    m("negative genre with spans", "NEG_GENRE", [c])
    c = copy.deepcopy(base); c["text"] += "x" * 26001
    m("document longer than 26,000", "LONG", [c])
    c = copy.deepcopy(base); c["hard_negatives"][1]["value"] = "(02) 9555 0000"
    c["text"] = c["text"].replace("(02) 5550 0000", "(02) 9555 0000")
    m("non-fiction institutional phone", "PHONE", [c])
    c = copy.deepcopy(base); c["text"] = c["text"][:-2] + " key AKIA" + "ABCDEFGHIJKLMNOP" + ".\n"
    m("vendor-prefixed secret in text", "SECRET_PREFIX", [c])

    bad = 0
    for name, code, cases in muts:
        rep = run(cases)
        hit = code in rep.codes()
        print(f"  {'ok  ' if hit else 'MISS'} {name:55s} expects {code:13s} got {sorted(rep.codes())}")
        bad += not hit
    # JSONL-breaking character in a raw line
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        line = json.dumps(base, ensure_ascii=False).replace("Hello", "Hel lo")
        p.write_text(line + "\n", encoding="utf-8")
        rep = Report()
        load_cases(p, rep)
        hit = "JSONL" in rep.codes()
        print(f"  {'ok  ' if hit else 'MISS'} {'raw U+2028 in a JSONL line':55s} expects {'JSONL':13s} got "
              f"{sorted(rep.codes())}")
        bad += not hit
    print(f"MUTATION TEST: {len(muts) + 1 - bad}/{len(muts) + 1} mutations rejected")
    return 0 if bad == 0 else 1


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases", nargs="?")
    ap.add_argument("--profile")
    ap.add_argument("--quotas", action="store_true")
    ap.add_argument("--half", help="half letter (Q or C): enforces id prefix D<half>-, generator, split test-d")
    ap.add_argument("--json-out")
    ap.add_argument("--max-report", type=int, default=60)
    ap.add_argument("--kat", action="store_true")
    ap.add_argument("--mutation-test", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    errs = kat()
    if errs:
        print("KNOWN-ANSWER TESTS FAILED:")
        for e in errs:
            print("  -", e)
        return 2
    print(f"known-answer tests: ok ({len(TEST_CARDS)} published test cards loaded)")
    if a.kat:
        return 0
    if a.mutation_test:
        return mutation_test()
    if not a.cases:
        ap.print_usage()
        return 3
    path = Path(a.cases)
    if not path.is_file():
        print(f"no such file: {path}")
        return 3
    rep = Report()
    cases = load_cases(path, rep)
    check_all(cases, rep, a.half)
    profile = None
    if a.profile:
        try:
            profile = json.loads(Path(a.profile).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rep.fail("PROFILE", "profile", f"cannot read profile: {exc}")
        if isinstance(profile, dict):
            check_profile(profile, rep)
            prof, method = profiled_cases(cases, profile)
            rep.info["profiled_cases_method"] = method
            check_profile_hns(prof, profile, rep)
        else:
            profile = None
    man = path.with_name("manifest.json")
    if man.is_file():
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            mtxt = man.read_text(encoding="utf-8")
            if sha not in mtxt:
                rep.fail("MANIFEST", "manifest", "manifest.json does not carry the sha256 of cases.jsonl")
        except OSError:
            pass
    if a.quotas:
        quotas(cases, rep, profile)

    # print
    by_code = Counter(c for c, _, _ in rep.failures)
    print(f"cases: {len(cases)}   failures: {len(rep.failures)}   warnings: {len(rep.warnings)}")
    if by_code:
        print("failures by rule:")
        for code, k in by_code.most_common():
            print(f"  {code:13s} {k:5d}  rule: {RULES.get(code, '')}")
    seen, shown = set(), 0
    for code, cid, detail in rep.failures:
        key = (code, detail)
        if key in seen:
            continue
        seen.add(key)
        if shown >= a.max_report:
            print(f"  ... {len(rep.failures) - shown} more failures not shown")
            break
        print(f"FAIL {code:13s} {cid}: {detail}")
        shown += 1
    wc = Counter(c for c, _, _ in rep.warnings)
    if wc:
        print("warnings by rule:", dict(wc))
        for code, cid, detail in rep.warnings[: a.max_report]:
            print(f"WARN {code:13s} {cid}: {detail}")
    quota_fail = False
    if rep.quota_rows:
        print("\nquotas (brief.md):")
        for name, got, target, ok in rep.quota_rows:
            mark = "ok  " if ok else ("n/a " if ok is None else "SHORT")
            quota_fail |= ok is False
            print(f"  {mark} {name}: {got} (target {target})")
        for k, v in rep.info.items():
            print(f"  info {k}: {v}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({
            "cases": len(cases), "failures": rep.failures, "warnings": rep.warnings,
            "quotas": rep.quota_rows, "info": rep.info}, ensure_ascii=False, indent=1), encoding="utf-8")
    verdict = "PASS" if not rep.failures and not quota_fail else "FAIL"
    print(f"\nVERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
