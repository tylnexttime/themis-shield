"""FIFO, bounded, one-resident-model GPU scheduler.

The 8 GB policy is deliberately stricter than opportunistic VRAM arithmetic:
switching models always unloads the old owned model, even if both might fit.
Memory is re-read after unloading. Probe failure or insufficient capacity
holds the request locally; there is no alternate model or cloud fallback.

External runtimes (an attached Strata) are not admitted against free memory:
their memory is the user's and is already counted as occupied by the
resource reader. An optional launch planner (Marian) may choose a llama.cpp
entry's arguments; if the planned configuration does not fit, the
catalogue's arguments are used and the reason is recorded.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterator

from ..inference import (
    InferenceRequest, LocalInferenceTarget, NotEnoughMemory,
    RuntimeFailure, TargetUnavailable,
)
from .catalogue import Catalogue, ModelEntry
from .lease import DeviceLease
from .resources import ResourceSnapshot


class Scheduler:
    def __init__(
        self,
        catalogue: Catalogue,
        target_factory: Callable[[ModelEntry], LocalInferenceTarget],
        resource_reader: Callable[[], ResourceSnapshot],
        *,
        lease_path: Path,
        role_models: dict[str, str] | None = None,
        idle_seconds: float = 600,
        queue_limit: int = 32,
        queue_timeout: float = 900,
        vram_margin_gb: float = 0.5,
        ram_margin_gb: float = 2.0,
        memory_settle_seconds: float = 5,
        launch_planner: Callable[[ModelEntry], tuple[ModelEntry, Any]] | None = None,
    ) -> None:
        if (
            idle_seconds <= 0 or queue_limit < 1 or queue_timeout <= 0
            or vram_margin_gb < 0 or ram_margin_gb < 0
            or memory_settle_seconds < 0
        ):
            raise ValueError("invalid scheduler settings")
        self.catalogue = catalogue
        self.target_factory = target_factory
        self.resource_reader = resource_reader
        self.role_models = role_models or {
            "finder-fast": "gemma4-e4b",
            "finder-thorough": "gemma4-26b-a4b",
            "answer": "gemma4-e4b",
        }
        for role, model_id in self.role_models.items():
            if role not in catalogue.get(model_id).roles:
                raise ValueError("role mapping does not match catalogue")
        self.idle_seconds = idle_seconds
        self.queue_limit = queue_limit
        self.queue_timeout = queue_timeout
        self.vram_margin_gb = vram_margin_gb
        self.ram_margin_gb = ram_margin_gb
        self.memory_settle_seconds = memory_settle_seconds
        self.launch_planner = launch_planner
        self._launch_plan: dict[str, Any] | None = None
        self._condition = threading.Condition()
        self._queue: deque[object] = deque()
        self._busy = False
        self._closed = False
        self._target: LocalInferenceTarget | None = None
        self._loading: LocalInferenceTarget | None = None
        self._model_id: str | None = None
        self._mode: str | None = None
        self._loaded_entry = None
        self._last_used = time.monotonic()
        self._history: deque[dict[str, Any]] = deque(maxlen=256)
        self._last_status = "ready"
        self._lease = DeviceLease(lease_path)
        self._stop_reaper = threading.Event()
        self._reaper = threading.Thread(
            target=self._idle_loop, name="themis-idle-unload", daemon=True,
        )
        self._reaper.start()

    def _resolve(self, model_role: str) -> ModelEntry:
        model_id = self.role_models.get(model_role, model_role)
        return self.catalogue.get(model_id)

    @contextmanager
    def _slot(
        self, cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> Iterator[float]:
        began = time.monotonic()
        deadline = began + (self.queue_timeout if timeout is None else timeout)
        ticket = object()
        acquired = False
        with self._condition:
            if self._closed:
                raise TargetUnavailable("local scheduler closed")
            if len(self._queue) >= self.queue_limit:
                raise TargetUnavailable("local inference queue full")
            self._queue.append(ticket)
            try:
                while True:
                    if self._closed:
                        raise TargetUnavailable("local scheduler closed")
                    if cancel_event is not None and cancel_event.is_set():
                        raise TargetUnavailable("local inference request cancelled")
                    if time.monotonic() >= deadline:
                        raise TargetUnavailable("local inference queue timeout")
                    if not self._busy and self._queue[0] is ticket:
                        self._queue.popleft()
                        self._busy = True
                        acquired = True
                        break
                    self._condition.wait(timeout=0.1)
            finally:
                if not acquired:
                    self._queue.remove(ticket)
                    self._condition.notify_all()
        try:
            yield (time.monotonic() - began) * 1000
        finally:
            with self._condition:
                self._busy = False
                self._last_used = time.monotonic()
                self._condition.notify_all()

    def _unload(self) -> None:
        target = self._target
        if target is not None:
            # Do not clear ownership if unload fails.
            target.stop()
        with self._condition:
            self._target = None
            self._model_id = None
            self._mode = None
            self._loaded_entry = None
            self._loading = None
            self._launch_plan = None

    def _check_capacity(
        self, entry: ModelEntry, snapshot: ResourceSnapshot, *, warm: bool,
    ) -> None:
        if getattr(entry, "external", False):
            # Attaching loads nothing; the runtime's memory is the user's and
            # is already counted as occupied by the resource reader.
            return
        if (
            entry.est_vram_gb is None or entry.est_ram_gb is None
            or entry.min_vram_gb is None
        ):
            raise TargetUnavailable("model memory budget unknown; configure estimates")
        if snapshot.vram_total_gb is None or snapshot.vram_free_gb is None:
            raise TargetUnavailable("GPU memory unavailable; request held")
        needed_ram = self.ram_margin_gb + (0 if warm else entry.est_ram_gb)
        needed_vram = self.vram_margin_gb + (0 if warm else entry.est_vram_gb)
        if snapshot.ram_available_gb < needed_ram:
            raise NotEnoughMemory("not enough memory: available RAM below model budget")
        if snapshot.vram_total_gb < entry.min_vram_gb:
            raise NotEnoughMemory("not enough memory: GPU below model minimum")
        if snapshot.vram_free_gb < needed_vram:
            if getattr(snapshot, "external_holders", ()):
                raise NotEnoughMemory(
                    "not enough memory: free VRAM below model budget "
                    "(an attached runtime holds GPU memory)"
                )
            raise NotEnoughMemory("not enough memory: free VRAM below model budget")
        if (
            snapshot.commit_limit_gb is not None
            and snapshot.commit_used_gb is not None
            and snapshot.commit_limit_gb - snapshot.commit_used_gb < needed_ram
        ):
            raise NotEnoughMemory("not enough memory: commit headroom below model budget")

    def _load(self, entry: ModelEntry, mode=None) -> tuple[LocalInferenceTarget, float]:
        if (
            self._model_id == entry.id and self._target is not None
            and (mode is None or mode == self._mode)
        ):
            if self._target.health():
                self._check_capacity(self._loaded_entry, self.resource_reader(), warm=True)
                return self._target, 0.0
            self._unload()
        elif self._target is not None:
            self._unload()
        from .modes import select

        def admit(candidate: ModelEntry) -> ModelEntry:
            # Windows CUDA memory release can lag process exit.
            deadline = time.monotonic() + self.memory_settle_seconds
            while True:
                snapshot = self.resource_reader()
                try:
                    return select(self, candidate, snapshot, mode)
                except NotEnoughMemory:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)

        decision = None
        if self.launch_planner is not None and not getattr(entry, "external", False) and mode != "mmap":
            planned, decision = self.launch_planner(entry)
        if decision is not None and decision.planned:
            try:
                entry = admit(planned)
            except NotEnoughMemory:
                from ..inference.marian import PlanDecision
                decision = PlanDecision("catalogue", "plan-does-not-fit-now")
                entry = select(self, entry, self.resource_reader(), mode)
        else:
            entry = admit(entry)
        target = self.target_factory(entry)
        if decision is not None and hasattr(target, "set_launch_plan"):
            target.set_launch_plan(decision.source, decision.reason)
        with self._condition:
            self._loading = target
        try:
            target.start()
            if not target.health():
                raise TargetUnavailable("loaded local target unhealthy")
        except BaseException:
            # Keep failed ownership visible until stop succeeds.
            try:
                target.stop()
            except BaseException:
                with self._condition:
                    self._target = target
                    self._model_id = entry.id
                raise RuntimeFailure("failed local target could not be unloaded") from None
            finally:
                with self._condition:
                    self._loading = None
            raise
        with self._condition:
            self._target = target
            self._model_id = entry.id
            self._mode = entry.mode
            self._loaded_entry = entry
            self._loading = None
            self._launch_plan = decision.record() if decision is not None else None
        return target, target.load_ms

    @contextmanager
    def acquire(
        self, model_role: str, *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        mode: str | None = None,
    ) -> Iterator[LocalInferenceTarget]:
        entry = self._resolve(model_role)
        with self._slot(cancel_event, timeout) as queue_ms:
            began = time.monotonic()
            target: LocalInferenceTarget | None = None
            load_ms = 0.0
            outcome = "failed"
            try:
                target, load_ms = self._load(entry, mode)
                target.last_timings = {}
                if cancel_event is not None and cancel_event.is_set():
                    raise TargetUnavailable("local inference request cancelled")
                yield target
                outcome = "complete"
                self._last_status = "ready"
            except NotEnoughMemory:
                self._last_status = "not enough memory"
                raise
            except BaseException:
                self._last_status = "local request held"
                raise
            finally:
                timings = target.last_timings if target is not None else {}
                with self._condition:
                    plan = self._launch_plan if target is not None else None
                    self._history.append({
                        "model_id": entry.id,
                        "args_source": plan["args_source"] if plan else None,
                        "plan_reason": plan["reason"] if plan else None,
                        "mode": self._mode if target is not None else mode,
                        "outcome": outcome,
                        "queue_ms": queue_ms,
                        "load_ms": load_ms,
                        "wall_ms": (time.monotonic() - began) * 1000,
                        "prompt_tok_s": timings.get("prompt_per_second"),
                        "gen_tok_s": timings.get("predicted_per_second"),
                        "prompt_tokens": timings.get("prompt_n"),
                        "gen_tokens": timings.get("predicted_n"),
                    })

    def chat_json(
        self, model_role: str, messages: list[dict[str, Any]],
        schema: dict[str, Any], max_tokens: int = 512,
    ) -> dict[str, Any]:
        with self.acquire(model_role) as target:
            return target.chat_json(model_role, messages, schema, max_tokens)

    def chat(
        self, model_role: str,
        messages: InferenceRequest | list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> Any:
        if stream:
            if isinstance(messages, InferenceRequest):
                raise ValueError("legacy request cannot stream")
            return self._stream(model_role, messages, response_format, max_tokens)
        with self.acquire(model_role) as target:
            return target.chat(
                messages, response_format=response_format, max_tokens=max_tokens,
            )

    def _stream(
        self, role: str, messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None, max_tokens: int,
    ) -> Iterator[dict[str, Any]]:
        # Lease lasts through iterator exhaustion or explicit close().
        with self.acquire(role) as target:
            chunks = target.chat(
                messages, stream=True, response_format=response_format,
                max_tokens=max_tokens,
            )
            yield from chunks  # type: ignore[misc]

    def sampling_pid(self) -> int | None:
        with self._condition:
            target = self._loading or self._target
            return target.pid if target is not None else None

    def status(self) -> dict[str, Any]:
        try:
            resource_record = self.resource_reader().record()
            resource_error = None
        except RuntimeFailure:
            resource_record = None
            resource_error = "resource_probe_unavailable"
        with self._condition:
            return {
                "loaded_models": [self._model_id] if self._model_id else [],
                "loaded_modes": [{"id": self._model_id, "mode": self._mode}] if self._model_id else [],
                "launch_plan": dict(self._launch_plan) if self._launch_plan else None,
                "loading": self._loading is not None,
                "resources": resource_record,
                "resource_error": resource_error,
                "queue": len(self._queue),
                "inference_active": self._busy,
                "status": self._last_status,
                "timings": list(self._history),
            }

    def stop(self) -> None:
        """Queued, explicit unload; does not close the scheduler."""
        with self._slot():
            self._unload()
            self._last_status = "unloaded"

    def _idle_loop(self) -> None:
        interval = min(1.0, self.idle_seconds / 2)
        while not self._stop_reaper.wait(interval):
            with self._condition:
                eligible = (
                    not self._closed and not self._busy and not self._queue
                    and self._target is not None
                    and time.monotonic() - self._last_used >= self.idle_seconds
                )
                if not eligible:
                    continue
                self._busy = True
            try:
                self._unload()
                self._last_status = "idle unloaded"
            except RuntimeFailure:
                self._last_status = "idle unload failed"
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

    def close(self, timeout: float = 30) -> None:
        self._stop_reaper.set()
        deadline = time.monotonic() + timeout
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            while self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeFailure("scheduler shutdown waiting for active request")
                self._condition.wait(timeout=remaining)
            self._busy = True
        try:
            self._unload()
        finally:
            with self._condition:
                self._busy = False
                self._condition.notify_all()
        self._reaper.join(timeout=timeout)
        self._lease.close()

    def __enter__(self) -> Scheduler:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
