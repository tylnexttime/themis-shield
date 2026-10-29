"""Detector canary (v0.3): known-answer checks against silent detector failure. Synthetic canary text only."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from themis_shield import canary as canary_module, startup
from themis_shield.canary import (
    BLOCK_FAILED, BLOCK_PENDING, DOCUMENTS, CanaryMonitor, CanarySettings, canary_settings, check_finder,
    check_propagation, check_rules, check_tracer, expectations, run_canary,
)
from themis_shield.chat_boundary import ChatBoundary
from themis_shield.detectors import DetectionTargets, detect_batch
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.llm_finder import FinderModel
from themis_shield.detectors.rules import set_rule_packs
from themis_shield.inference.llamacpp import LlamaCppTarget
from themis_shield.router import GateFailure
from themis_shield.runtime.catalogue import BUILTINS, Catalogue
from themis_shield.runtime.modes import SequentialFinderClient
from themis_shield.runtime.scheduler import Scheduler
from themis_shield.startup import StartupRefused
from themis_shield.types import Label

from router_fakes import NOW, harness, request
from test_runtime import FakeTarget, enough_resources

NAME, EMAIL, STATEMENT = canary_module.NAME, canary_module.EMAIL, canary_module.STATEMENT


# --------------------------------------------------------------------------------------------------------------------
# Fakes: a healthy tracer and finder for the canary documents, and broken variants
# --------------------------------------------------------------------------------------------------------------------
class CanaryTracer:
    """Finds the canary's name and e-mail address wherever they occur; ``mode`` breaks it."""

    def __init__(self, mode="healthy"):
        self.mode = mode
        self.calls = 0

    def detect(self, text):
        self.calls += 1
        if self.mode == "crash":
            raise RuntimeError("synthetic tracer failure")
        found = []
        if self.mode != "empty":
            for value, label in ((NAME, "PERSON"), (EMAIL, "EMAIL")):
                for match in re.finditer(re.escape(value), text):
                    found.append(finding(
                        match.start(), match.end(), "QUASI_ID" if self.mode == "wrong-labels" else label,
                        "pii-tracer", 1, 0.99,
                    ))
        return StageResult(tuple(found), StageReport("pii-tracer", Status.OK, len(found)))


class CanaryFinder:
    """A local finder client: lists the canary's name and statement; ``mode`` breaks it."""

    def __init__(self, mode="healthy"):
        self.mode = mode
        self.calls = []

    def chat_json(self, model_role, messages, schema, max_tokens):
        self.calls.append(model_role)
        if self.mode == "crash":
            raise RuntimeError("synthetic finder failure")
        if self.mode == "empty":
            return {"entities": []}
        document = messages[-1]["content"]
        labels = ("ADDRESS", "OTHER_DATE") if self.mode == "wrong-labels" else ("PERSON", "HEALTH")
        return {"entities": [
            {"label": label, "value": value}
            for label, value in zip(labels, (NAME, STATEMENT)) if value in document
        ]}


def healthy_monitor(**changes):
    values = dict(
        targets=DetectionTargets(tracer=CanaryTracer()), finder_client=CanaryFinder(), clock=lambda: NOW,
    )
    values.update(changes)
    settings = values.pop("settings", CanarySettings())
    return CanaryMonitor(settings, **values)


@pytest.fixture
def au_rules():
    set_rule_packs(("core", "au"))
    yield
    set_rule_packs(None)


# --------------------------------------------------------------------------------------------------------------------
# The fixed documents and expectations
# --------------------------------------------------------------------------------------------------------------------
def test_documents_are_synthetic_and_the_identifiers_are_checksum_valid():
    from themis_shield.detectors.rules import validate_identifier
    assert validate_identifier(Label.TFN, canary_module.TFN).checksum_valid
    assert validate_identifier(Label.MEDICARE, canary_module.MEDICARE).checksum_valid
    assert canary_module.EMAIL.endswith("@example.org")  # a domain reserved for documentation
    assert canary_module.PHONE.replace(" ", "").startswith("0491570")  # the range set aside for fiction
    assert DOCUMENTS["statement"].count(canary_module.SURNAME) == 2  # the propagation target
    assert "health" not in DOCUMENTS["statement"].lower()  # the statement is indirect


