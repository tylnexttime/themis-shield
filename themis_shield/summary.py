"""Summaries derived exclusively from substituted outbound user text."""

from __future__ import annotations

import re

from .register.models import summary_guard
from .substitute import verify_no_originals


TOKEN_PATTERN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")


def problem_summary(tokenised_user_text, *, originals=(), marking=None):
    if marking is not None:
        from .detectors.markings import MarkingClass, active_marking_profile
        name = (
            active_marking_profile().label(marking)
            if isinstance(marking, MarkingClass) else getattr(marking, "name", marking)
        )
        return summary_guard(f"Local-only job (marking: {name})")
    if not isinstance(tokenised_user_text, str):
        raise TypeError("tokenised summary input required")
    count = len(TOKEN_PATTERN.findall(tokenised_user_text))
    collapsed = " ".join(tokenised_user_text.split())
    candidate = collapsed if len(collapsed) <= 280 else collapsed[:279] + "…"
    try:
        summary_guard(candidate)
        verify_no_originals(candidate, originals)
        return candidate
    except (ValueError, PermissionError):
        return summary_guard(
            f"Synthetic request with {count} placeholders; see redaction counts"
        )


def outbound_user_text(body):
    """Read user string leaves from the finished Chat Completions payload."""
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, dict):
            # Only content-bearing leaves, not arbitrary JSON keys.
            if value.get("type") in {"text", "input_text"}:
                yield from strings(value.get("text", ""))
    return " ".join(
        text
        for message in body.get("messages", [])
        if message.get("role") == "user"
        for text in strings(message.get("content", ""))
    )
