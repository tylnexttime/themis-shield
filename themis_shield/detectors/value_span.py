"""Is a contact value PII-Tracer found the whole of its value, or a part that leaves text beside it?

Read only by the opt-in ``empty-finder-substitute`` switch (``policy.empty_finder_value_doubt_only``; independent
review HL-06). That switch substitutes the tracer's contact values and lets the document go when the doubt is "about
values only". A span that covers part of a value is not that: in
``{"contact": "+61 (0)491 570 156 ext 22 (ask for the deaf lady)"}`` the real tracer's span starts on the opening
quote and stops after ``ext 22``, so substitution leaves ``(ask for the deaf lady)`` in clear (and broken JSON).

A finding is marked partial, and then keeps the empty-finder hold, when its span:

1. is cut inside a token (a letter or digit on both sides of an edge), or stops inside a number or an address that
   goes on (``0491 570`` before `` 156``; ``mara.quinlan`` before ``@``);
2. holds half of a double-quoted string or of a bracket pair (it starts on a quote that closes outside it);
3. lies inside a double-quoted string, on its line, that holds anything else with a letter or a digit in it that no
   finding covers.

This reads delimiters, not meaning. It does not see an unquoted tail on the same line
(``contact: 0491 570 156 ext 22 (ask for the deaf lady)``): what is left beside a value needs the statement-level
check the switch does not have (DEV-DEBT DD-37). Marking never removes or shortens a finding.
"""

from __future__ import annotations

from dataclasses import replace

_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = frozenset(_PAIRS.values())
_JOINERS = frozenset(" -./")
_ADDRESS_JOINERS = frozenset("@._-+")
TRACER_STAGE = "pii-tracer"


def _balanced(value: str) -> bool:
    if value.count('"') % 2:
        return False
    stack = []
    for char in value:
        if char in _PAIRS:
            stack.append(_PAIRS[char])
        elif char in _CLOSERS:
            if not stack or stack.pop() != char:
                return False
    return not stack


def _goes_on(text: str, start: int, end: int) -> bool:
    """A letter or digit directly against an edge, a number that continues after a space, hyphen, dot or slash, or
    an address that continues after ``@ . _ - +``."""
    size = len(text)
    if start > 0 and text[start - 1].isalnum() and text[start].isalnum():
        return True
    if end < size and text[end - 1].isalnum() and text[end].isalnum():
        return True
    if end + 1 < size and text[end - 1].isdigit() and text[end] in _JOINERS and text[end + 1].isdigit():
        return True
    if start > 1 and text[start].isdigit() and text[start - 1] in _JOINERS and text[start - 2].isdigit():
        return True
    if end + 1 < size and text[end - 1].isalnum() and text[end] in _ADDRESS_JOINERS and text[end + 1].isalnum():
        return True
    if start > 1 and text[start].isalnum() and text[start - 1] in _ADDRESS_JOINERS and text[start - 2].isalnum():
        return True
    return False


def partial_value(text: str, start: int, end: int, covered=lambda index: False) -> bool:
    """True when ``text[start:end]`` is not a whole value (see the module text). ``covered(index)`` says whether some
    finding covers that character."""
    if not 0 <= start < end <= len(text):
        return True
    if _goes_on(text, start, end) or not _balanced(text[start:end]):
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    quotes = [index for index in range(line_start, line_end)
              if text[index] == '"' and (index == line_start or text[index - 1] != "\\")]
    opening = [index for index in quotes if index < start]
    if len(opening) % 2:
        closing = next((index for index in quotes if index >= end), None)
        if closing is None:
            return True
        if any(text[index].isalnum() and not (start <= index < end or covered(index))
               for index in range(opening[-1] + 1, closing)):
            return True
    return False


def mark_partial_values(text: str, findings) -> tuple:
    """Flag PII-Tracer findings whose span is not a whole value (still substituted; only the opt-in switch reads it)."""
    spans = [(item.start, item.end) for item in findings]

    def covered(index: int) -> bool:
        return any(start <= index < end for start, end in spans)

    return tuple(
        replace(item, partial_value=True)
        if any(entry.stage == TRACER_STAGE for entry in item.evidence)
        and partial_value(text, item.start, item.end, covered) else item
        for item in findings
    )


__all__ = ["mark_partial_values", "partial_value"]
