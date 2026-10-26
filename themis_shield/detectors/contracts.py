"""Value-free detector evidence and the M2 integration boundary.

Offsets address the original string in half-open Python character positions.
These objects are local evidence, not an approved operational-audit schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol

from ..paths import default_tracer_dir
from ..types import Decision, DecisionResult, Finding, Label, Span


class Status(StrEnum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class FindingEvidence:
    label: Label
    stage: str
    tier: int
    # None means the finder did not supply a confidence; it is not zero.
    score: float | None = None


@dataclass(frozen=True, slots=True)
class DetectionFinding(Finding):
    evidence: tuple[FindingEvidence, ...] = ()
    # v0.3: the whole value is an organisation's contact (role mailbox, inbound number) or company number. Still
    # substituted; only the empty-finder disagreement rule reads it (such a finding is not contrary evidence).
    organisation_value: bool = False

    @property
    def start(self) -> int:
        return self.span.start

    @property
    def end(self) -> int:
        return self.span.end

    @property
    def label(self) -> Label:
        """Convenience only: consumers must honour all `labels`."""
        return self.primary_label

    @property
    def provenance(self) -> tuple[str, ...]:
        return tuple(sorted({item.stage for item in self.evidence}))

    @property
    def provenance_tiers(self) -> tuple[int, ...]:
        return tuple(sorted({item.tier for item in self.evidence}))

    @property
    def scores(self) -> tuple[FindingEvidence, ...]:
        """Scores remain associated with their label and producing stage."""
        return self.evidence


def finding(
    start: int,
    end: int,
    label: Label | str,
    stage: str,
    tier: int,
    score: float | None = None,
    *,
    checksum_valid: bool | None = None,
) -> DetectionFinding:
    native = Label(label)
    return DetectionFinding(
        span=Span(start, end),
        labels=frozenset({native}),
        primary_label=native,
        detector_id=stage,
        checksum_valid=checksum_valid,
        evidence=(FindingEvidence(native, stage, tier, score),),
    )


@dataclass(frozen=True, slots=True)
class StageReport:
    stage: str
    status: Status
    finding_count: int = 0
    person_count: int = 0
    doc_sensitivity: float = 0.0
    values_not_found: int = 0
    # Fixed codes only. Never store exception messages or model output here.
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class StageResult:
    findings: tuple[DetectionFinding, ...]
    report: StageReport


class Tracer(Protocol):
    def detect(self, text: str) -> StageResult: ...


class ChatJSONClient(Protocol):
    """M3 owns model identity, locality, deadlines and transport completion.

    Return the decoded schema payload, not an OpenAI response envelope.
    Raise TimeoutError for a deadline. Raise another exception for transport,
    identity, context-overflow, truncated-generation or completion failures.
    Do not log messages, schema values, response bodies or exception messages.
    """

    def chat_json(
        self,
        model_role: str,
        messages: list[dict[str, str]],
        schema: dict,
        max_tokens: int,
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class DetectionTargets:
    tracer: Tracer | None = field(default=None, repr=False)
    llm_client: ChatJSONClient | None = field(default=None, repr=False)
    has_attachment: bool = False
    thorough: bool = False
    tracer_model_directory: str = field(default_factory=lambda: str(default_tracer_dir()))
    tracer_cpu_threads: int = 8
    tracer_artefact_manifest_id: str = ""
    llm_max_tokens: int = 8192
    local_only: bool = False


@dataclass(frozen=True, slots=True)
class DetectionResult:
    findings: tuple[DetectionFinding, ...] = ()
    doc_sensitivity: float = 0.0
    tiers_run: tuple[int, ...] = (1,)
    escalation_reasons: tuple[str, ...] = ()
    stage_status: Mapping[str, StageReport] = field(
        default_factory=lambda: MappingProxyType({})
    )
    timings_ms: Mapping[int, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    redaction_counts: Mapping[Label, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    injection_signal: bool = False
    policy_result: DecisionResult | None = None

    @property
    def decision(self) -> Decision | None:
        return self.policy_result.decision if self.policy_result else None

    @property
    def reasons(self) -> tuple:
        return self.policy_result.reasons if self.policy_result else ()

    def log_summary(self) -> dict:
        """Explicit value-free diagnostic projection, excluding all offsets.

        Operational audit must further apply its approved count bands, timing
        granularity and metadata allow-list. Do not log this whole dataclass.
        """
        return {
            "decision": self.decision.value if self.decision else None,
            "reasons": [item.value for item in self.reasons],
            "tiers_run": list(self.tiers_run),
            "escalation_reasons": list(self.escalation_reasons),
            "stage_status": {
                key: {"status": item.status.value, "error_code": item.error_code}
                for key, item in self.stage_status.items()
            },
            "timings_ms": dict(self.timings_ms),
            "redaction_counts": {
                label.value: count for label, count in self.redaction_counts.items()
            },
        }


def validate_findings(
    text: str, findings: tuple[DetectionFinding, ...]
) -> None:
    for item in findings:
        if not isinstance(item, DetectionFinding):
            raise ValueError("invalid-finding")
        if (
            type(item.start) is not int
            or type(item.end) is not int
            or not 0 <= item.start < item.end <= len(text)
            or not item.labels
            or item.primary_label not in item.labels
            or any(not isinstance(label, Label) for label in item.labels)
            or not item.evidence
        ):
            raise ValueError("invalid-finding")
        for evidence in item.evidence:
            if evidence.label not in item.labels:
                raise ValueError("invalid-evidence")
            if evidence.score is not None and (
                isinstance(evidence.score, bool)
                or not isinstance(evidence.score, (int, float))
                or not isfinite(evidence.score)
                or not 0 <= evidence.score <= 1
            ):
                raise ValueError("invalid-score")


def sensitivity(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("invalid-sensitivity")
    return float(value)
