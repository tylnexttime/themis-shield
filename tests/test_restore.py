"""Synthetic token tolerance, all-offset streaming and tool-call JSON tests."""

import json

import pytest

from themis_shield.placeholders import escape_literals
from themis_shield.restore import (
    StreamingRestorationError, StreamingRestorer, ToolArgumentsError,
    ToolCallAccumulator, restore_text, restore_tool_arguments,
)


TOKEN = "⟦P:7f3a1c⟧"
MAPPING = {TOKEN: "Mara Lindahl"}


@pytest.mark.parametrize(
    "variant",
    [
        TOKEN, "P:7f3a1c", "p:7F3A1C", "⟦p:7F3A1C⟧",
        "⟦ P:7f3a1c ⟧", "\\⟦P:7f3a1c\\⟧",
        r"\u27e6P:7f3a1c\u27e7", "⟦ P : 7F3A1C ⟧",
    ],
)
def test_strict_then_restricted_tolerance(variant):
    result = restore_text("Answer " + variant + ".", MAPPING)
    assert result.text == "Answer Mara Lindahl."
    assert result.report.restored_count == 1
    assert result.report.tolerant_count == int(variant != TOKEN)
    assert not result.report.unknown_tokens


def test_unknown_and_changed_ids_never_guessed():
    text = "⟦P:ffffff⟧ and ⟦P:7f3a1d⟧ and P:123456"
    result = restore_text(text, MAPPING)
    assert result.text == text
    assert result.report.unknown_count == 3
    assert result.report.dropped_entities == (TOKEN,)


@pytest.mark.parametrize(
    "text", ["prefixP:7f3a1c", "P:7f3a1csuffix", "_P:7f3a1c", "xP:7f3a1c"]
)
def test_bare_tokens_must_stand_alone(text):
    assert restore_text(text, MAPPING).text == text


def test_counts_repetitions_omissions_and_explicit_sent_inventory():
    other = "⟦P:123456⟧"
    mapping = {**MAPPING, other: "Ravi Example"}
    result = restore_text(TOKEN + " " + TOKEN, mapping)
    assert result.report.restored_counts == {TOKEN: 2}
    assert result.report.dropped_entities == (other,)
    result = restore_text(TOKEN, mapping, sent_tokens=frozenset({TOKEN}))
    assert result.report.dropped_entities == ()


@pytest.mark.parametrize(
    "variant",
    [
        TOKEN, "P:7f3a1c", "⟦ P:7f3a1c ⟧",
        "\\⟦p:7F3A1C\\⟧", r"\u27e6P:7f3a1c\u27e7",
    ],
)
def test_stream_split_at_every_character_and_utf8_byte_offset(variant):
    for offset in range(len(variant) + 1):
        stream = StreamingRestorer(MAPPING)
        first = stream.feed(variant[:offset])
        if 0 < offset < len(variant):
            assert first == ""
        output = first + stream.feed(variant[offset:]) + stream.close()
        assert output == "Mara Lindahl"
        assert stream.report.restored_count == 1
    raw = variant.encode("utf-8")
    for offset in range(len(raw) + 1):
        stream = StreamingRestorer(MAPPING)
        first = stream.feed(raw[:offset])
        if 0 < offset < len(raw):
            assert first == ""
        assert first + stream.feed(raw[offset:]) + stream.close() == "Mara Lindahl"


def test_stream_every_single_byte_with_literals_and_adjacent_tokens():
    text = (
        escape_literals("⟦P:7f3a1c⟧ P:7f3a1c :: \\")
        + TOKEN + TOKEN + escape_literals("⟧ 🙂")
    )
    expected = restore_text(text, MAPPING).text
    stream = StreamingRestorer(MAPPING)
    output = []
    for byte in text.encode("utf-8"):
        output.append(stream.feed(bytes([byte])))
        assert stream.holdback_characters <= 64
    output.append(stream.close())
    assert "".join(output) == expected
    assert stream.report.restored_count == 2


def test_stream_boundary_lookahead_preserves_embedded_bare_run():
    stream = StreamingRestorer(MAPPING)
    assert stream.feed("P:7f3a1c") == ""
    assert stream.feed("suffix ") + stream.close() == "P:7f3a1csuffix "


@pytest.mark.parametrize("partial", ["⟦P:7f3", r"\u27e6P:7f3", "P:"])
def test_truncation_does_not_emit_partial_token(partial):
    stream = StreamingRestorer(MAPPING)
    assert stream.feed(partial) == ""
    with pytest.raises(StreamingRestorationError):
        stream.close()


def test_overlong_prefix_fails_instead_of_forcing_output():
    stream = StreamingRestorer(MAPPING)
    with pytest.raises(StreamingRestorationError, match="holdback"):
        stream.feed("⟦" + "a" * 100)
    assert stream.holdback_characters <= 64
    with pytest.raises(StreamingRestorationError):
        stream.feed("more")


def test_invalid_or_truncated_utf8_fails_closed():
    stream = StreamingRestorer(MAPPING)
    stream.feed(b"\xe2")
    with pytest.raises(StreamingRestorationError):
        stream.close()


