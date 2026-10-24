"""Offset-safe original-text substitution, with value-free replacement metadata.

M2 supplies original Python-character spans. A native Finding's statement_span
is required for sensitive labels; simple span triples assert whole-statement
boundaries themselves. M1 does not infer sentence or semantic boundaries.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping
import unicodedata

from .placeholders import (
    SENSITIVE_LABELS, SecretFindingError, TokenAllocator, TokenClass,
    escape_literals, token_class_for,
)
from .types import Finding, Label, Span


MAX_RESUBSTITUTION_ROUNDS = 3
# Explicit and frozen: sensitive > identifier > contact > address > person
# > quasi-identifier > date. Equal strengths use label lexical order.
CATEGORY_STRENGTH = {
    TokenClass.STATEMENT: 7,
    TokenClass.IDENTIFIER: 6,
    TokenClass.CONTACT: 5,
    TokenClass.ADDRESS: 4,
    TokenClass.PERSON: 3,
    TokenClass.QUASI_IDENTIFIER: 2,
    TokenClass.DATE: 1,
}


class SubstitutionError(ValueError):
    pass


class OriginalLeakError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class SpanFinding:
    start: int
    end: int
    label: Label | str


@dataclass(frozen=True, slots=True)
class Replacement:
    token: str
    label: Label
    category: TokenClass
    original_span: Span


@dataclass(frozen=True, slots=True)
class SubstitutionResult:
    tokenised_text: str = field(repr=False)
    replacements: tuple[Replacement, ...]
    redaction_summary: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "redaction_summary", MappingProxyType(dict(self.redaction_summary))
        )

    @property
    def authorised_tokens(self) -> frozenset[str]:
        return frozenset(item.token for item in self.replacements)


def _findings(
    findings: Iterable[SpanFinding | Finding | tuple[int, int, str]],
    length: int,
) -> list[tuple[int, int, Label]]:
    result = []
    for item in findings:
        if isinstance(item, Finding):
            labels = set(item.labels) | {item.primary_label}
            for label in labels:
                label = Label(label)
                span = item.span
                if label in SENSITIVE_LABELS:
                    if item.statement_span is None:
                        raise SubstitutionError("sensitive finding requires whole statement span")
                    span = item.statement_span
                    if not (
                        span.start <= item.span.start < item.span.end <= span.end
                    ):
                        raise SubstitutionError("statement span must cover finding")
                result.append((span.start, span.end, label))
        elif isinstance(item, SpanFinding):
            result.append((item.start, item.end, Label(item.label)))
        else:
            try:
                start, end, label = item
                result.append((start, end, Label(label)))
            except (TypeError, ValueError):
                raise SubstitutionError("invalid span finding") from None
    for start, end, label in result:
        if label == Label.SECRET:
            raise SecretFindingError("SECRET findings block substitution")
        if (
            type(start) is not int or type(end) is not int
            or not 0 <= start < end <= length
        ):
            raise SubstitutionError("finding is outside original text")
    # Exact duplicate detector evidence must not inflate the operator summary.
    return sorted(set(result), key=lambda item: (item[0], item[1], item[2].value))


def substitute(
    text: str,
    findings: Iterable[SpanFinding | Finding | tuple[int, int, str]],
    allocator: TokenAllocator,
) -> SubstitutionResult:
    if not isinstance(text, str):
        raise TypeError("substitution input must be text")
    spans = _findings(findings, len(text))
    groups: list[list[tuple[int, int, Label]]] = []
    for span in spans:
        if groups and span[0] <= max(item[1] for item in groups[-1]):
            groups[-1].append(span)
        else:
            groups.append([span])
    replacements: list[Replacement] = []
    output: list[str] = []
    summary: Counter[str] = Counter()
    cursor = 0
    for group in groups:
        start = min(item[0] for item in group)
        end = max(item[1] for item in group)
        label = max(
            (item[2] for item in group),
            key=lambda item: (CATEGORY_STRENGTH[token_class_for(item)], item.value),
        )
        category = token_class_for(label)
        token = allocator.allocate(text[start:end], category)
        output.extend((escape_literals(text[cursor:start]), token))
        replacements.append(Replacement(token, label, category, Span(start, end)))
        # A merged replacement contributes once to each distinct evidence label.
        summary.update({item[2].value for item in group})
        cursor = end
    output.append(escape_literals(text[cursor:]))
    return SubstitutionResult("".join(output), tuple(replacements), dict(summary))


def _normalised(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def verify_no_originals(tokenised_text: str, originals: Iterable[str]) -> bool:
    """Reject any known original still present after normalisation.

    This is a known-original lexical check, NOT proof of detector completeness,
    lawful de-identification or absence of contextual re-identification.
    """

    candidate = _normalised(tokenised_text)
    for value in originals:
        if not isinstance(value, str):
            raise TypeError("original values must be text")
        normalised = _normalised(value)
        if normalised and normalised in candidate:
            raise OriginalLeakError("known original remains in outbound text")
    return True
