"""GPU memory held by attached external runtimes, counted as occupied.

A running Strata already shows in ``nvidia-smi``. But Windows (WDDM) trims an
idle process's GPU memory: measured on an 8 GB laptop GPU, ~6.8 GB while
loaded and ~1.6 GB when idle. Free VRAM can therefore look larger than what
Strata takes back on its next request, and the scheduler could start a
llama-server that will not fit.

While a configured external runtime is attached (its endpoint answers
``/health`` as a loaded Strata), its reservation (the catalogue entry's
``est_vram_gb``) is deducted from the card's total:

    free = min(reported free, total - sum of reservations)

An unknown reservation counts as the whole card. Entries that share an
endpoint count once, with the largest reservation. Probes are cached for a
couple of seconds; a down endpoint costs one refused loopback connection.
"""

from __future__ import annotations

from dataclasses import replace
import math
import threading
import time
from typing import Any, Callable, Iterable, Protocol

from ..inference.strata import DEFAULT_API_KEY_ENV, StrataConfig, StrataTarget, loopback_origin
from .resources import ResourceSnapshot


class ExternalHolder(Protocol):
    label: str

    def occupied_vram_gb(self) -> float | None:
        """GiB held while attached (``math.inf`` if unknown), else None."""


class StrataHolder:
    def __init__(
        self,
        label: str,
        target: StrataTarget,
        reserve_gb: float | None,
        *,
        cache_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.label = label
        self.target = target
        self.reserve_gb = reserve_gb
        self.cache_seconds = cache_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[float, bool] | None = None
        self.last_facts: dict[str, Any] = {}

    def attached(self) -> bool:
        with self._lock:
            now = self.clock()
            if self._cached is not None and now - self._cached[0] < self.cache_seconds:
                return self._cached[1]
        facts = self.target.probe()
        # Any loaded Strata at this endpoint holds memory, whichever model it serves.
        value = bool(facts["service_ok"] and facts["loaded"])
        with self._lock:
            self._cached = (self.clock(), value)
            self.last_facts = facts
        return value

    def occupied_vram_gb(self) -> float | None:
        if not self.attached():
            return None
        return self.reserve_gb if self.reserve_gb is not None else math.inf


def strata_holders(
    entries: Iterable[Any], *, api_key_env: str | None = DEFAULT_API_KEY_ENV,
) -> list[StrataHolder]:
    """One holder per configured Strata endpoint in the catalogue."""
    grouped: dict[str, list[Any]] = {}
    for entry in entries:
        if entry.runtime == "strata" and getattr(entry, "endpoint", None):
            grouped.setdefault(loopback_origin(entry.endpoint), []).append(entry)
    holders = []
    for group in grouped.values():
        reserves = [entry.est_vram_gb for entry in group]
        reserve = None if any(value is None for value in reserves) else max(reserves)
        first = group[0]
        holders.append(StrataHolder(
            "+".join(sorted(entry.id for entry in group)),
            StrataTarget(StrataConfig.from_entry(first, api_key_env=api_key_env)),
            reserve,
        ))
    return holders


class OccupancyAwareReader:
    """Wrap a resource reader; deduct memory held by attached external runtimes."""

    def __init__(
        self, base: Callable[[], ResourceSnapshot], holders: Iterable[ExternalHolder],
    ) -> None:
        self.base = base
        self.holders = list(holders)

    @property
    def gpu_index(self) -> int | None:
        return getattr(self.base, "gpu_index", None)

    def __call__(self) -> ResourceSnapshot:
        snapshot = self.base()
        held: list[str] = []
        total_hold = 0.0
        for holder in self.holders:
            amount = holder.occupied_vram_gb()
            if amount is not None:
                held.append(holder.label)
                total_hold += amount
        if not held:
            return snapshot
        total = snapshot.vram_total_gb
        if total is None:
            return replace(snapshot, external_holders=tuple(held))
        hold = min(total_hold, total)
        free = snapshot.vram_free_gb
        if free is not None:
            free = max(0.0, min(free, total - hold))
        return replace(
            snapshot, vram_free_gb=free,
            external_vram_gb=round(hold, 3), external_holders=tuple(held),
        )
