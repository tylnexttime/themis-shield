"""Profile-driven security-marking gate, run before routing and the PII pipeline.

A *marking profile* describes one labelling scheme: which phrases are
classifications and which sensitivity tier each maps to, which caveats raise
the tier, which handling markers ask for confirmation, paragraph markers and
metadata keys. Shipped profiles live in ``themis_shield/profiles/markings``;
deployments add or override profiles from an external ``profiles_dir``
(see :mod:`themis_shield.profiles`). The default profile is ``generic``.

The tiers are an ordinal scale (``MarkingClass``). Which tiers need a
confirmation and which stay local-only is a :class:`MarkingPolicy`, carried by
the profile and overridable in configuration; nothing here hard-codes it.

False-positive policy: err towards local.
- Classification terms in marking positions count in any case.
- Marking positions include mostly-marking banners, the first/last three
  non-empty document/page lines, metadata values (``[KEY=...]`` or a
  ``Subject:`` line carrying ``KEY=``) and paragraph markings.
- UPPERCASE classification terms anywhere in the body count, quoted or not.
- Lowercase prose and Title-case classification words in the interior body
  do not count. First/last-line prose is deliberately more conservative.
- Spaced letters and the specified separator variants count.
- A recognised caveat without a classification implies at least the
  profile's ``caveat_floor`` tier.
- Paragraph markers count only at paragraph start, followed by whitespace and
  a non-space character; profile-listed short markers additionally need an
  ASCII capital letter or digit after them.
- Rules flagged ``any_case_in_body`` count in any case, including the body.
- Invisible characters and compatibility forms do not hide a marking: the
  surface is also read through the shared detection view (default-ignorable
  code points removed, NFKC), and the stricter of the two readings wins.

Findings contain canonical values and half-open original Python-character
offsets, never matched text or excerpts. HEADER_FOOTER denotes a mostly-marking
line; FIRST_LAST_LINES denotes the positional heuristic. EPMS (metadata) and
PARAGRAPH take precedence over those line heuristics. BODY_UPPER is also the
fallback position for recognised mixed-case any-case phrases.

This is text detection, not document extraction or classification assessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum
import re
from threading import Lock
from typing import Any, Mapping

from ..normalise import detection_view


class MarkingClass(IntEnum):
    """Ordinal sensitivity tiers; profiles map their own labels onto these."""

    NONE = 0
    INTERNAL = 1
    SENSITIVE = 2
    RESTRICTED = 3
    SECRET = 4
    TOP_SECRET = 5


class PositionKind(StrEnum):
    HEADER_FOOTER = "HEADER_FOOTER"
    FIRST_LAST_LINES = "FIRST_LAST_LINES"
    EPMS = "EPMS"
    PARAGRAPH = "PARAGRAPH"
    BODY_UPPER = "BODY_UPPER"
    BODY_QUOTED = "BODY_QUOTED"


class FindingCategory(StrEnum):
    CLASSIFICATION = "CLASSIFICATION"
    CAVEAT = "CAVEAT"
    HANDLING = "HANDLING"
    LEGACY = "LEGACY"


type MarkerValue = MarkingClass | str


@dataclass(frozen=True, slots=True)
class MarkingFinding:
    category: FindingCategory
    value: MarkerValue
    position_kind: PositionKind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MarkingResult:
    """Carry only canonical findings; caveats can raise the inferred class."""

    classification: MarkingClass = MarkingClass.NONE
    caveats: tuple[str, ...] = ()
    handling: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()
    findings: tuple[MarkingFinding, ...] = ()


# --------------------------------------------------------------------------
# Policy


def _tier(value: Any) -> MarkingClass:
    if isinstance(value, MarkingClass):
        return value
    if isinstance(value, str):
        try:
            return MarkingClass[value.strip().upper().replace(" ", "_")]
        except KeyError:
            pass
    raise ValueError("invalid marking tier")


@dataclass(frozen=True, slots=True)
class MarkingPolicy:
    """Which tiers need confirmation and which stay local-only.

    ``confirm_at <= tier < local_only_at`` offers local processing or a cloud
    send after an explicit confirmation; ``tier >= local_only_at`` has no
    cloud path and no override.
    """

    confirm_at: MarkingClass = MarkingClass.SENSITIVE
    local_only_at: MarkingClass = MarkingClass.RESTRICTED

    def __post_init__(self) -> None:
        confirm, local = _tier(self.confirm_at), _tier(self.local_only_at)
        if not MarkingClass.NONE < confirm <= local:
            raise ValueError("marking policy needs NONE < confirm_at <= local_only_at")
        object.__setattr__(self, "confirm_at", confirm)
        object.__setattr__(self, "local_only_at", local)

    def is_local_only(self, tier: MarkingClass) -> bool:
        return MarkingClass(tier) >= self.local_only_at

    def needs_confirm(self, tier: MarkingClass) -> bool:
        return self.confirm_at <= MarkingClass(tier) < self.local_only_at


# --------------------------------------------------------------------------
# Profiles


def _letters(word: str) -> str:
    return r"[ \t]*".join(re.escape(letter) for letter in word)


def phrase_pattern(phrase: str, *, spaced_letters: bool = True) -> str:
    """Compile a label such as ``TOP SECRET`` or ``OFFICIAL: SENSITIVE``.

    Words may be written with spaced letters; whitespace between words is one
    or more spaces/tabs; a colon may have spaces/tabs either side.
    """
    if not isinstance(phrase, str) or not phrase.strip():
        raise ValueError("empty marking phrase")
    parts: list[str] = []
    for chunk in phrase.strip().split(":"):
        words = chunk.split()
        if not words:
            raise ValueError("invalid marking phrase")
        parts.append(r"[ \t]+".join(
            _letters(word) if spaced_letters else re.escape(word) for word in words
        ))
    return r"[ \t]*:[ \t]*".join(parts)


def _compact(text: str) -> str:
    return re.sub(r"[ \t]", "", text).upper()


@dataclass(frozen=True, slots=True)
class MarkingRule:
    category: FindingCategory
    value: MarkerValue
    pattern: str
    any_case_in_body: bool = False
    phrase: str | None = None


@dataclass(frozen=True, slots=True)
class MarkingProfile:
    """One labelling scheme plus its default routing policy."""

    name: str
    rules: tuple[MarkingRule, ...]
    policy: MarkingPolicy = field(default_factory=MarkingPolicy)
    caveat_floor: MarkingClass = MarkingClass.RESTRICTED
    legacy_floor: MarkingClass = MarkingClass.SENSITIVE
    paragraph_markers: tuple[tuple[str, MarkingClass], ...] = ()
    paragraph_full_names: bool = False
    strict_tail_markers: tuple[str, ...] = ()
    metadata_keys: tuple[str, ...] = ()
    tier_labels: Mapping[str, str] = field(default_factory=dict)
    description: str = ""

    # Compiled state (derived in __post_init__).
    _token: re.Pattern | None = field(default=None, repr=False, compare=False)
    _paragraph: re.Pattern | None = field(default=None, repr=False, compare=False)
    _paragraph_tiers: Mapping[str, MarkingClass] = field(default_factory=dict, repr=False, compare=False)
    _metadata: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", self.name):
            raise ValueError("invalid marking profile name")
        if not self.rules:
            raise ValueError("marking profile has no rules")
        token = re.compile(
            "|".join(
                rf"(?P<R{index}>\b(?:{rule.pattern})\b)"
                for index, rule in enumerate(self.rules)
            ),
            re.IGNORECASE | re.ASCII,
        )
        object.__setattr__(self, "_token", token)
        tiers: dict[str, MarkingClass] = {}
        alternatives: list[str] = []
        for marker, tier in self.paragraph_markers:
            alternatives.append(phrase_pattern(marker, spaced_letters=False))
            tiers[_compact(marker)] = tier
        if self.paragraph_full_names:
            for rule in self.rules:
                if rule.category == FindingCategory.CLASSIFICATION and rule.phrase:
                    alternatives.append(rule.pattern)
                    tiers[_compact(rule.phrase)] = rule.value  # type: ignore[assignment]
        paragraph = (
            re.compile(rf"[ \t]*\((?P<marker>(?i:{'|'.join(alternatives)}))\)")
            if alternatives else None
        )
        object.__setattr__(self, "_paragraph", paragraph)
        object.__setattr__(self, "_paragraph_tiers", tiers)
        metadata = []
        for key in self.metadata_keys:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", key):
                raise ValueError("invalid metadata key")
            escaped = re.escape(key)
            metadata.append(re.compile(rf"\[[ \t]*{escaped}[ \t]*=[ \t]*([^\]\r\n\f]+)\]", re.I))
            metadata.append(re.compile(
                rf"^[ \t]*Subject[ \t]*:[^\r\n\f]*?\b{escaped}[ \t]*=[ \t]*([^\r\n\f]+)",
                re.I | re.M,
            ))
        object.__setattr__(self, "_metadata", tuple(metadata))

    def label(self, tier: MarkingClass) -> str:
        """Human label for a tier in this scheme (for user messages only)."""
        return self.tier_labels.get(tier.name, tier.name.replace("_", " "))

    def with_policy(self, policy: MarkingPolicy) -> MarkingProfile:
        return replace(self, policy=policy)


_CATEGORIES = {item.value: item for item in FindingCategory}
_PROFILE_KEYS = {
    "name", "description", "rules", "policy", "caveat_floor", "legacy_floor",
    "paragraph_markers", "paragraph_full_names", "strict_tail_markers",
    "metadata_keys", "tier_labels",
}
_RULE_KEYS = {"category", "value", "phrase", "regex", "any_case_in_body"}


def profile_from_dict(data: Mapping[str, Any]) -> MarkingProfile:
    """Validate and compile a marking profile document (JSON object)."""
    if not isinstance(data, Mapping) or set(data) - _PROFILE_KEYS or "name" not in data:
        raise ValueError("invalid marking profile document")
    rules: list[MarkingRule] = []
    for item in data.get("rules", ()):
        if not isinstance(item, Mapping) or set(item) - _RULE_KEYS:
            raise ValueError("invalid marking rule")
        category = _CATEGORIES.get(str(item.get("category", "")).upper())
        if category is None:
            raise ValueError("invalid marking rule category")
        if ("phrase" in item) == ("regex" in item):
            raise ValueError("marking rule needs exactly one of phrase or regex")
        phrase = item.get("phrase")
        pattern = phrase_pattern(phrase) if phrase is not None else item["regex"]
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("invalid marking rule pattern")
        re.compile(pattern)
        if category == FindingCategory.CLASSIFICATION:
            value: MarkerValue = _tier(item.get("value"))
        else:
            value = item.get("value")
            if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9_]{1,64}", value):
                raise ValueError("invalid marking rule value")
        any_case = item.get("any_case_in_body", False)
        if type(any_case) is not bool:
            raise ValueError("invalid any_case_in_body flag")
        rules.append(MarkingRule(category, value, pattern, any_case, phrase))
    policy_data = data.get("policy", {})
    if not isinstance(policy_data, Mapping) or set(policy_data) - {"confirm_at", "local_only_at"}:
        raise ValueError("invalid marking policy")
    policy = MarkingPolicy(**{key: _tier(value) for key, value in policy_data.items()})
    markers = []
    for item in data.get("paragraph_markers", ()):
        if not isinstance(item, Mapping) or set(item) != {"marker", "tier"}:
            raise ValueError("invalid paragraph marker")
        markers.append((str(item["marker"]), _tier(item["tier"])))
    labels = data.get("tier_labels", {})
    if not isinstance(labels, Mapping) or not all(
        isinstance(key, str) and key in MarkingClass.__members__ and isinstance(value, str)
        for key, value in labels.items()
    ):
        raise ValueError("invalid tier labels")
    for key in ("strict_tail_markers", "metadata_keys"):
        if not all(isinstance(value, str) for value in data.get(key, ())):
            raise ValueError(f"invalid {key}")
    if type(data.get("paragraph_full_names", False)) is not bool:
        raise ValueError("invalid paragraph_full_names flag")
    return MarkingProfile(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        rules=tuple(rules),
        policy=policy,
        caveat_floor=_tier(data.get("caveat_floor", "RESTRICTED")),
        legacy_floor=_tier(data.get("legacy_floor", "SENSITIVE")),
        paragraph_markers=tuple(markers),
        paragraph_full_names=data.get("paragraph_full_names", False),
        strict_tail_markers=tuple(_compact(value) for value in data.get("strict_tail_markers", ())),
        metadata_keys=tuple(data.get("metadata_keys", ())),
        tier_labels=dict(labels),
    )


DEFAULT_PROFILE_NAME = "generic"
PROFILE_ENV = "THEMIS_MARKINGS_PROFILE"

_active_lock = Lock()
_active: MarkingProfile | None = None


def load_marking_profile(name: str = DEFAULT_PROFILE_NAME, profiles_dir=None) -> MarkingProfile:
    """Load a profile by name: external ``profiles_dir`` first, then shipped."""
    from ..profiles import load_profile_json
    return profile_from_dict(load_profile_json("markings", name, profiles_dir))


def active_marking_profile() -> MarkingProfile:
    global _active
    with _active_lock:
        if _active is None:
            import os
            _active = load_marking_profile(os.environ.get(PROFILE_ENV) or DEFAULT_PROFILE_NAME)
        return _active


def set_marking_profile(profile: MarkingProfile | None) -> MarkingProfile:
    """Set the process-wide profile at startup; ``None`` restores the default."""
    global _active
    with _active_lock:
        _active = profile
    return active_marking_profile()


def marking_policy() -> MarkingPolicy:
    """The active routing policy, used by every release lock."""
    return active_marking_profile().policy


# --------------------------------------------------------------------------
# Detection


@dataclass(frozen=True, slots=True)
class _Line:
    start: int
    end: int
    page: int
    paragraph_start: bool


_PAGE = re.compile(r"^[ \t]*Page[ \t]+\d+[ \t]+of[ \t]+\d+[ \t]*$", re.I)
_LINE = re.compile(r"[^\r\n\f]*(?:\r\n|[\r\n\f]|$)")
_QUOTES = re.compile(
    r'''"[^"\r\n\f]*"|'[^'\r\n\f]*'|“[^”\r\n\f]*”|‘[^’\r\n\f]*’'''
)


def _lines(text: str) -> tuple[_Line, ...]:
    lines: list[_Line] = []
    page = 0
    previous_blank = True
    for match in _LINE.finditer(text):
        if match.start() == match.end():
            continue
        raw = match.group()
        body = raw.rstrip("\r\n\f")
        if _PAGE.fullmatch(body):
            page += 1
            previous_blank = True
        elif body.strip():
            lines.append(
                _Line(match.start(), match.start() + len(body), page, previous_blank)
            )
            previous_blank = False
        else:
            previous_blank = True
        if raw.endswith("\f"):
            page += 1
            previous_blank = True
    return tuple(lines)


def _edge_lines(lines: tuple[_Line, ...]) -> frozenset[int]:
    edges = {line.start for line in lines[:3] + lines[-3:]}
    pages: dict[int, list[_Line]] = {}
    for line in lines:
        pages.setdefault(line.page, []).append(line)
    for page_lines in pages.values():
        edges.update(line.start for line in page_lines[:3] + page_lines[-3:])
    return frozenset(edges)


def _sorted_values(findings, category) -> tuple[str, ...]:
    return tuple(sorted({item.value for item in findings if item.category == category}))


def detect_markings(text: str, profile: MarkingProfile | None = None) -> MarkingResult:
    """Inspect one complete textual surface; offsets always address ``text``.

    The surface is scanned twice: as written, and as the shared detection view
    (default-ignorable characters removed, NFKC per character; see
    :func:`themis_shield.normalise.detection_view`), so that SECRET with a
    zero-width joiner inside, or CONFIDENTIAL in full-width letters, still
    counts. View findings are mapped back to source offsets and added to the
    raw findings, so the result fails towards the stricter marking: the
    classification is never lower than the raw text's, and a raw finding is
    never dropped.
    """

    if not isinstance(text, str):
        raise TypeError("marking input must be text")
    profile = profile or active_marking_profile()
    findings = _scan(text, profile)
    view = detection_view(text)
    if view.changed:
        seen = {(item.category, item.value, item.start, item.end) for item in findings}
        for item in _scan(view.text, profile):
            start, end = view.to_source(item.start, item.end)
            key = (item.category, item.value, start, end)
            if key not in seen:
                seen.add(key)
                findings.append(replace(item, start=start, end=end))
    return _result(findings, profile)


def _scan(text: str, profile: MarkingProfile) -> list[MarkingFinding]:
    """One pass over one text; findings carry offsets into that text."""
    token_re = profile._token
    paragraph_re = profile._paragraph
    assert token_re is not None

    findings: list[MarkingFinding] = []
    lines = _lines(text)
    edges = _edge_lines(lines)
    metadata = tuple(
        match.span(1)
        for pattern in profile._metadata
        for match in pattern.finditer(text)
    )
    quotes = tuple(match.span() for match in _QUOTES.finditer(text))

    for line in lines:
        body = text[line.start:line.end]
        tokens = tuple(token_re.finditer(body))
        paragraph = (
            paragraph_re.match(body)
            if paragraph_re is not None and line.paragraph_start else None
        )
        paragraph_span: tuple[int, int] | None = None

        if paragraph is not None:
            marker = paragraph.group("marker")
            compact = _compact(marker)
            tail = body[paragraph.end():]
            valid_tail = bool(re.match(r"[ \t]+\S", tail))
            if compact in profile.strict_tail_markers:
                valid_tail = bool(re.match(r"[ \t]+[A-Z0-9]", tail))
            tier = profile._paragraph_tiers.get(compact)
            if valid_tail and tier is not None:
                opening = body.index("(", paragraph.start(), paragraph.end())
                paragraph_span = (opening, paragraph.end())
                findings.append(
                    MarkingFinding(
                        FindingCategory.CLASSIFICATION,
                        tier,
                        PositionKind.PARAGRAPH,
                        line.start + opening,
                        line.start + paragraph.end(),
                    )
                )

        residue = token_re.sub("", body)
        if paragraph_span is not None and paragraph_re is not None:
            # Abbreviations are not in the token pattern. Remove them for banner assessment.
            residue = paragraph_re.sub("", residue, count=1)
        residue_letters = len(re.sub(r"[\W_]+", "", residue))
        marking_letters = sum(
            len(re.sub(r"[\W_]+", "", match.group())) for match in tokens
        )
        mostly_marking = bool(tokens) and (
            residue_letters == 0
            or marking_letters / max(1, marking_letters + residue_letters) >= 0.75
        )

        for match in tokens:
            if paragraph_span is not None and (
                paragraph_span[0] <= match.start() < paragraph_span[1]
            ):
                continue

            rule = profile.rules[int(match.lastgroup[1:])]
            start, end = line.start + match.start(), line.start + match.end()
            if any(a <= start and end <= b for a, b in metadata):
                position = PositionKind.EPMS
            elif mostly_marking:
                position = PositionKind.HEADER_FOOTER
            elif line.start in edges:
                position = PositionKind.FIRST_LAST_LINES
            else:
                if not rule.any_case_in_body and not match.group().isupper():
                    continue
                position = (
                    PositionKind.BODY_QUOTED
                    if any(a <= start and end <= b for a, b in quotes)
                    else PositionKind.BODY_UPPER
                )
            findings.append(
                MarkingFinding(rule.category, rule.value, position, start, end)
            )

    return findings


def _result(findings: list[MarkingFinding], profile: MarkingProfile) -> MarkingResult:
    findings.sort(key=lambda item: (item.start, item.end, item.category.value))
    classification = max(
        (
            item.value
            for item in findings
            if item.category == FindingCategory.CLASSIFICATION
        ),
        default=MarkingClass.NONE,
    )
    caveats = _sorted_values(findings, FindingCategory.CAVEAT)
    handling = _sorted_values(findings, FindingCategory.HANDLING)
    legacy = _sorted_values(findings, FindingCategory.LEGACY)
    if caveats:
        classification = max(classification, profile.caveat_floor)
    return MarkingResult(
        MarkingClass(classification), caveats, handling, legacy, tuple(findings),
    )


def summarise_markings(
    results: tuple[MarkingResult, ...], profile: MarkingProfile | None = None,
) -> MarkingResult:
    """Combine routing signals across surfaces, without misattributing offsets.

    Individual results retain their surface-relative findings in local memory.
    This request-level summary intentionally contains no finding offsets.
    """

    profile = profile or active_marking_profile()
    caveats = tuple(sorted({value for result in results for value in result.caveats}))
    classification = max(
        (result.classification for result in results),
        default=MarkingClass.NONE,
    )
    if caveats:
        classification = max(classification, profile.caveat_floor)
    return MarkingResult(
        classification=classification,
        caveats=caveats,
        handling=tuple(sorted({value for result in results for value in result.handling})),
        legacy=tuple(sorted({value for result in results for value in result.legacy})),
    )
