"""Build cases.jsonl from the pipeline state and run the official validator CLI (value-free summary)."""
from __future__ import annotations

import re
import subprocess
import sys

import common as C
import validate as VAL
from store import load_entries, load_profile, load_state


def valid_cases(st: dict | None = None, statuses=("valid",)) -> list[dict]:
    st, entries, profile = st or load_state(), load_entries(), load_profile()
    rows = {i: r["raw"] for i, r in st.items() if r["status"] in statuses and r["raw"]}
    out = []
    for cid in sorted(rows):
        case, _ = VAL.parse_output(entries[cid], rows[cid], profile)
        out.append(case)
    return out


def write_cases(path, cases: list[dict]) -> None:
    C.write_jsonl(path, cases)


def run_validator(path, quotas: bool = True) -> tuple[int, str]:
    cmd = [C.PY, "-I", str(C.ROOT / "validator" / "check_cases.py"), str(path), "--profile",
           str(C.ROOT / "profile.json"), "--half", "O"] + (["--quotas"] if quotas else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    keep = []
    for line in r.stdout.splitlines():
        if line.startswith(("FAIL ", "WARN ")):
            continue  # detail lines can carry values; only rule codes, counts and quota rows are kept
        if re.match(r"\s+info (?!label_counts|genre_families|scripts|injection_subtypes)", line):
            continue
        keep.append(line)
    return r.returncode, "\n".join(keep)


def run_denylist(path) -> tuple[int, str]:
    r = subprocess.run([C.PY, str(C.ROOT / "validator" / "denylist_check.py"), str(path), "--profile",
                        str(C.ROOT / "profile.json")], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout.strip()


if __name__ == "__main__":
    cases = valid_cases()
    p = C.ROOT / "cases.jsonl"
    write_cases(p, cases)
    rc, text = run_validator(p)
    print(text)
    print("exit", rc)
    print(run_denylist(p)[1])