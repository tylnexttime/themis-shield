"""Restore safety (v0.3): a damaged placeholder never restores someone else's value, and is never guessed.

Fuzzy restoration is how a related project could put the wrong person's name into an answer. Here restoration is
exact, and the allocator never issues two placeholders in one conversation that are one character apart (one hex
digit changed, or two adjacent digits swapped), whatever their class letter.
"""

from __future__ import annotations

import itertools
import random

import pytest

from themis_shield.placeholders import TokenAllocator, confusable
from themis_shield.restore import StreamingRestorer, restore_text
from themis_shield.vault import InMemoryVault


HEX = "0123456789abcdef"
MARA, RAVI = "Mara Lindahl", "Ravi Example"


def neighbours(identifier):
    """Every identifier one character away: one digit changed, or one adjacent swap."""
    for index, digit in itertools.product(range(6), HEX):
        if digit != identifier[index]:
            yield identifier[:index] + digit + identifier[index + 1:]
    for index in range(5):
        if identifier[index] != identifier[index + 1]:
            yield identifier[:index] + identifier[index + 1] + identifier[index] + identifier[index + 2:]


def test_confusable_is_one_digit_or_one_adjacent_swap():
    assert confusable("7f3a1c", "7f3a1c")
    assert confusable("7f3a1c", "7f3a1d")
    assert confusable("7f3a1c", "f73a1c")
    assert not confusable("7f3a1c", "7f3a2d")
    assert not confusable("7f3a1c", "cf3a17")  # a distant swap is two changes
    assert all(confusable("7f3a1c", other) for other in neighbours("7f3a1c"))


def test_allocator_skips_identifiers_one_character_from_an_issued_one():
    offered = iter(["7f3a1c", "7f3a1d", "f73a1c", "7f3a1c", "9b2e40"])
    vault = InMemoryVault()
    allocator = TokenAllocator("synthetic", vault, id_factory=lambda: next(offered))
    first = allocator.allocate(MARA, "P")
    second = allocator.allocate(RAVI, "C")  # different class: still never a neighbour
    assert first == "⟦P:7f3a1c⟧" and second == "⟦C:9b2e40⟧"


def test_no_two_issued_identifiers_are_confusable():
    allocator = TokenAllocator("synthetic", InMemoryVault())
    tokens = [allocator.allocate(f"Synthetic Person {index}", "PQCI"[index % 4]) for index in range(400)]
    identifiers = [token[3:9] for token in tokens]
    for first, second in itertools.combinations(identifiers, 2):
        assert not confusable(first, second)


def mapping_with_neighbour_free_tokens():
    return {"⟦P:7f3a1c⟧": MARA, "⟦P:9b2e40⟧": RAVI}


@pytest.mark.parametrize("identifier", sorted(set(neighbours("7f3a1c"))))
def test_one_character_away_never_restores_the_wrong_value(identifier):
    mapping = mapping_with_neighbour_free_tokens()
    for damaged in (f"⟦P:{identifier}⟧", f"P:{identifier}", f"⟦p:{identifier.upper()}⟧"):
        text = f"Answer for {damaged}."
        result = restore_text(text, mapping)
        assert RAVI not in result.text and MARA not in result.text
        assert result.text == text  # left visible, exactly as written
        assert result.report.unknown_count == 1 and result.report.restored_count == 0


@pytest.mark.parametrize("letter", "CAIDSQ")
def test_a_changed_class_letter_never_restores(letter):
    mapping = mapping_with_neighbour_free_tokens()
    text = f"Answer for ⟦{letter}:7f3a1c⟧."
    result = restore_text(text, mapping)
    assert result.text == text and result.report.unknown_count == 1


@pytest.mark.parametrize("mangled", [
    "⟦P:7f3a1⟧", "⟦P:7f3a1c0⟧", "⟦P7f3a1c⟧", "⟦X:7f3a1c⟧", "⟦P:7f3a1g⟧", "⟦P:7f 3a1c⟧", "⟦P:7f3a1c",
    "P:7f3a1", "P:7f3a1c0", "⟦P:⟧", "⟦:7f3a1c⟧",
])
def test_mangled_placeholders_stay_visible_and_are_not_guessed(mangled):
    mapping = mapping_with_neighbour_free_tokens()
    text = f"Answer for {mangled}"
    result = restore_text(text, mapping)
    assert MARA not in result.text and RAVI not in result.text
    assert result.report.restored_count == 0


def test_streaming_path_matches_complete_text_for_neighbours():
    mapping = mapping_with_neighbour_free_tokens()
    rng = random.Random(20261027)
    for identifier in neighbours("7f3a1c"):
        text = f"Before ⟦P:{identifier}⟧ and ⟦P:7f3a1c⟧ after."
        restorer = StreamingRestorer(mapping)
        position, output = 0, []
        while position < len(text):
            step = rng.randint(1, 5)
            output.append(restorer.feed(text[position:position + step]))
            position += step
        output.append(restorer.close())
        streamed = "".join(output)
        assert streamed == restore_text(text, mapping).text
        assert streamed == f"Before ⟦P:{identifier}⟧ and {MARA} after."


@pytest.mark.parametrize("variant", ["[P:7f3a1c]", "(P:7f3a1c)", "⟦⟦P:7f3a1c⟧"])
def test_damaged_delimiters_around_an_intact_identifier_restore_only_that_value(variant):
    # Documented tolerance: the six-digit identifier is intact, so this is the same placeholder, not a guess.
    result = restore_text(f"Answer for {variant}", mapping_with_neighbour_free_tokens())
    assert MARA in result.text and RAVI not in result.text and result.report.restored_count == 1
