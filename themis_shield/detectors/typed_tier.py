"""The typed-decision tier (T1.5) inside detection: which questions to ask, asking them, recording the answers.

Runs after the finders and before the final decision, only with ``[typed] enabled``. The questions are about what
the cheaper tiers already found (ROADMAP section 2):

- **Q-S** for each sentence holding a sensitive-statement finding, on a document whose only hold is ASK:sensitive;
- **Q-M / Q-N / Q-P** for each tracer email or phone / date of birth or number / name behind an empty-finder hold
  (both finders returned nothing), on a document whose only hold is that one;
- **Q-I** for each sentence no detector touched, on a document that would otherwise be released.

The model's answers are attached to the evidence (``DetectionResult.typed``) and the policy (``policy.decide``)
decides what they may change. With ``indirect_substitute`` a Q-I sentence the policy holds for is also substituted
as a sensitive-statement span. No finding is ever removed.

A document that cannot be lifted by any answer is not asked lifting questions (no wasted calls), unless
``ask_all`` is set for an evaluation capture, which asks every question any setting could need and changes no effect.
"""

from __future__ import annotations

from contextlib import ExitStack
import logging
import os
from dataclasses import replace
from time import perf_counter
from types import MappingProxyType

from ..types import Decision, Label, ReasonCode
from .contracts import FindingEvidence, StageReport, Status, finding
from .typed import (
    INDIRECT_NONE, Decider, TypedEvidence, TypedSettings, TypedStageError, make_question, sentences,
    typed_settings,
)

STAGE = "typed-decisions"
_LOG = logging.getLogger("themis_shield.detection")
INDIRECT_LABELS = {
    "union_membership": Label.UNION,
    "criminal_history": Label.CRIMINAL,
    "ethnicity_or_origin": Label.ETHNICITY,
    "health_or_disability": Label.HEALTH,
    "religion": Label.RELIGION,
    "sexuality": Label.SEXUALITY,
    "family_violence": Label.FAMILY_VIOLENCE,
}
MIN_INDIRECT_WORDS = 3


def _merge(spans):
    out = []
    for start, end in sorted(spans):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _maximal(spans):
    """Drop spans nested inside another span of the list (an outer answer covers them)."""
    spans = sorted(set(spans), key=lambda item: (item[0], -item[1]))
    out = []
    for start, end in spans:
        if out and out[-1][0] <= start and end <= out[-1][1]:
            continue
        out.append((start, end))
    return out


def lift_targets(text: str, evidence, settings: TypedSettings) -> list[tuple[str, int, int]]:
    """(family, start, end) of the lifting questions for one document (empty when no answer could lift it)."""
    from ..policy import TYPED_LIFTABLE, decide, empty_llm_lift_candidates, sensitive_findings

    base = decide(replace(evidence, typed=None))
    reasons = set(base.reasons)
    if base.decision != Decision.ASK or not reasons or not reasons <= TYPED_LIFTABLE:
        return []
    wanted: list[tuple[str, int, int]] = []
    families: set[str] = set()
    liftable = True
    if ReasonCode.SENSITIVE in reasons:
        families.add("Q-S")
        units = sentences(text)
        spans = []
        for item in sensitive_findings(evidence):
            hits = [(s, e) for s, e in units if s < item.end and item.start < e]
            spans.append((min([item.start, *(s for s, _ in hits)]), max([item.end, *(e for _, e in hits)])))
        wanted.extend(("Q-S", start, end) for start, end in _merge(spans))
        liftable &= settings.family_on("Q-S")
    if ReasonCode.DISAGREEMENT_EMPTY_LLM in reasons:
        candidates = empty_llm_lift_candidates(evidence)
        if candidates is None:
            liftable = False
        else:
            by_family: dict[str, list] = {}
            for found, family in candidates:
                by_family.setdefault(family, []).append((found.start, found.end))
            for family, spans in sorted(by_family.items()):
                families.add(family)
                liftable &= settings.family_on(family)
                wanted.extend((family, start, end) for start, end in _maximal(spans))
    if len(families) > 1 and not settings.mixed_families:
        liftable = False
    if settings.ask_all:
        return wanted
    return wanted if liftable else []


def indirect_targets(text: str, evidence) -> list[tuple[str, int, int]]:
    """Q-I: every sentence of at least three words that no finding touches."""
    touched = [(item.start, item.end) for item in evidence.findings]
    out = []
    for start, end in sentences(text):
        if any(s < end and start < e for s, e in touched):
            continue
        words = [word for word in text[start:end].split() if any(char.isalpha() for char in word)]
        if len(words) >= MIN_INDIRECT_WORDS:
            out.append(("Q-I", start, end))
    return out


