"""Tiered detection and explicit worker execution contracts.

Workers receive detector input/configuration, never vaults or cloud credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import (
    ChatJSONClient, DetectionFinding, DetectionResult, DetectionTargets,
    FindingEvidence, StageReport, StageResult, Status,
)
from .engine import detect, detect_batch


class Detector(Protocol):
    detector_id: str
    def detect(self, text: str) -> StageResult: ...


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    detector_id: str
    timeout_seconds: float
    artefact_manifest_id: str
    network_denied: bool = True


class DetectorProcessRunner(Protocol):
    def run(self, spec: WorkerSpec, text: str) -> StageResult:
        """Verify owned worker identity and convert deadlines/crashes safely."""
        ...


__all__ = [
    "ChatJSONClient", "DetectionFinding", "DetectionResult", "DetectionTargets",
    "Detector", "DetectorProcessRunner", "FindingEvidence", "StageReport",
    "StageResult", "Status", "WorkerSpec", "detect", "detect_batch",
]
