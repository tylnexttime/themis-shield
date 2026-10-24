"""Owned child processes (llama-server): pidfile, startup reaper and Windows Job Object.

A hard kill of the gate (Task Manager, ``kill -9``, a crash) used to leave its
llama-server children alive and holding GPU memory; the next start then failed
its finder stage. Three layers prevent that:

1. **Windows Job Object** (when available): the gate process owns one job with
   ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; every child is assigned to it right
   after launch. When the gate dies for any reason, Windows closes the job
   handle and terminates the children.
2. **Pidfile**: each tracked child is recorded in
   ``<state dir>/children-<owner pid>.json`` with its pid, process creation
   time and executable name, plus the owner's pid and creation time.
3. **Startup reaper**: at runtime start, records whose owner is gone (pid not
   running, or running with a different creation time) are examined. A child
   is terminated only if a process with the recorded pid exists **and** its
   creation time **and** executable name both match the record. Nothing is
   ever killed by image name alone; a reused pid never matches.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterable

import psutil

CREATE_TIME_TOLERANCE = 0.01  # seconds; psutil reports the same float for one process
_RECORD = re.compile(r"children-([0-9]{1,10})\.json")


@dataclass(frozen=True, slots=True)
class ChildRecord:
    pid: int
    create_time: float
    exe_name: str
    owner_pid: int
    owner_create_time: float
    started_utc: float


def _identity(pid: int) -> tuple[float, str] | None:
    try:
        process = psutil.Process(pid)
        return process.create_time(), process.name()
    except (psutil.Error, OSError):
        return None


def _matches(pid: int, create_time: float, exe_name: str) -> psutil.Process | None:
    try:
        process = psutil.Process(pid)
        if (
            abs(process.create_time() - create_time) <= CREATE_TIME_TOLERANCE
            and process.name().casefold() == exe_name.casefold()
        ):
            return process
    except (psutil.Error, OSError):
        pass
    return None


def _owner_alive(pid: int, create_time: float) -> bool:
    try:
        process = psutil.Process(pid)
        return (
            abs(process.create_time() - create_time) <= CREATE_TIME_TOLERANCE
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.Error, OSError):
        return False


def terminate_tree(process: psutil.Process, timeout: float = 5.0) -> None:
    """Terminate a verified process and its descendants; kill what remains."""
    try:
        victims = [*process.children(recursive=True), process]
    except (psutil.Error, OSError):
        victims = [process]
    for victim in victims:
        try:
            victim.terminate()
        except (psutil.Error, OSError):
            pass
    _, alive = psutil.wait_procs(victims, timeout=timeout)
    for victim in alive:
        try:
            victim.kill()
        except (psutil.Error, OSError):
            pass
    psutil.wait_procs(alive, timeout=timeout)


class _WindowsJob:
    """A kill-on-close Job Object owned by this process (Windows only)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        extended_limit_information = 9
        if not kernel32.SetInformationJobObject(
            handle, extended_limit_information, ctypes.byref(info), ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        # The handle is deliberately never closed while the process lives:
        # process exit (clean or not) closes it and kills every assigned child.
        self.handle = handle

    def assign(self, pid: int) -> bool:
        process_set_quota, process_terminate = 0x0100, 0x0001
        handle = self._kernel32.OpenProcess(process_set_quota | process_terminate, False, pid)
        if not handle:
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self.handle, handle))
        finally:
            self._kernel32.CloseHandle(handle)


_job_lock = threading.Lock()
_job: _WindowsJob | None = None
_job_failed = False


def kill_on_close_job() -> _WindowsJob | None:
    """The process-wide kill-on-close job, or None off Windows / if unavailable."""
    global _job, _job_failed
    if os.name != "nt":
        return None
    with _job_lock:
        if _job is None and not _job_failed:
            try:
                _job = _WindowsJob()
            except (OSError, AttributeError):
                _job_failed = True
        return _job


class ChildRegistry:
    """Track owned children in a pidfile and reap stale ones from dead owners."""

    def __init__(self, state_dir: Path | str, *, use_job_object: bool = True) -> None:
        self.state_dir = Path(state_dir)
        self.use_job_object = use_job_object
        self._lock = threading.Lock()
        owner = psutil.Process(os.getpid())
        self.owner_pid = owner.pid
        self.owner_create_time = owner.create_time()
        self.path = self.state_dir / f"children-{self.owner_pid}.json"
        self._records: dict[int, ChildRecord] = {}

    # -- pidfile -------------------------------------------------------------

    def _write(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self._records:
            self.path.unlink(missing_ok=True)
            return
        payload = {
            "version": 1,
            "owner_pid": self.owner_pid,
            "owner_create_time": self.owner_create_time,
            "children": [asdict(record) for record in self._records.values()],
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, self.path)

    def track(self, pid: int) -> ChildRecord | None:
        """Record a freshly launched child and put it in the kill-on-close job."""
        identity = _identity(pid)
        if identity is None:
            return None
        create_time, exe_name = identity
        record = ChildRecord(
            pid, create_time, exe_name, self.owner_pid, self.owner_create_time, time.time(),
        )
        if self.use_job_object:
            job = kill_on_close_job()
            if job is not None:
                job.assign(pid)
        with self._lock:
            self._records[pid] = record
            self._write()
        return record

    def untrack(self, pid: int | None) -> None:
        if pid is None:
            return
        with self._lock:
            if self._records.pop(pid, None) is not None:
                self._write()

    @property
    def tracked(self) -> tuple[ChildRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    # -- startup reaper ------------------------------------------------------

    def _documents(self) -> Iterable[tuple[Path, dict[str, Any]]]:
        if not self.state_dir.is_dir():
            return
        for path in sorted(self.state_dir.glob("children-*.json")):
            if not _RECORD.fullmatch(path.name) or path == self.path:
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = None
            yield path, document

    def reap_stale(self, *, timeout: float = 5.0) -> list[int]:
        """Terminate verified children of dead owners; return the reaped pids."""
        reaped: list[int] = []
        for path, document in self._documents():
            if not isinstance(document, dict):
                path.unlink(missing_ok=True)
                continue
            owner_pid = document.get("owner_pid")
            owner_time = document.get("owner_create_time")
            if (
                type(owner_pid) is int and isinstance(owner_time, (int, float))
                and _owner_alive(owner_pid, float(owner_time))
            ):
                continue  # another live gate instance owns these children
            for item in document.get("children", ()):
                try:
                    pid, created, name = int(item["pid"]), float(item["create_time"]), str(item["exe_name"])
                except (KeyError, TypeError, ValueError):
                    continue
                process = _matches(pid, created, name)
                if process is not None:
                    terminate_tree(process, timeout=timeout)
                    reaped.append(pid)
            path.unlink(missing_ok=True)
        return reaped