def test_expectations_depend_on_the_rule_packs():
    assert expectations(("core",))["rules"] == ("EMAIL", "CONTROL_CLEAN")
    assert expectations(("core", "au"))["rules"] == ("EMAIL", "CONTROL_CLEAN", "PHONE", "TFN", "MEDICARE")
    assert expectations()["gemma4-26b-a4b"] == ("PERSON", "SENSITIVE_STATEMENT")


# --------------------------------------------------------------------------------------------------------------------
# Healthy detectors pass; stubbed ones fail with the named expectation
# --------------------------------------------------------------------------------------------------------------------
def test_healthy_detectors_pass(au_rules):
    result = run_canary(DetectionTargets(tracer=CanaryTracer()), finder_client=CanaryFinder())
    assert result.passed, result.record()
    assert [check.detector for check in result.checks] == [
        "rules", "pii-tracer", "propagate", "gemma4-e4b", "gemma4-26b-a4b",
    ]
    assert all(check.status == "pass" and check.duration_ms >= 0 for check in result.checks)


def test_real_rules_and_propagation_pass_with_every_pack():
    for packs in (("core",), ("core", "au"), ("core", "au", "intl")):
        assert check_rules(packs=packs).passed, packs
    assert check_propagation().passed


@pytest.mark.parametrize("mode, failed", [
    ("empty", ("PERSON", "EMAIL")), ("wrong-labels", ("PERSON", "EMAIL")), ("crash", ("STAGE_OK",)),
])
def test_a_silent_or_broken_tracer_fails(mode, failed):
    check = check_tracer(CanaryTracer(mode))
    assert check.status == "fail" and check.failed == failed
    if mode == "crash":
        assert check.error_code == "stage-failed"


@pytest.mark.parametrize("mode, failed", [
    ("empty", ("PERSON", "SENSITIVE_STATEMENT")), ("wrong-labels", ("PERSON", "SENSITIVE_STATEMENT")),
    ("crash", ("STAGE_OK",)),
])
def test_a_silent_or_broken_finder_fails(mode, failed):
    for model in FinderModel:
        check = check_finder(model, CanaryFinder(mode))
        assert check.detector == model.value and check.status == "fail" and check.failed == failed


def test_rules_that_return_nothing_fail(au_rules, monkeypatch):
    from themis_shield.detectors import rules
    monkeypatch.setattr(rules.RulesDetector, "detect",
                        lambda self, text: StageResult((), StageReport("rules", Status.OK)))
    check = check_rules()
    assert check.failed == ("EMAIL", "PHONE", "TFN", "MEDICARE")


def test_rules_that_flag_the_clean_control_fail(monkeypatch):
    from themis_shield.detectors import rules
    original = rules.RulesDetector.detect

    def noisy(self, text):
        found = original(self, text)
        extra = (finding(0, 3, "SECRET", "rules", 1),)
        return StageResult(found.findings + extra, found.report)

    monkeypatch.setattr(rules.RulesDetector, "detect", noisy)
    assert check_rules(packs=("core",)).failed == ("CONTROL_CLEAN",)


def test_propagation_that_adds_nothing_fails(monkeypatch):
    monkeypatch.setattr(canary_module, "propagate", lambda text, spans, *args: spans)
    assert check_propagation().failed == ("REPEATED_NAME",)


def test_overlap_not_exact_spans():
    """Conservative: a finding overlapping the expected place counts (model spans vary)."""
    text = DOCUMENTS["statement"]
    start = text.index(STATEMENT)
    partial = (finding(start + 3, start + 15, "DISABILITY", "gemma4-e4b", 2),)
    expectation = canary_module.FINDER[1]
    assert expectation.met(partial)
    assert not expectation.met((finding(0, 9, "HEALTH", "gemma4-e4b", 2),))


