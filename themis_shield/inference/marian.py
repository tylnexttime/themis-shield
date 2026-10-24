"""Marian planner adapter (optional): measured llama.cpp launch arguments.

Marian (Apache-2.0, https://github.com/tylnexttime/marian) measures what a machine can do with a
llama.cpp model and saves the result as a profile per machine, model and
engine. From those saved measurements it plans the fastest configuration
that fits a requested context (context size, KV-cache type, GPU/CPU split
such as ``-ncmoe``), and refuses rather than extrapolates.

themis-shield uses Marian only as a *planner*:

- Only catalogue entries that ask for it (``"planner": "marian"``) are
  planned. Marian is imported lazily, so it is an optional extra.
- Plans come from saved profiles. themis never runs a Marian profile (that
  starts servers on the GPU) and never launches through Marian: the
  llama.cpp target keeps owning the process, the argument allow-list, the
  loopback listener, the API key and the orphan reaper.
- A plan becomes ordinary llama.cpp tuning arguments, validated by the same
  allow-list as catalogue arguments. Anything else is refused.
- Whenever Marian is not installed, has no profile for this machine and
  model, finds no measured configuration that fits, or returns something
  unusable, the decision says so and the catalogue's own arguments are used.

Interface used: the Python API ``marian.plan.load(profile_dir, model)``,
``marian.plan.choose(rows, need, objective)`` and
``marian.plan.to_config(row)``. Marian's CLI has no ``--json`` switch (its
``plan`` command prints one JSON row), and the Python API is small and
dependency-free, so the CLI is not used. ``plan.load`` reads a short
fingerprint of the machine (an ``nvidia-smi`` query, no model load) and
hashes the first 64 MiB of the model file.

An unrelated package called ``marian`` exists on PyPI. The adapter checks for
the planner API before trusting the import; install Marian from
https://github.com/tylnexttime/marian (``pip install "themis-shield[marian]"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
import threading
from typing import Any, Callable

from . import InferenceCapabilities, TargetUnavailable
from .llamacpp import engine_extra_args, tuning_args

OBJECTIVES = {"tg", "pp"}       # generation speed, or prompt (reading) speed
REASONS = {
    "planned",                    # Marian's plan is used
    "static",                     # the entry does not ask for a planner
    "marian-not-installed",
    "marian-incompatible",        # something called marian, without the planner API
    "no-profiles-dir",
    "model-missing",
    "no-profile",                 # no saved profile for this machine and model
    "no-fit",                     # profiles exist, none measured at this context
    "plan-invalid",               # the plan failed the argument allow-list
    "plan-needs-patched-engine",  # the plan uses expert-cache flags; entry has no engine_path
    "planner-error",
    "plan-does-not-fit-now",      # set by the scheduler: measured peak above free memory
}


@dataclass(frozen=True, slots=True)
class PlanDecision:
    """Which planner chose the launch arguments, and why. Content-free."""

    source: str                              # "marian" or "catalogue"
    reason: str
    args: tuple[str, ...] = ()
    ctx: int | None = None
    extra_args: tuple[str, ...] = ()
    vram_peak_gb: float | None = None
    tg_tps: float | None = None
    pp_tps: float | None = None
    profile: str | None = None               # profile file name, never a path

    def __post_init__(self) -> None:
        if self.source not in {"marian", "catalogue"} or self.reason not in REASONS:
            raise ValueError("invalid plan decision")

    @property
    def planned(self) -> bool:
        return self.source == "marian"

    def record(self) -> dict[str, Any]:
        return {
            "args_source": self.source, "reason": self.reason,
            "ctx": self.ctx, "args": list(self.args),
            "extra_args": list(self.extra_args),
            "vram_peak_gb": self.vram_peak_gb,
            "tg_tps": self.tg_tps, "pp_tps": self.pp_tps,
            "profile": self.profile,
        }


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _number(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and value >= 0 else None


@dataclass
class MarianPlanner:
    """Ask Marian (if installed) for launch arguments from saved profiles."""

    profiles_dir: Path | None = None
    objective: str = "tg"
    importer: Callable[[str], Any] = importlib.import_module
    _cache: dict[tuple, PlanDecision] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.objective not in OBJECTIVES:
            raise ValueError("Marian objective must be 'tg' or 'pp'")
        if self.profiles_dir is not None:
            self.profiles_dir = Path(self.profiles_dir)

    def _plan_module(self) -> tuple[Any | None, str]:
        try:
            module = self.importer("marian.plan")
        except ImportError:
            return None, "marian-not-installed"
        except Exception:
            return None, "marian-incompatible"
        if not all(callable(getattr(module, name, None)) for name in ("load", "choose", "to_config")):
            return None, "marian-incompatible"
        return module, "planned"

    def resolve_profiles_dir(self) -> Path | None:
        if self.profiles_dir is not None:
            return self.profiles_dir if self.profiles_dir.is_dir() else None
        try:
            default = getattr(self.importer("marian.cli"), "PROFILES", None)
        except Exception:
            return None
        if default is None:
            return None
        default = Path(default)
        return default if default.is_dir() else None

    def available(self) -> tuple[bool, str]:
        module, reason = self._plan_module()
        return module is not None, ("installed" if module is not None else reason)

    def status(self) -> dict[str, Any]:
        installed, reason = self.available()
        directory = self.resolve_profiles_dir() if installed else None
        return {
            "installed": installed,
            "reason": reason,
            "objective": self.objective,
            "profiles_dir_configured": self.profiles_dir is not None,
            "profiles_dir_found": directory is not None,
            "profile_files": (
                len(list(directory.glob("*.json"))) if directory is not None else 0
            ),
        }

    def plan(self, model_path: Path, need: int, *, allow_extra_args: bool = False) -> PlanDecision:
        """A decision for launching ``model_path`` with a context of ``need``."""
        if type(need) is not int or need <= 0:
            raise ValueError("invalid planning context")
        module, reason = self._plan_module()
        if module is None:
            return PlanDecision("catalogue", reason)
        directory = self.resolve_profiles_dir()
        if directory is None:
            return PlanDecision("catalogue", "no-profiles-dir")
        model_path = Path(model_path)
        try:
            stat = model_path.stat()
        except OSError:
            return PlanDecision("catalogue", "model-missing")
        try:
            profiles = tuple(sorted(
                (item.name, item.stat().st_mtime_ns) for item in directory.glob("*.json")
            ))
        except OSError:
            return PlanDecision("catalogue", "no-profiles-dir")
        key = (
            str(model_path), stat.st_size, stat.st_mtime_ns, need,
            self.objective, allow_extra_args, str(directory), profiles,
        )
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        decision = self._decide(module, directory, model_path, need, allow_extra_args)
        with self._lock:
            self._cache[key] = decision
        return decision

    def _decide(
        self, module: Any, directory: Path, model_path: Path, need: int,
        allow_extra_args: bool,
    ) -> PlanDecision:
        try:
            rows = module.load(directory, model_path)
            if not isinstance(rows, list):
                return PlanDecision("catalogue", "planner-error")
            if not rows:
                return PlanDecision("catalogue", "no-profile")
            row = module.choose(rows, need, self.objective)
            if row is None:
                return PlanDecision("catalogue", "no-fit")
            if not isinstance(row, dict):
                return PlanDecision("catalogue", "plan-invalid")
            config = module.to_config(row)
        except Exception:
            # Never surface planner messages: they can carry paths.
            return PlanDecision("catalogue", "planner-error")
        ctx = _int(getattr(config, "ctx", None))
        kv = getattr(config, "cache_type", None)
        layers = _int(getattr(config, "n_gpu_layers", None))
        cpu_moe = getattr(config, "n_cpu_moe", None)
        threads = getattr(config, "threads", None)
        moe_cache = getattr(config, "moe_cache_mib", None)
        extra = getattr(config, "extra", None) or []
        if (
            ctx is None or ctx < need or not isinstance(kv, str) or layers is None
            or (cpu_moe is not None and _int(cpu_moe) is None)
            or (threads is not None and _int(threads) is None)
            or (moe_cache is not None and _int(moe_cache) is None)
            or extra
        ):
            return PlanDecision("catalogue", "plan-invalid")
        args = ["-ngl", str(layers), "-c", str(ctx), "-ctk", kv, "-ctv", kv]
        if cpu_moe is not None:
            args += ["-ncmoe", str(cpu_moe)]
        if threads:
            args += ["-t", str(threads)]
        extra_args: list[str] = []
        if moe_cache:
            if not allow_extra_args:
                return PlanDecision("catalogue", "plan-needs-patched-engine")
            extra_args = ["--moe-cache-mib", str(moe_cache)]
        try:
            tuning_args(args, ctx)
            engine_extra_args(extra_args)
        except ValueError:
            return PlanDecision("catalogue", "plan-invalid")
        # Newer profiles record the model's own share (peak minus what the
        # desktop held when the row was measured). Older rows only carry the
        # whole-GPU peak, which overstates the need: safe, never optimistic.
        peak = _int(row.get("vram_model_mib"))
        if not peak or peak <= 0:
            peak = _int(row.get("vram_peak_mib"))
        file_name = row.get("_file")
        return PlanDecision(
            "marian", "planned", tuple(args), ctx, tuple(extra_args),
            round(peak / 1024, 3) if peak and peak > 0 else None,
            _number(row.get("tg_tps")), _number(row.get("pp_tps")),
            file_name if isinstance(file_name, str) and "/" not in file_name
            and "\\" not in file_name and len(file_name) <= 200 else None,
        )


class MarianTarget:
    """Not a runtime. Marian plans launches for the llama.cpp target.

    Use ``"runtime": "llamacpp", "planner": "marian"`` in a catalogue entry
    instead. This class remains so that ``"runtime": "marian"`` fails closed
    with a clear message.
    """

    name = "marian"
    capabilities = InferenceCapabilities(text_chat=False)
    pid = None
    load_ms = 0.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.last_timings: dict[str, Any] = {}

    def health(self) -> bool:
        return False

    def start(self) -> None:
        raise TargetUnavailable(
            "Marian is a planner, not a runtime: use runtime llamacpp with planner marian"
        )

    def stop(self) -> None:
        pass

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.start()

    def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.start()
        raise AssertionError("unreachable")
