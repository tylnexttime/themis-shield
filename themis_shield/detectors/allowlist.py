"""Label-aware institutional projection, without deleting raw evidence.

"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ..types import Label, Span
from .contracts import DetectionFinding
from .labels import is_bare_time_or_weekday
from .profile_adapter import adapt
from .propagate import TABLE_IDENTIFIER_STAGE, TABLE_QUASI_STAGE
from .rules import CHECKSUM_LABELS, PHONE, canonical_phone, validate_identifier
from .settings import fix_on, v02
from .union import union_findings


NEVER_TRIM = frozenset({
    Label.SECRET, Label.QUASI_ID, Label.HEALTH, Label.DISABILITY,
    Label.INDIGENOUS, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
    Label.FAMILY_VIOLENCE, Label.DATE_OF_BIRTH, Label.ETHNICITY, Label.UNION,
})
CHECKSUMMED = CHECKSUM_LABELS
CONTEXT_CHARS = 40


@dataclass(frozen=True, slots=True)
class AllowList:
    literal_patterns: tuple[re.Pattern, ...] = ()
    patterns: tuple[re.Pattern, ...] = ()
    dates: tuple[re.Pattern, ...] = ()
    public_phones: frozenset[str] = frozenset()
    # v0.2: the organisation's own checksummed identifiers (its ABN), digits only. These are allow-listed even when
    # checksum-valid; every other checksum-valid identifier is still never trimmed.
    own_identifiers: frozenset[str] = frozenset()
    # v0.4 "bare-number-formats": (number pattern, key-word pattern) pairs; a number counts only after its key words.
    context_patterns: tuple[tuple[re.Pattern, re.Pattern], ...] = ()


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
    own_abn = adapted.get("institute_abn")
    own_identifiers = frozenset(
        digits for digits in (re.sub(r"[^0-9]", "", str(own_abn)),) if own_abn and len(digits) >= 9
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
        own_identifiers,
        tuple((re.compile(number), re.compile(words)) for number, words in adapted.get("context_patterns", [])),
    )


def terms(text: str, profile: AllowList) -> tuple[tuple[int, int, str], ...]:
    out = []
    for pattern in profile.literal_patterns + profile.patterns:
        out.extend((m.start(), m.end(), "term") for m in pattern.finditer(text))
    for number, words in profile.context_patterns:
        for m in number.finditer(text):
            if words.search(text[max(0, m.start() - CONTEXT_CHARS):m.start()]):
                out.append((m.start(), m.end(), "term"))
    for pattern in profile.dates:
        out.extend((m.start(), m.end(), "date") for m in pattern.finditer(text))
    for match in PHONE.finditer(text):
        if canonical_phone(match.group()) in profile.public_phones:
            out.append((match.start(), match.end(), "phone"))
    return tuple(sorted(set(out)))


def checksum_ok(label: Label | str, value: str) -> bool:
    return validate_identifier(Label(label), value).checksum_valid


_TRAILING = ".,;:!?)]}'\"’”"
_LEADING = "([{'\"<‘“"


def _own_identifier(value: str, own: frozenset[str]) -> bool:
    return bool(own) and re.sub(r"[^0-9]", "", value) in own


def trim(
    span: DetectionFinding,
    intervals: tuple[tuple[int, int, str], ...],
    text: str,
    own_identifiers: frozenset[str] = frozenset(),
) -> tuple[DetectionFinding, ...]:
    fixes = v02()
    if fix_on("column-identifiers") and any(item.stage == TABLE_IDENTIFIER_STAGE for item in span.evidence):
        # v0.4: a cell of a column headed as a personal identifier is never cancelled by the organisation's terms or
        # formats. The header is explicit context; a format is only a shape (split D: "\d{8}" work orders
        # cancelled every licence number in a "licence" column).
        return (span,)
    if span.labels.intersection(NEVER_TRIM):
        if (
            fixes and span.labels == frozenset({Label.QUASI_ID})
            and all(item.stage == TABLE_QUASI_STAGE for item in span.evidence)
        ):
            # v0.2: a table cell the column-aware pass called rare is not a quasi-identifier when the whole cell is
            # one of the organisation's own terms (a site, a building). Whole-value only, like contacts.
            covered = any(a <= span.start and span.end <= b and kind == "term" for a, b, kind in intervals)
            return () if covered else (span,)
        return (span,)
    value = text[span.start:span.end]
    if any(
        label in CHECKSUMMED and checksum_ok(label, value)
        for label in span.labels
    ) and not (fixes and _own_identifier(value, own_identifiers)):
        return (span,)

    # Contact exemptions are whole-value exemptions, never substring removal.
    # A role mailbox embedded in a longer personal address cannot cancel it.
    if span.labels.intersection({Label.EMAIL, Label.PHONE}):
        starts, ends = {span.start}, {span.end}
        if fixes:
            # v0.2: a detector that takes the sentence's full stop (or a bracket) into the span must not defeat the
            # whole-value match. The span as found still counts too: "(02) ..." is a whole public number.
            start, end = span.start, span.end
            while end > start and text[end - 1] in _TRAILING:
                end -= 1
            while start < end and text[start] in _LEADING:
                start += 1
            starts.add(start)
            ends.add(end)
        if fix_on("contact-brackets"):
            # v0.3: a detector span that lost the value's opening bracket ("07) 5550 0145") is still the whole
            # value; the interval may start one bracket earlier. Leading brackets only, never a longer prefix.
            starts.update(a for a, _, kind in intervals if kind != "date" and a < span.start
                          and text[a:span.start] and all(ch in _LEADING for ch in text[a:span.start]))
        eligible = any(
            a in starts and b in ends and kind != "date"
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
    bare_dates = fixes and span.labels == frozenset({Label.OTHER_DATE})
    if not touched:
        if bare_dates and is_bare_time_or_weekday(value):
            # v0.2: a weekday name or a time of day on its own dates nobody.
            return ()
        return (span,)
    out = []
    for a, b in pieces:
        while a < b and not text[a].isalnum():
            a += 1
        while b > a and not text[b - 1].isalnum():
            b -= 1
        if a < b and not (bare_dates and is_bare_time_or_weekday(text[a:b])):
            out.append(replace(span, span=Span(a, b)))
    return tuple(out)


def apply_allowlist(
    text: str,
    findings: tuple[DetectionFinding, ...],
    profile: AllowList,
) -> tuple[DetectionFinding, ...]:
    intervals = terms(text, profile)
    return union_findings(
        piece for item in findings
        for piece in trim(item, intervals, text, profile.own_identifiers)
    )
