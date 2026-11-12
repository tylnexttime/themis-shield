#!/usr/bin/env python3
"""denylist_check.py: scan cases (and a profile) against a list of forbidden terms (one regex per line).

The shared validator (check_cases.py, copied from split E) has no --denylist option, so this separate script
does the scan. It NEVER prints, logs or stores a pattern or a matched string: only pattern counts, rule code
DENYLIST, case ids and hit counts. Python 3.12 standard library only.

    python denylist_check.py <cases.jsonl> [--profile profile.json] [--denylist FILE]
Exit code 0 = no hit, 1 = at least one hit, 3 = usage / unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_DENYLIST = Path(r"C:\dev\themis-work\tools\origin-denylist.txt")


def load_patterns(path: Path = DEFAULT_DENYLIST) -> tuple[list[re.Pattern], int]:
    """Return (compiled patterns, number of unusable lines). Never echoes a line."""
    pats: list[re.Pattern] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pats.append(re.compile(line, re.IGNORECASE))
        except re.error:
            bad += 1
    return pats, bad


def _strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return [obj] if isinstance(obj, str) else []


def scan_text(text: str, pats: list[re.Pattern]) -> int:
    return sum(len(p.findall(text)) for p in pats)


def scan_case(case: dict, pats: list[re.Pattern]) -> int:
    """Hits in the text and in every other string of the case record (organisation, genre, span values ...)."""
    return sum(scan_text(s, pats) for s in _strings(case))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases")
    ap.add_argument("--profile")
    ap.add_argument("--denylist", default=str(DEFAULT_DENYLIST))
    a = ap.parse_args(argv)
    try:
        pats, bad = load_patterns(Path(a.denylist))
        cases = [json.loads(l) for l in Path(a.cases).read_text(encoding="utf-8").split("\n") if l.strip()]
        profile = json.loads(Path(a.profile).read_text(encoding="utf-8")) if a.profile else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read input: {type(exc).__name__}")
        return 3
    hits = {c.get("id", "?"): n for c in cases if (n := scan_case(c, pats))}
    print(f"denylist: {len(pats)} patterns loaded ({bad} unusable lines); {len(cases)} cases scanned")
    for cid, n in sorted(hits.items()):
        print(f"FAIL DENYLIST {cid}: {n} hit(s)")
    prof_hits = scan_case(profile, pats) if profile is not None else 0
    if profile is not None:
        print(f"profile: {prof_hits} hit(s)")
    ok = not hits and not prof_hits and pats
    print(f"DENYLIST VERDICT: {'PASS' if ok else 'FAIL'}  (cases with hits: {len(hits)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
