"""Pure provisional tier-selection rule with fixed reason codes.

Only the INJECTION expression is used here; tier selection is a separate design decision. The expression is two
lists joined: the injection phrases (``INJECTION_CORE``) and the claims that a text is already de-identified or holds
nothing personal (``DEIDENTIFIED_CLAIM``, added after the hold-lift review HL-04). A match can only add a hold.
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

INJECTION_CORE = (
    r"(?i)(ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|messages)|disregard (the|all|any) "
    r"(instructions|rules)|you are (now )?(an?|the) (ai|assistant|model|redaction|privacy)|system\s*(prompt|message)?\s*:|"
    r"\[\s*system\s*\]|<\s*/?\s*system\s*>|do not (redact|mask|anonymi[sz]e|remove)|(names|people|persons) (here |below |above )?"
    r"are (fictional|fake|made up|not real)|(output|return|respond with|reply with) (nothing|an empty|\{\s*\"entities\"|\[\])|"
    r"approve (this|the) (text|request|message)|no (pii|personal information) (here|in this|is present)|as an ai\b|"
    r"assistant\s*:|role\s*:\s*(system|assistant))"
)
# A text that says of itself that the work is already done: "this file was already de-identified", "the rows are
# anonymised", "contains no personal data", "there is nothing personal to list in it", "nothing to redact"
# (independent review HL-04, 13/11/2026). Such a note asks the finders for silence without any of the phrases above,
# and it is also what a careless export header says about a file that still holds a name. Either way a person should
# look. Like every alternative here it can only ADD the injection hold (ASK:injection-signal); it never removes a
# hold and never changes a finding. The cost is friction: a clean notice that says it "contains no personal
# information" is held too (one clean FAQ on the development sets).
_DONE = r"(?:de-?identified|anonymi[sz]ed|pseudonymi[sz]ed|redacted|saniti[sz]ed|scrubbed)"
_KIND = r"(?:pii|personal|sensitive|identifying|private|confidential)"
DEIDENTIFIED_CLAIM = (
    r"(?:\b(?:already|previously)\s+(?:been\s+)?(?:fully\s+|completely\s+|properly\s+)?" + _DONE + r"\b"
    r"|\b(?:has|have|had)\s+been\s+(?:fully\s+|completely\s+|properly\s+|carefully\s+)?" + _DONE + r"\b"
    r"|\b(?:is|are|was|were)\s+(?:now\s+|all\s+|fully\s+|completely\s+)?(?:de-?identified|anonymi[sz]ed|pseudonymi[sz]ed)\b"
    r"|\bnothing\s+" + _KIND + r"\b"
    r"|\b(?:contains?|holds?|has|have|includes?|with)\s+no\s+" + _KIND + r"\s+(?:information|data|details)\b"
    r"|\bno\s+(?:pii|(?:personal|sensitive|identifying)\s+(?:information|data|details))\s+"
    r"(?:here|in\s+(?:it|this|the)|is\s+present|to\s+(?:list|find|report|redact|remove|flag|mask)|remains?|left)\b"
    r"|\bnothing\s+(?:here\s+|in\s+it\s+|in\s+this\s+\w+\s+)?(?:to|needs?\s+to\s+be|needs?)\s+"
    r"(?:redact(?:ed|ing)?|mask(?:ed|ing)?|anonymi[sz](?:e|ed|ing)|de-?identif(?:y|ied|ying))\b"
    r"|\bno\s+need\s+to\s+(?:redact|mask|anonymi[sz]e|de-?identify)\b)"
)
INJECTION = re.compile(INJECTION_CORE + "|" + DEIDENTIFIED_CLAIM)


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
