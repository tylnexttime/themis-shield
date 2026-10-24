"""Resource snapshots and a hardware-only SHA-256 device fingerprint.

Windows psutil.virtual_memory().used is the Windows CommitTotal counter;
total is CommitLimit on that platform. Physical available remains available.
Elsewhere commit is unknown: swap usage must not be mislabelled as commit.
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
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeFailure("GPU resource probe unavailable") from None
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or len(lines) != 1:
        raise RuntimeFailure("GPU resource probe unavailable")
    return lines[0]


class ResourceReader:
    def __init__(self, gpu_index: int = 0) -> None:
        if type(gpu_index) is not int or gpu_index < 0:
            raise ValueError("invalid GPU index")
        self.gpu_index = gpu_index

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
            values = _nvidia("memory.total,memory.used", self.gpu_index).split(",")
            if len(values) != 2:
                raise ValueError
            total_mib, used_mib = (float(value.strip()) for value in values)
            if not 0 <= used_mib <= total_mib or total_mib <= 0:
                raise ValueError
            total = total_mib / 1024
            free = (total_mib - used_mib) / 1024
        except (RuntimeFailure, ValueError):
            error = "gpu_probe_unavailable"
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
    gpu_name = _nvidia("name", gpu_index).strip()
    return DeviceIdentity(
        cpu_name(), gpu_name,
        round(snapshot.ram_total_gb * GIB),
        round(snapshot.vram_total_gb * GIB),
    )
