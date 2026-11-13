"""The cloud trip-wire's correction loop (run before approval), against a scripted fake cloud model. Synthetic data only.

The fake answers the trip-wire's fixed instruction with the character spans of configured phrases, if they are still
visible in the package it was sent, so a phrase the loop replaced is not reported again.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from themis_shield.catalogues.cli import tripwire_main
from themis_shield.catalogues.gate import settings
from themis_shield.catalogues.tripwire import (
    MAX_FLAGS, Flag, Tripwire, candidates, document, shape_pattern,
)
from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.restore import restore_text

from catalogue_fakes import MISSED, TripwireCloud, known_miss, tripwire_harness, with_cloud
from router_fakes import request


TOKEN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")
DOCUMENT_ONE = "Mara Lindahl is the only synthetic night-desk lead on Ward 7B."
DOCUMENT_TWO = "For the roster ask Birdie Okonkwo, who keeps badge " + MISSED + " for Ward 7B."
PHRASES = ("Ward 7B", "Birdie Okonkwo", "night-desk lead", MISSED)


def two_documents():
    return {
        "model": "themis/auto",
        "messages": [{"role": "user", "content": DOCUMENT_ONE}, {"role": "user", "content": DOCUMENT_TWO}],
    }


def scripted(*rounds):
    """A trip-wire that reports, in round n, every visible occurrence of the phrases of ``rounds[n-1]`` (the last
    entry repeats)."""
    seen = []

    def reply(body):
        text = body["messages"][1]["content"]
        seen.append(text)
        residual = []
        for phrase, category in rounds[min(len(seen), len(rounds)) - 1]:
            for match in re.finditer(re.escape(phrase), text):
                residual.append({"start": match.start(), "end": match.end(), "placeholder": None, "category": category})
        return json.dumps({"residual": residual, "stop": bool(residual)})

    reply.seen = seen
    return reply


def text_of(response):
    return response.body["choices"][0]["message"]["content"]


def conversation(h, body):
    return h.router._conversation_id(body, {})


def assert_round_trip(h, body, task):
    """Every message the cloud received restores, locally, to exactly the client's original text."""
    mapping = h.router.vault.mapping(conversation(h, body))
    sent = [message for message in task["messages"] if message["role"] == "user"]
    assert [restore_text(message["content"], mapping).text for message in sent] == [
        message["content"] for message in body["messages"]
    ]


def assert_value_free(h, job_id, *phrases):
    blobs = [record["flags_json"] for record in h.register.tripwire_records(job_id)]
    blobs += [loop["rounds_json"] for loop in h.register.tripwire_loops(job_id)]
    for blob in blobs:
        for phrase in phrases:
            assert phrase not in blob


# -- the loop through the router --------------------------------------------------------------------------------


def test_residuals_in_two_documents_are_corrected_in_round_one_and_round_two_is_clean(tmp_path):
    reply = scripted([
        ("Ward 7B", "quasi id"), ("Birdie Okonkwo", "person"), ("night-desk lead", None), (MISSED, "internal id"),
    ])
    h = tripwire_harness(tmp_path, reply)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        assert len(h.fake.tripwire_requests) == 2
        # Round 2 saw the corrected package: no phrase is left in it, nor in anything the gate kept or showed.
        for phrase in PHRASES:
            assert phrase not in reply.seen[1] and phrase in reply.seen[0]
            assert phrase not in text_of(held)
        assert_value_free(h, held.job_id, *PHRASES, "Mara", "Okonkwo")
        message = text_of(held)
        assert "came back clean in round 2" in message
        assert "Round 1: 5 finding(s) (INTERNAL_ID 1, OTHER 1, PERSON 1, QUASI_ID 2)" in message
        assert "Round 2: nothing found." in message
        first, second = h.register.tripwire_records(held.job_id)
        assert first["outcome"] == "flagged" and second["outcome"] == "clear"
        assert {flag["round"] for flag in first["flags"]} == {1}
        mapping = h.router.vault.mapping(conversation(h, body))
        by_value = {value: token for token, value in mapping.items()}
        assert by_value["Ward 7B"][1] == "Q" and by_value["Birdie Okonkwo"][1] == "P"
        assert by_value["night-desk lead"][1] == "Q" and by_value[MISSED][1] == "I"
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert loop["converged"] is True
        assert sorted(loop["rounds"][0]["added"]) == sorted(
            by_value[value] for value in ("Ward 7B", "Birdie Okonkwo", "night-desk lead", MISSED)
        )
        # The package the Operator approves is the corrected one, and its counts include the loop's placeholders.
        details = h.register.get_details(held.job_id)
        assert loop["manifest_hash"] == details["manifest_hash"]
        assert json.loads(details["pii_counts_json"])["QUASI_ID"] >= 3
        events = [row["event"] for row in h.register.audit_rows(held.job_id)]
        assert events.count("TRIPWIRE_SENT") == 2 and "TRIPWIRE_CORRECTED" in events
        h.register.approve(held.job_id)
        sent = h.router.handle(body, source_document_count=2)
        assert sent.route == "cloud-tokenised"
        assert len(h.fake.tripwire_requests) == 2  # the approved package is not checked again
        task = h.fake.requests[-1]
        outbound = json.dumps(task, ensure_ascii=False)
        assert all(phrase not in outbound for phrase in PHRASES)
        assert set(loop["rounds"][0]["added"]) <= set(TOKEN.findall(outbound))
        assert_round_trip(h, body, task)
    finally:
        h.cloud.close()


