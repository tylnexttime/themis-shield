"""The trip-wire's two independent modes: ``before_human`` (the correction loop) and ``after_human`` (one check of the
approved package that never replaces anything). Fake cloud model only; synthetic data only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from themis_shield.catalogues.cli import tripwire_main
from themis_shield.catalogues.gate import configure, settings
from themis_shield.catalogues.tripwire import INSTRUCTION, Tripwire

from catalogue_fakes import MISSED, known_miss, tripwire_harness
from router_fakes import NOW, request
from test_tripwire_loop import PHRASES, assert_round_trip, conversation, scripted, text_of, two_documents


CLEAR = '{"residual": [], "stop": false}'


def after_only(h, **extra):
    h.router.tripwire = Tripwire(512, before_human=False, after_human=True, **extra)
    return h


def task_requests(h):
    return [body for body in h.fake.requests if body["messages"][0].get("content") != INSTRUCTION]


# -- settings ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("section, before, after", [
    ({}, False, False),
    ({"enabled": False}, False, False),
    ({"before_human": False, "after_human": False}, False, False),
    ({"enabled": True, "before_human": False, "after_human": False}, False, False),  # the toggles win
    ({"enabled": True}, True, False),  # the deprecated alias: the correction loop only
    ({"before_human": True}, True, False),
    ({"after_human": True}, False, True),
    ({"before_human": True, "after_human": True}, True, True),
    ({"enabled": False, "after_human": True}, False, True),
])
def test_two_independent_toggles_and_the_deprecated_alias(section, before, after):
    values = settings({"tripwire": section})["tripwire"]
    assert (values["before_human"], values["after_human"], values["enabled"]) == (before, after, before or after)


@pytest.mark.parametrize("section", [{"before_human": "yes"}, {"after_human": 1}])
def test_toggles_must_be_booleans(section):
    with pytest.raises(ValueError):
        settings({"tripwire": section})


def test_both_toggles_off_attach_no_trip_wire_and_create_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    router = SimpleNamespace(clock=lambda: NOW)
    configure(router, {"tripwire": {"enabled": True, "before_human": False, "after_human": False}})
    assert router.tripwire is None and list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError):
        Tripwire(before_human=False, after_human=False)


def test_configure_attaches_the_chosen_modes(tmp_path):
    router = SimpleNamespace(clock=lambda: NOW)
    configure(router, {"known_miss": {"path": str(tmp_path / "k.db")}, "tripwire": {"after_human": True}})
    assert router.tripwire.after_human is True and router.tripwire.before_human is False
    assert router.tripwire.proposals is not None and router.known_miss is None


# -- both off ---------------------------------------------------------------------------------------------------


def test_both_off_sends_nothing_to_the_trip_wire(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("Ward 7B", "quasi id")]))
    configure(h.router, {"tripwire": {"before_human": False, "after_human": False}})
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED" and "trip-wire" not in text_of(held)
        h.register.approve(held.job_id)
        assert h.router.handle(body, source_document_count=2).route == "cloud-tokenised"
        assert h.fake.tripwire_requests == [] and h.register.tripwire_stats()["runs"] == 0
    finally:
        h.cloud.close()


# -- before_human alone -----------------------------------------------------------------------------------------


def test_before_human_alone_corrects_and_never_checks_the_approved_package_again(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("Ward 7B", "quasi id")]))
    h.router.tripwire = Tripwire(512, before_human=True, after_human=False)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED" and len(h.fake.tripwire_requests) == 2
        h.register.approve(held.job_id)
        assert h.router.handle(body, source_document_count=2).route == "cloud-tokenised"
        assert len(h.fake.tripwire_requests) == 2
        assert {row["stage"] for row in h.register.tripwire_records(held.job_id)} == {"before_human"}
    finally:
        h.cloud.close()


# -- after_human alone ------------------------------------------------------------------------------------------


def test_after_human_alone_checks_only_the_approved_package_and_a_clean_pass_sends_the_task(tmp_path):
    h = after_only(tripwire_harness(tmp_path, CLEAR))
    try:
        body = request()
        held = h.router.handle(body)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED" and h.fake.requests == []  # nothing before the humans
        h.register.approve(held.job_id)
        sent = h.router.handle(body)
        assert sent.route == "cloud-tokenised"
        trip, task = h.fake.requests
        assert trip["messages"][0]["content"] == INSTRUCTION
        assert json.loads(trip["messages"][1]["content"])["messages"] == task["messages"]
        (record,) = h.register.tripwire_records(held.job_id)
        assert record["stage"] == "after_human" and record["outcome"] == "clear"
        assert h.register.tripwire_loops(held.job_id) == ()
    finally:
        h.cloud.close()


def test_after_human_never_replaces_and_sends_the_job_back_with_every_finding(tmp_path, capsys):
    reply = scripted([
        ("Ward 7B", "quasi id"), ("Birdie Okonkwo", "person"), ("night-desk lead", None), (MISSED, "internal id"),
    ])
    h = after_only(tripwire_harness(tmp_path, reply), proposals=known_miss(tmp_path))
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED" and h.fake.requests == []
        before = h.register.get_details(held.job_id)
        vault_before = h.router.vault.mapping(conversation(h, body))
        h.register.approve(held.job_id)
        stopped = h.router.handle(body, source_document_count=2)
        assert stopped.route == "held" and stopped.reason == "TRIPWIRE_FLAGGED"
        assert h.register.get_job(held.job_id).status.value == "Held-Offline"
        assert task_requests(h) == [] and len(h.fake.tripwire_requests) == 1  # the task was not sent
        # Never auto-replaced: the vault, the package and its hash are exactly what the Operator approved.
        assert h.router.vault.mapping(conversation(h, body)) == vault_before
        after = h.register.get_details(held.job_id)
        for name in ("manifest_hash", "pii_counts_json", "confirmation_required"):
            assert after[name] == before[name]
        # Every finding is listed (all five occurrences, four categories), value-free, each with a proposal.
        message = text_of(stopped)
        assert "found 5 possible residual item(s)" in message and message.count("\n- ") == 5
        for category in ("QUASI_ID", "PERSON", "OTHER", "INTERNAL_ID"):
            assert f"({category})" in message
        assert all(phrase not in message for phrase in PHRASES)
        assert "--reapprove" in message and "--refuse" in message
        (record,) = h.register.tripwire_records(held.job_id)
        assert record["stage"] == "after_human" and record["outcome"] == "flagged" and len(record["flags"]) == 5
        assert all(phrase not in record["flags_json"] for phrase in PHRASES)
        proposals = {flag["proposal"] for flag in record["flags"]}
        assert None not in proposals and len(proposals) == 4
        assert {entry["status"] for entry in h.router.tripwire.proposals.entries()} == {"proposed"}
        events = [row["event"] for row in h.register.audit_rows(held.job_id)]
        assert "TRIPWIRE_FLAGGED" in events
        # Held until an Operator acts; re-sending does not ask the trip-wire again.
        assert h.router.handle(body, source_document_count=2).reason == "TRIPWIRE_REVIEW_REQUIRED"
        assert len(h.fake.tripwire_requests) == 1
        # Re-approval sends exactly the approved package, unchanged and without a second check.
        assert tripwire_main(
            ["review", held.job_id, "--reapprove", "--identity", "synthetic-operator"], register=h.register,
        ) == 0
        assert h.register.get_job(held.job_id).status.value == "Approved"
        sent = h.router.handle(body, source_document_count=2)
        assert sent.route == "cloud-tokenised" and len(h.fake.tripwire_requests) == 1
        (task,) = task_requests(h)
        outbound = json.dumps(task, ensure_ascii=False)
        assert "Ward 7B" in outbound and "Birdie Okonkwo" in outbound  # the person approved them; never replaced
        assert h.register.get_details(held.job_id)["manifest_hash"] == before["manifest_hash"]
        assert_round_trip(h, body, task)
        capsys.readouterr()
    finally:
        h.cloud.close()


def test_after_human_refusal_closes_the_job(tmp_path):
    h = after_only(tripwire_harness(tmp_path, scripted([("synthetic", "quasi id")])))
    try:
        body = request("Mara Lindahl is the only synthetic night-desk lead.")
        held = h.router.handle(body)
        h.register.approve(held.job_id)
        assert h.router.handle(body).reason == "TRIPWIRE_FLAGGED"
        assert tripwire_main(
            ["review", held.job_id, "--refuse", "--identity", "synthetic-operator"], register=h.register,
        ) == 0
        assert h.register.get_job(held.job_id).status.value == "Refused"
        assert h.router.handle(body).reason == "APPROVAL_NOT_ACTIVE"
        assert task_requests(h) == [] and h.register.tripwire_stats()["flag_precision"] == 1.0
    finally:
        h.cloud.close()


@pytest.mark.parametrize("reply, reason", [
    ("Nothing personal here.", "TRIPWIRE_MALFORMED"),
    ('{"residual": [{"start": 0, "end": 5, "category": "Mara Lindahl"}], "stop": true}', "TRIPWIRE_UNAVAILABLE"),
])
def test_after_human_still_fails_closed(tmp_path, reply, reason):
    h = after_only(tripwire_harness(tmp_path, reply))
    try:
        body = request()
        held = h.router.handle(body)
        h.register.approve(held.job_id)
        stopped = h.router.handle(body)
        assert stopped.route == "blocked" and stopped.reason == reason
        assert h.register.get_job(held.job_id).status.value == "Refused" and task_requests(h) == []
    finally:
        h.cloud.close()


# -- both together ----------------------------------------------------------------------------------------------


def test_both_on_the_loop_corrects_and_the_approved_package_is_checked_again(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("Ward 7B", "quasi id")]))
    h.router.tripwire = Tripwire(512, before_human=True, after_human=True)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED" and len(h.fake.tripwire_requests) == 2
        h.register.approve(held.job_id)
        sent = h.router.handle(body, source_document_count=2)
        assert sent.route == "cloud-tokenised" and len(h.fake.tripwire_requests) == 3
        check, task = h.fake.tripwire_requests[-1], task_requests(h)[-1]
        assert json.loads(check["messages"][1]["content"])["messages"] == task["messages"]
        assert "Ward 7B" not in json.dumps(task, ensure_ascii=False)
        assert [row["stage"] for row in h.register.tripwire_records(held.job_id)] == [
            "before_human", "before_human", "after_human",
        ]
        assert_round_trip(h, body, task)
    finally:
        h.cloud.close()


def test_both_on_what_the_loop_missed_is_caught_after_approval_and_not_replaced(tmp_path):
    # The loop's two rounds see nothing new after round 1; the check of the approved package reports Birdie Okonkwo.
    reply = scripted([("Ward 7B", "quasi id")], [], [("Birdie Okonkwo", "person")])
    h = tripwire_harness(tmp_path, reply)
    h.router.tripwire = Tripwire(512, before_human=True, after_human=True)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        approved = h.register.get_details(held.job_id)["manifest_hash"]
        h.register.approve(held.job_id)
        stopped = h.router.handle(body, source_document_count=2)
        assert stopped.reason == "TRIPWIRE_FLAGGED" and task_requests(h) == []
        assert "(PERSON)" in text_of(stopped) and "Okonkwo" not in text_of(stopped)
        assert h.register.get_details(held.job_id)["manifest_hash"] == approved
        assert "Birdie Okonkwo" in h.fake.tripwire_requests[-1]["messages"][1]["content"]
        assert not any(value == "Birdie Okonkwo" for value in h.router.vault.mapping(conversation(h, body)).values())
    finally:
        h.cloud.close()
