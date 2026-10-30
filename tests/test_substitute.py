"""Synthetic original-span substitution and lexical leak checks."""

from dataclasses import asdict

import pytest

from themis_shield.placeholders import SecretFindingError, TokenAllocator, TokenClass
from themis_shield.restore import restore_text
from themis_shield.substitute import (
    OriginalLeakError, SubstitutionError, substitute, verify_no_originals,
)
from themis_shield.types import Finding, Label, Span

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


def test_substitute_original_offsets_and_value_free_metadata():
    text = "⟦ Mara Lindahl uses S1234567."
    start = text.index("Mara Lindahl")
    identifier = text.index("S1234567")
    allocator = TokenAllocator("synthetic")
    result = substitute(text, [
        (start, start + 12, "PERSON"),
        (identifier, identifier + 8, "STUDENT_ID"),
    ], allocator)
    assert restore_text(result.tokenised_text, allocator.mapping()).text == text
    assert result.redaction_summary == {"PERSON": 1, "STUDENT_ID": 1}
    assert "Mara Lindahl" not in repr(result)
    for replacement in result.replacements:
        metadata = asdict(replacement)
        assert "Mara Lindahl" not in repr(metadata)
        assert "S1234567" not in repr(metadata)
        assert set(metadata) == {"token", "label", "category", "original_span", "labels"}
        assert replacement.labels == {replacement.label}  # label names only (the legend reads them), never values
    assert verify_no_originals(result.tokenised_text, allocator.mapping().values())


def test_overlap_and_adjacent_spans_union_sensitive_wins():
    text = "Mara Lindahl has synthetic health context."
    allocator = TokenAllocator("synthetic")
    result = substitute(text, [
        (0, 12, "PERSON"),
        (12, len(text), "HEALTH"),
        (5, 12, "PERSON"),
    ], allocator)
    assert len(result.replacements) == 1
    replacement = result.replacements[0]
    assert replacement.category == TokenClass.STATEMENT
    assert replacement.original_span == Span(0, len(text))
    assert result.redaction_summary == {"PERSON": 1, "HEALTH": 1}
    assert allocator.mapping()[replacement.token] == text


def test_native_sensitive_finding_requires_statement_boundaries():
    text = "Mara Lindahl has synthetic health context."
    finding = Finding(
        Span(26, 32), frozenset({Label.HEALTH}), Label.HEALTH,
        "synthetic", statement_span=Span(0, len(text)),
    )
    allocator = TokenAllocator("synthetic")
    result = substitute(text, [finding], allocator)
    assert result.replacements[0].original_span == Span(0, len(text))
    assert allocator.mapping()[result.replacements[0].token] == text
    incomplete = Finding(
        Span(26, 32), frozenset({Label.HEALTH}), Label.HEALTH, "synthetic"
    )
    with pytest.raises(SubstitutionError, match="statement"):
        substitute(text, [incomplete], TokenAllocator("another"))


def test_secret_blocks_before_any_allocation():
    allocator = TokenAllocator("synthetic")
    with pytest.raises(SecretFindingError):
        substitute("Mara Lindahl synthetic credential", [
            (0, 12, "PERSON"), (13, 33, "SECRET"),
        ], allocator)
    assert allocator.mapping() == {}


@pytest.mark.parametrize(
    "span", [(-1, 3, "PERSON"), (0, 100, "PERSON"), (3, 3, "PERSON"), (True, 3, "PERSON")]
)
def test_bad_spans_fail_closed(span):
    with pytest.raises(SubstitutionError):
        substitute("Mara Lindahl", [span], TokenAllocator("synthetic"))


def test_leak_check_normalises_case_unicode_and_whitespace():
    with pytest.raises(OriginalLeakError):
        verify_no_originals("Unchanged MARA \n LINDAHL remains.", ["Mara Lindahl"])
    with pytest.raises(OriginalLeakError):
        verify_no_originals("Ｓ１２３４５６７", ["S1234567"])
    assert verify_no_originals("Only ⟦P:abcdef⟧ remains.", ["Mara Lindahl"])


@pytest.mark.parametrize("label", [label for label in Label if label != Label.SECRET])
def test_complete_lab_label_mapping(label):
    allocator = TokenAllocator("synthetic")
    result = substitute("synthetic statement", [(0, 19, label)], allocator)
    assert result.replacements[0].category.value in "PCAIDSQ"
