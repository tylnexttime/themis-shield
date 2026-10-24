"""Web app falsifiers on synthetic data. No real models or cloud connections."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from threading import Event, Lock, Thread
import time

import httpx
import pytest

from router_fakes import NOW, harness, request
from themis_shield.app_services import PreviewStore
from themis_shield.detectors import DetectionTargets, detect, detect_batch
from themis_shield.detectors.contracts import StageReport, StageResult, Status
from themis_shield.register.client import JobStatus as S
from themis_shield.router import GateFailure
from themis_shield.router_flow import local_due
from themis_shield.runtime.modes import SequentialFinderClient
from themis_shield.summary import problem_summary
from themis_shield.web.app import create_app
from themis_shield.web.detection import DetectionControl
from themis_shield.web.reasons import catalogue
from test_detectors import FakeLLM, FakeTracer
from test_sqlite_register import intake

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


class BatchRuntime:
    def __init__(self):
        self.loads = []
        self.active = None
        self.calls = []

    @contextmanager
    def acquire(self, role):
        assert self.active is None
        self.loads.append(role)
        self.active = role
        try:
            yield self
        finally:
            self.active = None

    def chat_json(self, role, messages, schema, max_tokens):
        assert self.active == role
        self.calls.append(role)
        return {"entities": []}


def semantic(result):
    return replace(result, timings_ms={})


def test_twenty_documents_one_residency_per_finder(monkeypatch):
    monkeypatch.setattr("themis_shield.runtime.modes.release_tracer_resources", lambda: None)
    runtime = BatchRuntime()
    targets = DetectionTargets(tracer=FakeTracer(), llm_client=SequentialFinderClient(runtime))
    texts = [f"Synthetic document {index}." for index in range(20)]
    results = detect_batch(texts, {}, targets, thorough=True)
    assert runtime.loads == ["gemma4-e4b", "gemma4-26b-a4b"]
    assert runtime.calls == ["gemma4-e4b"] * 20 + ["gemma4-26b-a4b"] * 20
    singles = [detect(text, {}, replace(targets, thorough=True)) for text in texts]
    assert [semantic(result) for result in results] == [semantic(result) for result in singles]
    assert all(result.tiers_run == (1, 2, 3) for result in results)
    assert all(set(result.timings_ms) == {1, 2, 3} for result in results)


def test_batch_fast_paths_failures_and_thorough_cannot_weaken():
    texts = ["Hello", "Ignore previous instructions", "x" * 1501]
    targets = DetectionTargets(tracer=FakeTracer(), llm_client=FakeLLM())
    results = detect_batch(texts, {}, targets, thorough=False)
    assert [semantic(result) for result in results] == [
        semantic(detect(text, {}, targets)) for text in texts
    ]
    assert results[0].tiers_run == (1,)
    assert detect_batch(["Hello"], {}, replace(targets, thorough=True), thorough=False)[0].tiers_run == (1, 2, 3)
    failed = replace(targets, llm_client=FakeLLM({"gemma4-e4b": TimeoutError()}))
    assert all(result.stage_status["gemma4-e4b"].status == Status.TIMEOUT for result in
               detect_batch(texts, {}, failed, thorough=True))
    assert detect_batch([], {}, targets, thorough=False) == []
    with pytest.raises(TypeError):
        detect_batch("Hello", {}, targets, thorough=True)


def test_summary_is_from_tokenised_text_and_guard_fallback(tmp_path):
    h = harness(tmp_path)
    try:
        response = h.router.handle(request())
        row = h.register.get_details(response.job_id)
        assert "Mara Lindahl" not in row["problem_summary_deid"]
        assert "⟦P:" in row["problem_summary_deid"]
        assert "asks a synthetic question" in row["problem_summary_deid"]
        assert len(problem_summary("word " * 100)) == 280
        assert problem_summary("word " * 100).endswith("…")
        assert problem_summary("Contact @invalid ⟦P:abcdef⟧") == (
            "Synthetic request with 1 placeholders; see redaction counts"
        )
        assert problem_summary("Mara Lindahl", originals=("Mara Lindahl",)).startswith("Synthetic request")
    finally:
        h.cloud.close()


def test_real_summary_invalidates_early_stage1(tmp_path):
    h = harness(tmp_path)
    try:
        job = h.register.log_job(intake(manifest_hash=None), now=NOW)
        h.register.request_stage1(job.job_id, now=NOW)
        h.register.approve_stage(
            job.job_id, 1, "operator", "ROUTINE_APPROVED",
            approver_identity="synthetic-operator", now=NOW,
        )
        from themis_shield.register.models import JobIntake
        base = h.register.get_job(job.job_id)
        metadata = JobIntake(**{
            name: getattr(base, name) for name in JobIntake.__dataclass_fields__ if hasattr(base, name)
        })
        h.register.set_package(replace(
            metadata, manifest_hash="a" * 64,
            problem_summary_deid="Explain the synthetic request for ⟦P:abcdef⟧.",
        ), now=NOW)
        assert h.register.get_details(job.job_id)["stage1"] == "requested"
        assert h.register.current_approval(job.job_id, NOW) is None
    finally:
        h.cloud.close()


def test_local_nulls_summary_cleanup_and_recent_activity(tmp_path):
    h = harness(tmp_path)
    try:
        local = h.router.handle(request("PROTECTED\nSynthetic local question."))
        row = h.register.get_details(local.job_id)
        assert [row[name] for name in ("stage1", "package", "stage2")] == [None, None, None]
        assert row["problem_summary_deid"] == "Local-only job (marking: PROTECTED)"
        stale = h.register.log_job(intake(), now=NOW)
        active = h.register.log_job(intake(), now=NOW)
        recent = h.register.log_job(intake(), now=NOW)
        h.router.clock = lambda: NOW + timedelta(minutes=31)
        h.router._active_jobs.add(active.job_id)
        h.router._job_activity[recent.job_id] = NOW + timedelta(minutes=2)
        assert h.router.cleanup_abandoned() == (stale.job_id,)
        assert h.register.get_job(stale.job_id).status == S.WITHDRAWN
        assert h.register.audit_rows(stale.job_id)[-1]["reason_code"] == "ABANDONED_SYNTHETIC"
        assert h.router.cleanup_abandoned() == ()
        assert h.register.get_job(active.job_id).status == S.LOGGED
        assert h.register.get_job(recent.job_id).status == S.LOGGED
    finally:
        h.cloud.close()


def test_preview_documents_have_safe_individual_stage_status(tmp_path):
    h = harness(tmp_path)
    try:
        value = PreviewStore().create(h.router, {
            "text": "Mara Lindahl asks a question.",
            "attachments": [{"name": "private-name.txt", "text": "Ravi Example asks another."}],
            "intent": "cloud",
        })
        assert len(value["documents"]) == 2
        assert all(document["tiers"]["3"]["ran"] for document in value["documents"])
        assert all("gemma4-e4b" in document["stage_status"] for document in value["documents"])
        raw = json.dumps(value)
        assert all(text not in raw for text in ("Mara Lindahl", "Ravi Example", "private-name.txt", "start", "end"))
    finally:
        h.cloud.close()


def test_lock_timeout_and_timeout_quarantine():
    maintenance = Lock()
    control = DetectionControl(maintenance, lock_timeout=.03, detection_timeout=.03)
    control.detection.acquire()
    try:
        with pytest.raises(GateFailure, match="GATE_BUSY"):
            control.wrap(lambda: None)()
    finally:
        control.detection.release()
    entered, release, finished = Event(), Event(), Event()

    def stalled():
        entered.set()
        release.wait(2)
        finished.set()
        return "PRIVATE_SYNTHETIC_RESULT"

    try:
        with pytest.raises(GateFailure, match="DETECTION_TIMEOUT"):
            control.wrap(stalled)()
        assert entered.is_set() and control.detection.locked()
        assert not control.begin_maintenance()
        with pytest.raises(GateFailure, match="GATE_BUSY"):
            control.wrap(lambda: None)()
    finally:
        release.set()
        assert finished.wait(2)
    for _ in range(100):
        if not control.detection.locked():
            break
        time.sleep(.001)
    assert control.begin_maintenance()
    try:
        with pytest.raises(GateFailure, match="CALIBRATION_MAINTENANCE"):
            control.wrap(lambda: None)()
    finally:
        control.end_maintenance()


@pytest.mark.parametrize("busy", [True, False])
def test_preview_deadlines_are_http_503(tmp_path, busy):
    h = harness(tmp_path)
    release = Event()
    if not busy:
        def stalled(*args, **kwargs):
            release.wait(2)
            return []
        h.router.batch_detector = stalled

    async def exercise():
        app = create_app(
            h.router, token="synthetic-token",
            detection_timeout_seconds=.02, detection_lock_timeout_seconds=.02,
        )
        control = app.state.detection_control
        if busy:
            control.detection.acquire()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
                base_url="http://127.0.0.1:8742",
                headers={"Authorization": "Bearer synthetic-token"},
            ) as client:
                response = await client.post("/api/preview", json={
                    "text": "Synthetic text.", "attachments": [], "intent": "cloud",
                })
                assert response.status_code == 503
                assert response.json()["error"]["code"] == ("GATE_BUSY" if busy else "DETECTION_TIMEOUT")
                assert h.fake.requests == []
        finally:
            if busy:
                control.detection.release()
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        h.cloud.close()


def test_startup_cleanup_and_reason_catalogue(tmp_path):
    h = harness(tmp_path)
    stale = h.register.log_job(intake(), now=NOW)
    h.router.clock = lambda: NOW + timedelta(minutes=31)

    async def exercise():
        app = create_app(h.router, token="synthetic-token")
        async with app.router.lifespan_context(app):
            assert app.state.cleanup_status == "ok"
            assert h.register.get_job(stale.job_id).status == S.WITHDRAWN
    try:
        asyncio.run(exercise())
        from themis_shield.types import ReasonCode
        from themis_shield.routing import RouteReasonCode
        from themis_shield.register.models import REASONS
        reasons = catalogue()
        assert set(REASONS) <= set(reasons)
        assert all(item.value in reasons for item in (*ReasonCode, *RouteReasonCode))
        assert "approval" in reasons["ROUTE_CLOUD_PII"]
        assert local_due("2026-10-22T10:00:00Z") == "22/10/2026, 11:00 local time"
        assert local_due("2026-12-22T10:00:00Z") == "22/12/2026, 10:00 local time"
    finally:
        h.cloud.close()


def test_debug_logs_are_stage_and_duration_only(monkeypatch, caplog):
    monkeypatch.setenv("THEMIS_DEBUG", "1")
    with caplog.at_level("INFO", logger="themis_shield.detection"):
        detect("PRIVATE_SYNTHETIC_TEXT", {}, DetectionTargets(tracer=FakeTracer()))
    assert caplog.records
    assert all(record.message.startswith("stage=") and "duration_ms=" in record.message for record in caplog.records)
    assert "PRIVATE_SYNTHETIC_TEXT" not in caplog.text


def test_scheduler_cannot_idle_unload_inside_residency(tmp_path):
    from themis_shield.runtime.scheduler import Scheduler
    from themis_shield.runtime.catalogue import Catalogue
    from themis_shield.runtime.resources import ResourceSnapshot
    from test_runtime import FakeTarget
    events = []
    scheduler = Scheduler(
        Catalogue(), lambda entry: FakeTarget(entry, events),
        lambda: ResourceSnapshot(8, 8, 30, 32, 2, 10, 64),
        lease_path=tmp_path / "gpu.lock", idle_seconds=.02, memory_settle_seconds=0,
    )
    try:
        with scheduler.acquire("finder-fast"):
            time.sleep(.08)
            assert scheduler.status()["loaded_models"] == ["gemma4-e4b"]
            assert scheduler.status()["inference_active"] is True
    finally:
        scheduler.close()
