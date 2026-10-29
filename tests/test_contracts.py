"""Substitution, router and offset-map contracts. (The per-request evidence ledger is on the roadmap, DEV-DEBT DD-03.)"""

import json
import random

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


def test_offset_map_is_conservative():
    """Every view span maps to a source span covering each contributing character and every removed invisible one.

    Random text from letters, digits, combining marks, ligatures, full-width forms and default-ignorables; 300
    documents, every span of up to 12 view characters.
    """
    from themis_shield.normalise import detection_view, is_default_ignorable

    alphabet = list("Mara Lindahl 0412") + ["́", "̈", "ﬁ", "Ｍ", "​", "‍", "­",
                                            "️", "󠁁", "⁠", "é", "Å"]
    rng = random.Random(20261030)
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))
        view = detection_view(text)
        for start in range(len(view.text)):
            for end in range(start + 1, min(len(view.text), start + 12) + 1):
                a, b = view.to_source(start, end)
                assert 0 <= a < b <= len(text)
                # every source character that produced a view character in the span is covered
                for index in range(start, end):
                    low, high = view.offsets[index]
                    assert a <= low and high <= b
                # nothing visible outside the span's own sources is swallowed: the envelope starts and ends on them
                assert a == view.offsets[start][0] and b == view.offsets[end - 1][1]
                # removed invisible characters strictly inside the envelope are covered by construction
                assert all(is_default_ignorable(ch) or any(lo <= i < hi for lo, hi in view.offsets[start:end])
                           for i, ch in enumerate(text[a:b], a))


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
