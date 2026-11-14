"""Convert a sealed split-H half profile into a gate institution profile, mechanically (adapted from split G).

Uses eval/convert_profile.py's own `convert()` (the only bridge between the two formats), writes
profiles/institution/test-h-<x>.json for `--profiles-dir split-h/profiles`, and a finder-context file
profiles/context-<x>.txt (the profile's first description sentence, at most 120 characters, cut at a word).
Prints only names, sizes and sha256 values: whoever runs the gate must not read the test set, and the profile
describes its organisation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(r"C:\dev\themis-shield")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "eval"))
from convert_profile import convert  # noqa: E402


def first_sentence(text: str, cap: int = 120) -> str:
    sentence = re.split(r"(?<=[.!?])\s", " ".join((text or "").split()), maxsplit=1)[0]
    if len(sentence) > cap:
        sentence = sentence[:cap].rsplit(" ", 1)[0]
    return sentence.rstrip(" ,;:")


WRAPPED_NAME_LISTS = ("buildings", "committees", "initiatives")


def unwrap_names(source: dict) -> tuple[dict, dict]:
    """The converter reads `buildings`, `committees` and `initiatives` as lists of bare strings (the schema says "list of
    names"; only sites, programmes, research projects and public dates may be objects). If an author wrote each name as the
    one-key object {"name": <string>}, unwrap it to the string, by code. Any other shape is an error (never guessed).
    Returns (a copy of the profile for the converter, counts of unwrapped entries). The sealed profile is not changed."""
    out, counts = dict(source), {}
    for key in WRAPPED_NAME_LISTS:
        items, new, n = source.get(key, []), [], 0
        for x in items:
            if isinstance(x, dict):
                if set(x) != {"name"} or not isinstance(x["name"], str):
                    raise SystemExit(f"profile list {key}: an entry has a shape that is not {{name: string}}")
                new.append(x["name"])
                n += 1
            else:
                new.append(x)
        out[key] = new
        if n:
            counts[key] = n
    return out, counts


def main(halves: list[str]) -> int:
    out_dir = HERE / "profiles" / "institution"
    out_dir.mkdir(parents=True, exist_ok=True)
    for half in halves:
        half = half.lower()
        source_path = HERE / "sealed" / f"profile-{half}.json"
        if not source_path.exists():
            print(f"half {half}: no sealed/profile-{half}.json yet")
            continue
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source, unwrapped = unwrap_names(source)  # code only; counts are recorded, the sealed profile stays as written
        (HERE / "profiles" / f"normalised-{half}.json").write_text(json.dumps({"entries_unwrapped_from_name_objects": unwrapped}),
                                                                   encoding="utf-8")
        if unwrapped:
            print(f"half {half}: name objects unwrapped to strings before conversion: {unwrapped}")
        converted = convert(source, about=(
            f"Institution profile for split H half {half.upper()}, converted from that half's profile "
            "by eval/convert_profile.py (do not edit by hand). Every value is institutional and invented."))
        target = out_dir / f"test-h-{half}.json"
        data = json.dumps(converted, ensure_ascii=False, indent=2) + "\n"
        target.write_text(data, encoding="utf-8", newline="\n")
        context = first_sentence(source.get("description", "")) or "an organisation"
        (HERE / "profiles" / f"context-{half}.txt").write_text(context, encoding="utf-8", newline="\n")
        print(f"half {half}: {target.name} {len(data)} bytes sha256 {hashlib.sha256(data.encode()).hexdigest()}"
              f" | context {len(context)} chars sha256 {hashlib.sha256(context.encode()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["a", "b"]))