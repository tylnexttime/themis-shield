"""Frozen synthetic regression recall test; never a tuning source."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from themis_shield.detectors.allowlist import apply_allowlist, compile_profile
from themis_shield.detectors.contracts import Status
from themis_shield.detectors.pii_tracer import PIITracerDetector, PIITracerSpec
from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.rules import RulesDetector
from themis_shield.detectors.union import union_findings
from themis_shield.paths import default_tracer_dir
from themis_shield.types import Label


FIXTURES = Path(__file__).parent / "fixtures"
DIRECT_IDENTIFIERS = frozenset({
    Label.PERSON, Label.STUDENT_ID, Label.USI, Label.TFN, Label.MEDICARE,
    Label.ABN, Label.DRIVER_LICENCE, Label.PASSPORT, Label.BANK_ACCOUNT,
    Label.EMAIL, Label.PHONE, Label.ADDRESS, Label.DATE_OF_BIRTH,
    Label.URL_PERSONAL, Label.SECRET,
})


def covered_fully(gold: dict, findings: tuple, text: str) -> bool:
    """Frozen coverage definition: every alphanumeric gold character.

    Label-independent geometric coverage matches gate_decision.py; punctuation
    coverage is separately relevant to M1's exact substitution/fidelity tests.
    """
    required = {
        index for index in range(gold["start"], gold["end"])
        if text[index].isalnum()
    }
    if not required:
        raise AssertionError("empty-direct-identifier")
    covered = set()
    for item in findings:
        covered.update(range(
            max(item.start, gold["start"]), min(item.end, gold["end"])
        ))
    return required <= covered


@pytest.mark.slow
def test_real_tracer_tier1_full_coverage_recall():
    """Measure rules + real tracer + deterministic passes, without LLM help."""
    cases = [
        json.loads(line)
        for line in (FIXTURES / "fixture-set-sample.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert len(cases) == 34, "Regression inventory changed; require explicit review"
    profile = json.loads(
        (FIXTURES / "institution_profile.json").read_text(encoding="utf-8")
    )
    allowlist = compile_profile(profile)
    tracer = PIITracerDetector(PIITracerSpec(
        Path(os.environ.get(
            "THEMIS_PII_TRACER_PATH", str(default_tracer_dir())
        )),
        cpu_threads=int(os.environ.get("THEMIS_PII_TRACER_THREADS", "8")),
    ))
    rules = RulesDetector()
    total = covered = 0
    misses = []
    for case in cases:
        text = case["text"]
        # Validate supplied annotations; never repair them from predictions.
        for gold in case["spans"]:
            assert 0 <= gold["start"] < gold["end"] <= len(text), case["id"]
            assert text[gold["start"]:gold["end"]] == gold["value"], case["id"]
        traced = tracer.detect(text)
        assert traced.report.status == Status.OK, case["id"]
        found = union_findings(rules.detect(text).findings, traced.findings)
        found = apply_allowlist(text, propagate(text, found), allowlist)
        for gold in case["spans"]:
            if Label(gold["label"]) not in DIRECT_IDENTIFIERS:
                continue
            total += 1
            if covered_fully(gold, found, text):
                covered += 1
            else:
                # No original values, snippets or offsets in assertion output.
                misses.append((case["id"], gold["label"]))
    assert total > 0
    recall = covered / total
    assert recall >= 0.95, {
        "full_coverage_recall": recall,
        "covered": covered,
        "total": total,
        "misses": misses,
    }
