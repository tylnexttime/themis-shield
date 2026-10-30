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

# Policy behaviour version, set at start-up from [policy] version. "v0.1" is the evaluated v0.1 policy, kept as the
# negative control.
POLICY_VERSIONS = ("v0.1", "v0.2")
DEFAULT_POLICY_VERSION = "v0.2"
DEFAULT_QUASI_ID_ASK = False
_policy = {"version": DEFAULT_POLICY_VERSION, "quasi_id_ask": DEFAULT_QUASI_ID_ASK}


def configure_policy(version: str | None = None, *, quasi_id_ask: bool | None = None) -> dict:
    """Set the process-wide policy behaviour; ``None`` restores each default. Returns the active settings."""
    version = DEFAULT_POLICY_VERSION if version is None else version
    if version not in POLICY_VERSIONS:
        raise ValueError("unknown policy version")
    quasi = DEFAULT_QUASI_ID_ASK if quasi_id_ask is None else quasi_id_ask
    if type(quasi) is not bool:
        raise ValueError("invalid quasi_id_ask")
    _policy.update(version=version, quasi_id_ask=quasi)
    return dict(_policy)


def policy_settings() -> dict:
    return dict(_policy)


# v0.2 empty-finder disagreement: when both LLM finders return nothing, only PII-Tracer findings that SURVIVE the
# final allow-list count as contrary evidence, by their (normalised) tracer label. v0.1 counted the tracer's raw
# findings, so an allow-listed role mailbox or a public date the tracer had called a date of birth held a clean
# document. A date the tracer flags that is not a date of birth (OTHER_DATE after normalisation: a time stamp, a
# weekday, a public date) is not contrary evidence on its own; every other label still is.
# A tracer finding counts only when the tracer is confident in it (or gave no score): an unsure tracer span is still
# redacted, but it does not hold a document that both finders read as clean.
DISAGREEMENT_IGNORED_TRACER_LABELS = frozenset({Label.OTHER_DATE})
DISAGREEMENT_TRACER_MIN_SCORE = 0.9


def contrary_tracer_findings(evidence: DetectionResult) -> list[tuple]:
    """``(finding, tracer labels)`` for every finding that counts as contrary evidence for the empty-finder hold.

    v0.3 (detection fix ``organisation-contacts`` / ``rule-completeness``): a finding whose whole value is an
    organisation's role mailbox, inbound number or company number is still substituted but is not contrary evidence.
    The flag is only ever set by detection; it cannot cancel a finding and is never read when a finder found anything.
    """
    out = []
    for found in evidence.findings:
        if found.organisation_value or getattr(found, "implausible_tracer_value", False):
            continue
        labels = frozenset(
            item.label for item in found.evidence
            if item.stage == "pii-tracer" and item.label not in DISAGREEMENT_IGNORED_TRACER_LABELS
            and (item.score is None or item.score >= DISAGREEMENT_TRACER_MIN_SCORE)
        )
        if labels:
            out.append((found, labels))
    return out


def surviving_tracer_labels(evidence: DetectionResult) -> frozenset[Label]:
    """Labels PII-Tracer attached to findings that are still present after the final allow-list (value-free)."""
    return frozenset(label for _, labels in contrary_tracer_findings(evidence) for label in labels)


# ----------------------------------------------------------------------------------------------------------------------
# v0.3 typed-decision tier (T1.5; ROADMAP section 2). Off unless [typed] enabled. The model's answers live on the
# evidence (``evidence.typed``); this policy decides what they may do:
# - Q-S may lift ASK:sensitive, Q-M / Q-N / Q-P may lift ASK:disagreement-empty-llm, each only when every finding
#   behind that hold has a confident lifting answer from its family, the hold has no other cause, and (unless
#   ``mixed_families``) one family is the hold's sole cause. Never a marking, secret, injection, unmatched-value,
#   few-persons, stage or quasi-identifier hold: any of those keeps the document held. A lifted hold means RELEASE of
#   the substituted text; no finding is removed.
# - Q-I may only ADD a hold (ASK:typed-indirect) to a document that would otherwise be released.
# ----------------------------------------------------------------------------------------------------------------------
TYPED_STAGE = "typed-indirect"
TYPED_LIFTABLE = frozenset({ReasonCode.SENSITIVE, ReasonCode.DISAGREEMENT_EMPTY_LLM})
TRACER_FAMILY = {
    Label.EMAIL: "Q-M", Label.PHONE: "Q-M",
    Label.DATE_OF_BIRTH: "Q-N", Label.STUDENT_ID: "Q-N",
    Label.PERSON: "Q-P",
}


def typed_only(item) -> bool:
    """A finding the typed tier added (a Q-I sentence): substituted, but it holds only through ASK:typed-indirect."""
    return bool(item.evidence) and all(entry.stage == TYPED_STAGE for entry in item.evidence)


def sensitive_findings(evidence: DetectionResult) -> list:
    return [item for item in evidence.findings if not typed_only(item) and item.labels & SENSITIVE_LABELS]