def test_records_carry_no_text(au_rules):
    result = run_canary(DetectionTargets(tracer=CanaryTracer("empty")), finder_client=CanaryFinder("empty"))
    record = json.dumps(result.record())
    for value in (NAME, canary_module.SURNAME, EMAIL, STATEMENT, canary_module.TFN, canary_module.PHONE):
        assert value not in record
    assert not result.passed


def test_replay_skips_the_finders_and_records_why(au_rules):
    finder = CanaryFinder()
    result = run_canary(DetectionTargets(tracer=CanaryTracer()), finder_client=finder, finders_skipped="replay")
    assert result.passed and finder.calls == []
    skipped = [check for check in result.checks if check.status == "skipped"]
    assert [check.detector for check in skipped] == ["gemma4-e4b", "gemma4-26b-a4b"]
    assert all(check.error_code == "replay" for check in skipped)


# --------------------------------------------------------------------------------------------------------------------
# The template problem, simulated with the fake llama-server
# --------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def fake_server(tmp_path):
    targets = []
    for entry in BUILTINS:
        (tmp_path / entry.file).write_bytes(b"SYNTHETIC-GGUF-STUB")

    def start(case):
        target = LlamaCppTarget(
            BUILTINS[0], models_dir=tmp_path, logs_dir=tmp_path / "logs", startup_timeout=5, retry_pause=0.01,
            stop_timeout=2, poll_interval=0.02, timeout_seconds=5,
            command_prefix=(sys.executable, str(Path(__file__).with_name("fake_llama_server.py")),
                            "--output-case", case),
        )
        targets.append(target)
        target.start()
        return target

    yield start
    for target in targets:
        target.stop()


@pytest.mark.parametrize("case, passed, error", [
    ("canary", True, None),
    ("empty", False, None),  # a valid, empty answer: the silent failure the canary exists for
    ("thought-channel", False, "stage-failed:runtime"),  # the answer begins in a thought channel
    ("bare-list", False, "stage-failed:runtime"),  # "[]" instead of the schema's object
])
def test_finder_canary_against_the_fake_llama_server(fake_server, case, passed, error):
    check = check_finder(FinderModel.E4B, fake_server(case))
    assert check.passed is passed, check.record()
    assert check.error_code == error
    if case == "empty":
        assert check.failed == ("PERSON", "SENSITIVE_STATEMENT")


# --------------------------------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------------------------------
def test_settings_defaults_and_validation():
    assert canary_settings({}) == CanarySettings(True, True, True, 30.0, 0)
    assert canary_settings({"canary": {"interval_minutes": 0, "every_documents": 50}}).every_documents == 50
    for bad in ({"enabled": "yes"}, {"bogus": 1}, {"interval_minutes": -1}, {"every_documents": 1.5},
                {"after_reload": 1}):
        with pytest.raises(ValueError):
            canary_settings({"canary": bad})


def test_example_config_documents_every_canary_key():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "themis.example.toml").open("rb") as stream:
        section = tomllib.load(stream)["canary"]
    assert set(section) == canary_module.CANARY_KEYS
    assert canary_settings({"canary": section}) == CanarySettings()


# --------------------------------------------------------------------------------------------------------------------
# Start-up
# --------------------------------------------------------------------------------------------------------------------
def test_startup_runs_the_canary_and_refuses_on_failure(au_rules):
    good = healthy_monitor()
    assert startup.check_canary(SimpleNamespace(canary=good)).passed
    assert good.block_reason() is None and good.status()["state"] == "healthy"
    bad = healthy_monitor(targets=DetectionTargets(tracer=CanaryTracer("empty")))
    with pytest.raises(StartupRefused) as caught:
        startup.check_canary(SimpleNamespace(canary=bad))
    assert caught.value.code == "CANARY_FAILED"
    assert caught.value.detail == "pii-tracer: PERSON, EMAIL"
    assert "[CANARY_FAILED]" in caught.value.message and NAME not in caught.value.message
    crashed = healthy_monitor(finder_client=CanaryFinder("crash"))
    with pytest.raises(StartupRefused) as caught:
        startup.check_canary(SimpleNamespace(canary=crashed))
    assert caught.value.detail == "gemma4-e4b: STAGE_OK [stage-failed]"