def _ask(decider: Decider, text: str, targets, settings: TypedSettings):
    return [decider.decide(make_question(text, family, start, end, settings.max_state_chars))
            for family, start, end in targets]


def _with_answers(evidence, answers, calls, wall_ms, status=Status.OK, added=0):
    reports = dict(evidence.stage_status)
    reports[STAGE] = StageReport(
        STAGE, status, added, error_code=None if status == Status.OK else "stage-failed",
    )
    return replace(
        evidence, typed=TypedEvidence(tuple(answers), calls, wall_ms),
        stage_status=MappingProxyType(reports),
    )


def _substitute_indirect(text: str, evidence):
    """Add the Q-I sentences the policy holds for as sensitive-statement findings (substituted, never removed)."""
    from ..policy import TYPED_STAGE, decide

    decision = decide(evidence)
    if ReasonCode.TYPED_INDIRECT not in decision.reasons:
        return evidence
    added = []
    for answer in evidence.typed.answers:
        if answer.family != "Q-I" or answer.unsure or answer.choice in (None, INDIRECT_NONE):
            continue
        label = INDIRECT_LABELS[answer.choice]
        item = finding(answer.start, answer.end, label, TYPED_STAGE, 1)
        added.append(replace(item, evidence=(FindingEvidence(label, TYPED_STAGE, 1, None),)))
    if not added:
        return evidence
    findings = tuple(sorted((*evidence.findings, *added), key=lambda item: (item.start, item.end)))
    counts = dict(evidence.redaction_counts)
    for item in added:
        counts[item.primary_label] = counts.get(item.primary_label, 0) + 1
    reports = dict(evidence.stage_status)
    reports[STAGE] = replace(reports[STAGE], finding_count=len(added))
    return replace(evidence, findings=findings, redaction_counts=MappingProxyType(counts),
                   stage_status=MappingProxyType(reports))


def run_typed_tier(texts, results, runtime):
    """Ask the typed questions for a batch and return the results with answers attached and decisions recomputed.

    One residency for the whole batch (the 26B is usually still resident from the finder tier), entered only when a
    document needs a question. A tier that cannot start (no runtime, a model that will not load, an option letter
    that is not one token) fails its stage for every document of the batch: the policy then blocks, as for any
    failed stage. A single failed question is only unsure.
    """
    from ..policy import TYPED_LIFTABLE, decide

    settings = typed_settings()
    if not settings.enabled:
        return list(results)
    texts, results = list(texts), list(results)
    plans = [lift_targets(text, result, settings) for text, result in zip(texts, results)]
    bases = [decide(replace(result, typed=None)) for result in results]
    answered: list[list] = [[] for _ in results]
    calls = [0 for _ in results]
    walls = [0.0 for _ in results]

    def needs_indirect(index, provisional):
        if settings.ask_all:
            base = bases[index]
            return base.decision == Decision.RELEASE or (
                base.decision == Decision.ASK and set(base.reasons) <= TYPED_LIFTABLE
            )
        return settings.family_on("Q-I") and provisional.decision == Decision.RELEASE

    began = perf_counter()
    try:
        with ExitStack() as stack:
            decider = None
            for index, (text, result) in enumerate(zip(texts, results)):
                start = perf_counter()
                if plans[index] or needs_indirect(index, bases[index]):
                    begin = getattr(runtime, "begin_document", None)
                    if begin is not None:
                        begin(index)
                    if decider is None:
                        if runtime is None:
                            raise TypedStageError("typed tier enabled without a runtime")
                        decider = Decider(stack.enter_context(runtime.session(settings)), settings)
                    before = decider.calls
                    answered[index] = _ask(decider, text, plans[index], settings)
                    provisional = decide(_with_answers(result, answered[index], 0, 0.0))
                    if needs_indirect(index, provisional):
                        answered[index] += _ask(decider, text, indirect_targets(text, result), settings)
                    calls[index] = decider.calls - before
                walls[index] = (perf_counter() - start) * 1000
    except Exception as error:
        if os.environ.get("THEMIS_DEBUG") == "1":
            # The exception class only (fixed names such as NotEnoughMemory): never its message or document text.
            _LOG.info("stage=%s failed=%s", STAGE, type(error).__name__)
        wall = (perf_counter() - began) * 1000
        failed = []
        for index, result in enumerate(results):
            evidence = _with_answers(result, (), 0, wall if index == 0 else 0.0, Status.FAILED)
            failed.append(replace(evidence, policy_result=decide(evidence)))
        return failed
    out = []
    for index, (text, result) in enumerate(zip(texts, results)):
        evidence = _with_answers(result, answered[index], calls[index], walls[index])
        if settings.indirect_substitute:
            evidence = _substitute_indirect(text, evidence)
        out.append(replace(evidence, policy_result=decide(evidence)))
    return out
