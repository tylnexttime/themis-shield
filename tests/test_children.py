"""Orphaned llama-server prevention: pidfile, verified startup reaper, Job Object.

Fake children are short Python sleepers; no model, GPU or llama.cpp needed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from themis_shield.runtime.children import ChildRegistry, kill_on_close_job


def _sleeper(seconds: int = 120) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _dead_owner() -> tuple[int, float]:
    """A pid/creation-time pair for an owner that has exited."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    created = psutil.Process(finished.pid).create_time() if psutil.pid_exists(finished.pid) else 1.0
    finished.wait(timeout=30)
    return finished.pid, created


def _write_stale(state: Path, child: subprocess.Popen, *, owner=None, **overrides) -> Path:
    owner_pid, owner_time = owner or _dead_owner()
    process = psutil.Process(child.pid)
    record = {
        "pid": child.pid, "create_time": process.create_time(), "exe_name": process.name(),
        "owner_pid": owner_pid, "owner_create_time": owner_time, "started_utc": time.time(),
    }
    record.update(overrides)
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"children-{owner_pid}.json"
    path.write_text(json.dumps({
        "version": 1, "owner_pid": owner_pid, "owner_create_time": owner_time,
        "children": [record],
    }), encoding="utf-8")
    return path


def _gone(proc: subprocess.Popen, timeout: float = 15) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _cleanup(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=30)


def test_stale_child_of_a_dead_owner_is_reaped(tmp_path):
    child = _sleeper()
    try:
        path = _write_stale(tmp_path / "state", child)
        reaped = ChildRegistry(tmp_path / "state", use_job_object=False).reap_stale()
        assert reaped == [child.pid]
        assert _gone(child)
        assert not path.exists()
    finally:
        _cleanup(child)


def test_reused_pid_with_a_different_creation_time_is_never_killed(tmp_path):
    child = _sleeper()
    try:
        created = psutil.Process(child.pid).create_time()
        path = _write_stale(tmp_path / "state", child, create_time=created - 1000)
        assert ChildRegistry(tmp_path / "state", use_job_object=False).reap_stale() == []
        assert child.poll() is None
        assert not path.exists()  # the stale record is discarded, the process left alone
    finally:
        _cleanup(child)


def test_matching_pid_with_a_different_executable_is_never_killed(tmp_path):
    child = _sleeper()
    try:
        _write_stale(tmp_path / "state", child, exe_name="llama-server.exe")
        assert ChildRegistry(tmp_path / "state", use_job_object=False).reap_stale() == []
        assert child.poll() is None
    finally:
        _cleanup(child)


def test_children_of_a_live_owner_are_left_alone(tmp_path):
    child = _sleeper()
    owner = _sleeper()
    try:
        owner_proc = psutil.Process(owner.pid)
        path = _write_stale(tmp_path / "state", child, owner=(owner.pid, owner_proc.create_time()))
        assert ChildRegistry(tmp_path / "state", use_job_object=False).reap_stale() == []
        assert child.poll() is None
        assert path.exists()
    finally:
        _cleanup(child)
        _cleanup(owner)


def test_track_and_untrack_maintain_the_pidfile(tmp_path):
    registry = ChildRegistry(tmp_path / "state", use_job_object=False)
    child = _sleeper()
    try:
        record = registry.track(child.pid)
        assert record is not None and record.pid == child.pid
        document = json.loads(registry.path.read_text(encoding="utf-8"))
        assert document["owner_pid"] == os.getpid()
        assert [item["pid"] for item in document["children"]] == [child.pid]
        # The current process is alive, so its own records are never reaped.
        assert ChildRegistry(tmp_path / "state", use_job_object=False).reap_stale() == []
        registry.untrack(child.pid)
        assert not registry.path.exists()
    finally:
        _cleanup(child)


def test_corrupt_pidfile_is_discarded_without_killing(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "children-12345.json").write_text("{not json", encoding="utf-8")
    assert ChildRegistry(state, use_job_object=False).reap_stale() == []
    assert not (state / "children-12345.json").exists()


GATE_LIKE_PARENT = r"""
import subprocess, sys, time
from themis_shield.runtime.children import ChildRegistry
registry = ChildRegistry(sys.argv[1], use_job_object=sys.argv[2] == "job")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
registry.track(child.pid)
print(child.pid, flush=True)
time.sleep(120)
"""


def _hard_killed_parent(state: Path, mode: str) -> psutil.Process:
    parent = subprocess.Popen(
        [sys.executable, "-c", GATE_LIKE_PARENT, str(state), mode],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    child_pid = int(parent.stdout.readline())
    child = psutil.Process(child_pid)
    parent.kill()  # TerminateProcess / SIGKILL: no cleanup code runs
    parent.wait(timeout=30)
    return child


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects only")
def test_job_object_kills_children_when_the_gate_is_hard_killed(tmp_path):
    assert kill_on_close_job() is not None
    child = _hard_killed_parent(tmp_path / "state", "job")
    try:
        psutil.wait_procs([child], timeout=15)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if child.is_running():
            child.kill()


def test_startup_reaper_recovers_after_a_hard_kill_without_a_job(tmp_path):
    state = tmp_path / "state"
    child = _hard_killed_parent(state, "nojob")
    try:
        assert child.is_running()  # orphaned and still holding its resources
        reaped = ChildRegistry(state, use_job_object=False).reap_stale()
        assert reaped == [child.pid]
        psutil.wait_procs([child], timeout=15)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert list(state.glob("children-*.json")) == []
    finally:
        if child.is_running():
            child.kill()


def test_llamacpp_target_tracks_and_untracks_its_server(tmp_path):
    from themis_shield.inference.llamacpp import LlamaCppTarget
    from themis_shield.runtime.catalogue import BUILTINS

    entry = BUILTINS[0]
    (tmp_path / entry.file).write_bytes(b"SYNTHETIC-GGUF-STUB")
    registry = ChildRegistry(tmp_path / "state")
    target = LlamaCppTarget(
        entry, models_dir=tmp_path, logs_dir=tmp_path / "logs",
        startup_timeout=10, retry_pause=0.01, stop_timeout=5, poll_interval=0.02,
        command_prefix=(sys.executable, str(Path(__file__).with_name("fake_llama_server.py"))),
        child_registry=registry, timeout_seconds=2,
    )
    try:
        target.start()
        assert [record.pid for record in registry.tracked] == [target.pid]
        assert registry.path.exists()
    finally:
        target.stop()
    assert registry.tracked == ()
    assert not registry.path.exists()
