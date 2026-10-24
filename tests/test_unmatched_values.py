"""Unmatched LLM values are skipped and counted (evaluated behaviour); frequent ones mean ASK, never BLOCK alone."""
from __future__ import annotations

from dataclasses import replace

from themis_shield.detectors.contracts import StageReport, Status
from themis_shield.policy import UNMATCHED_ASK_MINIMUM, decide
from themis_shield.types import Decision, ReasonCode

from test_detectors import detect, targets


def _with_unmatched(result, count):
    status = dict(result.stage_status)
    report = status["gemma4-e4b"]
    status["gemma4-e4b"] = StageReport(report.stage, Status.OK, report.finding_count, report.person_count,
                                       report.doc_sensitivity, count, None)
    return replace(result, stage_status=status)


def test_frequent_unmatched_values_ask_and_never_block():
    result = detect("Please email the synthetic class list to the coordinator.", {}, targets())
    if 2 not in result.tiers_run:
        result = detect("x" * 1600, {}, targets())
    decision = decide(_with_unmatched(result, UNMATCHED_ASK_MINIMUM))
    assert ReasonCode.UNMATCHED_LLM_VALUES in decision.reasons
    assert decision.decision != Decision.BLOCK or any(r.value.startswith("BLOCK:") and r != ReasonCode.INCOMPLETE_OUTPUT
                                                      for r in decision.reasons)
    assert ReasonCode.INCOMPLETE_OUTPUT not in decision.reasons
