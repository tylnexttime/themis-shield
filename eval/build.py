#!/usr/bin/env python3
"""Build and validate the themis-shield evaluation set from its two independent generators.

    python build.py            run generate_a + generate_b, validate everything, write cases/dev.jsonl,
                               cases/test.jsonl and manifest.json
    python build.py --check    rebuild in memory and confirm the files on disk match byte for byte

The validator here is a THIRD implementation of every checksum (it does not import the generators' own), so a bug in
one generator's arithmetic cannot certify itself. The build fails if any check fails. Python 3.12 stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generate_a  # noqa: E402
import generate_b  # noqa: E402

CASES = HERE / "cases"
MANIFEST = HERE / "manifest.json"
PROFILE = json.loads((HERE / "profiles" / "harbourline.json").read_text(encoding="utf-8"))

LABELS = (
    "PERSON", "USERNAME", "INTERNAL_ID", "TFN", "MEDICARE", "ABN", "NATIONAL_ID", "DRIVER_LICENCE", "PASSPORT",
    "BANK_ACCOUNT", "PAYMENT_CARD", "EMAIL", "PHONE", "ADDRESS", "DATE_OF_BIRTH", "OTHER_DATE", "URL_PERSONAL",
    "SECRET", "QUASI_ID", "HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL",
    "FAMILY_VIOLENCE", "UNION",
)
SENSITIVE = {"HEALTH": "health", "DISABILITY": "disability", "ETHNICITY": "ethnicity", "SEXUALITY": "sexuality",
             "RELIGION": "religion", "CRIMINAL": "criminal", "FAMILY_VIOLENCE": "family_violence", "UNION": "union"}
# Optional deny-list (--denylist FILE, one case-insensitive regex per line): terms that must never appear in any case,
# e.g. the names of a real organisation whose documents inspired a derived set. Kept OUTSIDE the repository on purpose,
# so the list itself leaks nothing. None by default.
BANNED = None


# ------------------------------------------------------------------------------------------------------------------
# Third, independent checksum implementations
# ------------------------------------------------------------------------------------------------------------------
def dig(v):
    return [int(c) for c in unicodedata.normalize("NFKC", v) if c in "0123456789"]


def ok_tfn(v):
    d = dig(v)
    return len(d) == 9 and not sum(x * w for x, w in zip(d, (1, 4, 3, 7, 5, 8, 6, 9, 10))) % 11


def ok_medicare(v):
    d = dig(v)
    return len(d) == 10 and d[0] in range(2, 7) and d[9] != 0 and \
        sum(x * w for x, w in zip(d[:8], (1, 3, 7, 9, 1, 3, 7, 9))) % 10 == d[8]


def ok_abn(v):
    d = dig(v)
    if len(d) != 11:
        return False
    d[0] -= 1
    return not sum(x * w for x, w in zip(d, (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19))) % 89


def ok_luhn(v):
    d = dig(v)
    s = sum(x if i % 2 == 0 else (2 * x - 9 if 2 * x > 9 else 2 * x) for i, x in enumerate(reversed(d)))
    return 13 <= len(d) <= 19 and s % 10 == 0


def ok_iban(v):
    s = re.sub(r"\s", "", v).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def ok_national(v):
    return bool(re.fullmatch(r"987-65-432\d", v) or re.fullmatch(r"QQ \d{2} \d{2} \d{2} [A-D]", v))


assert ok_tfn("123456782") and not ok_tfn("123456789")
assert ok_medicare("2123456701") and not ok_medicare("2123456711")
assert ok_abn("51824753556") and not ok_abn("51824753557")
assert ok_luhn("4111111111111111") and not ok_luhn("4111111111111112")
assert ok_iban("GB82WEST12345698765432") and not ok_iban("GB83WEST12345698765432")

ACMA_MOBILES = set(generate_a.ACMA_MOBILES)
assert ACMA_MOBILES == set(generate_b.FICTION_MOBILES), "the two generators must use the same fiction mobile list"
WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
         "eight": "8", "nine": "9"}


def phone_ok(v):
    t = unicodedata.normalize("NFKC", v).lower()
    for w, d in WORDS.items():
        t = re.sub(rf"\b{w}\b", d, t)
    d = "".join(c for c in t if c.isdigit())
    if t.strip().startswith("+1"):
        return bool(re.fullmatch(r"1\d{3}55501\d{2}", d))
    if d.startswith("07700900") and len(d) == 11:
        return True
    if d.startswith("610"):
        d = "0" + d[3:]
    elif d.startswith("61"):
        d = "0" + d[2:]
    return (d in ACMA_MOBILES or bool(re.fullmatch(r"0[2378](5550|7010)\d{4}", d))
            or bool(re.fullmatch(r"1[38]00975(70[7-9]|71[01])", d)))


RESERVED_EMAIL = re.compile(r"@(example\.(com|net|org)|([a-z0-9-]+\.)*[a-z0-9-]+\.example|([a-z0-9-]+\.)*example)$")


def email_ok(v):
    t = re.sub(r"\s+", " ", v.lower()).strip()
    t = re.sub(r"\s*(\[at\]|\(at\)| at )\s*", "@", t)
    t = re.sub(r"\s*(\[dot\]|\(dot\)| dot )\s*", ".", t)
    t = t.replace(" ", "")
    return bool(RESERVED_EMAIL.search(t))


# ------------------------------------------------------------------------------------------------------------------
def validate(cases):
    errs = []
    ids, texts = set(), set()
    for c in cases:
        cid, t = c["id"], c["text"]
        if cid in ids:
            errs.append(f"{cid}: duplicate id")
        ids.add(cid)
        if t in texts:
            errs.append(f"{cid}: duplicate text")
        texts.add(t)
        if c["split"] not in ("dev", "test"):
            errs.append(f"{cid}: bad split")
        for ch in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"):
            if ch in t:
                errs.append(f"{cid}: line-separator character U+{ord(ch):04X} in text")
        blob = "\n".join([t, c.get("notes", ""), c["template"]] + [s["value"] for s in c["spans"]])
        if BANNED is not None and BANNED.search(blob):
            errs.append(f"{cid}: deny-listed term {BANNED.search(blob).group(0)!r}")
        last = -1
        for s in c["spans"]:
            if s["label"] not in LABELS:
                errs.append(f"{cid}: unknown label {s['label']}")
            if not (0 <= s["start"] < s["end"] <= len(t)) or t[s["start"]:s["end"]] != s["value"]:
                errs.append(f"{cid}: offset mismatch {s}")
            if s["start"] < last:
                errs.append(f"{cid}: overlapping or unsorted spans at {s['start']}")
            last = max(last, s["end"])
            if s["value"] != s["value"].strip():
                errs.append(f"{cid}: outer whitespace {s['value']!r}")
            L, v = s["label"], s["value"]
            check = {"TFN": ok_tfn, "MEDICARE": ok_medicare, "ABN": ok_abn, "PAYMENT_CARD": ok_luhn,
                     "NATIONAL_ID": ok_national, "PHONE": phone_ok, "EMAIL": email_ok}.get(L)
            if check and not check(v):
                errs.append(f"{cid}: {L} fails its check {v!r}")
            if L == "BANK_ACCOUNT" and re.match(r"[A-Z]{2}\d{2}", v) and not ok_iban(v):
                errs.append(f"{cid}: IBAN fails {v!r}")
            if L == "ABN" and dig(v) == dig(PROFILE["business_identifier"]["value"]):
                errs.append(f"{cid}: institute ABN labelled personal")
        for h in c["hard_negatives"]:
            if not (0 <= h["start"] < h["end"] <= len(t)) or t[h["start"]:h["end"]] != h["value"]:
                errs.append(f"{cid}: hard negative offset mismatch {h}")
            if any(h["start"] < s["end"] and s["start"] < h["end"] for s in c["spans"]):
                errs.append(f"{cid}: hard negative overlaps a gold span {h}")
            if h["kind"] in ("business_phone", "reception_phone") and not phone_ok(h["value"]):
                errs.append(f"{cid}: institutional phone outside fiction ranges {h['value']!r}")
            if h["kind"] in ("organisation_abn", "business_identifier") and not ok_abn(h["value"]):
                errs.append(f"{cid}: institutional ABN fails checksum {h['value']!r}")
            if "mailbox" in h["kind"] and not email_ok(h["value"]):
                errs.append(f"{cid}: mailbox not on a reserved domain {h['value']!r}")
        # whole-text scans: every email-like string is reserved; every AU-mobile-like string is a fiction number
        for m in re.finditer(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t):
            if not email_ok(m.group(0)):
                errs.append(f"{cid}: email-like string on a non-reserved domain {m.group(0)!r}")
        for m in re.finditer(r"(?<![\d])(?:\+61\s?(?:\(0\)\s?)?|0)4\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)", t):
            if not phone_ok(m.group(0)):
                errs.append(f"{cid}: mobile-like string outside the fiction list {m.group(0)!r}")
        # every repeat of a labelled value is itself labelled (or institutional)
        covered = [(s["start"], s["end"]) for s in c["spans"]] + [(h["start"], h["end"]) for h in c["hard_negatives"]]
        if c.get("injection"):
            covered.append((c["injection"]["start"], c["injection"]["end"]))
        for s in c["spans"]:
            if s["label"] in SENSITIVE or s["label"] in ("QUASI_ID", "OTHER_DATE", "ADDRESS") or len(s["value"]) < 2:
                continue
            for m in re.finditer(r"(?<!\w)" + re.escape(s["value"]) + r"(?!\w)", t):
                if not any(a <= m.start() and m.end() <= b for a, b in covered):
                    errs.append(f"{cid}: unlabelled repeat of {s['label']} {s['value']!r} at {m.start()}")
        cats = sorted({SENSITIVE[s["label"]] for s in c["spans"] if s["label"] in SENSITIVE})
        if c["case_level"] != {"contains_pi": bool(c["spans"]), "sensitive": cats}:
            errs.append(f"{cid}: case_level disagrees with spans")
        if c["genre"].startswith("injection") or c.get("injection"):
            inj = c.get("injection")
            if not inj or not (0 <= inj["start"] < inj["end"] <= len(t)) or "position" not in inj:
                errs.append(f"{cid}: injection record missing or invalid")
            elif any(inj["start"] < s["end"] and s["start"] < inj["end"] for s in c["spans"]):
                errs.append(f"{cid}: injection overlaps a gold span")
        if c["genre"] == "long_document" and not (8000 <= len(t) <= 26000):
            errs.append(f"{cid}: long document length {len(t)} outside 8,000-26,000 characters (about 2K-6K tokens)")
        if c["genre"] in ("negative", "hard_negative", "injection_negative") and c["spans"]:
            errs.append(f"{cid}: negative genre has gold spans")
    # the two name pools are disjoint
    toks_a = {w for b in generate_a.NAMES.values() for k in ("f", "m", "fam") for n in b[k] for w in n.split()}
    toks_b = {w for f, m, fam in generate_b.POOL.values() for n in f + m + fam for w in n.split()}
    if toks_a & toks_b:
        errs.append(f"name pools overlap: {sorted(toks_a & toks_b)}")
    return errs


def dumps(cases):
    out = []
    for c in cases:
        line = json.dumps(c, ensure_ascii=False, separators=(", ", ": "))
        out.append(line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return ("\n".join(out) + "\n").encode("utf-8")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def build():
    cases = generate_a.build() + generate_b.build()
    for gen_errs in (generate_a.validate([c for c in cases if c["generator"] == "A"]),
                     generate_b.validate([c for c in cases if c["generator"] == "B"])):
        if gen_errs:
            raise SystemExit("generator self-validation failed:\n" + "\n".join(gen_errs[:30]))
    errs = validate(cases)
    if errs:
        raise SystemExit(f"VALIDATION FAILED ({len(errs)}):\n" + "\n".join(errs[:60]))
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    files = {"cases/dev.jsonl": dumps(dev), "cases/test.jsonl": dumps(test)}
    return cases, files


def manifest(cases, files):
    by = defaultdict(Counter)
    for c in cases:
        key = f"{c['generator']}/{c['split']}"
        by["cases"][key] += 1
        for s in c["spans"]:
            by[s["label"]][key] += 1
    labels = {L: {"spans": sum(by[L].values()), "cases": sum(1 for c in cases if any(s["label"] == L for s in c["spans"])),
                  **{k: by[L][k] for k in sorted(by[L])}} for L in LABELS}
    srcs = ["generate_a.py", "generate_b.py", "build.py", "score.py", "taxonomy.md", "profiles/harbourline.json"]
    return {
        "name": "themis-shield synthetic de-identification evaluation set",
        "licence": "Apache-2.0",
        "synthetic": True,
        "seeds": {"generator_a": generate_a.SEED_A, "generator_b": generate_b.SEED_B},
        "cases": {"total": len(cases), **{k: by["cases"][k] for k in sorted(by["cases"])},
                  "dev": sum(c["split"] == "dev" for c in cases), "test": sum(c["split"] == "test" for c in cases),
                  "no_personal_information": sum(not c["case_level"]["contains_pi"] for c in cases),
                  "with_injection": sum(bool(c.get("injection")) for c in cases)},
        "gold_spans": sum(len(c["spans"]) for c in cases),
        "hard_negative_markers": sum(len(c["hard_negatives"]) for c in cases),
        "labels": labels,
        "genres": {g: dict(sorted(Counter(f"{c['generator']}/{c['split']}" for c in cases if c["genre"] == g).items()))
                   for g in sorted({c["genre"] for c in cases})},
        "sensitive_categories": dict(sorted(Counter(k for c in cases for k in c["case_level"]["sensitive"]).items())),
        "templates": {"A": len({c["template"] for c in cases if c["generator"] == "A"}),
                      "B": len({c["template"] for c in cases if c["generator"] == "B"}),
                      "shared_between_dev_and_test": sorted(
                          {c["template"].split("+")[0] for c in cases if c["split"] == "dev"}
                          & {c["template"].split("+")[0] for c in cases if c["split"] == "test"})},
        "sha256": {**{k: sha(v) for k, v in files.items()},
                   **{s: sha((HERE / s).read_bytes()) for s in srcs if (HERE / s).exists()}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--denylist", type=Path, help="file of case-insensitive regexes (one per line) that must not appear")
    a = ap.parse_args()
    if a.denylist:
        global BANNED
        pats = [ln.strip() for ln in a.denylist.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        BANNED = re.compile("(?i)" + "|".join(f"(?:{p})" for p in pats))
        print(f"deny-list: {len(pats)} patterns from {a.denylist}")
    cases, files = build()
    if a.check:
        bad = [k for k, v in files.items() if not (HERE / k).exists() or (HERE / k).read_bytes() != v]
        print("CHECK", "FAILED: " + ", ".join(bad) if bad else "OK: files match the rebuild byte for byte")
        sys.exit(1 if bad else 0)
    CASES.mkdir(exist_ok=True)
    for k, v in files.items():
        (HERE / k).write_bytes(v)
    man = manifest(cases, files)
    MANIFEST.write_text(json.dumps(man, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases ({man['cases']['dev']} dev / {man['cases']['test']} test), {man['gold_spans']} gold spans, "
          f"{man['hard_negative_markers']} hard negatives, {man['cases']['with_injection']} injection cases")
    for k, v in man["sha256"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