def tracer_family(labels) -> str | None:
    families = {TRACER_FAMILY.get(label) for label in labels}
    return families.pop() if len(families) == 1 and None not in families else None


def empty_llm_lift_candidates(evidence: DetectionResult) -> list[tuple] | None:
    """``(finding, family)`` behind an empty-finder hold the typed tier could lift, or None when no answer can lift
    it (sensitivity score at or over the threshold, the v0.1 policy, a finding of no typed family)."""
    if _policy["version"] == "v0.1" or evidence.doc_sensitivity >= EMPTY_LLM_SENSITIVITY_THRESHOLD:
        return None
    contrary = contrary_tracer_findings(evidence)
    if not contrary:
        return None
    out = []
    for found, labels in contrary:
        family = tracer_family(labels)
        if family is None:
            return None
        out.append((found, family))
    return out


def _covered(answers, family, item):
    return [a for a in answers if a.family == family and a.start <= item.start and item.end <= a.end]


def _cleared(answers, family, items, settings):
    """None when every item has a confident lifting answer from ``family``; else why not (a fixed code)."""
    from .detectors.typed import LIFTING_CHOICES

    if not settings.family_on(family):
        return "family-off"
    if not items:
        return "no-finding"
    for item in items:
        found = _covered(answers, family, item)
        if not found:
            return "unasked"
        if any(answer.unsure for answer in found):
            return "unsure"
        if any(answer.choice not in LIFTING_CHOICES[family] for answer in found):
            return "answered-keep"
    return None


def _typed_effects(evidence: DetectionResult, reasons: tuple) -> tuple[tuple, tuple[str, ...]]:
    from .detectors.typed import INDIRECT_NONE, typed_settings

    settings = typed_settings()
    typed = getattr(evidence, "typed", None)
    if typed is None or not settings.enabled:
        return reasons, ()
    answers = typed.answers
    notes: list[str] = []
    if reasons and set(reasons) <= TYPED_LIFTABLE:
        families: set[str] = set()
        why: dict = {}
        for reason in reasons:
            if reason == ReasonCode.SENSITIVE:
                why[reason] = _cleared(answers, "Q-S", sensitive_findings(evidence), settings)
                families.add("Q-S")
                continue
            candidates = empty_llm_lift_candidates(evidence)
            if candidates is None:
                why[reason] = "not-liftable"
                continue
            by_family: dict[str, list] = {}
            for found, family in candidates:
                by_family.setdefault(family, []).append(found)
            failures = [_cleared(answers, family, items, settings) for family, items in sorted(by_family.items())]
            why[reason] = next((item for item in failures if item), None)
            families.update(by_family)
        if len(families) > 1 and not settings.mixed_families:
            # Several families behind the holds: not one sole cause, whatever the answers (none are asked then).
            why = {reason: value if value == "not-liftable" else "mixed-families" for reason, value in why.items()}
        if all(value is None for value in why.values()):
            notes.extend(f"lifted:{reason.value}:{'+'.join(sorted(families))}" for reason in reasons)
            reasons = ()
        else:
            notes.extend(f"kept:{reason.value}:{value or 'other-hold'}" for reason, value in why.items())
    if not reasons and settings.family_on("Q-I"):
        flagged = [a for a in answers if a.family == "Q-I" and not a.unsure and a.choice != INDIRECT_NONE]
        if flagged:
            reasons = (ReasonCode.TYPED_INDIRECT,)
            notes.append(f"added:{ReasonCode.TYPED_INDIRECT.value}:Q-I")
    return reasons, tuple(notes)


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

    # Sentences the typed tier added (Q-I) are substituted, but hold only through ASK:typed-indirect below.
    labels = {label for item in evidence.findings if not typed_only(item) for label in item.labels}
    if Label.SECRET in labels:
        reasons.append(ReasonCode.SECRET)
    if labels.intersection(SENSITIVE_LABELS):
        reasons.append(ReasonCode.SENSITIVE)
    v01 = _policy["version"] == "v0.1"
    if not v01 and _policy["quasi_id_ask"] and Label.QUASI_ID in labels:
        reasons.append(ReasonCode.QUASI_ID)

    if escalated:
        tracer = evidence.stage_status.get("pii-tracer")
        llms = [evidence.stage_status.get(stage) for stage in LLM_STAGES]
        if tracer is not None and all(item is not None for item in llms):
            llm_count = sum(item.finding_count for item in llms)
            llm_person_count = sum(item.person_count for item in llms)
            if llm_count == 0 and (
                (tracer.finding_count > 0 if v01 else bool(surviving_tracer_labels(evidence)))
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
    reasons, typed_notes = _typed_effects(evidence, reasons)
    decision = (
        Decision.BLOCK if any(item.value.startswith("BLOCK:") for item in reasons)
        else Decision.ASK if reasons else Decision.RELEASE
    )
    return DecisionResult(decision, reasons, typed_notes)
