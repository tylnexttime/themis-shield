"""v0.4 placeholder legend: optional, off by default, generic for sensitive statements. Synthetic only."""

from __future__ import annotations

import json

import pytest

from themis_shield.integration import configure_legend_settings
from themis_shield.legend import HEADER, configure_legend, describe, legend_settings, legend_text
from themis_shield.placeholders import TokenAllocator
from themis_shield.substitute import substitute
from themis_shield.types import Label

from router_fakes import approved_response, harness, request

pytestmark = pytest.mark.usefixtures("au_pspf_markings")


@pytest.fixture(autouse=True)
def default_legend():
    configure_legend()
    yield
    configure_legend()


@pytest.mark.parametrize("token, labels, expected", [
    ("⟦P:0c44a7⟧", frozenset({Label.PERSON}), "a person"),
    ("⟦I:3fa9c1⟧", frozenset({Label.BANK_ACCOUNT}), "a bank account number"),
    ("⟦D:91b2e0⟧", frozenset({Label.DATE_OF_BIRTH}), "a date of birth"),
    ("⟦C:5d1e22⟧", frozenset({Label.EMAIL}), "an email address"),
    ("⟦I:3fa9c1⟧", None, "an identifier"),                                       # first sent in an earlier turn
    ("⟦I:3fa9c1⟧", frozenset({Label.TFN, Label.MEDICARE}), "an identifier"),     # two of the class: never guess
    ("⟦I:3fa9c1⟧", frozenset({Label.TFN, Label.PERSON}), "a tax file number"),   # only one label of the I class
    ("⟦S:7a1b2c⟧", frozenset({Label.HEALTH}), "a sensitive statement about a person"),
    ("⟦S:7a1b2c⟧", None, "a sensitive statement about a person"),
])
def test_describe_by_default(token, labels, expected):
    assert describe(token, labels) == expected


@pytest.mark.parametrize("labels, expected", [
    (frozenset({Label.HEALTH}), "a statement about a person's health"),
    (frozenset({Label.CRIMINAL, Label.PERSON}), "a statement about a person's criminal record"),
    (frozenset({Label.UNION, Label.HEALTH, Label.DISABILITY}),
     "a statement about a person's disability, health and trade-union membership"),
    (None, "a sensitive statement about a person"),  # unknown category stays generic
])
def test_sensitive_categories_only_when_asked(labels, expected):
    assert describe("⟦S:7a1b2c⟧", labels, sensitive_categories=True) == expected


def test_legend_text_lists_each_placeholder_once_in_order():
    text = legend_text({"⟦P:bbbbbb⟧": frozenset({Label.PERSON}), "⟦D:aaaaaa⟧": frozenset({Label.OTHER_DATE})})
    assert text == HEADER + "\n- ⟦D:aaaaaa⟧: a date\n- ⟦P:bbbbbb⟧: a person"
    assert legend_text({}) == ""


def test_settings_default_off_and_validated():
    assert legend_settings() == {"enabled": False, "sensitive_categories": False}
    assert configure_legend_settings({"legend": {"enabled": True}}) == {"enabled": True, "sensitive_categories": False}
    assert configure_legend_settings({}) == {"enabled": False, "sensitive_categories": False}
    with pytest.raises(ValueError):
        configure_legend_settings({"legend": {"enabled": "yes"}})
    with pytest.raises(ValueError):
        configure_legend_settings({"legend": {"categories": True}})


def test_replacements_carry_every_merged_label():
    text = "Ring 0491 570 156 today."
    start = text.index("0491")
    result = substitute(text, [(start, start + 12, "PHONE"), (start, start + 12, "STUDENT_ID")], TokenAllocator("c"))
    (item,) = result.replacements
    assert item.labels == frozenset({Label.PHONE, Label.STUDENT_ID}) and item.label == Label.STUDENT_ID


def test_legend_off_sends_the_request_unchanged(tmp_path):
    h = harness(tmp_path)
    try:
        approved_response(h, request())
        (sent,) = h.fake.requests
        assert [message["role"] for message in sent["messages"]] == ["user"]
    finally:
        h.cloud.close()


def test_legend_on_is_the_first_message_and_holds_no_original(tmp_path):
    configure_legend(True)
    h = harness(tmp_path)
    try:
        response = approved_response(h, request())
        assert "Mara Lindahl" in response.body["choices"][0]["message"]["content"]  # restoration unchanged
        (sent,) = h.fake.requests
        legend, user = sent["messages"]
        assert legend["role"] == "system" and legend["content"].startswith(HEADER)
        (token,) = {line.split(": ")[0][2:] for line in legend["content"].splitlines()[1:]}
        assert legend["content"].endswith(f"- {token}: a person")
        assert token in user["content"]
        assert "Mara" not in json.dumps(sent)
    finally:
        h.cloud.close()


def test_a_second_turn_reconciles_and_keeps_the_legend_out_of_history(tmp_path):
    # History pairs each client message with what was sent for it; the gate's own legend must not shift that.
    configure_legend(True)
    h = harness(tmp_path)
    try:
        first_request = request()
        first = approved_response(h, first_request)
        conversation = h.router._conversation_id(first_request, {})
        turns = h.router.history.outbound(conversation)
        assert [turn.role for turn in turns] == ["user", "assistant"]
        assert all(HEADER not in turn.payload.body.decode() for turn in turns)
        second_request = request()
        second_request["messages"] += [
            first.body["choices"][0]["message"], {"role": "user", "content": "Continue the synthetic answer."},
        ]
        second = approved_response(h, second_request)
        assert second.route == "cloud-tokenised"
        sent = h.fake.requests[-1]["messages"]
        assert [message["role"] for message in sent] == ["system", "user", "assistant", "user"]
        assert sent[1] == h.fake.requests[0]["messages"][1]  # the first turn went back exactly as sent
        token = sent[1]["content"].split("⟦")[1].split("⟧")[0]
        assert f"- ⟦{token}⟧: a person" in sent[0]["content"]  # reused: described by its class
        assert "Mara Lindahl" not in json.dumps(h.fake.requests)
    finally:
        h.cloud.close()