def test_tool_json_restores_nested_values_keys_quotes_and_newlines():
    value = 'Mara Lindahl "synthetic"\nsecond line'
    mapping = {TOKEN: value}
    raw = json.dumps({TOKEN: [TOKEN, {"nested": TOKEN}], "number": 3, "flag": True})
    result = json.loads(restore_tool_arguments(raw, mapping))
    assert result == {value: [value, {"nested": value}], "number": 3, "flag": True}


@pytest.mark.parametrize(
    "raw",
    ['{', '{"a":NaN}', '{"a":1,"a":2}', '{"a":"⟦P:ffffff⟧"}'],
)
def test_bad_tool_json_and_unknowns_block(raw):
    with pytest.raises(ToolArgumentsError):
        restore_tool_arguments(raw, MAPPING)


def test_restored_key_collision_blocks():
    raw = json.dumps({TOKEN: 1, "Mara Lindahl": 2})
    with pytest.raises(ToolArgumentsError, match="collide"):
        restore_tool_arguments(raw, MAPPING)


def test_interleaved_tool_argument_deltas_restore_only_on_completion():
    accumulator = ToolCallAccumulator(MAPPING)
    first = json.dumps({"name": TOKEN})
    second = json.dumps({"nested": [TOKEN]})
    for offset in range(max(len(first), len(second))):
        if offset < len(first):
            assert accumulator.feed(0, first[offset]) is None
        if offset < len(second):
            assert accumulator.feed(1, second[offset]) is None
    assert json.loads(accumulator.complete(1)) == {"nested": ["Mara Lindahl"]}
    assert json.loads(accumulator.complete(0)) == {"name": "Mara Lindahl"}
    with pytest.raises(ToolArgumentsError):
        accumulator.feed(0, "more")


def test_incomplete_tool_call_and_buffer_limits_block():
    accumulator = ToolCallAccumulator(MAPPING)
    accumulator.feed(0, '{"name":')
    with pytest.raises(ToolArgumentsError):
        accumulator.complete(0)
    bounded = ToolCallAccumulator(MAPPING, maximum_argument_bytes=4)
    with pytest.raises(ToolArgumentsError, match="bound"):
        bounded.feed(0, "12345")


# ---------------------------------------------------------------------------------------------------------------------
# DD-18: text the cloud model writes itself comes back exactly. Restoration undoes only the escapes the gate adds.
# ---------------------------------------------------------------------------------------------------------------------
MODEL_AUTHORED = [
    # C++, Rust, PHP and IPv6: a scope operator or a compressed group is a doubled colon.
    "std::vector<int>", "std::collections::HashMap::new()", "Foo::bar", "A::B", "D::default()", "C::from(x)",
    "self::CONSTANT", "parent::__construct()", "$object::class", "App\\Models\\User::find(1)",
    "::1", "fe80::1", "2001:db8::8a2e:370:7334", "::ffff:192.0.2.1", "ratio 3::1", "10:30", "P::7f3a1c",
    # A doubled backslash: a UNC path, an escaped Windows path, an escaped newline in code; and four in a row, as in a
    # string literal that holds a UNC path or a regular expression for one backslash.
    "\\\\server\\share", "C:\\\\Users\\\\x", "printf(\"a\\\\nb\")", "\\\\", "\"\\\\\\\\server\\\\share\"",
    "re.compile(\"\\\\\\\\\")", "\\\\\\\\\\\\",
]


def _stream_at_every_offset(text, mapping=MAPPING):
    outputs = []
    for offset in range(len(text) + 1):
        stream = StreamingRestorer(mapping)
        output = stream.feed(text[:offset]) + stream.feed(text[offset:]) + stream.close()
        outputs.append((output, stream.report.restored_count, stream.report.unknown_count))
    return outputs


@pytest.mark.parametrize("text", MODEL_AUTHORED)
def test_model_written_colons_and_backslashes_survive_restoration(text):
    sentence = f"Use {text} here."
    result = restore_text(sentence, MAPPING)
    assert result.text == sentence
    assert result.report.restored_count == 0 and not result.report.unknown_tokens
    assert set(_stream_at_every_offset(sentence)) == {(sentence, 0, 0)}


@pytest.mark.parametrize("text", MODEL_AUTHORED)
@pytest.mark.parametrize(
    "shape", ["{text} {token}", "{text}{token}", "{token}{text}", "{token} {text}", "{text}\n{token}"],
)
def test_model_written_text_next_to_a_real_placeholder(text, shape):
    reply = shape.format(text=text, token=TOKEN)
    expected = shape.format(text=text, token="Mara Lindahl")
    result = restore_text(reply, MAPPING)
    assert result.text == expected
    assert result.report.restored_count == 1 and not result.report.unknown_tokens
    assert set(_stream_at_every_offset(reply)) == {(expected, 1, 0)}


