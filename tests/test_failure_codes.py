"""A failed stage names the kind of failure from a fixed list (DD-04): diagnosable after the fact, never a message."""

from __future__ import annotations

import pytest

from themis_shield.detectors.contracts import Status, failure_code
from themis_shield.detectors.engine import run_stage
from themis_shield.detectors.typed import TypedStageError
from themis_shield.inference import NotEnoughMemory, RuntimeFailure, TargetUnavailable


@pytest.mark.parametrize("error, code", [
    (NotEnoughMemory("SYNTHETIC free VRAM below budget"), "stage-failed:not-enough-memory"),
    (TargetUnavailable("SYNTHETIC unhealthy"), "stage-failed:target-unavailable"),
    (RuntimeFailure("SYNTHETIC"), "stage-failed:runtime"),
    (TypedStageError("SYNTHETIC"), "stage-failed:typed-configuration"),
    (ConnectionResetError("SYNTHETIC"), "stage-failed:transport"),
    (PermissionError("SYNTHETIC"), "stage-failed:os"),
    (MemoryError(), "stage-failed:out-of-memory"),
    (RuntimeError("SYNTHETIC Mara Lindahl"), "stage-failed"),
])
def test_failure_code_names_the_kind_never_the_message(error, code):
    assert failure_code(error) == code
    assert "SYNTHETIC" not in failure_code(error) and "Mara" not in failure_code(error)


def test_a_failed_stage_carries_the_kind():
    def call():
        raise NotEnoughMemory("SYNTHETIC")

    result = run_stage("SYNTHETIC text", "gemma4-26b-a4b", call)
    assert result.report.status == Status.FAILED
    assert result.report.error_code == "stage-failed:not-enough-memory"
    assert result.findings == ()