def test_startup_canary_can_be_off_or_deferred():
    assert startup.check_canary(SimpleNamespace(canary=None)) is None
    deferred = healthy_monitor(settings=CanarySettings(at_startup=False))
    assert startup.check_canary(SimpleNamespace(canary=deferred)) is None
    assert deferred.block_reason() == BLOCK_PENDING  # cloud-bound work waits for a first pass


# --------------------------------------------------------------------------------------------------------------------
# The scheduler hook: after every model load
# --------------------------------------------------------------------------------------------------------------------
class SwitchTarget(FakeTarget):
    """FakeTarget whose finder answer follows a shared switch (healthy, empty, crash)."""

    switch = {"mode": "healthy"}

    def chat_json(self, model_role, messages, schema, max_tokens=512):
        self.chat(messages)
        return CanaryFinder(self.switch["mode"]).chat_json(model_role, messages, schema, max_tokens)


@pytest.fixture
def scheduler(tmp_path):
    events = []
    SwitchTarget.switch = {"mode": "healthy"}
    instance = Scheduler(
        Catalogue(), lambda entry: SwitchTarget(entry, events), enough_resources,
        lease_path=tmp_path / "lease.lock", memory_settle_seconds=0, queue_timeout=2,
    )
    yield instance
    instance.close()


def test_scheduler_reports_loads_to_its_listener(scheduler):
    seen = []
    scheduler.load_listener = lambda entry, target, loaded: seen.append((entry.id, loaded))
    with scheduler.acquire("finder-fast"):
        pass
    with scheduler.acquire("finder-fast"):
        pass  # warm: no load
    with scheduler.acquire("finder-thorough"):
        pass
    assert seen == [("gemma4-e4b", True), ("gemma4-e4b", False), ("gemma4-26b-a4b", True)]


def test_a_failing_listener_fails_the_acquisition_closed(scheduler):
    def broken(entry, target, loaded):
        raise RuntimeError("synthetic listener failure")

    scheduler.load_listener = broken
    with pytest.raises(RuntimeError):
        with scheduler.acquire("finder-fast"):
            pass


def _attached(scheduler, **settings):
    monitor = CanaryMonitor(
        CanarySettings(**settings), targets=DetectionTargets(tracer=CanaryTracer()), runtime=scheduler,
        clock=lambda: NOW,
    )
    scheduler.load_listener = monitor.after_acquire
    return monitor


def test_reload_canary_degrades_and_recovers(scheduler, au_rules):
    monitor = _attached(scheduler)
    assert monitor.startup().passed and monitor.block_reason() is None
    # Start-up checks run in their own residencies; the hook does not repeat them.
    assert [entry["trigger"] for entry in monitor.history] == ["startup"]
    SwitchTarget.switch["mode"] = "empty"  # e.g. a template change picked up by the next load
    scheduler.stop()
    with scheduler.acquire("gemma4-e4b"):
        pass
    assert monitor.block_reason() == BLOCK_FAILED
    state = monitor.status()
    assert state["state"] == "degraded" and state["detectors"]["gemma4-e4b"]["trigger"] == "reload"
    assert state["detectors"]["gemma4-e4b"]["failed"] == ["PERSON", "SENSITIVE_STATEMENT"]
    with scheduler.acquire("gemma4-e4b"):
        pass  # warm, but the failed model is re-checked at once ("retry"), not only at the next interval
    assert monitor.status()["detectors"]["gemma4-e4b"]["trigger"] == "retry"
    assert monitor.block_reason() == BLOCK_FAILED
    SwitchTarget.switch["mode"] = "healthy"
    scheduler.stop()
    with scheduler.acquire("gemma4-e4b"):
        pass
    assert monitor.block_reason() is None


