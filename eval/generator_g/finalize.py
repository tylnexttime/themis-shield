"""Build cases.jsonl for one half from the pipeline state and run the official validator CLI (value-free summary)."""
from __future__ import annotations

import re
import subprocess
import sys

import common as C
import validate as VAL
from store import load_entries, load_profile, load_state


def valid_cases(h: C.Half, st: dict | None = None, statuses=("valid",)) -> list[dict]:
    st, entries, profile = st or load_state(h), load_entries(h), load_profile(h)
    rows = {i: r["raw"] for i, r in st.items() if r["status"] in statuses and r["raw"]}
    # Split G amendment (12/11/2026): an optional neutral relabel of one workplace's metadata label (case field
    # `organisation` only), written by rename_org_t.py; no document text, span or offset is touched.
    ovr = C.jload(h.dir / "work" / "org_label_override.json", {})
    out = []
    for cid in sorted(rows):
        case, _ = VAL.parse_output(entries[cid], rows[cid], profile, h.L)
        if ovr and case["organisation"].startswith(f"Other workplace {ovr['workplace']}: "):
            case["organisation"] = ovr["label"]
        out.append(case)
    return out


def write_cases(path, cases: list[dict]) -> None:
    C.write_jsonl(path, cases)


def run_validator(h: C.Half, path, quotas: bool = True) -> tuple[int, str]:
    cmd = [C.PY, "-I", str(C.ROOT / "validator" / "check_cases.py"), str(path), "--profile", str(h.profile_path),
           "--half", h.L] + (["--quotas"] if quotas else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    keep = []
    for line in r.stdout.splitlines():
        if line.startswith(("FAIL ", "WARN ")):
            continue  # detail lines can carry values; only rule codes, counts and quota rows are kept
        if re.match(r"\s+info (?!label_counts|genre_families|scripts|injection_subtypes)", line):
            continue
        keep.append(line)
    return r.returncode, "\n".join(keep)


def run_denylist(h: C.Half, path) -> tuple[int, str]:
    r = subprocess.run([C.PY, str(C.ROOT / "validator" / "denylist_check.py"), str(path), "--profile",
                        str(h.profile_path), "--denylist", C.DENYLIST], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout.strip()


def run_extra(h: C.Half, path) -> tuple[int, str]:
    r = subprocess.run([C.PY, str(C.ROOT / "extra_quotas.py"), str(path), "--profile", str(h.profile_path)],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout.strip()


if __name__ == "__main__":
    h = C.Half(sys.argv[1])
    cases = valid_cases(h)
    write_cases(h.cases_path, cases)
    rc, text = run_validator(h, h.cases_path)
    print(text)
    print("exit", rc)
    print(run_denylist(h, h.cases_path)[1])
    print(run_extra(h, h.cases_path)[1])