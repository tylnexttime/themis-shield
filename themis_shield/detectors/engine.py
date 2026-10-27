"""Tiered detection, ordered by model across a batch.

A residency belongs to one complete finder tier, including gaps between calls.
Per-document timings include preparation/finalisation and the first document
in each finder tier is charged that tier's residency acquisition time.
"""

from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
import logging
from math import isfinite
import os
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Callable

from ..normalise import detection_view
from ..types import Label
from .allowlist import AllowList, apply_allowlist, compile_profile
from .contracts import (
    DetectionFinding, DetectionResult, DetectionTargets, StageReport,
    StageResult, Status, sensitivity, validate_findings,
)
from .escalation import INJECTION, EscalationInput, escalation_reasons
from .labels import normalise_tracer_dates
from .llm_finder import FinderModel, LLMFinder
from .pii_tracer import PIITracerDetector, PIITracerSpec
from .profile_adapter import adapt
from .propagate import address_line_numbers, propagate
from .rules import RulesDetector, organisation_value
from .secret_context import relabel_secrets
from .settings import SECRET_CONTEXT_FIXES, fix_on, v02
from .union import union_findings


TIER1_STAGES = ("rules", "pii-tracer", "propagate-tier1", "allowlist-tier1")
FINAL_STAGES = ("union-final", "propagate-final", "allowlist-final")
LLM_STAGES = ("gemma4-e4b", "gemma4-26b-a4b")
_LOG = logging.getLogger("themis_shield.detection")


def _simple(stage, findings):
    return StageResult(findings, StageReport(stage, Status.OK))


def _run(text: str, stage: str, call: Callable[[], StageResult], timings: dict | None = None) -> StageResult:
    """Run one stage, validating its output and mapping any failure to a fixed status (fail closed).

    With ``timings``, the stage's wall time in milliseconds is added under its stage name (diagnostic only).
    """
    began = perf_counter()
    try:
        result = call()
        if not isinstance(result, StageResult) or not isinstance(result.report, StageReport):
            raise ValueError("invalid-stage-output")
        validate_findings(text, result.findings)
        report = result.report
        if not isinstance(report.status, Status):
            raise ValueError("invalid-stage-status")
        score = sensitivity(report.doc_sensitivity)
        if type(report.values_not_found) is not int or report.values_not_found < 0:
            raise ValueError("invalid-stage-count")
        errors = {
            Status.FAILED: "stage-failed",
            Status.TIMEOUT: "stage-timeout",
            Status.MALFORMED: (
                "unmatched-llm-value" if report.values_not_found else "malformed-output"
            ),
        }
        return StageResult(result.findings, StageReport(
            stage, report.status, len(result.findings),
            sum(Label.PERSON in item.labels for item in result.findings),
            score, report.values_not_found, errors.get(report.status),
        ))
    except TimeoutError:
        return StageResult((), StageReport(stage, Status.TIMEOUT, error_code="stage-timeout"))
    except (ValueError, TypeError, KeyError, IndexError):
        return StageResult((), StageReport(stage, Status.MALFORMED, error_code="malformed-output"))
    except Exception:
        return StageResult((), StageReport(stage, Status.FAILED, error_code="stage-failed"))
    finally:
        elapsed = (perf_counter() - began) * 1000
        if timings is not None:
            timings[stage] = timings.get(stage, 0.0) + elapsed
        if os.environ.get("THEMIS_DEBUG") == "1":
            # Fixed stage identifier and duration only. No exception formatting.
            _LOG.info("stage=%s duration_ms=%.3f", stage, elapsed)


# Public name for other gate components that run a single stage the same way (the detector canary).
run_stage = _run


def tracer_for(targets: DetectionTargets):
    """The PII-Tracer stage for ``targets``: the injected one, else the configured CPU model (lazily loaded)."""
    if targets.tracer is not None:
        return targets.tracer
    return PIITracerDetector(PIITracerSpec(
        Path(targets.tracer_model_directory),
        targets.tracer_artefact_manifest_id,
        targets.tracer_cpu_threads,
    ))