def test_reload_canary_can_be_switched_off(scheduler, au_rules):
    monitor = _attached(scheduler, after_reload=False)
    monitor.startup()
    count = len(monitor.history)
    scheduler.stop()
    with scheduler.acquire("gemma4-e4b"):
        pass
    assert len(monitor.history) == count  # not due: no check on this load


def test_detection_through_the_scheduler_runs_the_reload_canary_first(scheduler, au_rules):
    monitor = _attached(scheduler)
    targets = DetectionTargets(tracer=CanaryTracer(), llm_client=SequentialFinderClient(scheduler), thorough=True)
    SwitchTarget.switch["mode"] = "empty"
    result = detect_batch(["Synthetic text with nobody in it."], None, targets, thorough=True)[0]
    assert result.tiers_run == (1, 2, 3)
    assert monitor.block_reason() == BLOCK_FAILED
    assert {entry["checks"][0]["detector"] for entry in monitor.history} == {"gemma4-e4b", "gemma4-26b-a4b"}


# --------------------------------------------------------------------------------------------------------------------
# Periodic and per-document canaries
# --------------------------------------------------------------------------------------------------------------------
def test_periodic_tier1_canary_and_degraded_tracer(au_rules):
    now = {"t": NOW}
    tracer = CanaryTracer()
    monitor = healthy_monitor(targets=DetectionTargets(tracer=tracer), clock=lambda: now["t"])
    monitor.startup()
    calls = tracer.calls
    assert monitor.before_cloud_work() is None and tracer.calls == calls  # not due yet
    now["t"] += timedelta(minutes=31)
    tracer.mode = "empty"
    result = monitor.before_cloud_work()
    assert result.trigger == "periodic" and not result.passed
    assert monitor.block_reason() == BLOCK_FAILED
    tracer.mode = "healthy"
    assert monitor.before_cloud_work().trigger == "retry"  # re-checked at the next cloud-bound document
    assert monitor.block_reason() is None


def test_every_n_documents(au_rules):
    tracer = CanaryTracer()
    monitor = healthy_monitor(
        targets=DetectionTargets(tracer=tracer), settings=CanarySettings(interval_minutes=0, every_documents=3),
    )
    monitor.startup()
    assert monitor.before_cloud_work() is None and monitor.before_cloud_work() is None
    assert monitor.before_cloud_work().trigger == "documents"
    # The finders are due too; they are checked at their next residency (here: directly through the hook).
    assert monitor.after_acquire(SimpleNamespace(id="gemma4-e4b"), CanaryFinder(), False).trigger == "documents"
    assert monitor.after_acquire(SimpleNamespace(id="gemma4-e4b"), CanaryFinder(), False) is None


# --------------------------------------------------------------------------------------------------------------------
# The router: degraded state blocks cloud-bound work only, and is audited
# --------------------------------------------------------------------------------------------------------------------
def _monitor_for(h):
    monitor = healthy_monitor(recorder=lambda record: h.register.record_canary(record, now=NOW))
    h.router.canary = monitor
    return monitor


