"""Configurable catalogue with honest device-specific speed labels.

GB fields use GiB (1024**3), matching the supplied memory evidence.
Safety footprint estimates are not replaced by a short calibration run.
Unknown Qwen footprints remain unknown, not invented measurements.

Back-end fields (all optional):

- ``runtime: "strata"`` attaches to a Strata server the user runs:
  ``endpoint`` (http loopback only) and ``served_model`` (the id Strata
  serves) configure it; ``min_max_tokens`` sets the thinking-model floor.
  Strata entries are answering models only (role ``answer``) and take no
  llama.cpp arguments; their ``file`` is informational and may be "". Strata is *external*: the
  scheduler does not admit its memory, it counts it as occupied.
- ``engine_path`` (llamacpp) names an alternative llama-server binary,
  relative to the runtime's engines directory; ``engine_args`` passes it
  allow-listed extra flags (see ``inference.llamacpp.ENGINE_EXTRA_*``).
- ``planner: "marian"`` (llamacpp) asks Marian for launch arguments from
  saved profiles before each load, falling back to ``args`` (see
  ``inference.marian``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import datetime as dt
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable

from ..inference.json_output import strict_loads
from ..inference.llamacpp import engine_extra_args, tuning_args
from ..inference.strata import loopback_origin

RUNTIMES = {"llamacpp", "strata", "marian", "ollama", "dgx-spark"}
# Runtimes whose process and memory belong to the user, not the scheduler.
EXTERNAL_RUNTIMES = {"strata"}
PLANNERS = {"marian"}
ROLES = {"finder-fast", "finder-thorough", "answer"}
REFERENCE_LABEL = "estimated (reference device)"
from .modes import LoadMode


@dataclass(frozen=True, slots=True)
class ModelEntry:
    id: str
    display_name: str
    runtime: str
    file: str
    args: tuple[str, ...]
    ctx: int
    roles: tuple[str, ...]
    est_vram_gb: float | None
    est_ram_gb: float | None
    min_vram_gb: float | None
    expected_tok_s: float | None
    expected_prompt_tok_s: float | None = None
    expected_load_s: float | None = None
    measured: bool = False
    measured_utc: str | None = None
    device_fingerprint: str | None = None
    mode: str = "full"
    variants: tuple[LoadMode, ...] = ()
    endpoint: str | None = None
    served_model: str | None = None
    min_max_tokens: int | None = None
    engine_path: str | None = None
    engine_args: tuple[str, ...] = ()
    planner: str | None = None

    @property
    def external(self) -> bool:
        return self.runtime in EXTERNAL_RUNTIMES

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", self.id):
            raise ValueError("invalid catalogue id")
        if (
            not isinstance(self.display_name, str) or not self.display_name
            or len(self.display_name) > 200
            or any(ord(char) < 32 for char in self.display_name)
            or self.runtime not in RUNTIMES
        ):
            raise ValueError("invalid catalogue label or runtime")
        path = PurePosixPath(self.file)
        if not (self.runtime == "strata" and self.file == "") and (
            not self.file or "\\" in self.file or path.is_absolute()
            or ".." in path.parts or ":" in self.file
            or path.suffix.lower() != ".gguf"
        ):
            raise ValueError("catalogue file must be a relative GGUF path")
        if (
            not isinstance(self.roles, tuple) or not self.roles
            or not set(self.roles) <= ROLES
            or len(set(self.roles)) != len(self.roles)
        ):
            raise ValueError("invalid catalogue roles")
        tuning_args(self.args, self.ctx)
        self._check_backend_fields()
        if self.mode not in {"full", "mmap"}:
            raise ValueError("invalid catalogue load mode")
        if (
            not isinstance(self.variants, tuple)
            or any(not isinstance(item, LoadMode) for item in self.variants)
            or len(self.variants) > 1
        ):
            raise ValueError("invalid catalogue variants")
        for key in (
            "est_vram_gb", "est_ram_gb", "min_vram_gb", "expected_tok_s",
            "expected_prompt_tok_s", "expected_load_s",
        ):
            number = getattr(self, key)
            if number is not None and (
                type(number) not in (int, float)
                or not math.isfinite(number) or number <= 0
            ):
                raise ValueError("invalid catalogue numeric estimate")
        if (
            self.min_vram_gb is not None and self.est_vram_gb is not None
            and self.min_vram_gb > self.est_vram_gb
        ):
            raise ValueError("minimum VRAM exceeds estimated footprint")
        if type(self.measured) is not bool:
            raise ValueError("invalid measured flag")
        if self.measured:
            if (
                self.expected_tok_s is None or self.expected_prompt_tok_s is None
                or self.measured_utc is None or self.device_fingerprint is None
                or not re.fullmatch(r"[0-9a-f]{64}", self.device_fingerprint)
            ):
                raise ValueError("incomplete catalogue measurement")
            stamp = dt.datetime.fromisoformat(self.measured_utc)
            if stamp.tzinfo is None:
                raise ValueError("measurement needs UTC timezone")

    def _check_backend_fields(self) -> None:
        if self.runtime == "strata":
            if self.endpoint is not None:
                loopback_origin(self.endpoint)
            if self.served_model is not None and (
                not isinstance(self.served_model, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.served_model)
            ):
                raise ValueError("invalid Strata served model id")
            if (self.endpoint is None) != (self.served_model is None):
                raise ValueError("Strata needs both endpoint and served_model")
            if self.min_max_tokens is not None and (
                type(self.min_max_tokens) is not int
                or not 1 <= self.min_max_tokens <= self.ctx
            ):
                raise ValueError("invalid Strata max_tokens floor")
            # Not evaluated as a PII finder: answering only.
            if not set(self.roles) <= {"answer"}:
                raise ValueError("Strata entries may only take the answer role")
            if self.args or self.engine_path or self.engine_args or self.planner or self.variants:
                raise ValueError("Strata entries take no llama.cpp launch settings")
            return
        if (
            self.endpoint is not None or self.served_model is not None
            or self.min_max_tokens is not None
        ):
            raise ValueError("endpoint settings are for attached runtimes only")
        if (self.engine_path or self.engine_args or self.planner) and self.runtime != "llamacpp":
            raise ValueError("engine and planner settings are for llama.cpp entries only")
        if self.engine_path is not None:
            engine = PurePosixPath(self.engine_path) if isinstance(self.engine_path, str) else None
            if (
                engine is None or not self.engine_path or "\\" in self.engine_path
                or engine.is_absolute() or ".." in engine.parts or ":" in self.engine_path
                or len(self.engine_path) > 200
            ):
                raise ValueError("engine_path must be relative to the engines directory")
        if not isinstance(self.engine_args, tuple):
            raise ValueError("invalid engine args")
        engine_extra_args(self.engine_args)
        if self.engine_args and not self.engine_path:
            raise ValueError("engine_args need an alternative engine_path")
        if self.planner is not None and self.planner not in PLANNERS:
            raise ValueError("unknown launch planner")

    @property
    def speed_label(self) -> str:
        if self.measured:
            stamp = dt.datetime.fromisoformat(self.measured_utc or "")
            return f"measured on this device {stamp:%d/%m/%Y}"
        return REFERENCE_LABEL

    def display_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "speed_label": self.speed_label,
            "footprint_label": REFERENCE_LABEL,
            "load_label": self.speed_label if self.measured else REFERENCE_LABEL,
            "memory_units": "GiB",
            "startable_with_known_budget": (
                self.est_vram_gb is not None
                and self.est_ram_gb is not None
                and self.min_vram_gb is not None
            ),
        }


BUILTINS = (
    ModelEntry(
        "gemma4-e4b", "Gemma 4 E4B Q4_K_M", "llamacpp",
        "gemma-4-E4B-it-Q4_K_M.gguf", ("-ngl", "99"), 16384,
        ("finder-fast", "answer"), 3.5, 6.8, 3.5,
        50.0, 2000.0, 8.1,
    ),
    # Default 26B finder: UD-IQ3_XXS with 18 expert layers on the CPU, as adopted by the frozen evaluation (same recall
    # as UD-IQ4_XS, about 1.5x faster generation). The full-mode figures below are the UD-IQ4_XS reference
    # measurements, kept as conservative estimates for the smaller IQ3_XXS file until device calibration replaces them.
    # Alternative: UD-IQ4_XS ("gemma-4-26B-A4B-it-UD-IQ4_XS.gguf", args -ngl 99 -ncmoe 20 -c 16384), set through a
    # catalogue profile (see profiles/catalogue/example.json).
    ModelEntry(
        "gemma4-26b-a4b", "Gemma 4 26B-A4B UD-IQ3_XXS", "llamacpp",
        "gemma-4-26B-A4B-it-UD-IQ3_XXS.gguf",
        ("-ngl", "99", "-ncmoe", "18", "-c", "16384"), 16384,
        ("finder-thorough", "answer"), 7.1, 16.0, 7.1,
        23.6, 658.0, 13.6,
        variants=(LoadMode("mmap", 8.5, 7.1, 7.1, 29.0, 7.3),),
    ),
    ModelEntry(
        "qwen36-35b-a3b", "Qwen3.6-35B-A3B UD-Q4_K_XL", "llamacpp",
        "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        ("-ngl", "99", "-ncmoe", "30", "-t", "8"), 16384,
        ("answer",), None, None, None, 42.7, 494.0,
    ),
    # Strata (attach only: themis never starts, installs or updates it). Reference figures from an 8 GB laptop GPU:
    # decode 12.1 tok/s (median of 3, short and 4K; 9.9 at 16K), prefill ~100 tok/s from 4K, first token 40 s at
    # 4K / 159 s at 16K, 64K context, ~7.3 GB VRAM held while running (7.9 GB peak at load).
    # Router guidance: local-only work under ~8K prompt tokens, one request at a time; never beside the gate's Gemma
    # finders on 8 GB. Unconfigured here (no endpoint), so it shows as unavailable until a catalogue profile sets
    # endpoint and served_model (see profiles/catalogue/example.json).
    ModelEntry(
        "strata-qwen3.8-flash-next", "Qwen3.8 Flash-Next Gyro-S (Strata)", "strata",
        "Qwen3.8-Flash-Next-Gyro-S-TQ1_0.gguf", (), 65536,
        ("answer",), 7.9, None, 6.8, 12.1, 100.0, 45.0,
    ),
)


def _comment_ok(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= 4000)


class Catalogue:
    def __init__(self, entries: Iterable[ModelEntry] = BUILTINS) -> None:
        self._entries: dict[str, ModelEntry] = {}
        for entry in entries:
            if entry.id in self._entries:
                raise ValueError("duplicate catalogue id")
            self._entries[entry.id] = entry
        if not self._entries:
            raise ValueError("empty catalogue")

    def __iter__(self):
        return iter(self._entries.values())

    def get(self, model_id: str) -> ModelEntry:
        try:
            return self._entries[model_id]
        except KeyError:
            raise ValueError("unknown local model id") from None

    def contains(self, model_id: str) -> bool:
        return model_id in self._entries

    @classmethod
    def load(cls, path: Path | None = None) -> Catalogue:
        """JSON: {version: 1, models: [builtin patches or complete new entries]}.

        A string ``comment`` is allowed at the top level and in each model
        object, and ignored (JSON has no comments). Missing built-ins are retained. Measurements cannot be supplied by
        catalogue configuration; only the device calibration overlay adds them.
        """
        if path is None:
            return cls()
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("catalogue exceeds bound")
        data = strict_loads(path.read_bytes())
        if (
            not isinstance(data, dict) or not {"version", "models"} <= set(data)
            or set(data) - {"version", "models", "comment"}
            or not _comment_ok(data.get("comment"))
            or data["version"] != 1 or not isinstance(data["models"], list)
        ):
            raise ValueError("invalid catalogue document")
        entries = {entry.id: entry for entry in BUILTINS}
        allowed = {
            field.name for field in fields(ModelEntry)
        } - {"measured", "measured_utc", "device_fingerprint"}
        seen: set[str] = set()
        for patch in data["models"]:
            if isinstance(patch, dict) and "comment" in patch:
                if not _comment_ok(patch["comment"]):
                    raise ValueError("invalid catalogue comment")
                patch = {key: value for key, value in patch.items() if key != "comment"}
            if (
                not isinstance(patch, dict) or set(patch) - allowed
                or not isinstance(patch.get("id"), str)
                or patch["id"] in seen
            ):
                raise ValueError("invalid catalogue model patch")
            seen.add(patch["id"])
            values = (
                asdict(entries[patch["id"]])
                if patch["id"] in entries else {}
            )
            values.update(patch)
            if "variants" in values:
                converted = []
                for value in values["variants"]:
                    if isinstance(value, dict):
                        if any(key in value for key in ("measured", "measured_utc", "device_fingerprint")):
                            raise ValueError("catalogue cannot assert device measurements")
                        value = LoadMode(**value)
                    converted.append(value)
                values["variants"] = tuple(converted)
            for key in ("args", "roles", "engine_args"):
                if key in values:
                    if not isinstance(values[key], (tuple, list)):
                        raise ValueError("invalid catalogue sequence")
                    values[key] = tuple(values[key])
            try:
                entry = ModelEntry(**values)
            except TypeError:
                raise ValueError("incomplete catalogue model") from None
            entries[entry.id] = entry
        return cls(entries.values())

    def with_calibration(self, store: Any, fingerprint: str | None) -> Catalogue:
        from .modes import overlay
        return overlay(self, store, fingerprint)

    def _obsolete_with_calibration(self, store, fingerprint):

        if fingerprint is None:
            return Catalogue(self)
        result = []
        for entry in self:
            record = store.lookup(fingerprint, entry)
            if record is None:
                result.append(entry)
                continue
            try:
                result.append(replace(
                    entry,
                    expected_tok_s=record["gen_tok_s"],
                    expected_prompt_tok_s=record["prompt_tok_s"],
                    expected_load_s=record["load_seconds"],
                    measured=True,
                    measured_utc=record["measured_utc"],
                    device_fingerprint=fingerprint,
                ))
            except (KeyError, TypeError, ValueError):
                # Corrupt/stale calibration never creates a measured label.
                result.append(entry)
        return Catalogue(result)
