"""DD-35: a slow GPU probe is retried with longer, bounded timeouts before the stage fails, and a probe that never
answers fails closed with its own kind (``stage-failed:gpu-probe``), not ``target-unavailable``.

The probe is faked at ``subprocess.run`` inside ``runtime/resources.py``: no ``nvidia-smi`` is run, nothing sleeps.
"""

from __future__ import annotations

from dataclasses import replace
import json
import subprocess
from types import SimpleNamespace

import pytest

from themis_shield.detectors.contracts import failure_code
from themis_shield.inference import GpuProbeTimeout, RuntimeFailure, TargetUnavailable
from themis_shield.runtime import resources
from themis_shield.runtime.catalogue import Catalogue
from themis_shield.runtime.resources import DEFAULT_PROBE_TIMEOUTS, ResourceReader
from themis_shield.runtime.scheduler import Scheduler
from themis_shield.runtime.settings import RuntimeSettings


class FakeProbe:
    """``subprocess.run`` stand-in: per attempt, "slow" (times out), "ok", "missing" (no binary) or "fails"."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.timeouts = []

    def __call__(self, command, **kwargs):
        assert command[0] == "nvidia-smi"
        self.timeouts.append(kwargs["timeout"])
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if outcome == "slow":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "missing":
            raise FileNotFoundError("nvidia-smi")
        if outcome == "fails":
            return SimpleNamespace(returncode=9, stdout="")
        return SimpleNamespace(returncode=0, stdout="8192, 0\n")


@pytest.fixture
def probe(monkeypatch):
    def install(*outcomes):
        fake = FakeProbe(*outcomes)
        monkeypatch.setattr(resources.subprocess, "run", fake)
        return fake
    return install


class Target:
    load_ms = 1.0
    last_timings: dict = {}

    def __init__(self, entry, events):
        self.entry, self.events, self.resident = entry, events, False

    def start(self):
        self.events.append(("start", self.entry.id))
        self.resident = True

    def stop(self):
        self.resident = False

    def health(self):
        return self.resident


def _fixed_ram(reader):
    # The GPU numbers come from the (faked) probe; RAM and commit are fixed so the test does not read this machine.
    return lambda: replace(reader(), ram_available_gb=30, ram_total_gb=32, ram_used_gb=2, commit_used_gb=10,
                           commit_limit_gb=64)


def _scheduler(tmp_path, reader):
    events = []
    reader = _fixed_ram(reader)
    scheduler = Scheduler(
        Catalogue(), lambda entry: Target(entry, events), reader, lease_path=tmp_path / "lease.lock",
        idle_seconds=600, memory_settle_seconds=0, queue_timeout=2,
    )
    return scheduler, events


def test_slow_then_answering_probe_is_retried_with_a_longer_timeout_and_succeeds(tmp_path, probe):
    fake = probe("slow", "ok")
    snapshot = ResourceReader(0)()
    assert fake.timeouts == [3.0, 10.0]
    assert snapshot.gpu_error is None and snapshot.vram_total_gb == 8.0 and snapshot.vram_free_gb == 8.0
    scheduler, events = _scheduler(tmp_path, ResourceReader(0))
    probe("slow", "slow", "ok")
    try:
        with scheduler.acquire("finder-fast"):
            pass
    finally:
        scheduler.close()
    assert [event[0] for event in events] == ["start"]


def test_always_slow_probe_fails_closed_with_its_own_kind_after_the_bounded_retries(tmp_path, probe):
    fake = probe("slow")
    snapshot = ResourceReader(0)()
    assert fake.timeouts == list(DEFAULT_PROBE_TIMEOUTS) == [3.0, 10.0, 20.0]
    assert snapshot.vram_total_gb is None and snapshot.gpu_error == "gpu_probe_timeout"
    scheduler, events = _scheduler(tmp_path, ResourceReader(0))
    try:
        with pytest.raises(GpuProbeTimeout) as raised:
            with scheduler.acquire("finder-fast"):
                pytest.fail("a probe that never answers must hold the request")
    finally:
        scheduler.close()
    assert events == []  # nothing launched
    # Still a refusal (TargetUnavailable: never a fallback), with its own fixed kind.
    assert isinstance(raised.value, TargetUnavailable)
    assert failure_code(raised.value) == "stage-failed:gpu-probe"


def test_missing_binary_is_not_retried_and_behaves_as_before(tmp_path, probe):
    fake = probe("missing")
    snapshot = ResourceReader(0)()
    assert fake.timeouts == [3.0] and snapshot.gpu_error == "gpu_probe_unavailable"
    scheduler, events = _scheduler(tmp_path, ResourceReader(0))
    try:
        with pytest.raises(TargetUnavailable) as raised:
            with scheduler.acquire("finder-fast"):
                pytest.fail("no GPU probe must hold the request")
    finally:
        scheduler.close()
    assert events == [] and not isinstance(raised.value, GpuProbeTimeout)
    assert failure_code(raised.value) == "stage-failed:target-unavailable"


def test_a_failing_probe_is_not_retried(probe):
    fake = probe("fails")
    assert ResourceReader(0)().gpu_error == "gpu_probe_unavailable" and fake.timeouts == [3.0]


def test_probe_timeouts_are_configurable_in_the_runtime_settings_with_safe_bounds(tmp_path, probe):
    assert RuntimeSettings().gpu_probe_timeouts_seconds == (3.0, 10.0, 20.0)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"gpu_probe_timeouts_seconds": [2, 5]}), encoding="utf-8")
    settings = RuntimeSettings.load(path)
    assert settings.gpu_probe_timeouts_seconds == (2.0, 5.0)
    from themis_shield.runtime.settings import resource_reader
    reader = resource_reader(settings, Catalogue())
    fake = probe("slow")
    assert reader().gpu_error == "gpu_probe_timeout" and fake.timeouts == [2.0, 5.0]
    for bad in ([], [0], [-1], [121], [1, 2, 3, 4, 5, 6], ["3"], [True], 3, None):
        with pytest.raises(ValueError):
            RuntimeSettings(gpu_probe_timeouts_seconds=bad)
        with pytest.raises(ValueError):
            ResourceReader(0, probe_timeouts=bad)


def test_runtime_failure_hierarchy_is_unchanged_for_callers():
    # Every caller that catches TargetUnavailable or RuntimeFailure still catches the probe timeout.
    assert issubclass(GpuProbeTimeout, TargetUnavailable) and issubclass(GpuProbeTimeout, RuntimeFailure)
