"""Fixed synthetic workload and device-keyed calibration.

Default store: %LOCALAPPDATA%/themis-shield/calibration.json
No prompt, generated content, CPU/GPU names, hostname or username is stored.
Peaks are sampled, not exact high-water marks; scope and sample period are
recorded. Short-workload peaks are not safe full-context admission budgets.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable

import psutil

from ..inference import RuntimeFailure
from ..inference.json_output import strict_loads
from .catalogue import ModelEntry
from .resources import DeviceIdentity, GIB, ResourceSnapshot
from .scheduler import Scheduler

WORKLOAD_VERSION = "synthetic-m3-v1"
SYNTHETIC_PROMPT = (
    "This is synthetic calibration data, not a real document. "
    "Continue with at least 128 short synthetic words. Do not summarise. "
    "Use only the words alpha, beta, gamma and delta.\n"
    + "Synthetic unit alpha beta gamma delta.\n" * 128
)


def model_signature(entry: ModelEntry) -> str:
    # Configuration identity, not an original-value hash.
    value = {
        "runtime": entry.runtime, "file": entry.file,
        "ctx": entry.ctx, "args": list(entry.args), "mode": entry.mode,
    }
    # Back-end choices change what is measured. They are added only when set,
    # so the signatures of existing entries are unchanged.
    for key in ("engine_path", "planner"):
        if getattr(entry, key, None):
            value[key] = getattr(entry, key)
    if getattr(entry, "engine_args", ()):
        value["engine_args"] = list(entry.engine_args)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class CalibrationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "devices": {}}
        if self.path.stat().st_size > 8 * 1024 * 1024:
            raise RuntimeFailure("calibration store exceeds bound")
        data = strict_loads(self.path.read_bytes())
        if (
            not isinstance(data, dict) or set(data) != {"version", "devices"}
            or data["version"] != 1 or not isinstance(data["devices"], dict)
        ):
            raise RuntimeFailure("calibration store malformed")
        return data

    def lookup(self, fingerprint: str, entry: ModelEntry) -> dict[str, Any] | None:
        try:
            key = entry.id if entry.mode == "full" else entry.id + "#mmap"
            record = self._read()["devices"].get(fingerprint, {}).get(key)
            if (
                not isinstance(record, dict)
                or record.get("model_signature") != model_signature(entry)
                or record.get("workload_version") != WORKLOAD_VERSION
            ):
                return None
            return record
        except (RuntimeFailure, OSError, AttributeError):
            return None

    def save(self, fingerprint: str, entry: ModelEntry, record: dict[str, Any]) -> None:
        import re

        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("invalid calibration fingerprint")
        data = self._read()
        key = entry.id if entry.mode == "full" else entry.id + "#mmap"
        data["devices"].setdefault(fingerprint, {})[key] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=True, allow_nan=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class PeakSampler:
    def __init__(
        self,
        reader: Callable[[], ResourceSnapshot],
        pid_reader: Callable[[], int | None],
        interval: float = 0.2,
    ) -> None:
        if interval <= 0:
            raise ValueError("invalid calibration sample interval")
        self.reader = reader
        self.pid_reader = pid_reader
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict[str, float | None]] = []
        self.failed = False
        self._lock = threading.Lock()
        self.thread = threading.Thread(
            target=self._run, name="themis-calibration-sampler", daemon=True,
        )

    def _sample(self) -> None:
        try:
            snapshot = self.reader()
            if snapshot.vram_total_gb is None or snapshot.vram_free_gb is None:
                raise RuntimeFailure("calibration GPU sample unavailable")
            private = rss = None
            pid = self.pid_reader()
            if pid is not None:
                try:
                    memory = psutil.Process(pid).memory_info()
                    rss = memory.rss / GIB
                    value = getattr(memory, "private", None)
                    private = value / GIB if value is not None else None
                except psutil.NoSuchProcess:
                    pass
                except (psutil.Error, OSError):
                    self.failed = True
            sample = {
                "vram": snapshot.vram_total_gb - snapshot.vram_free_gb,
                "ram": snapshot.ram_used_gb,
                "commit": snapshot.commit_used_gb,
                "private": private,
                "rss": rss,
            }
            with self._lock:
                self.samples.append(sample)
        except RuntimeFailure:
            self.failed = True

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self._sample()

    def start(self) -> None:
        self._sample()
        self.thread.start()

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join()
        self._sample()
        if self.failed or not self.samples:
            raise RuntimeFailure("calibration resource sampling incomplete")

        def peak(key: str) -> float | None:
            values = [sample[key] for sample in self.samples if sample[key] is not None]
            return max(values) if values else None

        return {
            "peak_vram_gb": peak("vram"),
            "peak_system_ram_gb": peak("ram"),
            "peak_system_commit_gb": peak("commit"),
            "peak_process_private_ram_gb": peak("private"),
            "peak_process_rss_gb": peak("rss"),
            "memory_units": "GiB",
            "peak_scope": "sampled whole-GPU/system totals plus owned-process memory",
            "sample_period_seconds": self.interval,
            "samples": len(self.samples),
        }


def calibrate(
    scheduler: Scheduler,
    model_id: str,
    store: CalibrationStore,
    identity: DeviceIdentity,
    *,
    sample_interval: float = 0.2,
    mode: str | None = None,
) -> dict[str, Any]:
    entry = scheduler.catalogue.get(model_id)
    fingerprint = identity.fingerprint
    # Force a cold load; existing clients must not submit work during calibration.
    scheduler.stop()
    sampler = PeakSampler(
        scheduler.resource_reader, scheduler.sampling_pid, sample_interval,
    )
    sampler.start()
    succeeded = False
    try:
        with scheduler.acquire(model_id, mode=mode) as target:
            entry = target.entry
            load_seconds = target.load_ms / 1000
            output = target.chat(
                [{"role": "user", "content": SYNTHETIC_PROMPT}],
                stream=False, max_tokens=128,
            )
            if not isinstance(output, dict):
                raise RuntimeFailure("calibration response malformed")
            timings = output["timings"]
            prompt_speed = timings.get("prompt_per_second")
            gen_speed = timings.get("predicted_per_second")
            if any(
                type(value) not in (int, float)
                or not math.isfinite(value) or value <= 0
                for value in (prompt_speed, gen_speed)
            ):
                raise RuntimeFailure("calibration timings unavailable")
            prompt_n = timings.get("prompt_n")
            gen_n = timings.get("predicted_n")
            if (
                type(prompt_n) is not int or prompt_n <= 0
                or type(gen_n) is not int or not 1 <= gen_n <= 128
            ):
                raise RuntimeFailure("calibration token counts unavailable")
            succeeded = True
    finally:
        try:
            peaks = sampler.stop()
        finally:
            scheduler.stop()
    if not succeeded:
        raise RuntimeFailure("calibration incomplete")
    record = {
        "model_signature": model_signature(entry),
        "workload_version": WORKLOAD_VERSION,
        "measured_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prompt_tok_s": prompt_speed,
        "gen_tok_s": gen_speed,
        "prompt_tokens": prompt_n,
        "gen_tokens": gen_n,
        "load_seconds": load_seconds,
        "max_output_tokens": 128,
        **peaks,
    }
    store.save(fingerprint, entry, record)
    scheduler.catalogue = scheduler.catalogue.with_calibration(store, fingerprint)
    return {"device_fingerprint": fingerprint, "model_id": entry.id, "mode": entry.mode, **record}
