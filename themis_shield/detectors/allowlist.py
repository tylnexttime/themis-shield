"""Label-aware institutional projection, without deleting raw evidence.

"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ..types import Label, Span
from .contracts import DetectionFinding
from .profile_adapter import adapt
from .rules import CHECKSUM_LABELS, PHONE, canonical_phone, validate_identifier
from .union import union_findings


NEVER_TRIM = frozenset({
    Label.SECRET, Label.QUASI_ID, Label.HEALTH, Label.DISABILITY,
    Label.INDIGENOUS, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
    Label.FAMILY_VIOLENCE, Label.DATE_OF_BIRTH, Label.ETHNICITY, Label.UNION,
})
CHECKSUMMED = CHECKSUM_LABELS


@dataclass(frozen=True, slots=True)
class AllowList:
    literal_patterns: tuple[re.Pattern, ...] = ()
    patterns: tuple[re.Pattern, ...] = ()
    dates: tuple[re.Pattern, ...] = ()
    public_phones: frozenset[str] = frozenset()


def compile_profile(profile: object) -> AllowList:
    adapted = adapt(profile)
    literals = list(adapted.get("literals", []))
    for key in ("campus_phrases", "reception_numbers", "institute_names"):
        literals.extend(adapted.get(key, []))
    if adapted.get("institute_abn"):
        literals.append(adapted["institute_abn"])
    if adapted.get("provider_code"):
        # The organisation's own registration/provider number and its labelled forms.
        code = str(adapted["provider_code"])
        literals.append(code)
        literals.extend(f"{label} {code}" for label in adapted.get("provider_code_labels", []))
    patterns = list(adapted.get("patterns", []))
    for local in adapted.get("role_mailbox_local_parts", []):
        for domain in adapted.get("institution_email_domains", []):
            patterns.append(
                r"(?i)(?<![\w.+@-])" + re.escape(local) + "@"
                + re.escape(domain) + r"(?![\w@-])"
            )
    if any(type(value) is not str for value in literals + patterns):
        raise ValueError("invalid-allowlist-term")
    dates = adapted.get("date_literals", [])
    if any(type(value) is not str for value in dates):
        raise ValueError("invalid-allowlist-date")
    public_phones = frozenset(
        canonical_phone(value) for value in literals
        if PHONE.fullmatch(value)
    )
    return AllowList(
        tuple(
            re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)", re.I)
            for value in literals if value
        ),
        tuple(re.compile(pattern) for pattern in patterns),
        tuple(
            re.compile(r"(?<![0-9])" + re.escape(value) + r"(?![0-9])")
            for value in dates
        ),
        public_phones,
    )


def terms(text: str, profile: AllowList) -> tuple[tuple[int, int, str], ...]:
    out = []
    for pattern in profile.literal_patterns + profile.patterns:
        out.extend((m.start(), m.end(), "term") for m in pattern.finditer(text))
    for pattern in profile.dates:
        out.extend((m.start(), m.end(), "date") for m in pattern.finditer(text))
    for match in PHONE.finditer(text):
        if canonical_phone(match.group()) in profile.public_phones:
            out.append((match.start(), match.end(), "phone"))
    return tuple(sorted(set(out)))


def checksum_ok(label: Label | str, value: str) -> bool:
    return validate_identifier(Label(label), value).checksum_valid


def trim(
    span: DetectionFinding,
    intervals: tuple[tuple[int, int, str], ...],
    text: str,
) -> tuple[DetectionFinding, ...]:
    if span.labels.intersection(NEVER_TRIM):
        return (span,)
    value = text[span.start:span.end]
    if any(
        label in CHECKSUMMED and checksum_ok(label, value)
        for label in span.labels
    ):
        return (span,)

    # Contact exemptions are whole-value exemptions, never substring removal.
    # A role mailbox embedded in a longer personal address cannot cancel it.
    if span.labels.intersection({Label.EMAIL, Label.PHONE}):
        eligible = any(
            a == span.start and b == span.end and kind != "date"
            for a, b, kind in intervals
        )
        return () if eligible else (span,)

    pieces = [(span.start, span.end)]
    touched = False
    for start, end, kind in intervals:
        if kind == "phone":
            continue
        if kind == "date" and span.labels != frozenset({Label.OTHER_DATE}):
            continue
        next_pieces = []
        for a, b in pieces:
            if end <= a or start >= b:
                next_pieces.append((a, b))
            else:
                touched = True
                if a < start:
                    next_pieces.append((a, start))
                if end < b:
                    next_pieces.append((end, b))
        pieces = next_pieces
    if not touched:
        return (span,)
    out = []
    for a, b in pieces:
        while a < b and not text[a].isalnum():
            a += 1
        while b > a and not text[b - 1].isalnum():
            b -= 1
        if a < b:
            out.append(replace(span, span=Span(a, b)))
    return tuple(out)


def apply_allowlist(
    text: str,
    findings: tuple[DetectionFinding, ...],
    profile: AllowList,
) -> tuple[DetectionFinding, ...]:
    intervals = terms(text, profile)
    return union_findings(
        piece for item in findings for piece in trim(item, intervals, text)
    )
