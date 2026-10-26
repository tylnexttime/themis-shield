"""Specify S0 normalisation with conservative mapping to original characters.

Carries forward the v0.2 §4 offset-map contract. Normalisation may expand,
collapse or remove characters, but must not shrink required source protection.
The original source is substituted, never reconstructed from normalised text.

Real today: :func:`detection_view`, the shared detection view used by the
rule packs, the marking gate and the injection signal (v0.2.1). The
document-level :func:`normalise` contract below is still a skeleton.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata

from .types import Span


# --------------------------------------------------------------------------
# Detection view (real)

# Unicode Default_Ignorable_Code_Point, as listed in DerivedCoreProperties.txt
# of the Unicode Character Database
# (https://www.unicode.org/Public/15.0.0/ucd/DerivedCoreProperties.txt; the
# same ranges in 15.1 and 16.0). These code points render as nothing, so text
# can carry them inside a word or a value without any visible change.
# Inclusive ranges.
DEFAULT_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),    # SOFT HYPHEN
    (0x034F, 0x034F),    # COMBINING GRAPHEME JOINER
    (0x061C, 0x061C),    # ARABIC LETTER MARK
    (0x115F, 0x1160),    # HANGUL CHOSEONG/JUNGSEONG FILLER
    (0x17B4, 0x17B5),    # KHMER VOWEL INHERENT AQ..AA
    (0x180B, 0x180F),    # MONGOLIAN FREE VARIATION SELECTORS, VOWEL SEPARATOR
    (0x200B, 0x200F),    # ZERO WIDTH SPACE..RIGHT-TO-LEFT MARK (incl. ZWNJ, ZWJ)
    (0x202A, 0x202E),    # bidirectional embeddings and overrides
    (0x2060, 0x206F),    # WORD JOINER, invisible operators, bidi isolates, deprecated formats
    (0x3164, 0x3164),    # HANGUL FILLER
    (0xFE00, 0xFE0F),    # VARIATION SELECTOR-1..16
    (0xFEFF, 0xFEFF),    # ZERO WIDTH NO-BREAK SPACE (byte order mark)
    (0xFFA0, 0xFFA0),    # HALFWIDTH HANGUL FILLER
    (0xFFF0, 0xFFF8),    # reserved
    (0x1BCA0, 0x1BCA3),  # SHORTHAND FORMAT CONTROLS
    (0x1D173, 0x1D17A),  # MUSICAL SYMBOL BEGIN BEAM..END PHRASE
    (0xE0000, 0xE0FFF),  # tag characters, VARIATION SELECTOR-17..256, reserved
)


def is_default_ignorable(character: str) -> bool:
    """True for a single Unicode Default_Ignorable_Code_Point."""
    point = ord(character)
    return point >= 0xAD and any(low <= point <= high for low, high in DEFAULT_IGNORABLE_RANGES)


def strip_default_ignorable(text: str) -> str:
    """Remove every default-ignorable code point (no offset mapping)."""
    return "".join(character for character in text if not is_default_ignorable(character))


@dataclass(frozen=True, slots=True)
class DetectionView:
    """A text to run detectors on, plus where each of its characters came from.

    ``offsets[i]`` is the half-open source interval of the one source character
    that produced view character ``i``. A view span ``[a, b)`` maps back to
    ``[offsets[a][0], offsets[b - 1][1])``, which also covers every removed
    invisible character between the first and the last mapped character.
    """

    source: str = field(repr=False)
    text: str = field(repr=False)
    offsets: tuple[tuple[int, int], ...]

    @property
    def changed(self) -> bool:
        return self.text != self.source

    def to_source(self, start: int, end: int) -> tuple[int, int]:
        """Map a non-empty view span to its covering source span."""
        if not 0 <= start < end <= len(self.text):
            raise ValueError("view span out of range")
        return self.offsets[start][0], self.offsets[end - 1][1]


def detection_view(text: str) -> DetectionView:
    """Build the detection view: drop default-ignorables, then NFKC per character.

    Per-character NFKC keeps the back-mapping exact: each view character points
    to the complete source character it came from (a compatibility character
    that expands, such as a ligature, maps every output character to that one
    source character). Nothing here changes the source; detectors map view
    spans back with :meth:`DetectionView.to_source`, and substitution works on
    the original text.
    """
    if not isinstance(text, str):
        raise TypeError("detection view input must be text")
    characters: list[str] = []
    offsets: list[tuple[int, int]] = []
    for index, character in enumerate(text):
        if is_default_ignorable(character):
            continue
        for normal in unicodedata.normalize("NFKC", character):
            if is_default_ignorable(normal):  # none in Unicode 15; kept as a guard
                continue
            characters.append(normal)
            offsets.append((index, index + 1))
    return DetectionView(text, "".join(characters), tuple(offsets))


# --------------------------------------------------------------------------
# Document normalisation (skeleton)


@dataclass(frozen=True, slots=True)
class OffsetMap:
    """Map normalised characters to covering intervals in the original source.

    Each output character has an original covering interval. Removed characters
    at affected boundaries require boundary coverage as well. Projection of a
    span must cover every contributing source character, including combining
    marks and removed controls within the protected interval.
    """

    original_length: int
    normalised_length: int
    character_sources: tuple[Span, ...]
    boundary_sources: tuple[tuple[Span, ...], ...]

    def to_original(self, normalised_span: Span) -> Span:
        """Return a conservative source envelope; reject unmappable offsets."""
        raise NotImplementedError("skeleton: not implemented")

    def validate(self) -> None:
        """Check bounds, coverage, monotonicity and boundary-map completeness."""
        raise NotImplementedError("skeleton: not implemented")


@dataclass(frozen=True, slots=True)
class NormalisedDocument:
    original: str = field(repr=False)
    normalised: str = field(repr=False)
    offsets: OffsetMap


def normalise(text: str) -> NormalisedDocument:
    """Normalise reviewed Unicode and control variants without losing coverage.

    Exact transformation choices must be frozen and property-tested before
    acceptance. Invalid mapping blocks release rather than guessing offsets.
    """
    raise NotImplementedError("skeleton: not implemented")
