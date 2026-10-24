"""Small synthetic router regressions and release falsifiers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from themis_shield.app_services import job_view
from themis_shield.detectors import detect
from themis_shield.register.client import JobStatus as S
from themis_shield.register.models import JobIntake
from themis_shield.register.sqlite_store import SqliteRegister, _SqliteRegisterCore
from themis_shield.runtime.modes import SequentialFinderClient
from themis_shield.web.app import create_app

from router_fakes import NOW, harness, request
from test_sqlite_register import ACTOR, OPERATOR, intake

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


def metadata(job, **changes):
    result = JobIntake(**{
        name: getattr(job, name)
        for name in JobIntake.__dataclass_fields__
        if hasattr(job, name)
    })
    return replace(result, **changes)


def test_extended_schema_and_cloud_receipt_exist_at_intake(tmp_path):
    store = SqliteRegister(tmp_path / "register.db", requester_identity=ACTOR)
    job = store.log_job(intake(manifest_hash=None), now=NOW)
    row = store.get_details(job.job_id)
    assert isinstance(row["received_utc"], str)
    assert isinstance(row["due_utc"], str)
    assert (row["stage1"], row["package"], row["stage2"]) == (
        "requested", "building", "pending",
    )
    assert job_view(row)["package"] == "building"
    assert any(
        event["event"] == "STAGE1_REQUESTED"
        for event in store.audit_rows(job.job_id)
    )
    reopened = SqliteRegister(store.path, requester_identity=ACTOR)
    assert reopened.get_details(job.job_id) == row
    assert reopened.current_approval(job.job_id, NOW) is None


def test_old_approval_is_not_grandfathered_by_schema_migration(tmp_path):
    path = tmp_path / "old.db"
    old = _SqliteRegisterCore(path, requester_identity=ACTOR)
    job = old.log_job(intake(), now=NOW)
    old.submit_for_approval(job, now=NOW)
    assert old.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", NOW,
        approver_identity=OPERATOR,
    )
    store = SqliteRegister(path, requester_identity=ACTOR)
    row = store.get_details(job.job_id)
    assert row["status"] == S.APPROVED.value
    assert row["stage2"] == "pending"
    assert store.current_approval(job.job_id, NOW) is None
    with pytest.raises(PermissionError):
        with store.release_guard(job.job_id, "a" * 64, now=NOW):
            pytest.fail("legacy approval reached the connector boundary")


def test_legacy_package_readiness_and_audit_commit_together(tmp_path, monkeypatch):
    store = SqliteRegister(tmp_path / "register.db", requester_identity=ACTOR)
    job = store.log_job(intake(), now=NOW)
    before = store.audit_rows(job.job_id)
    original = store._stage_event

    def refuse_ready(db, row, event, *args, **kwargs):
        if event == "PACKAGE_READY":
            raise sqlite3.OperationalError("synthetic audit failure")
        return original(db, row, event, *args, **kwargs)

    monkeypatch.setattr(store, "_stage_event", refuse_ready)
    with pytest.raises(sqlite3.Error):
        store.submit_for_approval(job, now=NOW)
    row = store.get_details(job.job_id)
    assert row["status"] == S.LOGGED.value
    assert row["package"] == "building"
    assert store.audit_rows(job.job_id) == before


def test_original_detector_fakes_and_sequential_adapter_are_valid(tmp_path):
    h = harness(tmp_path)
    try:
        evidence = detect(
            "Mara Lindahl asks a synthetic question.",
            h.router.profile, replace(h.router.targets, thorough=True),
        )
        assert evidence.tiers_run == (1, 2, 3)
        assert all(
            report.status.value == "ok"
            for report in evidence.stage_status.values()
        )
        h.finders.roles.clear()
        adapter_runtime = SimpleNamespace(chat_json=h.finders.chat_json)
        h.router.targets = replace(
            h.router.targets,
            llm_client=SequentialFinderClient(adapter_runtime),
        )
        held = h.router.handle(request())
        assert held.route == "held" and held.status == 200
        assert h.finders.roles == ["gemma4-e4b", "gemma4-26b-a4b"]
        assert h.router._jobs[held.job_id]["package"] == "ready"
        assert h.fake.requests == []
    finally:
        h.cloud.close()


@pytest.mark.parametrize("first_stage", [1, 2])
def test_one_stage_never_calls_connector(tmp_path, first_stage):
    h = harness(tmp_path)
    try:
        body = request()
        held = h.router.handle(body)
        assert held.route == "held"
        row = h.register.get_details(held.job_id)
        h.register.approve_stage(
            held.job_id, first_stage, "operator", "ROUTINE_APPROVED",
            approver_identity=OPERATOR, now=NOW,
            manifest_hash=row["manifest_hash"] if first_stage == 2 else None,
        )
        assert h.router.handle(body).route == "held"
        assert h.fake.requests == []
        second_stage = 3 - first_stage
        h.register.approve_stage(
            held.job_id, second_stage, "operator", "ROUTINE_APPROVED",
            approver_identity=OPERATOR, now=NOW,
            manifest_hash=row["manifest_hash"] if second_stage == 2 else None,
        )
        assert h.fake.requests == []
        assert h.router.handle(body).route == "cloud-tokenised"
        assert len(h.fake.requests) == 1
    finally:
        h.cloud.close()


def test_one_changed_byte_cannot_enter_release_guard(tmp_path):
    store = SqliteRegister(tmp_path / "register.db", requester_identity=ACTOR)
    raw = b"synthetic clean A"
    digest = hashlib.sha256(raw).hexdigest()
    job = store.log_job(intake(manifest_hash=None), now=NOW)
    store.request_stage1(job.job_id, now=NOW)
    store.set_package(metadata(job, manifest_hash=digest), now=NOW)
    store.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", NOW,
        approver_identity=OPERATOR,
    )
    recording_connector = []
    changed = raw[:-1] + b"B"
    with pytest.raises(PermissionError):
        with store.release_guard(
            job.job_id, hashlib.sha256(changed).hexdigest(), now=NOW
        ):
            recording_connector.append(changed)
    assert recording_connector == []


def test_stream_cleanup_releases_the_same_conversation(tmp_path):
    h = harness(tmp_path)
    try:
        headers = {"X-Themis-Conversation": "same-synthetic-conversation"}
        body = request(stream=True)
        held = h.router.handle(body, headers)
        h.register.approve(held.job_id)
        response = h.router.handle(body, headers)
        assert response.route == "cloud-tokenised"
        assert "data: [DONE]" in "".join(response.sse())
        state = next(iter(h.router._conversations.values()))
        assert state.lock.acquire(False)
        state.lock.release()
    finally:
        h.cloud.close()


def test_operator_http_uses_router_clock_and_settings_fake_contract(tmp_path):
    h = harness(tmp_path)
    h.router.operator_console_enabled = True
    h.router.operator_identity = OPERATOR
    held = h.router.handle(request())

    async def exercise():
        transport = httpx.ASGITransport(
            app=create_app(h.router, token="synthetic-local-bearer"),
            client=("127.0.0.1", 12345),
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8742",
            headers={"Authorization": "Bearer synthetic-local-bearer"},
        ) as client:
            settings = {
                "local_target": "dgx-spark",
                "default_local_model": "gemma4-e4b",
                "default_cloud_model": "themis/cloud:example-cloud-model",
            }
            changed = await client.post("/api/settings", json=settings)
            assert changed.status_code == 200
            assert changed.json()["local_target_status"] == "not configured"
            settings["local_target"] = "laptop"
            assert (
                await client.post("/api/settings", json=settings)
            ).status_code == 200
            for stage in (1, 2):
                approved = await client.post(
                    f"/api/operator/{held.job_id}/approve",
                    json={"stage": stage, "reason": "ROUTINE_APPROVED"},
                )
                assert approved.status_code == 200
                assert h.fake.requests == []
            row = h.register.get_details(held.job_id)
            assert row["approved_utc"] == "2026-10-22T00:00:00.000000Z"
            assert h.router.handle(request()).route == "cloud-tokenised"

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()
