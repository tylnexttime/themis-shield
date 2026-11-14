"""Operator review, point by point: show where each hold came from, reveal originals carefully, settle every point.

The design (approved 13/11/2026): three end states (stopped, de-identified and released, released as-is), ASK only a
waypoint; the console shows the de-identified text with each hold trigger and a plain-English reason; the signed-in
Operator (Operator credential only) may reveal a highlighted point's original, which is never recorded; each point is
settled "not personal", "replace it" (a placeholder in the job's codebook, marked de-identified-by-operator) or "stop";
release needs every point settled; nothing an Operator does changes the gate by itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import httpx
import pytest

from themis_shield import review
from themis_shield.catalogues.cli import operator_main
from themis_shield.vault import InMemoryVault, OPERATOR_SOURCE, Vault
from themis_shield.dpapi import NullProtector
from themis_shield.register.sqlite_store import stamp
from themis_shield.web.app import create_app

from catalogue_fakes import plain_harness
from router_fakes import NOW, harness, request

CLIENT = "SYNTHETIC_LOCAL_GATE_TOKEN_1234567890"
OPERATOR = "SYNTHETIC_LOCAL_OPERATOR_TOKEN_0987654321"
ORIGIN = "http://127.0.0.1:8742"
TEXT = "Blake Morrison ships the synthetic widget on Tuesday."
LONG = "A synthetic opening line.\nA second line.\n\nThe widget team says Blake will ship it on Tuesday."


def _gate(tmp_path, phrases=("Blake",)):
    h = plain_harness(tmp_path, phrases=phrases)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"
    return h


def _client(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)), base_url=ORIGIN,
        headers={"Authorization": "Bearer " + token} if token else {},
    )


def _run(h, exercise):
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR)

    async def wrapped():
        async with _client(app, CLIENT) as client, _client(app, OPERATOR) as operator, _client(app) as anonymous:
            await exercise(client, operator, anonymous)

    try:
        asyncio.run(wrapped())
    finally:
        h.cloud.close()


async def _held(client, operator, text=TEXT):
    first = await client.post("/v1/chat/completions", json=request(text))
    assert first.headers["x-themis-reason"] == "REQUESTER_CONFIRM_REQUIRED"
    job = first.headers["x-themis-job"]
    view = (await operator.get(f"/api/operator/{job}/review")).json()
    return job, view


def _dump(path):
    """Every row of every register table, for before/after comparisons."""
    db = sqlite3.connect(path)
    try:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: db.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
    finally:
        db.close()


def test_review_shows_each_hold_with_a_plain_reason_and_place_and_no_original(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator, LONG)
        assert view["total"] == view["unresolved"] == 1 and view["end_state"] is None
        (point,) = view["points"]
        assert point["reason"] == "ASK:disagreement-empty-llm" and point["label"] == "PERSON"
        assert point["why"] == "the detectors disagreed on whether this is a name"
        assert point["location"] == "paragraph 2, line 4" and point["kind"] == "placeholder"
        assert view["text"][point["start"]:point["end"]] == point["placeholder"]
        assert point["category"] == "person" and point["resolution"] is None
        queue = (await operator.get("/api/operator/queue")).json()["jobs"]
        assert [row["job_id"] for row in queue] == [job] and queue[0]["review"]["points"] == view["points"]
        # Value-free unless the reveal endpoint is called: the original is in none of these payloads.
        for payload in (view, queue):
            assert "Blake" not in json.dumps(payload, ensure_ascii=False)
        # The review itself is an Operator view: the client token and no credential are refused.
        assert (await client.get(f"/api/operator/{job}/review")).status_code == 403
        assert (await anonymous.get(f"/api/operator/{job}/review")).status_code == 401

    _run(h, exercise)


def test_reveal_needs_the_operator_credential_and_records_only_that_it_happened(tmp_path, caplog):
    """DD-30: a reveal adds exactly one value-free audit row (who, job, point, UTC time); the original, its length and
    any text are in no register table, no log line; a refused reveal (client or no credential) writes nothing."""
    h = _gate(tmp_path)
    caplog.set_level(logging.DEBUG)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        path = f"/api/operator/{job}/points/{point}/reveal"
        before = _dump(tmp_path / "register.db")
        refused = await client.post(path, json={})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "OPERATOR_CREDENTIAL_REQUIRED"
        assert (await anonymous.post(path, json={})).status_code == 401
        for response in (refused,):
            assert "Blake" not in response.text
        # A refused reveal is refused at the boundary, like every refused Operator request: nothing is written.
        assert _dump(tmp_path / "register.db") == before
        shown = await operator.post(path, json={})
        assert shown.status_code == 200 and shown.json() == {"point_id": point, "original": "Blake", "reveals": 1}
        assert shown.headers["cache-control"] == "no-store"
        after = _dump(tmp_path / "register.db")
        changed = {table for table in after if after[table] != before.get(table)}
        assert changed == {"review_reveals", "review_reveal_actors"}
        (row,) = after["review_reveals"]
        (actor,) = after["review_reveal_actors"]
        reveal_id, row_job, row_point, manifest, role, occurred = row
        assert (row_job, row_point, role) == (job, point, "operator")
        assert manifest == view["manifest_hash"] and actor == (reveal_id, "synthetic-operator")
        assert occurred == stamp(h.router.clock())  # UTC
        # Never the value, its length or any text: every table, every row, compared to the original.
        dump = repr(after)
        assert "Blake" not in dump and b"Blake" not in (tmp_path / "register.db").read_bytes()
        assert set(row) - {reveal_id} <= {job, point, manifest, "operator", occurred}
        assert "Blake" not in caplog.text
        assert h.fake.requests == []

    _run(h, exercise)


def test_reveal_audit_survives_a_restart_and_is_shown_in_the_console_and_the_cli(tmp_path, capsys):
    h = _gate(tmp_path)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        for expected in (1, 2):
            shown = await operator.post(f"/api/operator/{job}/points/{point}/reveal", json={})
            assert shown.json()["reveals"] == expected
        queue = (await operator.get("/api/operator/queue")).json()["jobs"]
        (reveals,) = [row["review"]["reveals"] for row in queue if row["job_id"] == job]
        assert [(item["point"], item["by"]) for item in reveals] == [(1, "synthetic-operator")] * 2
        assert "Blake" not in json.dumps(queue)

    _run(h, exercise)
    # A restart: a fresh register on the same file reads the same rows (nothing was held in memory).
    from themis_shield.register.sqlite_store import SqliteRegister
    fresh = SqliteRegister(tmp_path / "register.db")
    (job,) = fresh.reveal_counts()
    assert fresh.reveal_counts() == {job: 2}
    assert [item["by"] for item in fresh.reveal_records(job)] == ["synthetic-operator"] * 2
    assert fresh.operator_review_stats()["originals_shown"] == 2
    with pytest.raises(sqlite3.DatabaseError):
        db = sqlite3.connect(tmp_path / "register.db")
        try:
            db.execute("DELETE FROM review_reveals")
        finally:
            db.close()
    assert operator_main(["reveals"], register=fresh) == 0
    assert json.loads(capsys.readouterr().out) == {"originals_shown_per_job": {job: 2}}
    assert operator_main(["reveals", "--job", job], register=fresh) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["by"] for item in listed["reveals"]] == ["synthetic-operator"] * 2
    assert "Blake" not in json.dumps(listed)


def test_replace_it_adds_an_operator_placeholder_and_restores_exactly(tmp_path):
    h = _gate(tmp_path)
    h.fake.answer = "Dear {token}, thanks."

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]
        span = {"start": 0, "end": view["text"].index(" ships"), "category": "person"}  # adjusted: "Blake Morrison"
        assert (await client.post(f"/api/operator/{job}/points/{point['id']}/replace", json=span)).status_code == 403
        replaced = await operator.post(f"/api/operator/{job}/points/{point['id']}/replace", json=span)
        assert replaced.status_code == 200, replaced.text
        after = replaced.json()
        assert after["unresolved"] == 0 and after["package"] == "building"
        resolution = after["points"][0]["resolution"]
        assert (resolution["action"], resolution["category"], resolution["by"]) == (
            "replaced", "person", "synthetic-operator",
        )
        assert (resolution["start"], resolution["end"]) == (span["start"], span["end"])
        # The placeholder lives in this job's codebook (its conversation's vault), marked as the Operator's.
        state = h.router._reviews[job]
        sources = h.router.vault.sources(state.conversation_id)
        (token,) = [item for item, source in sources.items() if source == OPERATOR_SOURCE]
        assert h.router.vault.mapping(state.conversation_id)[token] == "Blake Morrison"
        # The next send rebuilds and re-binds the package with the new placeholder; the point stays settled.
        headers = {"X-Themis-Confirm": job}
        rebuilt = await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        assert rebuilt.headers["x-themis-reason"] == "OPERATOR_APPROVAL_REQUIRED"
        view = (await operator.get(f"/api/operator/{job}/review")).json()
        assert view["text"] == token + " ships the synthetic widget on Tuesday."
        assert view["unresolved"] == 0 and view["package"] == "ready"
        for stage in (1, 2):
            approved = await operator.post(
                f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
            )
            assert approved.status_code == 200, approved.text
        sent = await client.post("/v1/chat/completions", json=request(TEXT), headers=headers)
        assert sent.headers["x-themis-route"] == "cloud-tokenised"
        outbound = json.dumps(h.fake.requests, ensure_ascii=False)
        assert "Blake" not in outbound and "Morrison" not in outbound and token in outbound
        assert sent.json()["choices"][0]["message"]["content"] == "Dear Blake Morrison, thanks."
        assert h.register.get_details(job)["status"] == "Completed"
        assert (await client.get(f"/api/jobs/{job}")).json()["end_state"] == "de-identified-and-released"
        # The record holds positions, categories and decisions only: never the value it replaced.
        assert b"Blake" not in (tmp_path / "register.db").read_bytes()

    _run(h, exercise)


def test_replace_refuses_a_span_that_cuts_a_placeholder_or_misses_the_point(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]
        path = f"/api/operator/{job}/points/{point['id']}/replace"
        far = view["text"].index("widget")
        for span in (
            {"start": 2, "end": 14, "category": "person"},  # cuts through the placeholder
            {"start": far, "end": far + 6, "category": "person"},  # does not cover the point
            {"start": 0, "end": 10, "category": "pet"},  # not a category
            {"start": 5, "end": 5, "category": "person"},  # empty
        ):
            refused = await operator.post(path, json=span)
            assert refused.status_code == 400, span
        assert (await operator.post(path, json={"start": 0})).json()["error"]["code"] == "INVALID_REVIEW_ACTION"
        assert h.register.unresolved_points(job) == (point["id"],)
        # Confirming the gate's own placeholder as it is changes nothing in the package.
        confirmed = await operator.post(path, json={"start": point["start"], "end": point["end"], "category": "person"})
        assert confirmed.status_code == 200 and confirmed.json()["package"] == "ready"

    _run(h, exercise)


def test_not_personal_is_recorded_and_counted_and_never_promoted(tmp_path, capsys):
    h = _gate(tmp_path)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        assert (await client.post(f"/api/operator/{job}/points/{point}/not-personal", json={})).status_code == 403
        settled = await operator.post(f"/api/operator/{job}/points/{point}/not-personal", json={})
        assert settled.status_code == 200
        resolution = settled.json()["points"][0]["resolution"]
        assert (resolution["action"], resolution["by"]) == ("not-personal", "synthetic-operator")
        # Settled once: a second verdict on the same point is refused.
        assert (await operator.post(f"/api/operator/{job}/points/{point}/not-personal", json={})).status_code == 403
        # The text stays as shown: the gate's placeholder is not undone.
        assert view["points"][0]["placeholder"] in h.router._reviews[job].text
        # A new document with the same look-alike still holds: the verdict changed nothing in the gate.
        again = await client.post("/v1/chat/completions", json=request(TEXT + " Again."))
        assert again.headers["x-themis-reason"] == "REQUESTER_CONFIRM_REQUIRED"
        assert len(h.register.unresolved_points(again.headers["x-themis-job"])) == 1
        assert getattr(h.router, "fp_catalogue", None) is None

    _run(h, exercise)
    stats = h.register.operator_review_stats()
    assert stats["by_reason"]["ASK:disagreement-empty-llm"] == {
        "held": 2, "not-personal": 1, "replaced": 0, "stopped": 0, "open": 1,
    }
    assert operator_main(["stats"], register=h.register) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["by_reason"]["ASK:disagreement-empty-llm"]["not-personal"] == 1
    assert "Blake" not in json.dumps(printed)


def test_release_is_refused_until_every_point_is_settled(tmp_path):
    h = _gate(tmp_path, phrases=("Blake", "Corvin"))

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator, "Blake met Corvin about the synthetic widget.")
        assert view["unresolved"] == 2
        refused = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "REVIEW_POINTS_UNRESOLVED"
        # Every approval path, not only the console: the register itself refuses.
        with pytest.raises(PermissionError):
            h.register.approve_stage(
                job, 1, "operator", "ROUTINE_APPROVED", approver_identity="synthetic-operator", now=NOW,
            )
        first, second = (item["id"] for item in view["points"])
        await operator.post(f"/api/operator/{job}/points/{first}/not-personal", json={})
        still = await operator.post(f"/api/operator/{job}/approve", json={"stage": 1, "reason": "ROUTINE_APPROVED"})
        assert still.status_code == 409
        await operator.post(f"/api/operator/{job}/points/{second}/not-personal", json={})
        headers = {"X-Themis-Confirm": job}
        await client.post("/v1/chat/completions", json=request("Blake met Corvin about the synthetic widget."),
                          headers=headers)
        for stage in (1, 2):
            approved = await operator.post(
                f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
            )
            assert approved.status_code == 200, approved.text
        sent = await client.post(
            "/v1/chat/completions", json=request("Blake met Corvin about the synthetic widget."), headers=headers,
        )
        assert sent.headers["x-themis-route"] == "cloud-tokenised" and len(h.fake.requests) == 1

    _run(h, exercise)


def test_stop_ends_the_job_in_the_stopped_state(tmp_path):
    h = _gate(tmp_path)

    async def exercise(client, operator, anonymous):
        job, view = await _held(client, operator)
        point = view["points"][0]["id"]
        assert (await client.post(f"/api/operator/{job}/points/{point}/stop", json={})).status_code == 403
        stopped = await operator.post(f"/api/operator/{job}/points/{point}/stop", json={})
        assert stopped.status_code == 200
        assert stopped.json()["end_state"] == "stopped" and stopped.json()["stopped"] is True
        assert h.register.get_details(job)["status"] == "Refused"
        assert (await operator.get("/api/operator/queue")).json()["jobs"] == []
        again = await client.post("/v1/chat/completions", json=request(TEXT), headers={"X-Themis-Confirm": job})
        assert again.headers["x-themis-reason"] == "APPROVAL_NOT_ACTIVE"
        assert h.fake.requests == []
        assert any(row["reason_code"] == "STOPPED_BY_OPERATOR" for row in h.register.audit_rows(job))

    _run(h, exercise)


def test_a_release_decision_has_no_points_and_approves_as_before(tmp_path):
    h = harness(tmp_path)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"

    async def exercise(client, operator, anonymous):
        for text, state in (
            ("Mara Lindahl asks a synthetic question.", "de-identified-and-released"),
            ("What is a synthetic test dataset for?", "released-as-is"),
        ):
            job = (await client.post("/v1/chat/completions", json=request(text))).headers["x-themis-job"]
            view = (await operator.get(f"/api/operator/{job}/review")).json()
            assert view["total"] == 0 and len(view["gate_settled"]) == (1 if "Mara" in text else 0)
            if view["gate_settled"]:
                assert view["gate_settled"][0]["why"] == "a name, replaced by the gate"
            for stage in (1, 2):
                assert (await operator.post(
                    f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
                )).status_code == 200
            sent = await client.post("/v1/chat/completions", json=request(text))
            assert sent.headers["x-themis-route"] == "cloud-tokenised"
            assert (await client.get(f"/api/jobs/{job}")).json()["end_state"] == state

    _run(h, exercise)


def test_review_records_refuse_update_and_delete(tmp_path):
    h = _gate(tmp_path)
    try:
        held = h.router.handle(request(TEXT))
        point = h.register.unresolved_points(held.job_id)[0]
        h.register.resolve_point(held.job_id, point, "not-personal", actor_identity="synthetic-operator", now=NOW)
    finally:
        h.cloud.close()
    db = sqlite3.connect(tmp_path / "register.db")
    try:
        for table in ("review_points", "review_resolutions", "review_resolution_actors"):
            for statement in (f"UPDATE {table} SET rowid=rowid", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                    db.execute(statement)
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_vault_marks_operator_placeholders_and_wipes_the_marks(tmp_path, kind):
    vault = InMemoryVault() if kind == "memory" else Vault(
        tmp_path / "vault.db", protector=NullProtector(), insecure_test_only=True,
    )
    try:
        vault.put("conversation-a", "⟦P:00aa11⟧", "Synthetic Person")
        with pytest.raises(KeyError):
            vault.mark_source("conversation-a", "⟦P:00bb22⟧", OPERATOR_SOURCE)
        with pytest.raises(ValueError):
            vault.mark_source("conversation-a", "⟦P:00aa11⟧", "learned")
        vault.mark_source("conversation-a", "⟦P:00aa11⟧", OPERATOR_SOURCE)
        assert vault.sources("conversation-a") == {"⟦P:00aa11⟧": OPERATOR_SOURCE}
        vault.wipe("conversation-a")
        assert vault.sources("conversation-a") == {}
    finally:
        vault.close()


def test_point_reasons_are_fixed_text(tmp_path):
    from themis_shield.web.reasons import point_reason
    assert point_reason("ASK:sensitive", "HEALTH") == "a statement about someone's health"
    assert point_reason("ASK:injection-signal", None) == "words that look like instructions to the AI model"
    assert review.location("one\n\ntwo\nthree", 10) == "paragraph 2, line 4"
    assert review.location(None, None) == "whole document"