def test_degraded_gate_blocks_cloud_work_but_not_local_work_and_recovers(tmp_path, au_rules):
    h = harness(tmp_path)
    try:
        monitor = _monitor_for(h)
        assert monitor.startup().passed
        held = h.router.handle(request())
        assert held.route == "held" and held.reason == "OPERATOR_APPROVAL_REQUIRED"
        # A reload canary fails (the finder answers with nothing).
        monitor.after_acquire(SimpleNamespace(id="gemma4-26b-a4b"), CanaryFinder("empty"), True)
        blocked = h.router.handle(request("Ravi Example asks another synthetic question."))
        assert blocked.route == "blocked" and blocked.reason == BLOCK_FAILED
        assert h.fake.requests == []
        audit = sqlite3.connect(tmp_path / "register.db").execute(
            "SELECT event, reason_code FROM audit WHERE job_id=? ORDER BY audit_id", (blocked.job_id,),
        ).fetchall()
        assert ("DETECTOR_CANARY_FAILED", "DETECTOR_CANARY_FAILED") in audit
        assert audit[-1][0] == "REFUSED"
        local = h.router.handle(request("Synthetic local question.", model="themis/local:gemma4-e4b"))
        assert local.route == "local"
        assert h.router.health()["canary"]["state"] == "degraded"
        # The next reload canary passes: cloud-bound work flows again.
        monitor.after_acquire(SimpleNamespace(id="gemma4-26b-a4b"), CanaryFinder(), True)
        again = h.router.handle(request("Mara Lindahl asks a third synthetic question."))
        assert again.route == "held" and again.reason == "OPERATOR_APPROVAL_REQUIRED"
        runs = h.register.canary_runs()
        assert [run["trigger"] for run in runs][:3] == ["reload", "reload", "startup"]
        assert [run["passed"] for run in runs][:3] == [True, False, True]
        stored = json.dumps([dict(run) for run in runs])
        assert NAME not in stored and STATEMENT not in stored
    finally:
        h.cloud.close()


def test_a_canary_never_run_keeps_cloud_work_pending(tmp_path, au_rules):
    h = harness(tmp_path)
    try:
        monitor = _monitor_for(h)  # no start-up run and no scheduler hook to check the finders
        blocked = h.router.handle(request())
        assert blocked.route == "blocked" and blocked.reason == BLOCK_PENDING
        # The due tier-1 canary ran before the scan; the finders are still unchecked.
        assert monitor.status()["detectors"]["rules"]["status"] == "pass"
        assert monitor.status()["detectors"]["gemma4-e4b"]["status"] == "not-yet-run"
    finally:
        h.cloud.close()


def test_hooks_path_is_blocked_too(tmp_path, au_rules):
    h = harness(tmp_path)
    try:
        monitor = _monitor_for(h)
        monitor.startup()
        monitor.after_acquire(SimpleNamespace(id="gemma4-e4b"), CanaryFinder("crash"), True)
        boundary = ChatBoundary(request(), frozenset(), h.router.cloud_model)
        with pytest.raises(GateFailure) as caught:
            h.router._protect(boundary, "synthetic-probe", set())
        assert caught.value.code == BLOCK_FAILED
    finally:
        h.cloud.close()


