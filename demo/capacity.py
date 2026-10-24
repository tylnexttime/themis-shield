"""Synthetic-only capacity measurement, decision 21.

Only the frozen tracked fixture set is admissible.
Reading, decode and end-to-end pipeline capacity are distinct measurements.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time

from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.detectors.pii_tracer import PIITracerDetector, PIITracerSpec, _load, token_windows
from themis_shield.integration import build_router
from themis_shield.placeholders import TokenAllocator, SENSITIVE_LABELS
from themis_shield.runtime.modes import release_tracer_resources
from themis_shield.substitute import SpanFinding, substitute, verify_no_originals
from themis_shield.vault import InMemoryVault


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fixture-set-sample.jsonl"


def synthetic_cases(input_path):
    path = Path(input_path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise PermissionError("linked synthetic input refused")
    if path.resolve(strict=True) != FIXTURE.resolve(strict=True):
        raise PermissionError("only the frozen synthetic sample is permitted")
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:tests/fixtures/fixture-set-sample.jsonl"],
        capture_output=True, check=False, timeout=10,
    )
    raw = path.read_bytes()
    if result.returncode != 0 or result.stdout.replace(b"\r\n", b"\n") != raw.replace(b"\r\n", b"\n"):
        raise PermissionError("synthetic fixture must match its reviewed tracked snapshot")
    rows = [strict_json(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != 34 or any(not isinstance(row.get("text"), str) for row in rows):
        raise PermissionError("frozen synthetic inventory mismatch")
    return [row["text"] for row in rows]


def document_job(cases):
    words = " ".join(cases).split()
    if not words:
        raise ValueError("empty synthetic inventory")
    return [
        " ".join(words[(index * 750 + offset) % len(words)] for offset in range(750))
        for index in range(20)
    ]


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".capacity-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def measure(router, cases):
    spec = PIITracerSpec(
        Path(router.targets.tracer_model_directory),
        router.targets.tracer_artefact_manifest_id, router.targets.tracer_cpu_threads,
    )
    tracer = PIITracerDetector(spec)
    _, tokenizer, _ = _load(spec)

    def count(text):
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])

    tiers = []
    for approximate in (500, 2000, 6000):
        text = "alpha beta gamma delta " * max(1, approximate // 4)
        n = count(text)
        offsets = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)["offset_mapping"]
        seen = sum(count(text[a:b]) for a, b in token_windows(text, offsets))
        began = time.perf_counter()
        tracer.detect(text)
        elapsed = time.perf_counter() - began
        tiers.append({
            "tier": 1, "model_id": "pii-tracer", "mode": "full",
            "requested_input_tokens_approx": approximate, "input_tokens": n,
            "effective_window_tokens": seen, "prefill_tok_s": seen / elapsed,
            "decode_tok_s": None, "decode_applicable": False,
            "metric": "tokenisation-plus-classification-wall-throughput",
            "seconds": elapsed, "measured": True,
            "label": "measured on this device " + datetime.now().strftime("%d/%m/%Y"),
        })
    release_tracer_resources()
    for tier, model_id in ((2, "gemma4-e4b"), (3, "gemma4-26b-a4b")):
        for approximate in (500, 2000, 6000):
            router.runtime.stop()
            prompt = (
                "Synthetic calibration only. Generate alpha beta gamma delta repeatedly.\n"
                + "alpha beta gamma delta " * max(1, approximate // 4)
            )
            began = time.perf_counter()
            with router.runtime.acquire(model_id) as target:
                output = target.chat(
                    [{"role": "user", "content": prompt}], stream=False, max_tokens=128,
                )
                timing = output["timings"]
                if any(
                    type(timing.get(key)) not in (int, float) or timing[key] <= 0
                    for key in ("prompt_per_second", "predicted_per_second")
                ):
                    raise ValueError("required real timing unavailable")
                tiers.append({
                    "tier": tier, "model_id": model_id, "mode": target.entry.mode,
                    "requested_input_tokens_approx": approximate,
                    "input_tokens": timing["prompt_n"], "output_tokens": timing["predicted_n"],
                    "prefill_tok_s": timing["prompt_per_second"],
                    "decode_tok_s": timing["predicted_per_second"],
                    "metric": "llama.cpp-server-prefill-and-decode",
                    "seconds": time.perf_counter() - began, "measured": True,
                    "label": "measured on this device " + datetime.now().strftime("%d/%m/%Y"),
                })
            router.runtime.stop()
    documents = document_job(cases)
    per_tier = {str(tier): {
        "seconds": 0.0, "documents": 0, "words": 0, "input_tokens": 0,
    } for tier in (1, 2, 3)}
    outcomes, document_results = Counter(), []
    common_tokens = sum(count(text) for text in documents)
    vault = InMemoryVault()
    began = time.perf_counter()
    try:
        results = router.batch_detector(documents, router.profile, router.targets, thorough=True)
        if len(results) != len(documents):
            raise PermissionError("capacity run omitted a document")
        for index, (text, evidence) in enumerate(zip(documents, results)):
            if evidence.tiers_run != (1, 2, 3):
                raise PermissionError("capacity run omitted a required tier")
            for tier in evidence.tiers_run:
                row = per_tier[str(tier)]
                row["seconds"] += evidence.timings_ms[tier] / 1000
                row["documents"] += 1
                row["words"] += len(text.split())
                row["input_tokens"] += count(text)
            outcomes[evidence.decision.value] += 1
            document_results.append({
                "document": index + 1, "input_tokens": count(text),
                **evidence.log_summary(),
            })
            if evidence.decision.value != "BLOCK":
                findings = [
                    SpanFinding(finding.start, finding.end, label)
                    for finding in evidence.findings for label in finding.labels
                ]
                if any(finding.labels & SENSITIVE_LABELS for finding in evidence.findings):
                    findings.append(SpanFinding(0, len(text), "HEALTH"))
                allocator = TokenAllocator(f"capacity-{index}", vault)
                clean = substitute(text, findings, allocator)
                verify_no_originals(clean.tokenised_text, allocator.mapping().values())
                hashlib.sha256(clean.tokenised_text.encode()).hexdigest()
            vault.wipe(f"capacity-{index}")
        elapsed = time.perf_counter() - began
    finally:
        vault.close()
    return {
        "schema_version": 1, "synthetic_only": True,
        "measured_utc": datetime.now(timezone.utc).isoformat(), "tiers": tiers,
        "end_to_end_input_tokens_per_hour": common_tokens * 3600 / elapsed,
        "reference_job": {
            "documents": 20, "words": 15_000, "input_tokens": common_tokens,
            "seconds": elapsed, "per_tier": per_tier, "outcomes": dict(outcomes),
            "document_results": document_results, "measured": True,
            "scope": "local checking, substitution and clean hashing; no approval wait or cloud inference",
            "token_denominator": "unique source tokens using the provisioned tracer tokenizer",
            "execution": "batch-by-model",
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--input", type=Path, default=FIXTURE)
    args = parser.parse_args()
    router = None
    try:
        cases = synthetic_cases(args.input)
        router = build_router(args.config)
        if router.pilot_fixture_enabled:
            raise PermissionError("capacity run must not enable automatic approval")
        report = measure(router, cases)
        day = datetime.now(timezone.utc).date().isoformat()
        path = ROOT / "runs" / "capacity" / f"CAPACITY-{day}.json"
        atomic_write(path, canonical(report) + b"\n")
        lines = [
            "# Synthetic capacity " + day, "",
            "| Tier | Mode | Approx input | Actual input | Prefill tok/s | Decode tok/s |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for row in report["tiers"]:
            decode = row["decode_tok_s"]
            lines.append(
                f'| {row["model_id"]} | {row["mode"]} | '
                f'{row["requested_input_tokens_approx"]} | {row["input_tokens"]} | '
                f'{row["prefill_tok_s"]:.2f} | '
                + (f"{decode:.2f}" if decode is not None else "not applicable") + " |"
            )
        lines.extend([
            "", f'End-to-end unique input tokens checked/hour: '
            f'{report["end_to_end_input_tokens_per_hour"]:.0f}.', "",
            "Execution: batch-by-model. No cloud send or approval-wait time is included.",
            "Tracer reading includes tokenisation/classification wall time; LLM rates are prefill/decode.",
            "", "| Tier | Seconds | Documents seen | Words seen | Source tokens seen |",
            "|---|---:|---:|---:|---:|",
        ])
        for tier, row in report["reference_job"]["per_tier"].items():
            lines.append(
                f'| {tier} | {row["seconds"]:.2f} | {row["documents"]} | '
                f'{row["words"]} | {row["input_tokens"]} |'
            )
        atomic_write(path.with_suffix(".md"), ("\n".join(lines) + "\n").encode())
        print("Synthetic numerical capacity evidence written.")
    except Exception:
        # Even debug mode must not format exceptions or retain model text.
        print("Capacity refused or incomplete. No successful capacity result claimed.")
        raise SystemExit(2) from None
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
    main()
