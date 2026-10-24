"""Synthetic router integration falsifiers and contract smoke tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import itertools
import json
from pathlib import Path
import zipfile

import httpx
import pytest

from themis_shield.app_services import PreviewStore
from themis_shield.inference import NotEnoughMemory
from themis_shield.register.client import JobStatus as S
from themis_shield.register.models import JobIntake
from themis_shield.register.sqlite_store import SqliteRegister
from themis_shield.runtime.catalogue import Catalogue
from themis_shield.runtime.modes import select
from themis_shield.runtime.resources import ResourceSnapshot
from themis_shield.web.app import create_app

from router_fakes import NOW, harness, request
from test_sqlite_register import intake
from test_runtime import FakeTarget

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


def test_all_stage_orders_and_exact_hash(tmp_path):
    for order in itertools.permutations(("package", "stage1", "stage2")):
        if order.index("stage2") < order.index("package"):
            continue
        store = SqliteRegister(
            tmp_path / ("-".join(order) + ".db"), requester_identity="synthetic-requester",
        )
        job = store.log_job(intake(manifest_hash=None), now=NOW)
        store.request_stage1(job.job_id, now=NOW)
        row = store.get_details(job.job_id)
        assert row["stage1"] == "requested" and row["package"] == "building"
        for action in order:
            if action == "package":
                current = store.get_job(job.job_id)
                metadata = JobIntake(
                    **{name: getattr(current, name)
                       for name in JobIntake.__dataclass_fields__ if hasattr(current, name)}
                )
                store.set_package(replace(metadata, manifest_hash="a" * 64), now=NOW)
            else:
                store.approve_stage(
                    job.job_id, 1 if action == "stage1" else 2,
                    "operator", "ROUTINE_APPROVED",
                    approver_identity="synthetic-operator",
                    manifest_hash="a" * 64 if action == "stage2" else None,
                    now=NOW,
                )
            current = store.get_details(job.job_id)
            complete = (
                current["stage1"] == "approved" and current["package"] == "ready"
                and current["stage2"] == "approved"
            )
            assert bool(store.current_approval(job.job_id, NOW)) == complete
        with pytest.raises(PermissionError):
            with store.release_guard(job.job_id, "b" * 64, now=NOW):
                pytest.fail("changed package was released")


def test_stage2_without_package_and_self_approval(tmp_path):
    store = SqliteRegister(tmp_path / "register.db", requester_identity="synthetic-requester")
    job = store.log_job(intake(manifest_hash=None), now=NOW)
    store.request_stage1(job.job_id, now=NOW)
    with pytest.raises(PermissionError):
        store.approve_stage(
            job.job_id, 2, "operator", "ROUTINE_APPROVED",
            approver_identity="synthetic-operator", manifest_hash="a" * 64, now=NOW,
        )
    with pytest.raises(PermissionError):
        store.approve_stage(
            job.job_id, 1, "operator", "ROUTINE_APPROVED",
            approver_identity="synthetic-requester", now=NOW,
        )


@pytest.mark.parametrize("source", ["logged", "local", "approved"])
def test_gate_failure_closes_active_jobs(tmp_path, source):
    store = SqliteRegister(tmp_path / "register.db", requester_identity="synthetic-requester")
    if source == "local":
        from themis_shield.routing import Route
        job = store.log_job(intake(route=Route.LOCAL_ONLY), now=NOW)
        store.auto_allow_local(job.job_id, now=NOW)
    else:
        job = store.log_job(intake(), now=NOW)
        if source == "approved":
            store.submit_for_approval(job, now=NOW)
            store.approve(
                job.job_id, "operator", "ROUTINE_APPROVED", NOW,
                approver_identity="synthetic-operator",
            )
    store.gate_refuse(job.job_id, now=NOW)
    assert store.get_job(job.job_id).status == S.REFUSED


def test_mode_selection_and_scheduler_timings(tmp_path):
    from themis_shield.runtime.scheduler import Scheduler

    entry = Catalogue().get("gemma4-26b-a4b")
    snapshot = [ResourceSnapshot(8, 8, 30, 32, 2, 10, 64)]
    events = []
    scheduler = Scheduler(
        Catalogue(), lambda item: FakeTarget(item, events), lambda: snapshot[0],
        lease_path=tmp_path / "gpu.lock", memory_settle_seconds=0,
    )
    try:
        assert select(scheduler, entry, snapshot[0]).mode == "full"
        snapshot[0] = replace(snapshot[0], ram_available_gb=12)
        assert select(scheduler, entry, snapshot[0]).mode == "mmap"
        with scheduler.acquire("finder-thorough") as target:
            assert target.entry.mode == "mmap"
        assert scheduler.status()["timings"][-1]["mode"] == "mmap"
        scheduler.stop()
        snapshot[0] = replace(snapshot[0], ram_available_gb=10)
        with pytest.raises(NotEnoughMemory):
            with scheduler.acquire("finder-thorough"):
                pytest.fail("neither variant fits")
    finally:
        scheduler.close()


def test_preview_expiry(tmp_path):
    h = harness(tmp_path)
    now = [0]
    store = PreviewStore(lambda: now[0])
    safe = store.create(h.router, {
        "text": "Synthetic question.", "attachments": [], "intent": "cloud",
    })
    now[0] = 600
    with pytest.raises(KeyError):
        store.get(safe["preview_id"])
    h.cloud.close()


def test_contract_endpoints_and_one_use_launcher(tmp_path, monkeypatch):
    h = harness(tmp_path)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"
    h.router.runtime_settings = object()

    monkeypatch.setattr(
        "themis_shield.web.app.calibration_run",
        lambda *args: {
            "model_id": "gemma4-e4b", "mode": "full", "prompt_tok_s": 2000,
            "gen_tok_s": 50, "prompt_tokens": 500, "gen_tokens": 128,
        },
    )

    async def exercise():
        app = create_app(
            h.router, token="synthetic-local-bearer",
            launcher_code="synthetic-one-use-code",
        )
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8742",
        ) as client:
            first = await client.get("/?t=synthetic-one-use-code")
            assert first.status_code == 303
            cookie = first.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie
            assert (await client.get("/?t=synthetic-one-use-code")).status_code == 401
            forbidden = ("Mara Lindahl", "SYNTHETIC_PRIVATE_ERROR", "requester_identity", "original_span")

            def safe(response):
                assert response.status_code == 200, response.text
                assert all(value not in response.text for value in forbidden)
                return response.json()

            for path in (
                "/api/models", "/api/runtime", "/api/capacity", "/api/jobs",
                "/api/operator/queue", "/api/settings",
            ):
                safe(await client.get(path))
            local_models = safe(await client.get("/api/models"))["local"]
            assert {"full", "mmap"} == {
                variant["mode"]
                for item in local_models if item["id"] == "gemma4-26b-a4b"
                for variant in item["variants"]
            }
            safe(await client.post("/api/settings", json={
                "local_target": "dgx-spark", "default_local_model": "gemma4-e4b",
                "default_cloud_model": "themis/cloud:example-cloud-model",
            }))
            safe(await client.post("/api/settings", json={
                "local_target": "laptop", "default_local_model": "gemma4-e4b",
                "default_cloud_model": "themis/cloud:example-cloud-model",
            }))
            # The app previews the whole conversation as "[role]\n<text>" turns (see server/static/app.js).
            preview = safe(await client.post("/api/preview", json={
                "text": "[user]\nMara Lindahl asks a synthetic question.",
                "attachments": [], "intent": "cloud",
            }))
            chat = {
                "preview_id": preview["preview_id"],
                "messages": [{"role": "user", "content": "Mara Lindahl asks a synthetic question."}],
                "model": "themis/auto", "confirm_cloud": False,
                "conversation_id": "synthetic-app",
            }
            response = await client.post("/api/chat", json=chat)
            assert response.status_code == 200 and "data: [DONE]" in response.text
            rows = safe(await client.get("/api/jobs"))["jobs"]
            identifier = rows[-1]["job_id"]
            assert h.fake.requests == []
            safe(await client.get(f"/api/jobs/{identifier}"))
            safe(await client.get(f"/api/jobs/{identifier}/cloud-view"))
            safe(await client.post(f"/api/operator/{identifier}/approve", json={
                "stage": 1, "reason": "ROUTINE_APPROVED",
            }))
            assert h.fake.requests == []
            safe(await client.post(f"/api/operator/{identifier}/approve", json={
                "stage": 2, "reason": "ROUTINE_APPROVED",
            }))
            assert h.fake.requests == []
            sent = await client.post("/api/chat", json=chat)
            assert "Mara Lindahl" in sent.text  # The only original-bearing app response.
            assert '"answer_source":"cloud"' in sent.text
            assert len(h.fake.requests) == 1

            safe(await client.post("/api/handover", json={"job_id": identifier}))
            peer = safe(await client.get(f"/api/handover/{identifier}/peer-view"))
            assert "⟦P:" in peer["text"]
            safe(await client.post(f"/api/handover/{identifier}/peer-check", json={
                "checked": True, "note": "CHECKED",
            }))
            downloaded = await client.get(f"/api/handover/{identifier}/download")
            assert downloaded.status_code == 200
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                assert set(archive.namelist()) == {"manifest.json", "inputs/clean.json"}
                for name in archive.namelist():
                    raw = archive.read(name)
                    assert b"Mara Lindahl" not in raw and b"wrapped_data_key" not in raw

            for action, reason in (("refuse", "REFUSED"), ("escalate", "OPERATOR_DOUBT")):
                held = h.router.handle(request("Synthetic " + action + " question."))
                safe(await client.post(f"/api/operator/{held.job_id}/{action}", json={"reason": reason}))
            calibration = safe(await client.post("/api/calibrate", json={
                "model_id": "gemma4-e4b", "mode": "full",
            }))
            for _ in range(100):
                status = safe(await client.get("/api/calibrate/" + calibration["job"]))
                if status["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "completed"

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_operator_default_off(tmp_path):
    h = harness(tmp_path)
    async def exercise():
        transport = httpx.ASGITransport(
            app=create_app(h.router, token="synthetic-local-bearer"),
            client=("127.0.0.1", 12345),
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8742",
            headers={"Authorization": "Bearer synthetic-local-bearer"},
        ) as client:
            assert (await client.get("/api/operator/queue")).status_code == 404
    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()