def test_a_finding_that_only_appears_after_round_one_is_caught_in_round_two(tmp_path):
    reply = scripted(
        [("Ward 7B", "quasi id")],
        [("Ward 7B", "quasi id"), ("Birdie Okonkwo", "person")],
    )
    h = tripwire_harness(tmp_path, reply)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        assert len(h.fake.tripwire_requests) == 3
        assert "Birdie Okonkwo" in reply.seen[1] and "Birdie Okonkwo" not in reply.seen[2]
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert loop["converged"] is True
        assert [item["outcome"] for item in loop["rounds"]] == ["corrected", "corrected", "clear"]
        assert [item["findings"] for item in loop["rounds"]] == [2, 1, 0]
        assert loop["rounds"][1]["categories"] == {"PERSON": 1}
        assert [record["outcome"] for record in h.register.tripwire_records(held.job_id)] == [
            "flagged", "flagged", "clear",
        ]
    finally:
        h.cloud.close()


def test_still_finding_after_three_rounds_goes_to_the_operator_not_converged(tmp_path):
    reply = scripted(
        [("Ward 7B", "quasi id")],
        [("Birdie Okonkwo", "person")],
        [("night-desk lead", "quasi id")],
    )
    h = tripwire_harness(tmp_path, reply)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.route == "held" and held.reason == "TRIPWIRE_NOT_CONVERGED"
        assert len(h.fake.tripwire_requests) == 3
        message = text_of(held)
        assert "NOT CONVERGED" in message
        for number in (1, 2, 3):
            assert f"Round {number}: " in message
        assert "night-desk lead" not in message
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert loop["converged"] is False
        assert [item["outcome"] for item in loop["rounds"]] == ["corrected", "corrected", "not_converged"]
        assert all(len(item["added"]) == 1 for item in loop["rounds"])  # round 3's finding was replaced too
        events = [row["event"] for row in h.register.audit_rows(held.job_id)]
        assert "TRIPWIRE_NOT_CONVERGED" in events
        stats = h.register.tripwire_stats()
        assert stats["loops_not_converged"] == 1 and stats["loops_converged"] == 0
        # The Operator decides: an approval sends the corrected package, without another trip-wire round.
        again = h.router.handle(body, source_document_count=2)
        assert again.reason == "TRIPWIRE_NOT_CONVERGED" and len(h.fake.tripwire_requests) == 3
        h.register.approve(held.job_id)
        sent = h.router.handle(body, source_document_count=2)
        assert sent.route == "cloud-tokenised" and len(h.fake.tripwire_requests) == 3
        assert_round_trip(h, body, h.fake.requests[-1])
    finally:
        h.cloud.close()


def flag_everything(body):
    text = body["messages"][1]["content"]
    residual = [
        {"start": match.start(), "end": match.end(), "placeholder": None, "category": "person"}
        for match in re.finditer(r"\w+", text)
    ][:MAX_FLAGS]
    return json.dumps({"residual": residual, "stop": bool(residual)})


