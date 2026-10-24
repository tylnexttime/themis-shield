"""Synthetic canonical-history reconstruction and mismatch tests."""

import pytest

from themis_shield.history import CanonicalHistory, ClientTurn, HistoryError
from themis_shield.placeholders import TokenAllocator
from themis_shield.restore import restore_text
from themis_shield.substitute import substitute, verify_no_originals


def test_restored_client_echo_rebuilt_from_canonical_store():
    allocator = TokenAllocator("synthetic")
    clean = substitute(
        "Mara Lindahl asks.", [(0, 12, "PERSON")], allocator
    ).tokenised_text
    local = restore_text(clean, allocator.mapping()).text
    history = CanonicalHistory()
    history.record("synthetic", "one", "user", clean, local_text=local)
    resolution = history.reconcile(
        "synthetic", (ClientTurn("one", "user", local),)
    )
    assert not resolution.needs_detection
    assert resolution.outbound[0].payload.body.decode("utf-8") == clean
    assert verify_no_originals(
        resolution.outbound[0].payload.body.decode("utf-8"), ["Mara Lindahl"]
    )
    assert "Mara Lindahl" not in repr(history.__dict__)


def test_changed_client_turn_is_new_and_no_outbound_is_authorised():
    history = CanonicalHistory()
    history.record(
        "synthetic", "one", "assistant", "⟦P:abcdef⟧",
        local_text="Mara Lindahl",
    )
    client = ClientTurn("one", "assistant", "Mara Lindahl changed")
    result = history.reconcile("synthetic", (client,))
    assert result.needs_detection
    assert result.new_turns == (client,)
    assert result.outbound == ()


def test_new_tail_requires_detection_but_matching_prefix_is_redacted():
    history = CanonicalHistory()
    history.record("synthetic", "one", "user", "Synthetic question")
    result = history.reconcile("synthetic", (
        ClientTurn("one", "user", "Synthetic question"),
        ClientTurn("two", "user", "Ravi Example asks a new question"),
    ))
    assert result.needs_detection
    assert len(result.matched_turns) == 1
    assert result.new_turns[0].turn_id == "two"
    assert result.outbound == ()


@pytest.mark.parametrize(
    "client",
    [
        ClientTurn("one", "assistant", "Synthetic question"),
        ClientTurn("wrong", "user", "Synthetic question"),
        ClientTurn("one", "user", "synthetic question"),
    ],
)
def test_role_id_and_exact_hash_must_match(client):
    history = CanonicalHistory()
    history.record("synthetic", "one", "user", "Synthetic question")
    assert history.reconcile("synthetic", (client,)).needs_detection


def test_reordering_and_duplicate_ids_are_not_canonical():
    history = CanonicalHistory()
    history.record("synthetic", "one", "user", "First")
    history.record("synthetic", "two", "assistant", "Second")
    assert history.reconcile("synthetic", (
        ClientTurn("two", "assistant", "Second"),
        ClientTurn("one", "user", "First"),
    )).needs_detection
    assert history.reconcile("synthetic", (
        ClientTurn("one", "user", "First"),
        ClientTurn("one", "user", "First"),
    )).needs_detection


def test_immutable_turn_scope_and_wipe():
    history = CanonicalHistory()
    first = history.record("one", "turn", "user", "Synthetic question")
    history.append_redacted("one", first)
    assert len(history.outbound("one")) == 1
    with pytest.raises(HistoryError, match="immutable"):
        history.record("one", "turn", "user", "Changed")
    assert history.outbound("two") == ()
    assert history.reconcile(
        "two", (ClientTurn("turn", "user", "Synthetic question"),)
    ).needs_detection
    history.wipe("one")
    assert history.outbound("one") == ()
    history.record("two", "turn", "user", "Second")
    history.wipe_all()
    assert history.outbound("two") == ()
