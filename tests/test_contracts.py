"""Substitution and router contracts; offset-map and ledger contracts remain deferred."""

import json

import pytest

from themis_shield.config import UseCasePolicy
from themis_shield.detectors.markings import detect_markings
from themis_shield.placeholders import TokenAllocator
from themis_shield.restore import StreamingRestorer, restore_text, restore_tool_arguments
from themis_shield.routing import Route, route_request
from themis_shield.substitute import substitute
from themis_shield.vault import InMemoryVault

from router_fakes import approved_response, harness, request

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


@pytest.mark.xfail(reason="M2 offset map deferred", strict=True, raises=NotImplementedError)
def test_offset_map_is_conservative():
    raise NotImplementedError("M2: conservative Unicode source mapping")


@pytest.mark.xfail(reason="M2 ledger deferred", strict=True, raises=NotImplementedError)
def test_ledger_is_add_only():
    raise NotImplementedError("M2: immutable mandatory evidence")


def test_same_value_has_same_token():
    vault = InMemoryVault()
    allocator = TokenAllocator("synthetic-one", vault)
    full = allocator.allocate("Mara Lindahl", "P")
    assert allocator.allocate("Mara Lindahl", "P") == full
    given = allocator.allocate("Mara", "P")
    assert given != full
    assert allocator.entity_map.entity_id(given) == allocator.entity_map.entity_id(full)
    assert vault.get("synthetic-two", full) is None


def test_literal_escaping_round_trip():
    text = "Literal ⟦P:7f3a1c⟧ and P:7f3a1c \\ ⟦ ⟧; Mara Lindahl"
    allocator = TokenAllocator("synthetic")
    start = text.index("Mara Lindahl")
    result = substitute(text, [(start, start + 12, "PERSON")], allocator)
    restored = restore_text(result.tokenised_text, allocator.mapping())
    assert restored.text == text
    assert restored.report.restored_count == 1


def test_stream_holdback_never_emits_partial_token():
    token = "⟦P:7f3a1c⟧"
    raw = token.encode("utf-8")
    for offset in range(1, len(raw)):
        stream = StreamingRestorer({token: "Mara Lindahl"})
        assert stream.feed(raw[:offset]) == ""
        assert stream.feed(raw[offset:]) + stream.close() == "Mara Lindahl"


def test_unknown_token_is_never_guessed():
    token = "⟦P:ffffff⟧"
    result = restore_text(token, {"⟦P:7f3a1c⟧": "Mara Lindahl"})
    assert result.text == token
    assert result.report.unknown_tokens == (token,)


def test_tool_use_arguments_are_restored_before_execution():
    token = "⟦P:7f3a1c⟧"
    original = 'Mara Lindahl "synthetic"\nsecond line'
    arguments = restore_tool_arguments(json.dumps({"name": token}), {token: original})
    assert json.loads(arguments)["name"] == original


def test_restored_text_is_never_sent_back_to_cloud(tmp_path):
    h = harness(tmp_path)
    try:
        first_request = request()
        first = approved_response(h, first_request)
        restored_message = first.body["choices"][0]["message"]
        second_request = request()
        second_request["messages"] += [
            restored_message, {"role": "user", "content": "Continue the synthetic answer."},
        ]
        second = approved_response(h, second_request)
        assert second.route == "cloud-tokenised"
        assert len(h.fake.requests) == 2
        assert all("Mara Lindahl" not in json.dumps(payload) for payload in h.fake.requests)
        assert "⟦P:" in json.dumps(h.fake.requests[-1], ensure_ascii=False)
    finally:
        h.cloud.close()


@pytest.mark.parametrize("stage", ["register", "tracer", "finders", "vault"])
def test_any_required_stage_error_fails_closed(tmp_path, stage):
    h = harness(tmp_path)
    try:
        if stage == "register":
            h.register.available = False
        elif stage == "tracer":
            h.tracer.fail = True
        elif stage == "finders":
            h.finders.fail = True
        else:
            h.router.vault.close()
        response = h.router.handle(request())
        assert response.status == 503
        assert response.route == "blocked"
        assert h.fake.requests == []
    finally:
        h.cloud.close()


@pytest.mark.parametrize("text", ["PROTECTED", "SECRET", "TOP SECRET", "PROTECTED//AUSTEO"])
def test_security_markings_route_without_pii(text):
    decision = route_request(
        detect_markings(text + "\nSynthetic arithmetic: 2 + 2"),
        UseCasePolicy("synthetic", ()),
    )
    assert decision.route == Route.LOCAL_ONLY
    assert decision.audit_fields.no_override
    assert not decision.audit_fields.cloud_offered