def test_a_hostile_reply_that_flags_everything_is_bounded_by_the_cap(tmp_path):
    h = tripwire_harness(tmp_path, flag_everything)
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "TRIPWIRE_NOT_CONVERGED"
        assert 1 <= len(h.fake.tripwire_requests) <= 3
        for trip in h.fake.tripwire_requests:
            assert "Mara Lindahl" not in json.dumps(trip, ensure_ascii=False)
        h.register.approve(held.job_id)
        sent = h.router.handle(body, source_document_count=2)
        assert sent.route == "cloud-tokenised"
        assert_round_trip(h, body, h.fake.requests[-1])
    finally:
        h.cloud.close()


def test_a_round_that_can_replace_nothing_hands_over_at_once(tmp_path):
    # A finding by placeholder only names no text: nothing can change, so asking again would only repeat the round.
    def placeholder_only(body):
        token = TOKEN.search(body["messages"][1]["content"]).group()
        return json.dumps({"residual": [{"placeholder": token, "category": "person"}], "stop": True})

    h = tripwire_harness(tmp_path, placeholder_only)
    try:
        held = h.router.handle(request())
        assert held.reason == "TRIPWIRE_NOT_CONVERGED" and len(h.fake.tripwire_requests) == 1
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert loop["rounds"][0]["added"] == [] and loop["rounds"][0]["outcome"] == "not_converged"
    finally:
        h.cloud.close()


def test_max_rounds_lowers_the_cap(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("Ward 7B", "quasi id")], [("Birdie Okonkwo", "person")]))
    h.router.tripwire = Tripwire(512, max_rounds=1)
    try:
        held = h.router.handle(two_documents(), source_document_count=2)
        assert held.reason == "TRIPWIRE_NOT_CONVERGED" and len(h.fake.tripwire_requests) == 1
    finally:
        h.cloud.close()


# -- fail closed, as before -------------------------------------------------------------------------------------


def test_a_malformed_reply_in_round_two_still_refuses_the_job(tmp_path):
    first = scripted([("Ward 7B", "quasi id")])
    replies = []

    def reply(body):
        replies.append(body)
        return first(body) if len(replies) == 1 else "Nothing else to report."

    h = tripwire_harness(tmp_path, reply)
    try:
        stopped = h.router.handle(two_documents(), source_document_count=2)
        assert stopped.route == "blocked" and stopped.reason == "TRIPWIRE_MALFORMED"
        assert h.register.get_job(stopped.job_id).status.value == "Refused"
        assert [record["outcome"] for record in h.register.tripwire_records(stopped.job_id)] == [
            "flagged", "malformed",
        ]
        assert h.register.tripwire_loops(stopped.job_id) == ()
    finally:
        h.cloud.close()


class UnreachableTripwire(TripwireCloud):
    def __call__(self, request):
        first = strict_json(request.content)["messages"][0]
        if first.get("role") == "system" and first.get("content", "").startswith("You are a privacy trip-wire"):
            self.requests.append(strict_json(request.content))
            return httpx.Response(503, content=b"synthetic outage")
        return super().__call__(request)


def test_a_transport_failure_refuses_the_job(tmp_path):
    h = tripwire_harness(tmp_path)
    with_cloud(h, tmp_path, UnreachableTripwire())
    try:
        stopped = h.router.handle(request())
        assert stopped.route == "blocked" and stopped.reason == "TRIPWIRE_UNAVAILABLE"
        assert h.register.get_job(stopped.job_id).status.value == "Refused"
        assert h.register.tripwire_records(stopped.job_id)[0]["outcome"] == "failed"
    finally:
        h.cloud.close()


def test_a_reply_echoing_an_original_value_in_round_two_fails_closed(tmp_path):
    # Round 1 adds Ward 7B to the vault; round 2's reply quotes it and is stopped at the transport.
    first = scripted([("Ward 7B", "quasi id")])
    calls = []

    def reply(body):
        calls.append(body)
        if len(calls) == 1:
            return first(body)
        return '{"residual": [{"start": 0, "end": 2, "category": "Ward 7B"}], "stop": true}'

    h = tripwire_harness(tmp_path, reply)
    try:
        stopped = h.router.handle(two_documents(), source_document_count=2)
        assert stopped.route == "blocked" and stopped.reason == "TRIPWIRE_UNAVAILABLE"
        assert "Ward 7B" not in json.dumps(stopped.body)
    finally:
        h.cloud.close()