@dataclass
class _Document:
    text: str = field(repr=False)
    statuses: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    # v0.3: per-stage wall times (ms), diagnostic only: tier-1 stages, each finder call (and, on the first document
    # of a finder tier, "<model>:acquire" for the residency), and the final union, propagation and allow-list.
    stage_timings: dict = field(default_factory=dict)
    compiled: AllowList | None = None
    raw: tuple = ()
    tracer: StageReport | None = None
    injection: bool = False
    reasons: tuple = ()
    tiers: list = field(default_factory=lambda: [1])
    llm_findings: list = field(default_factory=list)


def _public_dates(profile) -> tuple[str, ...]:
    """The deployment's public dates for tracer date normalisation; none when the profile cannot be read (the
    allow-list stage reports an invalid profile itself)."""
    try:
        values = adapt(profile).get("date_literals", [])
    except (ValueError, TypeError, KeyError, AttributeError):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _tier1(document, profile, targets):
    text = document.text
    began = perf_counter()
    stages = document.stage_timings
    rules = _run(text, "rules", lambda: RulesDetector().detect(text), stages)
    tracer = _run(text, "pii-tracer", lambda: targets.tracer.detect(text), stages)
    if v02() and tracer.report.status == Status.OK:
        tracer = StageResult(
            normalise_tracer_dates(text, tracer.findings, _public_dates(profile)), tracer.report,
        )
    document.statuses.update({"rules": rules.report, "pii-tracer": tracer.report})
    document.tracer = tracer.report
    document.raw = union_findings(rules.findings, tracer.findings)
    propagated = _run(
        text, "propagate-tier1",
        lambda: _simple("propagate-tier1", propagate(text, document.raw)), stages,
    )
    document.statuses["propagate-tier1"] = propagated.report

    def project():
        document.compiled = compile_profile(profile)
        return _simple(
            "allowlist-tier1",
            apply_allowlist(text, propagated.findings, document.compiled),
        )

    projected = _run(text, "allowlist-tier1", project, stages)
    document.statuses["allowlist-tier1"] = projected.report
    findings = (
        projected.findings if projected.report.status == Status.OK
        else propagated.findings
    )
    # The signal fires on the text as written or on the shared detection view (invisible characters removed,
    # NFKC), so a zero-width character inside "ignore all previous instructions" no longer hides it.
    document.injection = bool(INJECTION.search(text) or INJECTION.search(detection_view(text).text))
    document.reasons = escalation_reasons(EscalationInput(
        len(text), bool(findings), tracer.report.doc_sensitivity,
        targets.has_attachment, document.injection, targets.thorough,
    ))
    if targets.local_only:
        document.reasons = ()
    document.timings[1] = (perf_counter() - began) * 1000


def _raise(error):
    raise error


def _finder_tier(documents, targets, tier, model):
    selected = [document for document in documents if document.reasons]
    if not selected:
        return
    for document in selected:
        document.tiers.append(tier)

    client = targets.llm_client
    residency = getattr(client, "residency", None)
    began = perf_counter()
    try:
        if client is None:
            raise RuntimeError("required-llm-client-unavailable")
        context = residency(model.value) if residency is not None else nullcontext(client)
        with context as resident:
            acquisition_ms = (perf_counter() - began) * 1000
            for index, document in enumerate(selected):
                start = perf_counter()
                if index == 0:
                    document.stage_timings[f"{model.value}:acquire"] = acquisition_ms
                result = _run(
                    document.text, model.value,
                    lambda document=document: LLMFinder(
                        model, resident, targets.llm_max_tokens,
                    ).detect(document.text),
                    document.stage_timings,
                )
                document.statuses[model.value] = result.report
                document.llm_findings.append(result.findings)
                document.timings[tier] = (
                    (perf_counter() - start) * 1000
                    + (acquisition_ms if index == 0 else 0)
                )
    except Exception as error:
        # Entry/exit failures invalidate this tier for every affected document.
        # Tier 3 still runs following a tier-2 failure.
        elapsed = (perf_counter() - began) * 1000
        for index, document in enumerate(selected):
            result = _run(
                document.text, model.value,
                lambda error=error: _raise(error), document.stage_timings,
            )
            document.statuses[model.value] = result.report
            document.timings.setdefault(tier, elapsed if index == 0 else 0.0)
            if index == 0:
                document.stage_timings.setdefault(f"{model.value}:acquire", elapsed)


