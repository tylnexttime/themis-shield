"""Pure CLOUD technical decision from M2 evidence.


Markings, mandatory route confirmation, approvals and final payload validation
remain separate gates. RELEASE here does not authorise a cloud transmission.
The previous EvidenceLedger input stub is replaced by DetectionResult.
"""

from __future__ import annotations

from .config import UseCasePolicy
from .detectors.contracts import DetectionResult, Status
from .detectors.engine import FINAL_STAGES, LLM_STAGES, TIER1_STAGES
from .types import Decision, DecisionResult, Label, ReasonCode


EMPTY_LLM_SENSITIVITY_THRESHOLD = 0.5
TRACER_PERSON_ESCALATION_MINIMUM = 3
LLM_PERSON_RATIO_DENOMINATOR = 3

SENSITIVE_LABELS = frozenset({
    Label.HEALTH, Label.DISABILITY, Label.INDIGENOUS, Label.SEXUALITY,
    Label.RELIGION, Label.CRIMINAL, Label.FAMILY_VIOLENCE,
    Label.ETHNICITY, Label.UNION,
})
STATUS_REASONS = {
    Status.FAILED: ReasonCode.STAGE_FAILED,
    Status.TIMEOUT: ReasonCode.STAGE_TIMEOUT,
    Status.MALFORMED: ReasonCode.MALFORMED_OUTPUT,
}


# LLM values that are not in the text verbatim: ASK when there are at least this many, or at least 10 % of the
# stage's findings. Skipping and merely counting them was measured to let a leak through.
UNMATCHED_ASK_MINIMUM = 3


def decide(
    evidence: DetectionResult,
    use_case: UseCasePolicy | None = None,
) -> DecisionResult:
    """BLOCK > ASK > RELEASE, retaining all applicable fixed reason codes.

    M2's tier selection determines required LLM stages; a use-case requiring
    those finders must be integrated by requesting thorough detection. This
    function never permits missing stages in an already escalated result.
    """
    if not isinstance(evidence, DetectionResult):
        # Unsupported legacy evidence is not interpreted as a clean document.
        return DecisionResult(Decision.BLOCK, (ReasonCode.MALFORMED_OUTPUT,))
    reasons = []
    escalated = bool(evidence.escalation_reasons)
    expected_tiers = (1, 2, 3) if escalated else (1,)
    if evidence.tiers_run != expected_tiers:
        reasons.append(ReasonCode.STAGE_MISSING)
    required = TIER1_STAGES + FINAL_STAGES + (LLM_STAGES if escalated else ())
    for stage in required:
        report = evidence.stage_status.get(stage)
        if report is None:
            reasons.append(ReasonCode.STAGE_MISSING)
        elif report.status != Status.OK:
            reasons.append(STATUS_REASONS.get(
                report.status, ReasonCode.MALFORMED_OUTPUT
            ))
        elif report.values_not_found and (
            report.values_not_found >= UNMATCHED_ASK_MINIMUM
            or report.values_not_found * 10 >= max(1, report.finding_count)
        ):
            # Frequent paraphrased values: the user confirms the de-identified text (ASK), never a silent release.
            reasons.append(ReasonCode.UNMATCHED_LLM_VALUES)
    # Additional completed stages are also fail-closed.
    for report in evidence.stage_status.values():
        if report.status != Status.OK:
            reasons.append(STATUS_REASONS.get(
                report.status, ReasonCode.MALFORMED_OUTPUT
            ))

    labels = {label for item in evidence.findings for label in item.labels}
    if Label.SECRET in labels:
        reasons.append(ReasonCode.SECRET)
    if labels.intersection(SENSITIVE_LABELS):
        reasons.append(ReasonCode.SENSITIVE)

    if escalated:
        tracer = evidence.stage_status.get("pii-tracer")
        llms = [evidence.stage_status.get(stage) for stage in LLM_STAGES]
        if tracer is not None and all(item is not None for item in llms):
            llm_count = sum(item.finding_count for item in llms)
            llm_person_count = sum(item.person_count for item in llms)
            if llm_count == 0 and (
                tracer.finding_count > 0
                or evidence.doc_sensitivity >= EMPTY_LLM_SENSITIVITY_THRESHOLD
            ):
                reasons.append(ReasonCode.DISAGREEMENT_EMPTY_LLM)
            if (
                tracer.person_count >= TRACER_PERSON_ESCALATION_MINIMUM
                and llm_person_count * LLM_PERSON_RATIO_DENOMINATOR
                < tracer.person_count
            ):
                reasons.append(ReasonCode.DISAGREEMENT_FEW_PERSONS)
    if evidence.injection_signal:
        reasons.append(ReasonCode.INJECTION_SIGNAL)
    reasons = tuple(dict.fromkeys(reasons))
    decision = (
        Decision.BLOCK if any(item.value.startswith("BLOCK:") for item in reasons)
        else Decision.ASK if reasons else Decision.RELEASE
    )
    return DecisionResult(decision, reasons)