def test_a_secret_reported_by_the_trip_wire_refuses_the_job(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("Ward 7B", "secret")]))
    try:
        stopped = h.router.handle(two_documents(), source_document_count=2)
        assert stopped.route == "blocked" and stopped.reason == "BLOCK:secret"
        assert h.register.get_job(stopped.job_id).status.value == "Refused"
    finally:
        h.cloud.close()


def test_a_package_built_while_the_trip_wire_was_off_is_refused_once_it_is_on(tmp_path):
    h = tripwire_harness(tmp_path)
    tripwire, h.router.tripwire = h.router.tripwire, None
    try:
        body = request()
        held = h.router.handle(body)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        h.register.approve(held.job_id)
        h.router.tripwire = tripwire
        stopped = h.router.handle(body)
        assert stopped.route == "blocked" and stopped.reason == "TRIPWIRE_NOT_RUN"
        assert h.fake.requests == []
    finally:
        h.cloud.close()


# -- proposals and reviews --------------------------------------------------------------------------------------


def test_every_finding_becomes_a_pending_proposal_that_changes_nothing(tmp_path):
    reply = scripted([("Birdie Okonkwo", "person"), ("Ward 7B", "quasi id"), (MISSED, "internal id")])
    h = tripwire_harness(tmp_path, reply)
    register = known_miss(tmp_path)
    h.router.tripwire = Tripwire(512, proposals=register)
    try:
        held = h.router.handle(two_documents(), source_document_count=2)
        (record, _) = h.register.tripwire_records(held.job_id)
        proposals = [flag["proposal"] for flag in record["flags"]]
        assert all(proposals) and len(set(proposals)) == 3  # Ward 7B twice: one proposal per value
        entries = {entry["entry_id"]: entry for entry in register.entries()}
        assert set(proposals) <= set(entries)
        for entry_id in set(proposals):
            entry = entries[entry_id]
            assert entry["status"] == "proposed" and entry["source"] == "cloud_tripwire"
            assert entry["job_id"] == held.job_id
            stored = json.dumps(entry, default=str)
            assert all(phrase not in stored for phrase in PHRASES) and "Okonkwo" not in stored
        kinds = {entries[flag["proposal"]]["entity_type"]: entries[flag["proposal"]]["entry_kind"]
                 for flag in record["flags"]}
        assert kinds == {"PERSON": "rule", "QUASI_ID": "rule", "INTERNAL_ID": "fingerprint"}
        assert register.entries("in_sweep") == ()  # nothing is learned until a person confirms it
        assert getattr(h.router, "known_miss", None) is None  # no sweep: a proposal is never swept
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert sorted(loop["rounds"][0]["proposals"]) == sorted(set(proposals))
    finally:
        h.cloud.close()


def test_operator_verdicts_on_corrected_rounds_feed_precision_without_moving_the_job(tmp_path, capsys):
    reply = scripted([("Ward 7B", "quasi id")], [("Birdie Okonkwo", "person")], [])
    h = tripwire_harness(tmp_path, reply)
    try:
        held = h.router.handle(two_documents(), source_document_count=2)
        status = h.register.get_job(held.job_id).status
        with pytest.raises(PermissionError):
            h.register.review_tripwire(held.job_id, "rejected", reviewer_identity="synthetic-requester")
        assert tripwire_main(
            ["review", held.job_id, "--confirm", "--identity", "synthetic-operator"], register=h.register,
        ) == 0
        assert tripwire_main(
            ["review", held.job_id, "--reject", "--identity", "synthetic-operator"], register=h.register,
        ) == 0
        assert h.register.get_job(held.job_id).status == status
        assert tripwire_main(
            ["review", held.job_id, "--reject", "--identity", "synthetic-operator"], register=h.register,
        ) != 0  # no flagged round left to review
        stats = h.register.tripwire_stats()
        assert stats["flags_confirmed"] == 1 and stats["flags_rejected"] == 1 and stats["flag_precision"] == 0.5
        assert stats["flags_pending"] == 0 and stats["loops_converged"] == 1
        capsys.readouterr()
        assert tripwire_main(["list", "--job", held.job_id], register=h.register) == 0
        listing = capsys.readouterr().out
        assert "round 1" in listing and "Ward 7B" not in listing
    finally:
        h.cloud.close()


# -- the parts --------------------------------------------------------------------------------------------------