def test_a_placeholder_between_the_models_colons_and_backslashes():
    for reply, expected in [
        ("std::" + TOKEN + "::vector", "std::Mara Lindahl::vector"),
        ("P::" + TOKEN, "P::Mara Lindahl"),
        (TOKEN + "::" + TOKEN, "Mara Lindahl::Mara Lindahl"),
        ("a:" + TOKEN + ":b", "a:Mara Lindahl:b"),
        ("\\\\" + TOKEN + "\\\\", "\\\\Mara Lindahl\\\\"),
        ("C:\\\\Users\\\\" + TOKEN + "\\\\notes", "C:\\\\Users\\\\Mara Lindahl\\\\notes"),
    ]:
        assert restore_text(reply, MAPPING).text == expected
        assert {out for out, *_ in _stream_at_every_offset(reply)} == {expected}


@pytest.mark.parametrize("variant", [
    "P:7f3a1c", "p:7F3A1C", "⟦P:7f3a1c⟧", "⟦ P : 7f3a1c ⟧", "\\⟦P:7f3a1c\\⟧", r"\u27e6P:7f3a1c\u27e7", "[P:7f3a1c]",
    "⟦⟦P:7f3a1c⟧⟧", "\\\\⟦P:7f3a1c⟧", "x:P:7f3a1c", "P:7f3a1c:P:7f3a1c", "P::7f3a1c", "P:::7f3a1c", "A:", "a:b", "\\",
    "\\\\\\⟦", ":::", "P:", "⟦P:7f3a1c", "7f3a1c⟧", "⟦", "⟧", "\\\\u27e6P:7f3a1c\\\\u27e7", "\\U27E6P:7F3A1C\\U27E7",
    "\\\\\\\\\\", "\\\\\\\\", "\\\\\\u27e6", "\\\\\\\\u27e7",
])
def test_document_text_shaped_like_a_placeholder_still_comes_back_exactly(variant):
    # Text of the document, echoed in place by the model: never a value, byte for byte, alone and next to real
    # placeholders (the gate escapes a segment that ends before a placeholder as if the placeholder followed).
    for shape in ("{v}", "{t} {v}", "{t}{v}", "{v}{t}", "{t}{v}{t}", "\n{v}"):
        escaped = shape.format(v=escape_literals(variant), t=TOKEN)
        expected = shape.format(v=variant, t="Mara Lindahl")
        result = restore_text(escaped, MAPPING)
        assert result.text == expected
        assert result.report.restored_count == shape.count("{t}") and not result.report.unknown_tokens
        assert {out for out, *_ in _stream_at_every_offset(escaped)} == {expected}


def test_the_triple_is_the_only_escape_restoration_removes():
    # After a standalone class letter, three colons are the gate's escape for one; two are the model's own.
    assert restore_text("P:::7f3a1c", MAPPING).text == "P:7f3a1c"
    assert restore_text("P::7f3a1c", MAPPING).text == "P::7f3a1c"
    assert restore_text("P:::::x", MAPPING).text == "P:::x"
    # Elsewhere a run of colons is plain text, however long; a letter inside a word is not a standalone letter.
    for plain in ("xP:::7f3a1c", ":::", "z::::b", "1P:::7", "Q.:::7"):
        assert restore_text(plain, MAPPING).text == plain
    # An odd run of backslashes before a delimiter, a spelled-out delimiter (u27e6, u27e7, any case) or the end of the
    # text: the last three are the gate's escape for one. Even runs are the model's own, and one is tolerated.
    for before, after in [
        ("\\\\\\" + TOKEN, "\\Mara Lindahl"), ("\\\\\\u27e6", "\\u27e6"), ("\\\\\\U27E7", "\\U27E7"), ("\\\\\\", "\\"),
        ("\\\\\\\\\\" + TOKEN, "\\\\\\Mara Lindahl"), ("\\\\\\\\\\⟧⟧", "\\\\\\⟧"),
        ("\\\\" + TOKEN, "\\\\Mara Lindahl"), ("\\\\\\\\" + TOKEN, "\\\\\\\\Mara Lindahl"),
        ("\\\\\\\\u27e6", "\\\\\\\\u27e6"), ("\\" + TOKEN, "Mara Lindahl"),
        # Anything else after three backslashes and the run is plain text.
        ("\\\\\\x", "\\\\\\x"), ("\\\\\\\\\\x", "\\\\\\\\\\x"), ("\\\\\\ ", "\\\\\\ "), ("\\\\\\u27e5", "\\\\\\u27e5"),
    ]:
        assert restore_text(before, MAPPING).text == after
        assert {out for out, *_ in _stream_at_every_offset(before)} == {after}


def test_long_runs_of_escapes_stream_without_a_holdback():
    for text in ("\\" * 199, "\\" * 200, "\\" * 201, "x" + ":" * 200, "P" + ":" * 200 + "7", "P" + ":" * 201):
        escaped = escape_literals(text)
        stream = StreamingRestorer(MAPPING)
        output = []
        for character in escaped:
            output.append(stream.feed(character))
            assert stream.holdback_characters <= 8
        output.append(stream.close())
        assert "".join(output) == text
