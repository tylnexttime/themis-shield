"""Explicit load variants and finder-tier residency."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import math


@dataclass(frozen=True, slots=True)
class LoadMode:
    mode: str
    est_ram_gb: float
    est_vram_gb: float
    min_vram_gb: float
    expected_prompt_tok_s: float
    expected_tok_s: float
    expected_load_s: float | None = None
    measured: bool = False
    measured_utc: str | None = None
    device_fingerprint: str | None = None

    def __post_init__(self):
        if self.mode != "mmap":
            raise ValueError("additional variant must be mmap")
        for name in (
            "est_ram_gb", "est_vram_gb", "min_vram_gb",
            "expected_prompt_tok_s", "expected_tok_s",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid mode estimate")


def entries(entry):
    full = replace(entry, mode="full", variants=())
    result = [full]
    for variant in entry.variants:
        result.append(replace(full, **{
            name: getattr(variant, name) for name in (
                "mode", "est_ram_gb", "est_vram_gb", "min_vram_gb",
                "expected_prompt_tok_s", "expected_tok_s", "expected_load_s",
                "measured", "measured_utc", "device_fingerprint",
            )
        }))
    return tuple(result)


def select(scheduler, entry, snapshot, forced_mode=None):
    if forced_mode is not None and forced_mode not in {"full", "mmap"}:
        raise ValueError("invalid requested load mode")
    candidates = [
        candidate for candidate in entries(entry)
        if forced_mode is None or candidate.mode == forced_mode
    ]
    if not candidates:
        raise ValueError("model has no requested load mode")
    failure = None
    for candidate in candidates:
        try:
            scheduler._check_capacity(candidate, snapshot, warm=False)
            return candidate
        except Exception as exc:
            from ..inference import NotEnoughMemory
            if not isinstance(exc, NotEnoughMemory):
                raise
            failure = exc
    raise failure


def overlay(catalogue, store, fingerprint):
    if fingerprint is None:
        return type(catalogue)(catalogue)
    result = []
    for original in catalogue:
        modes = []
        for candidate in entries(original):
            record = store.lookup(fingerprint, candidate)
            if record is not None:
                try:
                    candidate = replace(
                        candidate, expected_tok_s=record["gen_tok_s"],
                        expected_prompt_tok_s=record["prompt_tok_s"],
                        expected_load_s=record["load_seconds"], measured=True,
                        measured_utc=record["measured_utc"],
                        device_fingerprint=fingerprint,
                    )
                except (KeyError, TypeError, ValueError):
                    pass
            modes.append(candidate)
        variants = tuple(LoadMode(**{
            name: getattr(candidate, name) for name in LoadMode.__dataclass_fields__
        }) for candidate in modes[1:])
        result.append(replace(modes[0], variants=variants))
    return type(catalogue)(result)


def release_tracer_resources():
    """Release the CPU tracer after tier 1, before finder admission."""
    import gc
    from ..detectors import pii_tracer as module
    with module._PREDICT_LOCK, module._LOAD_LOCK:
        module._RESOURCE = None
    gc.collect()


class SequentialFinderClient:
    def __init__(self, runtime):
        self.runtime = runtime

    @contextmanager
    def residency(self, model_role):
        release_tracer_resources()
        acquire = getattr(self.runtime, "acquire", None)
        if acquire is None:
            # A runtime without residency control (e.g. a minimal adapter) still works: the finder calls this client,
            # and each call loads as needed.
            yield self
            return
        # Scheduler.acquire owns _busy until this context exits. Its idle
        # reaper therefore cannot unload during or between document calls.
        with acquire(model_role) as target:
            yield target

    def chat_json(self, *args, **kwargs):
        release_tracer_resources()
        return self.runtime.chat_json(*args, **kwargs)