def mark_organisation_values(text, findings, *, contacts, companies):
    """v0.3: flag findings whose whole value is an organisation's contact or company number (still substituted)."""
    return tuple(
        replace(item, organisation_value=True)
        if organisation_value(text, item.start, item.end, item.labels, contacts=contacts, companies=companies)
        else item
        for item in findings
    )


def _finish(document):
    text = document.text
    began = perf_counter()
    stages = document.stage_timings
    merged = _run(
        text, "union-final",
        lambda: _simple("union-final", union_findings(document.raw, *document.llm_findings)), stages,
    )
    document.statuses["union-final"] = merged.report
    tier = document.tiers[-1]
    propagated = _run(
        text, "propagate-final",
        lambda: _simple(
            "propagate-final",
            propagate(text, merged.findings, "propagate-final", tier),
        ), stages,
    )
    document.statuses["propagate-final"] = propagated.report

    def project():
        if document.compiled is None:
            raise ValueError("invalid-deployment-profile")
        return _simple(
            "allowlist-final",
            apply_allowlist(text, propagated.findings, document.compiled),
        )

    final = _run(text, "allowlist-final", project, stages)
    document.statuses["allowlist-final"] = final.report
    findings = (
        final.findings if final.report.status == Status.OK
        else union_findings(merged.findings, propagated.findings)
    )
    contacts, companies = fix_on("organisation-contacts"), fix_on("rule-completeness")
    if contacts or companies:
        findings = mark_organisation_values(text, findings, contacts=contacts, companies=companies)
    secret_fixes = frozenset(name for name in SECRET_CONTEXT_FIXES if fix_on(name))
    if secret_fixes:
        findings = relabel_secrets(text, findings, secret_fixes)
    if fix_on("address-line-numbers"):
        findings = address_line_numbers(text, findings)
    document.timings[tier] += (perf_counter() - began) * 1000
    if any(
        not isfinite(value) or value < 0
        for value in (*document.timings.values(), *document.stage_timings.values())
    ):
        raise RuntimeError("invalid-timing")
    result = DetectionResult(
        findings=findings,
        doc_sensitivity=document.tracer.doc_sensitivity,
        tiers_run=tuple(document.tiers),
        escalation_reasons=document.reasons,
        stage_status=MappingProxyType(document.statuses),
        timings_ms=MappingProxyType(document.timings),
        stage_timings_ms=MappingProxyType(dict(document.stage_timings)),
        redaction_counts=MappingProxyType(dict(Counter(
            label for item in findings for label in item.labels
        ))),
        injection_signal=document.injection,
    )
    from ..policy import decide
    return replace(result, policy_result=decide(result))


def detect_batch(texts, profile, targets=None, *, thorough):
    """Return results in input order, without reducing requested tier coverage.

    `thorough=False` cannot weaken `targets.thorough=True`.
    LOCAL_ONLY retains the existing trusted local-preview semantics.
    A client without the optional residency context remains a supported fake
    or adapter; the application-owned client implements residency explicitly.
    """
    if isinstance(texts, (str, bytes)):
        raise TypeError("detection-input-must-be-text-sequence")
    texts = tuple(texts)
    if any(not isinstance(text, str) for text in texts):
        raise TypeError("detection-input-must-be-text")
    if type(thorough) is not bool:
        raise TypeError("invalid-request-metadata")
    targets = DetectionTargets() if targets is None else targets
    if not isinstance(targets, DetectionTargets):
        raise TypeError("invalid-detection-targets")
    if any(type(getattr(targets, name)) is not bool for name in (
        "has_attachment", "thorough", "local_only",
    )):
        raise TypeError("invalid-request-metadata")
    if not texts:
        return []
    targets = replace(targets, thorough=targets.thorough or thorough)
    if targets.tracer is None:
        targets = replace(targets, tracer=tracer_for(targets))
    documents = [_Document(text) for text in texts]
    for document in documents:
        _tier1(document, profile, targets)
    _finder_tier(documents, targets, 2, FinderModel.E4B)
    _finder_tier(documents, targets, 3, FinderModel.GEMMA_26B_A4B)
    return [_finish(document) for document in documents]


def detect(text, profile, targets=None):
    """Inspect one complete original textual surface."""
    return detect_batch([text], profile, targets, thorough=False)[0]
