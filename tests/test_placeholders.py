"""Synthetic placeholder, linking and deterministic random property tests."""

import random

import pytest

from themis_shield.placeholders import (
    AllocationError, TokenAllocator, TokenClass, escape_literals,
    parse_strict_token, token_class_for, unescape_literals,
)
from themis_shield.restore import StreamingRestorer, restore_text
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


# Text shaped like a placeholder or like one of its escapes, as a document could contain it. The allocator below
# issues the identifier 7f3a1c, so the forms that spell it are forgeries of a token that really exists.
DOCUMENT_FRAGMENTS = [
    "P:7f3a1c", "p:7F3A1C", "⟦P:7f3a1c⟧", "⟦ P : 7f3a1c ⟧", "\\⟦P:7f3a1c\\⟧", "\\u27e6P:7f3a1c\\u27e7",
    "[P:7f3a1c]", "P::7f3a1c", "P:::7f3a1c", "A:", "a:b", ":::", "::", "\\\\", "\\\\\\", "\\", "\\\\\\\\\\",
    "\\\\u27e6P:7f3a1c\\\\u27e7", "\\U27E6P:7F3A1C\\U27E7",
]
# Text a cloud model writes itself: scope operators, IPv6 addresses, a time, doubled backslashes, and delimiter
# characters that are not a placeholder (a balanced pair, a closing bracket).
MODEL_FRAGMENTS = [
    "std::vector", "::1", "fe80::1", "A::B", "Foo::bar()", "self::CONST", "10:30",
    "\\\\", "C:\\\\Users\\\\x", "\\\\server\\share", "\\\\\\\\server\\\\share", "⟦note⟧", "⟧",
]
NAME = "Mara Lindahl"


def _literal_text(rng, alphabet):
    # Never empty: two names with nothing between them are one span, so one token.
    return "".join(
        rng.choice(DOCUMENT_FRAGMENTS) if rng.random() < 0.15 else rng.choice(alphabet)
        for _ in range(rng.randrange(1, 40))
    )


def test_random_round_trip_property_with_literals_and_substitution():
    rng = random.Random(20261022)
    alphabet = list("abc XYZ012:\n\t\\⟦⟧é🙂PdQ7f")
    for _ in range(400):
        pieces = [_literal_text(rng, alphabet) for _ in range(3)]
        text = pieces[0] + NAME + pieces[1] + NAME + pieces[2]
        spans, cursor = [], 0
        for piece in pieces[:2]:
            cursor += len(piece)
            spans.append((cursor, cursor + len(NAME), "PERSON"))
            cursor += len(NAME)
        allocator = TokenAllocator("synthetic", id_factory=lambda: "7f3a1c")
        result = substitute(text, spans, allocator)
        mapping = allocator.mapping()
        token = next(iter(mapping))
        restored = restore_text(result.tokenised_text, mapping)
        assert restored.text == text
        assert restored.report.unknown_count == 0
        assert restored.report.restored_count == 2  # the two names, and no document text shaped like a token
        assert unescape_literals(escape_literals(text)) == text

        # The cloud model answers: a line of its own, with its own text around the placeholders (two in a row, and
        # next to each other), then the document's text repeated in place.
        line = [rng.choice(MODEL_FRAGMENTS) for _ in range(4)]
        reply = line[0] + token + line[1] + " " + line[2] + token + token + line[3] + "\n" + result.tokenised_text
        expected = line[0] + NAME + line[1] + " " + line[2] + NAME + NAME + line[3] + "\n" + text
        answer = restore_text(reply, mapping)
        assert answer.text == expected
        assert answer.report.unknown_count == 0 and answer.report.restored_count == 5  # three in the line, two echoed
        streamed = StreamingRestorer(mapping)
        output, position = [], 0
        while position < len(reply):
            step = rng.randint(1, 9)
            output.append(streamed.feed(reply[position:position + step]))
            position += step
        output.append(streamed.close())
        assert "".join(output) == expected
        assert streamed.report.restored_count == 5 and streamed.report.unknown_count == 0


