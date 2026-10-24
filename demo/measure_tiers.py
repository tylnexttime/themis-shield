"""Thin Markdown wrapper around capacity.py; no duplicated measurement logic.


Stop the gate first: capacity.py builds its own application-owned scheduler.
Run from the repository root:
    python demo/measure_tiers.py
Or render existing numerical evidence without loading any model:
    python demo/measure_tiers.py --from-json runs/capacity/CAPACITY-<date>.json
"""

from __future__ import annotations

import argparse
from datetime import datetime
import math
from pathlib import Path

import capacity
from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.integration import build_router


def positive(value):
    return (
        type(value) in (int, float)
        and math.isfinite(value) and value > 0
    )


def render(report):
    if report.get("synthetic_only") is not True:
        raise ValueError("synthetic evidence required")
    rows = report.get("tiers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("tier table required")
    if not positive(report.get("end_to_end_input_tokens_per_hour")):
        raise ValueError("end-to-end measurement required")
    tiers_seen = set()
    lines = [
        "# Synthetic local checking capacity", "",
        "| Tier / model | Mode | Approx input tokens | Actual input tokens | Prefill / reading tok/s | Decode tok/s | Basis |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        tier = row.get("tier")
        if tier not in {1, 2, 3} or row.get("mode") not in {"full", "mmap"}:
            raise ValueError("invalid tier or mode")
        if row.get("measured") is not True or not positive(row.get("prefill_tok_s")):
            raise ValueError("real reading measurements required")
        decode = row.get("decode_tok_s")
        if tier == 1:
            if decode is not None or row.get("decode_applicable") is not False:
                raise ValueError("tracer decode must be not applicable")
            decode_text = "not applicable"
        else:
            if not positive(decode):
                raise ValueError("decode measurement required")
            decode_text = f"{decode:.2f}"
        expected_model = {
            1: "pii-tracer", 2: "gemma4-e4b", 3: "gemma4-26b-a4b",
        }[tier]
        if row.get("model_id") != expected_model:
            raise ValueError("unexpected capacity model")
        tiers_seen.add(tier)
        lines.append(
            f'| {tier}: {expected_model} | {row["mode"]} | '
            f'{row["requested_input_tokens_approx"]} | {row["input_tokens"]} | '
            f'{row["prefill_tok_s"]:.2f} | {decode_text} | measured |'
        )
    if tiers_seen != {1, 2, 3}:
        raise ValueError("required tier missing")
    job = report["reference_job"]
    if job.get("measured") is not True or not positive(job.get("seconds")):
        raise ValueError("reference job measurement required")
    lines += [
        "",
        f'Reference mid-size checking job: **{job["documents"]} documents, '
        f'{job["words"]:,} words, {job["input_tokens"]:,} unique source tokens**.',
        f'Local preparation wall time: **{job["seconds"]:.2f} s**.',
        f'End-to-end unique input tokens checked per hour: '
        f'**{report["end_to_end_input_tokens_per_hour"]:,.0f}**.',
        "",
        "Decision 21 targets:",
        "- Large local model: about 20,000 checked input tokens/hour is acceptable.",
        "- Smaller gate models: 20–30 tok/s target; 72,000–108,000 tokens/hour "
        "is only the single-rate arithmetic, not a measured pipeline capacity.",
        "",
        "Reading and decode are separate. Tracer reading includes tokenisation "
        "and classifier wall time; it has no autoregressive decode. LLM figures "
        "are llama.cpp server prefill and decode measurements.",
        "",
        "End-to-end capacity includes local checks, substitution and clean hashing; "
        "it excludes operator waiting and cloud inference. The existing reference "
        "workload has 20 documents and therefore exceeds the routine five-document "
        "limit. This local-only capacity exercise is not approval to send it.",
        "",
        "The scheduler-selected mode is shown for every sample. This run does "
        "not force or claim measurements for both full and mmap modes.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--from-json", type=Path)
    args = parser.parse_args(argv)
    router = None
    try:
        if args.from_json is not None:
            path = args.from_json
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise ValueError("bounded regular evidence required")
            report = strict_json(path.read_bytes())
            output = render(report)
        else:
            cases = capacity.synthetic_cases(capacity.FIXTURE)
            router = build_router(args.config)
            if router.pilot_fixture_enabled:
                raise PermissionError("automatic pilot approval must be off")
            report = capacity.measure(router, cases)
            output = render(report)
            # Preserve capacity.py's numerical schema and filename convention
            # so [capacity].file can point to it without an app change.
            date = datetime.fromisoformat(report["measured_utc"]).date().isoformat()
            destination = capacity.ROOT / "runs" / "capacity" / f"CAPACITY-{date}.json"
            capacity.atomic_write(destination, canonical(report) + b"\n")
            capacity.atomic_write(destination.with_suffix(".md"), output.encode())
        print(output, end="")
        return 0
    except Exception:
        print("Capacity refused or incomplete; no successful measurement claimed.")
        return 2
    finally:
        if router is not None:
            try:
                router.cloud.close()
            finally:
                try:
                    router.vault.close()
                finally:
                    router.runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
