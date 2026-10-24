"""Specify S0 normalisation with conservative mapping to original characters.

Carries forward the v0.2 §4 offset-map contract. Normalisation may expand,
collapse or remove characters, but must not shrink required source protection.
The original source is substituted, never reconstructed from normalised text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Span


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
