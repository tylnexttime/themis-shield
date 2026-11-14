"""Resource snapshots and a hardware-only SHA-256 device fingerprint.

Windows psutil.virtual_memory().used is the Windows CommitTotal counter;
total is CommitLimit on that platform. Physical available remains available.
Elsewhere commit is unknown: swap usage must not be mislabelled as commit.

DD-35: on a busy machine ``nvidia-smi`` can take longer than a few seconds. A probe that times out is retried with
longer, bounded timeouts (``probe_timeouts``, default 3 s, then 10 s, then 20 s; ``gpu_probe_timeouts_seconds`` in
the runtime settings). Only a timeout is retried. If every attempt times out the snapshot says so
(``gpu_error = "gpu_probe_timeout"``) and the scheduler refuses with ``GpuProbeTimeout``
(``stage-failed:gpu-probe``). A missing binary, a non-zero exit or an unreadable answer is not retried and is
reported as before (``gpu_probe_unavailable``): both fail closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any

import psutil

from ..inference import RuntimeFailure

GIB = 1024 ** 3
DEFAULT_PROBE_TIMEOUTS = (3.0, 10.0, 20.0)
MAX_PROBE_ATTEMPTS = 5
MAX_PROBE_TIMEOUT = 120.0
GPU_PROBE_TIMEOUT = "gpu_probe_timeout"
GPU_PROBE_UNAVAILABLE = "gpu_probe_unavailable"


class _ProbeTimedOut(RuntimeFailure):
    """One ``nvidia-smi`` attempt ran past its timeout (internal: the reader retries it)."""


def validate_probe_timeouts(value) -> tuple[float, ...]:
    """Validate a probe timeout schedule: 1 to 5 attempts, each above 0 and at most 120 seconds."""
    if (
        not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_PROBE_ATTEMPTS
        or not all(
            type(item) in (int, float) and 0 < item <= MAX_PROBE_TIMEOUT for item in value
        )
    ):
        raise ValueError("invalid GPU probe timeouts")
    return tuple(float(item) for item in value)


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    vram_total_gb: float | None
    vram_free_gb: float | None
    ram_available_gb: float
    ram_total_gb: float
    ram_used_gb: float
    commit_used_gb: float | None
    commit_limit_gb: float | None
    gpu_error: str | None = None
    # GPU memory held by attached external runtimes (see runtime/occupancy.py),
    # already deducted from vram_free_gb.
    external_vram_gb: float = 0.0
    external_holders: tuple[str, ...] = ()

    def record(self) -> dict[str, Any]:
        record = asdict(self)
        record["external_holders"] = list(self.external_holders)
        return record


def _nvidia(query: str, gpu_index: int, timeout: float = 3) -> str:
    try:
        result = subprocess.run(
            [
                "nvidia-smi", f"--id={gpu_index}", f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", timeout=timeout,
            check=False, shell=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except subprocess.TimeoutExpired:
        raise _ProbeTimedOut("GPU resource probe timed out") from None
    except OSError:
        raise RuntimeFailure("GPU resource probe unavailable") from None
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or len(lines) != 1:
        raise RuntimeFailure("GPU resource probe unavailable")
    return lines[0]


def _nvidia_bounded(query: str, gpu_index: int, timeouts) -> str:
    """``_nvidia`` with escalating timeouts: only a timeout is retried; the last timeout is re-raised."""
    for number, timeout in enumerate(timeouts):
        try:
            return _nvidia(query, gpu_index, timeout)
        except _ProbeTimedOut:
            if number == len(timeouts) - 1:
                raise
    raise _ProbeTimedOut("GPU resource probe timed out")


class ResourceReader:
    def __init__(self, gpu_index: int = 0, *, probe_timeouts: tuple[float, ...] = DEFAULT_PROBE_TIMEOUTS) -> None:
        if type(gpu_index) is not int or gpu_index < 0:
            raise ValueError("invalid GPU index")
        self.gpu_index = gpu_index
        self.probe_timeouts = validate_probe_timeouts(probe_timeouts)

    def __call__(self) -> ResourceSnapshot:
        try:
            memory = psutil.virtual_memory()
            # psutil does not expose physical total/used consistently through
            # virtual_memory().total on Windows; sum physical memory separately.
            if os.name == "nt":
                physical_total = psutil._psplatform.cext.virtual_mem()[0]
                ram_total = physical_total / GIB
                commit_used = memory.used / GIB
                commit_limit = memory.total / GIB
            else:
                ram_total = memory.total / GIB
                commit_used = commit_limit = None
            available = memory.available / GIB
        except (psutil.Error, OSError, AttributeError, IndexError):
            raise RuntimeFailure("RAM resource probe unavailable") from None
        total = free = None
        error = None
        try:
            values = _nvidia_bounded("memory.total,memory.used", self.gpu_index, self.probe_timeouts).split(",")
            if len(values) != 2:
                raise ValueError
            total_mib, used_mib = (float(value.strip()) for value in values)
            if not 0 <= used_mib <= total_mib or total_mib <= 0:
                raise ValueError
            total = total_mib / 1024
            free = (total_mib - used_mib) / 1024
        except _ProbeTimedOut:
            error = GPU_PROBE_TIMEOUT
        except (RuntimeFailure, ValueError):
            error = GPU_PROBE_UNAVAILABLE
        return ResourceSnapshot(
            total, free, available, ram_total,
            max(0.0, ram_total - available),
            commit_used, commit_limit, error,
        )


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    cpu_name: str
    gpu_name: str
    total_ram_bytes: int
    total_vram_bytes: int

    @property
    def fingerprint(self) -> str:
        if (
            not self.cpu_name.strip() or not self.gpu_name.strip()
            or self.total_ram_bytes <= 0 or self.total_vram_bytes <= 0
        ):
            raise RuntimeFailure("device identity incomplete")
        canonical = json.dumps({
            "cpu": " ".join(self.cpu_name.split()),
            "gpu": " ".join(self.gpu_name.split()),
            "ram": self.total_ram_bytes,
            "vram": self.total_vram_bytes,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def cpu_name() -> str:
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                "HARDWARE/DESCRIPTION/System/CentralProcessor/0".replace("/", "\\"),
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            raise RuntimeFailure("CPU identity unavailable") from None
    proc = Path("/proc/cpuinfo")
    if proc.is_file():
        for line in proc.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    value = platform.processor()
    if not value:
        raise RuntimeFailure("CPU identity unavailable")
    return value


def read_identity(gpu_index: int = 0) -> DeviceIdentity:
    snapshot = ResourceReader(gpu_index)()
    if snapshot.vram_total_gb is None:
        raise RuntimeFailure("device GPU identity unavailable")
    gpu_name = _nvidia_bounded("name", gpu_index, DEFAULT_PROBE_TIMEOUTS).strip()
    return DeviceIdentity(
        cpu_name(), gpu_name,
        round(snapshot.ram_total_gb * GIB),
        round(snapshot.vram_total_gb * GIB),
    )
