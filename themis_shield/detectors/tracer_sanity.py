"""v0.4 fix "tracer-evidence-sanity": PII-Tracer values that cannot be what their label says.

When both LLM finders read a document as clean, a PII-Tracer finding still holds it for a person (the empty-finder
disagreement rule), because a double finder miss is exactly what that rule is there to catch. On split D's clean
documents most such holds came from values that cannot be personal data of the labelled kind:

- an identifier label on a money amount ("186.50", "8667.82,2026-11-15");
- a personal URL that is a single word ("registry");
- a person that is one role word ("Chair");
- a date of birth with no birth context nearby (an event date in a notice: "10 May 2024");
- an address with no number in it (a site or street name: "Kestrel Reservoir", "Harbour Way").

Such a finding is flagged, never removed: it is still substituted, and it simply stops counting as contrary evidence,
exactly like an organisation's own contact (``organisation_value``). Only findings that come from PII-Tracer alone are
looked at; anything a rule or a finder also found is left as it is.

The date and address rules apply only to a document in which no detector found a person (a lone role word aside): a
person the finders both missed, with a birth date or a street beside them, is exactly the double miss the hold is
for. On the development sets every document these two rules released had no person finding at all (30/10/2026).
"""

from __future__ import annotations

from dataclasses import replace
import re

from ..types import Label
from .propagate import common_role

_IDENTIFIER_LABELS = frozenset({
    Label.STUDENT_ID, Label.INTERNAL_ID, Label.USI, Label.TFN, Label.MEDICARE, Label.ABN, Label.NATIONAL_ID,
    Label.DRIVER_LICENCE, Label.PASSPORT, Label.BANK_ACCOUNT, Label.PAYMENT_CARD,
})
_AMOUNT = re.compile(r"[-−]?[$€£]?\s?\d{1,3}(?:[,\s]\d{3})*\.\d{2}(?:,\s?\d{4}-\d{2}-\d{2})?|[-−]?[$€£]?\d+\.\d{2}(?:,\s?\d{4}-\d{2}-\d{2})?")
_BIRTH_CONTEXT = re.compile(r"(?i)\b(?:born|birth|birthday|d\.?\s?o\.?\s?b\.?|aged?|years?\s+old|yo)\b|\bb\.\s")
_ROLE_WORDS = frozenset({
    "chair", "chairperson", "chairman", "chairwoman", "convenor", "convener", "secretary", "treasurer", "president",
    "deputy", "director", "minute-taker", "facilitator", "host", "speaker", "presenter", "member", "members",
})
BIRTH_WINDOW = 60
_SENTENCE_BREAK = re.compile(r"[.!?](?=\s)|\n")


def _birth_column(text: str, position: int) -> bool:
    """True when the position sits in a table whose header row (the first line of its block) names a birth date."""
    lines = text[:position].split("\n")
    current = lines[-1] + text[position:].split("\n", 1)[0]
    separator = next((sep for sep in ("\t", "|", ";", ",") if current.count(sep) >= 1), None)
    if separator is None:
        return False
    # A table row: the lines above it in the same block have the same number of separators; the first is the header.
    # A prose sentence that happens to hold a comma has no such block.
    width, header = current.count(separator), None
    for line in reversed(lines[:-1][-200:]):
        if not line.strip() or line.count(separator) != width:
            break
        header = line
    return header is not None and bool(_BIRTH_CONTEXT.search(header))


def _tracer_only(item) -> bool:
    stages = {entry.stage for entry in item.evidence}
    return bool(stages) and all(stage == "pii-tracer" or stage.startswith("propagate") for stage in stages) \
        and "pii-tracer" in stages


def implausible(text: str, item, person_in_document: bool = False) -> bool:
    value = text[item.start:item.end].strip()
    labels = item.labels
    if labels & _IDENTIFIER_LABELS and _AMOUNT.fullmatch(value):
        return True
    if Label.URL_PERSONAL in labels and not re.search(r"[./@:]", value):
        return True
    if Label.PERSON in labels and " " not in value and (value.casefold() in _ROLE_WORDS or common_role(value)):
        return True
    if person_in_document:
        return False  # the date and address rules below need a document with no person in it
    if labels == frozenset({Label.DATE_OF_BIRTH}):
        # Birth context must be in the same sentence or table row (and within BIRTH_WINDOW characters): a "Born ..."
        # in the next sentence says nothing about this date.
        low, high = max(0, item.start - BIRTH_WINDOW), min(len(text), item.end + BIRTH_WINDOW)
        before = max((m.end() for m in _SENTENCE_BREAK.finditer(text, low, item.start)), default=low)
        after = next((m.start() for m in _SENTENCE_BREAK.finditer(text, item.end, high)), high)
        if not _BIRTH_CONTEXT.search(text[before:after]) and not _birth_column(text, item.start):
            return True
    if labels == frozenset({Label.ADDRESS}) and not any(ch.isdigit() for ch in value):
        return True
    return False


def _role_word(text: str, item) -> bool:
    value = text[item.start:item.end].strip()
    return " " not in value and (value.casefold() in _ROLE_WORDS or bool(common_role(value)))


def mark_implausible_tracer_values(text: str, findings) -> tuple:
    person = any(Label.PERSON in item.labels and not _role_word(text, item) for item in findings)
    return tuple(
        replace(item, implausible_tracer_value=True) if _tracer_only(item) and implausible(text, item, person) else item
        for item in findings
    )