def test_candidates_decode_the_span_split_placeholders_and_skip_keys():
    raw = canonical({"model": "m", "messages": [{"role": "user", "content": 'Ask "Birdie" ⟦P:3fa9c1⟧ Okonkwo, now'}]})
    text = document(raw)
    start = text.index("Birdie") - 2  # starts inside the escaped quote: widened, never cut
    end = text.index("Okonkwo") + len("Okonkwo") + 1
    assert candidates(text, Flag(start, end, None, "PERSON")) == ("Birdie", "Okonkwo")
    key = text.index('"content"')
    assert candidates(text, Flag(key, key + 9, None, "PERSON")) == ()
    assert candidates(text, Flag(None, None, "⟦P:3fa9c1⟧", "PERSON")) == ()


def test_candidates_undo_the_gate_literal_escapes():
    raw = canonical({"messages": [{"role": "user", "content": "Code ⟦⟦X⟧⟧-41"}]})
    text = document(raw)
    start = text.index("⟦⟦")
    assert candidates(text, Flag(start, start + len("⟦⟦X⟧⟧-41"), None, "OTHER")) == ("⟦X⟧-41",)


def test_shape_patterns_never_hold_the_value():
    assert shape_pattern("Birdie Okonkwo") == r"[^\W\d_]{6}\s[^\W\d_]{7}"
    assert shape_pattern("Ward 7B") == r"[^\W\d_]{4}\s\d[^\W\d_]"


@pytest.mark.parametrize("value", [0, 4, "3", True])
def test_max_rounds_is_one_to_three(value):
    with pytest.raises(ValueError):
        settings({"tripwire": {"max_rounds": value}})
    with pytest.raises(ValueError):
        Tripwire(512, max_rounds=value)


def test_max_rounds_defaults_to_three():
    assert settings({})["tripwire"]["max_rounds"] == 3
    assert Tripwire().max_rounds == 3


def test_the_new_reasons_have_plain_english_text():
    from themis_shield.web.reasons import catalogue
    reasons = catalogue()
    assert reasons["TRIPWIRE_NOT_CONVERGED"].startswith("NOT CONVERGED:")
    assert "new request" in reasons["TRIPWIRE_NOT_RUN"]
    assert "placeholders" in reasons["TRIPWIRE_CORRECTED"]


# -- FAB-06: only plausible personal values are vaulted ----------------------------------------------------------
# The trip-wire reads untrusted text; a value it names becomes an original, and any later cloud answer holding those
# letters is refused. A fragment ("ni" inside "night", "7B", a two-letter word, a common word) is listed, not vaulted.
def test_fab_06_candidates_refuse_fragments_short_values_and_common_words():
    raw = canonical({"messages": [{"role": "user", "content": "Mara Lindahl keeps the night roster for Ward 7B."}]})
    text = document(raw)

    def at(value, offset=0, length=None):
        start = text.index(value) + offset
        return Flag(start, start + (len(value) if length is None else length), None, "PERSON")

    assert candidates(text, at("night", 0, 2)) == ()  # "ni": inside a word
    assert candidates(text, at("Lindahl", 0, 3)) == ()  # "Lin": cuts the name
    assert candidates(text, at("keeps the", 3, 5)) == ()  # "ps th": across two words, cut at both ends
    assert candidates(text, at("7B")) == ()  # two characters
    assert candidates(text, at("the")) == ()  # a common word
    assert candidates(text, at("Mara Lindahl")) == ("Mara Lindahl",)
    assert candidates(text, at("Ward 7B")) == ("Ward 7B",)


def test_fab_06_a_flagged_fragment_is_listed_for_the_operator_not_vaulted(tmp_path):
    h = tripwire_harness(tmp_path, scripted([("ni", "person"), ("7B", "quasi id")]))
    try:
        body = two_documents()
        held = h.router.handle(body, source_document_count=2)
        assert held.reason == "TRIPWIRE_NOT_CONVERGED"  # nothing could be replaced: handed over at once
        values = set(h.router.vault.mapping(conversation(h, body)).values())
        assert not {"ni", "7B"} & values
        (loop,) = h.register.tripwire_loops(held.job_id)
        assert loop["rounds"][0]["added"] == [] and loop["rounds"][0]["findings"] >= 2
    finally:
        h.cloud.close()
