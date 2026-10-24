#!/usr/bin/env python3
"""Print Markdown tables comparing scored runs (summary.json written by run_eval.py).

    python eval/compare_runs.py <run dir> [<run dir> ...] [--labels]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SENSITIVE = ["HEALTH", "DISABILITY", "ETHNICITY", "SEXUALITY", "RELIGION", "CRIMINAL", "FAMILY_VIOLENCE", "UNION"]


def pct(x):
    return "n/a" if x is None else f"{x:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--labels", action="store_true", help="also print per-label R_full / R_any")
    a = ap.parse_args()
    runs = [(p.name, json.loads((p / "summary.json").read_text(encoding="utf-8"))) for p in a.runs]
    print("| run | cases | R_full | R_any | precision | sens. R_any | released leaks | inj. leaks | ASK | BLOCK "
          "| clean friction | HN over-redaction | look-alike HN | wall min | s/doc (median) |")
    print("|" + "---|" * 15)
    for name, s in runs:
        d = s["decisions"]
        wall = (s.get("run") or {}).get("wall_seconds_total_this_session")
        print(f"| {name} | {s['cases']} | {pct(s['R_full'])} | {pct(s['R_any'])} | {pct(s['precision_any_label'])} "
              f"| {pct(s['sensitive_R_any'])} | {s['released_leaks']} | {s['injection_released_leaks']} "
              f"| {d['ASK']} | {d['BLOCK']} | {pct(s['friction_on_clean_cases'])} "
              f"| {pct(s['hard_negative_over_redaction'])} ({s['hard_negatives_hit']}/{s['hard_negatives_total']}) "
              f"| {s['sensitive_lookalikes_hit']}/{s['sensitive_lookalikes_total']} "
              f"| {'n/a' if wall is None else round(wall / 60)} | {s['seconds_per_document_median']} |")
    print()
    print("| run | model | calls | gen tok/s | prompt tok/s | s/call median | s/call mean | prompt tok processed | cached |")
    print("|" + "---|" * 9)
    for name, s in runs:
        for model, v in s["speed"].items():
            print(f"| {name} | {model} | {v['calls']} | {v['gen_tok_s_median']} | {v['prompt_tok_s_median']} "
                  f"| {v['seconds_per_call_median']} | {v['seconds_per_call_mean']} "
                  f"| {v['prompt_tokens_processed_total']} | {v['prompt_tokens_cached_total']} |")
    if a.labels:
        print()
        print("| label | gold | " + " | ".join(f"{n} R_full / R_any" for n, _ in runs) + " |")
        print("|" + "---|" * (2 + len(runs)))
        labels = list(runs[0][1]["per_label"])
        for label in labels:
            gold = runs[0][1]["per_label"][label]["gold"]
            cells = []
            for _, s in runs:
                r = s["per_label"].get(label, {})
                cells.append(f"{pct(r.get('R_full'))} / {pct(r.get('R_any'))}")
            mark = " *" if label in SENSITIVE or label == "QUASI_ID" else ""
            print(f"| {label}{mark} | {gold} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
