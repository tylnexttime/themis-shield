"""Pure provisional tier-selection rule with fixed reason codes.

Only the INJECTION expression is used here; tier selection is a separate design decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import sensitivity


ESCALATION_SENSITIVITY_THRESHOLD = 0.2
ESCALATION_CHARACTER_THRESHOLD = 1500
FAST_PATH_SKIP_PROVISIONAL = True

PII_FOUND = "ESCALATE:tier1-personal-finding"
DOCUMENT_SENSITIVE = "ESCALATE:document-sensitivity"
LONG_TEXT = "ESCALATE:text-length"
ATTACHMENT = "ESCALATE:attachment"
INJECTION_SIGNAL = "ESCALATE:injection-signal"
THOROUGH = "ESCALATE:thorough"

INJECTION = re.compile(
    r"(?i)(ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|messages)|disregard (the|all|any) "
    r"(instructions|rules)|you are (now )?(an?|the) (ai|assistant|model|redaction|privacy)|system\s*(prompt|message)?\s*:|"
    r"\[\s*system\s*\]|<\s*/?\s*system\s*>|do not (redact|mask|anonymi[sz]e|remove)|(names|people|persons) (here |below |above )?"
    r"are (fictional|fake|made up|not real)|(output|return|respond with|reply with) (nothing|an empty|\{\s*\"entities\"|\[\])|"
    r"approve (this|the) (text|request|message)|no (pii|personal information) (here|in this|is present)|as an ai\b|"
    r"assistant\s*:|role\s*:\s*(system|assistant))"
)


@dataclass(frozen=True, slots=True)
class EscalationInput:
    character_count: int
    tier1_personal_findings: bool = False
    doc_sensitivity: float = 0.0
    has_attachment: bool = False
    injection_signal: bool = False
    thorough: bool = False


def escalation_reasons(evidence: EscalationInput) -> tuple[str, ...]:
    """Return all applicable reasons in stable order; no models or I/O."""
    if type(evidence.character_count) is not int or evidence.character_count < 0:
        raise ValueError("invalid-character-count")
    score = sensitivity(evidence.doc_sensitivity)
    tests = (
        (evidence.tier1_personal_findings, PII_FOUND),
        (score >= ESCALATION_SENSITIVITY_THRESHOLD, DOCUMENT_SENSITIVE),
        (evidence.character_count > ESCALATION_CHARACTER_THRESHOLD, LONG_TEXT),
        (evidence.has_attachment, ATTACHMENT),
        (evidence.injection_signal, INJECTION_SIGNAL),
        (evidence.thorough, THOROUGH),
    )
    return tuple(reason for applies, reason in tests if applies)
