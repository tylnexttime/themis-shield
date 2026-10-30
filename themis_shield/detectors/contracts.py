"""Value-free detector evidence and the M2 integration boundary.

Offsets address the original string in half-open Python character positions.
These objects are local evidence, not an approved operational-audit schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Protocol

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
    # v0.4 ("tracer-evidence-sanity"): a PII-Tracer value that cannot be what its label says (a money amount called an
    # identifier, a word called a URL, a lone role word called a person, a date with no birth context called a date of
    # birth, a place without a number called an address). Still substituted; it only stops counting as contrary
    # evidence for the empty-finder hold, exactly like organisation_value.
    implausible_tracer_value: bool = False

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


# A failed stage's code names the KIND of failure from a fixed list (by exception class, never its message), so a
# run that failed closed can be diagnosed afterwards without debug logging (docs/DEV-DEBT.md, DD-04).
_FAILURE_KINDS = (
    ("NotEnoughMemory", "stage-failed:not-enough-memory"),
    ("TargetUnavailable", "stage-failed:target-unavailable"),
    ("TypedStageError", "stage-failed:typed-configuration"),
    ("RuntimeFailure", "stage-failed:runtime"),
    ("ConnectionError", "stage-failed:transport"),
    ("TransportError", "stage-failed:transport"),
    ("OSError", "stage-failed:os"),
    ("MemoryError", "stage-failed:out-of-memory"),
)


def failure_code(error: BaseException) -> str:
    """``stage-failed`` qualified by the first matching kind in the exception's class hierarchy."""
    names = [cls.__name__ for cls in type(error).__mro__]
    for name, code in _FAILURE_KINDS:
        if name in names:
            return code
    return "stage-failed"


def on_detection_view(text: str, detect) -> "StageResult":
    """v0.4 fix "detectors-detection-view" (DD-06): run a detector on the detection view, map its spans back.

    The rule packs, the marking gate and the injection signal read the detection view since v0.2.1 (invisible
    characters removed, NFKC per character); the LLM finders and PII-Tracer still read the raw text, so a zero-width
    character inside a name could hide it from them. With the fix on, they read the view too, and every span maps
    back to the source characters that produced it (``DetectionView.to_source``, conservative). A text the view
    leaves unchanged is read as it is.
    """
    from dataclasses import replace

    from ..normalise import detection_view
    from .settings import fix_on

    if not fix_on("detectors-detection-view"):
        return detect(text)
    view = detection_view(text)
    if view.text == text:
        return detect(text)
    result = detect(view.text)
    mapped = []
    for item in result.findings:
        statement = item.statement_span
        if statement is not None:
            statement = Span(*view.to_source(statement.start, statement.end))
        mapped.append(replace(item, span=Span(*view.to_source(item.start, item.end)), statement_span=statement))
    return StageResult(tuple(mapped), result.report)


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
    # v0.3 typed-decision tier (T1.5): where its back end comes from (``detectors.typed.TypedRuntime``). Used only
    # when ``[typed] enabled``; None with the tier enabled fails that stage closed.
    typed: Any = field(default=None, repr=False)


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
    # v0.3: per-stage wall times in ms (stage name -> ms; "<finder>:acquire" for a finder's residency on the first
    # document of its tier). Diagnostic only: nothing decides on it and it takes no part in equality, so two
    # detections of the same text compare equal as before. ``timings_ms`` keeps the per-tier totals.
    stage_timings_ms: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({}), compare=False,
    )
    redaction_counts: Mapping[Label, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    injection_signal: bool = False
    policy_result: DecisionResult | None = None
    # v0.3 typed-decision tier: the questions asked and the model's answers (``detectors.typed.TypedEvidence``), or
    # None when the tier did not run. The policy reads it; it never changes a finding by itself.
    typed: Any = None

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
            "stage_timings_ms": dict(self.stage_timings_ms),
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
