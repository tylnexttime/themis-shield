"""Synthetic placeholder, linking and deterministic random property tests."""

import random

import pytest

from themis_shield.placeholders import (
    AllocationError, TokenAllocator, TokenClass, escape_literals,
    parse_strict_token, token_class_for, unescape_literals,
)
from themis_shield.restore import restore_text
from themis_shield.substitute import substitute
from themis_shield.types import Label
from themis_shield.vault import InMemoryVault


def test_strict_grammar_and_exact_reuse():
    allocator = TokenAllocator("synthetic")
    token = allocator.allocate("Mara Lindahl", "P")
    assert len(parse_strict_token(token).identifier) == 6
    assert allocator.allocate("Mara Lindahl", TokenClass.PERSON) == token
    assert parse_strict_token(token).render() == token
    assert token_class_for(Label.ADDRESS) == TokenClass.ADDRESS


@pytest.mark.parametrize(
    "token", ["⟦P:abcd⟧", "⟦P:ABCDEF⟧", "⟦X:abcdef⟧", "P:abcdef"]
)
def test_bad_strict_tokens(token):
    with pytest.raises(ValueError):
        parse_strict_token(token)


def test_each_surface_has_one_exact_original_and_shared_entity():
    allocator = TokenAllocator("synthetic")
    names = ["Mara Lindahl", "Mara", "Lindahl", "Ms Lindahl", "M. Lindahl"]
    tokens = [allocator.allocate(name, "P") for name in names]
    assert len(set(tokens)) == len(names)
    assert len({allocator.entity_map.entity_id(token) for token in tokens}) == 1
    assert [allocator.mapping()[token] for token in tokens] == names


def test_bare_given_name_links_only_when_unique_and_can_become_ambiguous():
    allocator = TokenAllocator("synthetic")
    bare = allocator.allocate("Mara", "P")
    first = allocator.allocate("Mara Lindahl", "P")
    assert allocator.entity_map.entity_id(bare) == allocator.entity_map.entity_id(first)
    second = allocator.allocate("Mara Example", "P")
    entity_map = allocator.entity_map
    assert entity_map.entity_id(bare) not in {
        entity_map.entity_id(first), entity_map.entity_id(second)
    }
    assert allocator.mapping()[bare] == "Mara"


def test_initials_and_shared_surnames_do_not_guess():
    allocator = TokenAllocator("synthetic")
    first = allocator.allocate("Mara Lindahl", "P")
    second = allocator.allocate("Ravi Lindahl", "P")
    surname = allocator.allocate("Ms Lindahl", "P")
    initial = allocator.allocate("M. Lindahl", "P")
    assert allocator.entity_map.entity_id(surname) not in {
        allocator.entity_map.entity_id(first), allocator.entity_map.entity_id(second)
    }
    assert allocator.entity_map.entity_id(initial) == allocator.entity_map.entity_id(first)


def test_collision_checked_and_conversation_isolated():
    identifiers = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    vault = InMemoryVault()
    allocator = TokenAllocator("one", vault, id_factory=lambda: next(identifiers))
    first = allocator.allocate("Mara Lindahl", "P")
    second = allocator.allocate("Ravi Example", "P")
    assert first != second
    assert vault.get("two", first) is None


def test_allocator_reloads_exact_values_from_vault():
    vault = InMemoryVault()
    first = TokenAllocator("synthetic", vault).allocate("Mara Lindahl", "P")
    assert TokenAllocator("synthetic", vault).allocate("Mara Lindahl", "P") == first


def test_conflicting_category_fails_closed():
    allocator = TokenAllocator("synthetic")
    allocator.allocate("Mara Lindahl", "P")
    with pytest.raises(AllocationError):
        allocator.allocate("Mara Lindahl", "S")


def test_collision_retry_bound():
    allocator = TokenAllocator("synthetic", id_factory=lambda: "aaaaaa")
    allocator.allocate("Mara Lindahl", "P")
    with pytest.raises(AllocationError, match="retry"):
        allocator.allocate("Ravi Example", "P")


def test_random_round_trip_property_with_literals_and_substitution():
    rng = random.Random(20261022)
    alphabet = list("abc XYZ012:\n\t\\⟦⟧é🙂")
    for _ in range(400):
        prefix = "".join(rng.choice(alphabet) for _ in range(rng.randrange(80)))
        suffix = "".join(rng.choice(alphabet) for _ in range(rng.randrange(80)))
        text = prefix + "Mara Lindahl" + suffix
        allocator = TokenAllocator("synthetic")
        result = substitute(
            text, [(len(prefix), len(prefix) + len("Mara Lindahl"), "PERSON")],
            allocator,
        )
        restored = restore_text(result.tokenised_text, allocator.mapping())
        assert restored.text == text
        assert restored.report.unknown_count == 0
        assert unescape_literals(escape_literals(text)) == text


def test_literal_strict_bare_markdown_and_json_tokens_are_not_resolved():
    mapping = {"⟦P:abcdef⟧": "Mara Lindahl"}
    literals = [
        "⟦P:abcdef⟧", "P:abcdef", "\\⟦P:abcdef\\⟧",
        r"\u27e6P:abcdef\u27e7", "::", "\\\\", "⟦⟦⟧⟧",
    ]
    for original in literals:
        result = restore_text(escape_literals(original), mapping)
        assert result.text == original
        assert result.report.restored_count == 0


def test_restored_original_is_not_unescaped_or_recursively_restored():
    original = "⟦⟦P:bbbbbb⟧⟧ :: \\\\"
    mapping = {"⟦S:aaaaaa⟧": original, "⟦P:bbbbbb⟧": "Mara Lindahl"}
    assert restore_text("⟦S:aaaaaa⟧", mapping).text == original