# ---------------------------------------------------------------------------------------------------------------------
# DD-18: escape only what restoration could read as a placeholder, so text the cloud model writes itself is not halved.
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "10:30", "std::vector<int>", "::1", "fe80::1", "2001:db8::8a2e:370:7334", "A::B and self::CONST and Foo::bar()",
    "C:\\Users\\x\\notes", "\\\\server\\share", "Q: what?\nA: this", "https://example.com/a?b=x", "a\\b\\n",
    "P: 7f3a1c", "xP:7f3a1c", "P7f3a1c", "A::7", "\\\\", "\\\\\\\\", "a\\\\\\b", "re.compile(\"\\\\\\\\\")",
    "\\\\u27e6",
])
def test_ordinary_colons_and_backslashes_go_out_unchanged(text):
    assert escape_literals(text) == text


@pytest.mark.parametrize("text, escaped", [
    # A colon right after a standalone class letter and before a hex digit (or the end): the bare token form.
    ("P:7f3a1c", "P:::7f3a1c"),
    ("Ask p:7F3A1C now", "Ask p:::7F3A1C now"),
    ("A:3 people", "A:::3 people"),
    ("A:", "A:::"),
    ("a:d:7", "a:::d:::7"),
    # The bracket forms: the delimiters are doubled and the colon of the form is escaped.
    ("⟦P:7f3a1c⟧", "⟦⟦P:::7f3a1c⟧⟧"),
    ("⟦⟧", "⟦⟦⟧⟧"),
    # An odd run of backslashes that would join a delimiter, or the placeholder that follows, into an escaped one.
    ("\\", "\\\\\\"),
    ("\\⟦", "\\\\\\⟦⟦"),
    ("\\u27e6P:7f3a1c\\u27e7", "\\\\\\u27e6P:7f3a1c\\\\\\u27e7"),
    ("\\U27E7", "\\\\\\U27E7"),
    ("x\\\\\\\\\\⟧", "x\\\\\\\\\\\\\\⟧⟧"),
    # An even run is never an escape, whatever follows it.
    ("\\\\⟦", "\\\\⟦⟦"),
    ("\\\\\\\\u27e6", "\\\\\\\\u27e6"),
    ("x\\\\\\\\", "x\\\\\\\\"),
    # A run that an escape could be mistaken for is escaped too, so that every escape decodes unambiguously.
    ("A:::", "A:::::"),
    ("\\\\\\", "\\\\\\\\\\"),
])
def test_what_could_complete_a_placeholder_is_escaped(text, escaped):
    assert escape_literals(text) == escaped
    assert unescape_literals(escaped) == text


def test_escaped_text_holds_nothing_restoration_reads_as_a_placeholder():
    # Escape any text over an alphabet built from the placeholder grammar: restoration returns it byte for byte, finds
    # no placeholder-shaped lexeme in it (an unknown one would be reported), and restores nothing, even though every
    # identifier it can spell exists in the mapping.
    rng = random.Random(20261111)
    alphabet = list("PCAIDSQpcaidsq7fF0a:::\\\\\\⟦⟧u2e6 \n-_é")
    mapping = {
        f"⟦{letter}:{identifier}⟧": f"value {letter}{identifier}"
        for letter in "PCAIDSQ" for identifier in ("7f3a1c", "abcdef", "000000")
    }
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 30)))
        if rng.random() < 0.3:
            text = text.replace("u", "\\u27e6", 1)
        escaped = escape_literals(text)
        assert unescape_literals(escaped) == text
        result = restore_text(escaped, mapping)
        assert result.text == text
        assert result.report.restored_count == 0 and result.report.unknown_count == 0
        streamed = StreamingRestorer(mapping)
        output = [streamed.feed(character) for character in escaped]
        output.append(streamed.close())
        assert "".join(output) == text
        assert streamed.report.restored_count == 0 and streamed.report.unknown_count == 0


def test_a_name_between_a_path_and_colons_goes_out_plain_and_comes_back_exact():
    text = "At 10:30 open C:\\Users\\Mara Lindahl\\notes (std::vector, ::1) and P:7f3a1c."
    start = text.index(NAME)
    allocator = TokenAllocator("synthetic", id_factory=lambda: "7f3a1c")
    result = substitute(text, [(start, start + len(NAME), "PERSON")], allocator)
    outbound = result.tokenised_text
    assert outbound == "At 10:30 open C:\\Users\\\\\\⟦P:7f3a1c⟧\\notes (std::vector, ::1) and P:::7f3a1c."
    assert "10::30" not in outbound
    restored = restore_text(outbound, allocator.mapping())
    assert restored.text == text
    assert restored.report.restored_count == 1 and restored.report.unknown_count == 0


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
