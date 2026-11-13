"""Operator console integration (0.5 candidate): undo a mis-clicked point, and the cloud trip-wire in the review.

Undo: until stage two of the package is approved, an Operator may undo a point's "Not personal" or "Replace it". The
undo is recorded (who, when), the point is open again, and undoing a replacement removes its placeholder from the
job's codebook so the next package rebuild excludes it.

Trip-wire: the console shows, inside the job's card, what each correction round (``before_human``) flagged and added,
and what the check after approval (``after_human``) flagged, with re-approve and refuse for the latter. Placeholders
the correction loop added are points settled by the trip-wire, never new open points. Synthetic data only; a scripted
fake cloud model plays the trip-wire.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import httpx
import pytest

from themis_shield.catalogues.tripwire import Tripwire
from themis_shield.register.review_records import TRIPWIRE_REASON
from themis_shield.restore import restore_text
from themis_shield.vault import OPERATOR_SOURCE
from themis_shield.web.app import create_app

from catalogue_fakes import TripwireCloud, plain_harness, with_cloud
from router_fakes import NOW, request

CLIENT = "SYNTHETIC_LOCAL_GATE_TOKEN_1234567890"
OPERATOR = "SYNTHETIC_LOCAL_OPERATOR_TOKEN_0987654321"
ORIGIN = "http://127.0.0.1:8742"
TEXT = "Blake Morrison ships the synthetic widget on Tuesday."
WARD = "Blake Morrison ships the synthetic widget to Ward 7B on Tuesday."
TOKEN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")
CLEAN = '{"residual": [], "stop": false}'


def _gate(tmp_path, *, tripwire=None, reply=CLEAN):
    """The gate holds any text naming "Blake" (one finder sees a name, the others do not); optionally a trip-wire."""
    h = plain_harness(tmp_path, phrases=("Blake",))
    if tripwire is not None:
        with_cloud(h, tmp_path, TripwireCloud(reply))
        h.router.tripwire = tripwire
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"
    return h


def phrases(*rounds):
    """A trip-wire reply that reports, in round n, every visible occurrence of ``rounds[n-1]``'s phrases (the last
    entry repeats); a phrase the loop replaced is no longer visible and so no longer reported."""
    seen = []

    def reply(body):
        text = body["messages"][1]["content"]
        seen.append(text)
        residual = [
            {"start": match.start(), "end": match.end(), "placeholder": None, "category": category}
            for phrase, category in rounds[min(len(seen), len(rounds)) - 1]
            for match in re.finditer(re.escape(phrase), text)
        ]
        return json.dumps({"residual": residual, "stop": bool(residual)})

    reply.seen = seen
    return reply


def first_the(body):
    """A finding nothing can replace: the word "the" (it is in the trip-wire's own instruction, so the loop never
    vaults it). Reported at its first whole-word place, every round."""
    text = body["messages"][1]["content"]
    match = re.search(r"\bthe\b", text)
    return json.dumps({"residual": [{"start": match.start(), "end": match.end(), "placeholder": None,
                                     "category": "other"}], "stop": True})


def _client(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)), base_url=ORIGIN,
        headers={"Authorization": "Bearer " + token} if token else {},
    )


def _run(h, exercise):
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR)

    async def wrapped():
        async with _client(app, CLIENT) as client, _client(app, OPERATOR) as operator:
            await exercise(client, operator)

    try:
        asyncio.run(wrapped())
    finally:
        h.cloud.close()


async def _held(client, operator, text=TEXT):
    first = await client.post("/v1/chat/completions", json=request(text))
    assert first.headers["x-themis-reason"] == "REQUESTER_CONFIRM_REQUIRED", first.text
    job = first.headers["x-themis-job"]
    return job, (await operator.get(f"/api/operator/{job}/review")).json()


async def _review(operator, job):
    return (await operator.get(f"/api/operator/{job}/review")).json()


async def _approve(operator, job):
    for stage in (1, 2):
        approved = await operator.post(
            f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
        )
        assert approved.status_code == 200, approved.text


def _rows(path, query):
    db = sqlite3.connect(path)
    try:
        return db.execute(query).fetchall()
    finally:
        db.close()


# -- undo -------------------------------------------------------------------------------------------------------


def test_undo_not_personal_reopens_the_point_and_records_who_and_when(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        base = f"/api/operator/{job}/points/{point}"
        # Nothing to undo yet.
        refused = await operator.post(f"{base}/undo", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "POINT_NOT_SETTLED"
        settled = (await operator.post(f"{base}/not-personal", json={})).json()
        assert settled["unresolved"] == 0 and settled["points"][0]["undo"] is True
        # Only the Operator credential undoes.
        assert (await client.post(f"{base}/undo", json={})).status_code == 403
        undone = await operator.post(f"{base}/undo", json={})
        assert undone.status_code == 200, undone.text
        view = undone.json()
        assert view["unresolved"] == 1 and view["points"][0]["resolution"] is None
        (entry,) = view["points"][0]["undone"]
        assert (entry["action"], entry["by"], entry["undone_by"]) == (
            "not-personal", "synthetic-operator", "synthetic-operator",
        )
        assert entry["undone_at"].startswith(NOW.date().isoformat())
        # The point is open again: no stage is approved until it is settled anew.
        blocked = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "REVIEW_POINTS_UNRESOLVED"
        again = await operator.post(f"{base}/not-personal", json={})
        assert again.status_code == 200 and again.json()["unresolved"] == 0
        await _approve(operator, job)

    _run(h, exercise)
    undos = _rows(tmp_path / "register.db", "SELECT u.point_id, u.actor_role, a.actor_identity FROM review_undos u "
                                            "JOIN review_undo_actors a USING(undo_id)")
    assert [row[1:] for row in undos] == [("operator", "synthetic-operator")]
    assert [row[0] for row in _rows(tmp_path / "register.db", "SELECT attempt FROM review_resolutions")] == [0, 1]
    stats = h.register.operator_review_stats()["by_reason"]["ASK:disagreement-empty-llm"]
    assert (stats["not-personal"], stats["undone"], stats["open"]) == (1, 1, 0)


def test_undo_of_replace_removes_the_placeholder_and_the_rebuild_excludes_it(tmp_path):
    h = _gate(tmp_path)
    h.fake.answer = "Dear {token}, thanks."

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        span = {"start": 0, "end": view["text"].index(" ships"), "category": "person"}
        replaced = (await operator.post(f"{base}/replace", json=span)).json()
        assert replaced["package"] == "building" and replaced["points"][0]["undo"] is True
        conversation = h.router._reviews[job].conversation_id
        (token,) = [item for item, source in h.router.vault.sources(conversation).items() if source == OPERATOR_SOURCE]
        assert h.router.vault.mapping(conversation)[token] == "Blake Morrison"
        undone = (await operator.post(f"{base}/undo", json={})).json()
        assert undone["unresolved"] == 1 and undone["points"][0]["undone"][0]["action"] == "replaced"
        # The placeholder left the job's codebook: not in the vault, not marked, not required in the rebuild.
        assert token not in h.router.vault.mapping(conversation)
        assert h.router.vault.sources(conversation) == {}
        assert not h.router._operator_tokens.get(job)
        headers = {"X-Themis-Confirm": job}
        rebuilt = await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        assert rebuilt.headers["x-themis-reason"] == "OPERATOR_APPROVAL_REQUIRED", rebuilt.text
        view = await _review(operator, job)
        assert view["package"] == "ready" and token not in view["text"]
        assert view["text"].endswith(" Morrison ships the synthetic widget on Tuesday.")
        # Settled again, approved, sent: only the gate's own placeholder went out.
        assert (await operator.post(f"{base}/not-personal", json={})).status_code == 200
        await _approve(operator, job)
        sent = await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        assert sent.headers["x-themis-route"] == "cloud-tokenised"
        outbound = json.dumps(h.fake.requests, ensure_ascii=False)
        assert "Blake" not in outbound and token not in outbound
        assert sent.json()["choices"][0]["message"]["content"] == "Dear Blake, thanks."

    _run(h, exercise)


def test_undo_of_replace_after_the_rebuild_sends_the_ready_package_back_to_building(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        span = {"start": 0, "end": view["text"].index(" ships"), "category": "person"}
        await operator.post(f"{base}/replace", json=span)
        headers = {"X-Themis-Confirm": job}
        await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        ready = await _review(operator, job)
        (token,) = [item for item in TOKEN.findall(ready["text"])]
        assert ready["package"] == "ready" and ready["text"].startswith(token + " ships")
        undone = (await operator.post(f"{base}/undo", json={})).json()
        assert undone["package"] == "building" and undone["unresolved"] == 1
        events = [row["event"] for row in h.register.audit_rows(job)]
        assert events.count("PACKAGE_REOPENED") == 2
        await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        view = await _review(operator, job)
        assert view["package"] == "ready" and token not in view["text"] and " Morrison ships" in view["text"]

    _run(h, exercise)


def test_undo_is_refused_once_stage_two_is_approved(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        base = f"/api/operator/{job}/points/{point}"
        await operator.post(f"{base}/not-personal", json={})
        await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        await _approve(operator, job)
        view = await _review(operator, job)
        assert view["points"][0]["undo"] is False
        refused = await operator.post(f"{base}/undo", json={})
        assert refused.status_code == 403
        assert h.register.review_state(job)["points"][0]["resolution"]["action"] == "not-personal"
        with pytest.raises(PermissionError):
            h.register.undo_point(job, point, actor_identity="another-operator", now=NOW)

    _run(h, exercise)


def test_undo_of_a_replacement_is_refused_when_what_it_added_is_unknown_or_in_use(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        span = {"start": 0, "end": view["text"].index(" ships"), "category": "person"}
        await operator.post(f"{base}/replace", json=span)
        conversation = h.router._reviews[job].conversation_id
        (token,) = h.router.vault.sources(conversation)
        # The placeholder is already in another job's package: it stays, and so does the settlement.
        h.router._payloads["synthetic-other-job"] = ("{\"content\": \"" + token + "\"}").encode()
        refused = await operator.post(f"{base}/undo", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "PLACEHOLDER_IN_USE"
        assert token in h.router.vault.mapping(conversation)
        del h.router._payloads["synthetic-other-job"]
        # After a restart what the replacement added is unknown (it was kept in memory only): no undo.
        h.router._operator_points.clear()
        assert (await _review(operator, job))["points"][0]["undo"] is False
        refused = await operator.post(f"{base}/undo", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "UNDO_UNAVAILABLE"
        assert h.register.unresolved_points(job) == ()

    _run(h, exercise)


def test_undo_records_are_append_only(tmp_path):
    h = _gate(tmp_path)
    try:
        held = h.router.handle(request(TEXT))
        (point,) = h.register.unresolved_points(held.job_id)
        h.register.resolve_point(held.job_id, point, "not-personal", actor_identity="synthetic-operator", now=NOW)
        h.register.undo_point(held.job_id, point, actor_identity="synthetic-operator", now=NOW)
        with pytest.raises(ValueError):
            h.register.undo_point(held.job_id, point, actor_identity="synthetic-operator", reopen="yes", now=NOW)
        with pytest.raises(PermissionError):
            h.register.undo_point(held.job_id, point, actor_identity="synthetic-requester", now=NOW)
    finally:
        h.cloud.close()
    db = sqlite3.connect(tmp_path / "register.db")
    try:
        for table in ("review_undos", "review_undo_actors"):
            for statement in (f"UPDATE {table} SET rowid=rowid", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                    db.execute(statement)
    finally:
        db.close()


# -- the cloud trip-wire in the console ------------------------------------------------------------------------


def test_correction_rounds_are_listed_and_their_placeholders_are_settled_not_open_points(tmp_path):
    reply = phrases([("synthetic widget", "quasi id")], [])
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=reply)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        assert len(h.fake.tripwire_requests) == 2
        # One open point, the gate's; the trip-wire's placeholder is settled by it, with its round.
        assert view["total"] == view["unresolved"] == 1
        (added,) = [item for item in view["gate_settled"] if item["source"] == "tripwire"]
        assert added["round"] == 1 and view["text"][added["start"]:added["end"]] == added["placeholder"]
        assert "cloud trip-wire" in added["why"]
        loop = view["tripwire"]["before_human"]
        assert loop["converged"] is True and loop["current"] is True
        first, second = loop["rounds"]
        assert (first["round"], first["outcome"], first["findings"], first["categories"]) == (
            1, "corrected", 1, {"QUASI_ID": 1},
        )
        assert first["added"] == [added["placeholder"]] and first["items"][0]["added"] == [added["placeholder"]]
        assert second["outcome"] == "clear" and second["items"] == []
        assert view["tripwire"]["after_human"] is None
        # Value-free: the replaced phrase is in neither the text nor the trip-wire list nor the queue.
        queue = (await operator.get("/api/operator/queue")).json()["jobs"]
        for payload in (view, queue):
            assert "synthetic widget" not in json.dumps(payload, ensure_ascii=False)
        rows = _rows(tmp_path / "register.db", "SELECT reason_code, settled_by FROM review_points")
        assert (TRIPWIRE_REASON, "gate") in rows

    _run(h, exercise)


def test_not_converged_finding_is_highlighted_exactly_and_the_operator_decides(tmp_path):
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=first_the)

    async def exercise(client, operator):
        job, view = await _held(client, operator)
        loop = view["tripwire"]["before_human"]
        assert loop["converged"] is False
        (only,) = loop["rounds"]
        assert only["outcome"] == "not_converged" and only["added"] == []
        (item,) = only["items"]
        assert item["added"] == [] and item["category"] == "OTHER"
        # Placed exactly in the review text: the reported span maps onto the one content surface.
        assert view["text"][item["review_start"]:item["review_end"]] == "the"
        assert view["text"][item["review_start"] - 6:item["review_end"]] == "ships the"
        point = view["points"][0]["id"]
        await operator.post(f"/api/operator/{job}/points/{point}/not-personal", json={})
        confirm = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert confirm.headers["x-themis-reason"] == "TRIPWIRE_NOT_CONVERGED"
        await _approve(operator, job)
        sent = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert sent.headers["x-themis-route"] == "cloud-tokenised"

    _run(h, exercise)


def test_flag_after_approval_is_listed_and_reapproved_or_refused_from_the_console(tmp_path):
    reply = phrases([("synthetic widget", "quasi id")])
    h = _gate(tmp_path, tripwire=Tripwire(512, before_human=False, after_human=True), reply=reply)

    async def flagged(client, operator, text):
        job, view = await _held(client, operator, text)
        point = view["points"][0]["id"]
        await operator.post(f"/api/operator/{job}/points/{point}/not-personal", json={})
        await client.post("/v1/chat/completions", json=request(text), headers={"X-Themis-Confirm": job})
        await _approve(operator, job)
        held = await client.post("/v1/chat/completions", json=request(text), headers={"X-Themis-Confirm": job})
        assert held.headers["x-themis-reason"] == "TRIPWIRE_FLAGGED"
        return job

    async def exercise(client, operator):
        job = await flagged(client, operator, TEXT)
        queue = (await operator.get("/api/operator/queue")).json()["jobs"]
        (card,) = [row for row in queue if row["job_id"] == job]
        after = card["review"]["tripwire"]["after_human"]
        assert after["pending"] is True and after["outcome"] == "flagged" and after["verdict"] is None
        (item,) = after["items"]
        assert item["category"] == "QUASI_ID" and item["added"] == []
        assert card["review"]["text"][item["review_start"]:item["review_end"]] == "synthetic widget"
        assert (await client.post(f"/api/operator/{job}/tripwire/reapprove", json={})).status_code == 403
        reapproved = await operator.post(f"/api/operator/{job}/tripwire/reapprove", json={})
        assert reapproved.status_code == 200 and reapproved.json()["status"] == "Approved"
        again = await operator.post(f"/api/operator/{job}/tripwire/reapprove", json={})
        assert again.status_code == 409 and again.json()["error"]["code"] == "TRIPWIRE_NOT_PENDING"
        approved_package = h.router._payloads[job]
        sent = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert sent.headers["x-themis-route"] == "cloud-tokenised"
        assert len(h.fake.tripwire_requests) == 1  # sent unchanged, without a second check
        assert json.loads(approved_package)["messages"] == h.fake.requests[-1]["messages"]
        assert job not in [row["job_id"] for row in (await operator.get("/api/operator/queue")).json()["jobs"]]
        other = await flagged(client, operator, TEXT + " Again.")
        refused = await operator.post(f"/api/operator/{other}/tripwire/refuse", json={})
        assert refused.status_code == 200 and refused.json()["status"] == "Refused"
        assert h.register.get_details(other)["status"] == "Refused"
        assert len(h.fake.requests) == 3  # two trip-wire checks and one task: nothing sent for the refused job

    _run(h, exercise)


# -- the two features together, end to end ---------------------------------------------------------------------


def test_gate_hold_trip_wire_loop_operator_points_approval_task_and_exact_restore(tmp_path):
    """The gate holds the document (a name one finder sees); the correction loop replaces a detail the gate missed;
    the Operator mis-clicks "Not personal", undoes it, replaces the full name; the package is rebuilt (the loop runs
    again and comes back clean); approval; the task; the answer restores exactly."""
    reply = phrases([("Ward 7B", "quasi id")], [])
    h = _gate(tmp_path, tripwire=Tripwire(512), reply=reply)
    h.fake.answer = "Dear {token}, the widget is on its way."

    async def exercise(client, operator):
        job, view = await _held(client, operator, WARD)
        assert len(h.fake.tripwire_requests) == 2
        assert view["total"] == 1  # the trip-wire's placeholder is not a new open point
        (ward,) = [item for item in view["gate_settled"] if item["source"] == "tripwire"]
        conversation = h.router._reviews[job].conversation_id
        assert h.router.vault.mapping(conversation)[ward["placeholder"]] == "Ward 7B"
        point = view["points"][0]
        base = f"/api/operator/{job}/points/{point['id']}"
        await operator.post(f"{base}/not-personal", json={})
        await operator.post(f"{base}/undo", json={})
        span = {"start": 0, "end": view["text"].index(" ships"), "category": "person"}
        replaced = (await operator.post(f"{base}/replace", json=span)).json()
        assert replaced["package"] == "building" and replaced["unresolved"] == 0
        headers = {"X-Themis-Confirm": job}
        rebuilt = await client.post("/v1/chat/completions", json=request(WARD), headers=headers)
        assert rebuilt.headers["x-themis-reason"] == "OPERATOR_APPROVAL_REQUIRED", rebuilt.text
        assert len(h.fake.tripwire_requests) == 3  # the rebuilt package was checked once more, and came back clean
        view = await _review(operator, job)
        assert view["unresolved"] == 0 and view["total"] == 1 and view["package"] == "ready"
        (ward_again,) = [item for item in view["gate_settled"] if item["source"] == "tripwire"]
        assert ward_again["placeholder"] == ward["placeholder"] and ward_again["round"] == 1
        # The card lists the loop on the rebuilt package and, below it, the earlier loop that added the placeholder.
        loop = view["tripwire"]["before_human"]
        assert loop["current"] is True and [item["outcome"] for item in loop["rounds"]] == ["clear"]
        (earlier,) = loop["earlier"]
        assert [item["outcome"] for item in earlier["rounds"]] == ["corrected", "clear"]
        assert earlier["rounds"][0]["added"] == [ward["placeholder"]] and earlier["current"] is False
        await _approve(operator, job)
        sent = await client.post("/v1/chat/completions", json=request(WARD), headers=headers)
        assert sent.headers["x-themis-route"] == "cloud-tokenised"
        # The gate's placeholder always hid "Blake". The first trip-wire round read the gate's package, where
        # "Morrison" and "Ward 7B" were still visible (the trade-off of before_human); the check of the rebuilt package
        # and the task carry neither.
        assert "Blake" not in json.dumps(h.fake.requests, ensure_ascii=False)
        for body in (h.fake.tripwire_requests[-1], h.fake.requests[-1]):
            for value in ("Morrison", "Ward 7B"):
                assert value not in json.dumps(body, ensure_ascii=False)
        assert sent.json()["choices"][0]["message"]["content"] == "Dear Blake Morrison, the widget is on its way."
        task = h.fake.requests[-1]
        mapping = h.router.vault.mapping(conversation)
        (message,) = [item for item in task["messages"] if item["role"] == "user"]
        assert restore_text(message["content"], mapping).text == WARD
        assert (await client.get(f"/api/jobs/{job}")).json()["end_state"] == "de-identified-and-released"

    _run(h, exercise)
    assert b"Blake" not in (tmp_path / "register.db").read_bytes()
    assert b"Ward 7B" not in (tmp_path / "register.db").read_bytes()
