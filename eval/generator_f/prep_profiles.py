"""Convert the split-F half-O profile into a gate institution profile, mechanically (adapted from split E).

Uses eval/convert_profile.py's own `convert()` (the only bridge between the two formats), writes
split-f/profiles/institution/test-f-o.json for `--profiles-dir split-f/profiles`, and a finder-context file
(the profile's first description sentence, capped at 120 characters). Prints only names, sizes and sha256 values:
the person running the gate must not read the test set, and the profile describes its organisation.
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


def main(halves: list[str]) -> int:
    out_dir = HERE / "profiles" / "institution"
    out_dir.mkdir(parents=True, exist_ok=True)
    for half in halves:
        source_path = HERE / "sealed" / "profile.json"
        if not source_path.exists():
            print(f"half {half}: no sealed/profile.json yet")
            continue
        source = json.loads(source_path.read_text(encoding="utf-8"))
        converted = convert(source, about=(
            f"Institution profile for split F half {half.upper()}, converted from that half's profile "
            "by eval/convert_profile.py (do not edit by hand). Every value is institutional and invented."))
        target = out_dir / f"test-f-{half}.json"
        data = json.dumps(converted, ensure_ascii=False, indent=2) + "\n"
        target.write_text(data, encoding="utf-8", newline="\n")
        context = first_sentence(source.get("description", "")) or "an organisation"
        (HERE / "profiles" / f"context-{half}.txt").write_text(context, encoding="utf-8", newline="\n")
        print(f"half {half}: {target.name} {len(data)} bytes sha256 {hashlib.sha256(data.encode()).hexdigest()}"
              f" | context {len(context)} chars sha256 {hashlib.sha256(context.encode()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["o"]))