def test_canary_register_rows_are_value_free_and_immutable(tmp_path):
    from themis_shield.register.sqlite_store import SqliteRegister
    register = SqliteRegister(tmp_path / "r.db", requester_identity="synthetic-requester")
    record = run_canary(DetectionTargets(tracer=CanaryTracer()), finders_skipped="replay").record()
    register.record_canary(record, now=NOW)
    assert register.canary_runs()[0]["passed"] is True
    for bad in (
        {**record, "trigger": "free text"},
        {**record, "checks": [{**record["checks"][0], "failed": [NAME]}]},
        {**record, "checks": [{**record["checks"][0], "status": "fail"}], "passed": True},
    ):
        with pytest.raises(ValueError):
            register.record_canary(bad, now=NOW)
    db = sqlite3.connect(tmp_path / "r.db")
    for statement in ("UPDATE canary_runs SET passed=0", "DELETE FROM canary_runs"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(statement)


def test_healthy_canary_reproduces_the_v021_golden_transcript(tmp_path, au_rules):
    """With a passing canary attached, the fixed router corpus gives exactly the v0.2.1 transcript."""
    from test_catalogues_off import GOLDEN
    from v021_golden import run_corpus

    def prepare(router):
        monitor = healthy_monitor(recorder=lambda record: router.register.record_canary(record, now=NOW))
        router.canary = monitor
        assert monitor.startup().passed

    runs = tmp_path / "runs"
    runs.mkdir()
    assert run_corpus(prepare, runs) == GOLDEN["cases"]


# --------------------------------------------------------------------------------------------------------------------
# Integration and the web app
# --------------------------------------------------------------------------------------------------------------------
def test_configure_attaches_the_monitor_and_the_hook():
    runtime = SimpleNamespace(load_listener=None)
    router = SimpleNamespace(
        targets=DetectionTargets(tracer=CanaryTracer(), llm_client=CanaryFinder()), runtime=runtime,
        register=None, clock=lambda: NOW,
    )
    canary_module.configure(router, {})
    assert isinstance(router.canary, CanaryMonitor) and runtime.load_listener == router.canary.after_acquire
    canary_module.configure(router, {"canary": {"enabled": False}})
    assert router.canary is None


def test_web_app_shows_and_reruns_the_canary(tmp_path, au_rules):
    from themis_shield.web.app import create_app
    h = harness(tmp_path)
    try:
        monitor = _monitor_for(h)
        monitor.startup()
        monitor.after_acquire(SimpleNamespace(id="gemma4-e4b"), CanaryFinder("empty"), True)
        app = create_app(h.router, token="synthetic-token")

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
                base_url="http://127.0.0.1:8742", headers={"Authorization": "Bearer synthetic-token"},
            ) as client:
                first = (await client.get("/api/canary")).json()
                rerun = (await client.post("/api/canary/run")).json()
                reasons = (await client.get("/api/reasons")).json()["reasons"]
                return first, rerun, reasons

        first, rerun, reasons = asyncio.run(run())
        assert first["state"] == "degraded" and first["cloud_blocked"] is True
        assert first["detectors"]["gemma4-e4b"]["failed"] == ["PERSON", "SENSITIVE_STATEMENT"]
        assert rerun["state"] == "healthy" and rerun["result"]["trigger"] == "manual"
        assert "detector canary" in reasons[BLOCK_FAILED]
        index = (Path(__file__).resolve().parents[1] / "themis_shield" / "web" / "static" / "index.html").read_text(
            encoding="utf-8")
        assert 'id="canary-band"' in index
    finally:
        h.cloud.close()


# --------------------------------------------------------------------------------------------------------------------
# Finer timings (no behaviour change)
# --------------------------------------------------------------------------------------------------------------------
def test_stage_timings_are_recorded_per_stage_and_finder():
    targets = DetectionTargets(tracer=CanaryTracer(), llm_client=CanaryFinder(), thorough=True)
    first, second = detect_batch([DOCUMENTS["statement"], DOCUMENTS["control"]], None, targets, thorough=True)
    stages = {"rules", "pii-tracer", "propagate-tier1", "allowlist-tier1", "gemma4-e4b", "gemma4-26b-a4b",
              "union-final", "propagate-final", "allowlist-final"}
    assert set(first.stage_timings_ms) == stages | {"gemma4-e4b:acquire", "gemma4-26b-a4b:acquire"}
    assert set(second.stage_timings_ms) == stages  # the residency is charged to the first document only
    assert all(value >= 0 for value in first.stage_timings_ms.values())
    assert set(first.timings_ms) == {1, 2, 3}  # per-tier totals unchanged
    assert first.log_summary()["stage_timings_ms"].keys() == first.stage_timings_ms.keys()
    assert sum(first.stage_timings_ms[stage] for stage in ("rules", "pii-tracer")) <= first.timings_ms[1] + 1


def test_stage_timings_do_not_change_findings_or_decisions():
    targets = DetectionTargets(tracer=CanaryTracer(), llm_client=CanaryFinder(), thorough=True)
    texts = list(DOCUMENTS.values())
    once, twice = (detect_batch(texts, None, targets, thorough=True) for _ in range(2))
    for a, b in zip(once, twice):
        assert a.findings == b.findings and a.policy_result == b.policy_result
        assert replace(a, timings_ms={}, stage_timings_ms={}) == replace(b, timings_ms={}, stage_timings_ms={})
