"""Runtime-only settings, independent of shared governance configuration.

No settings permit cloud fallback, target authorisation or weaker detectors.
Windows ACLs remain a deployment responsibility; chmod is not a DACL.

Back-end settings:

- ``engines_dir``: where alternative engines named by a catalogue entry's
  ``engine_path`` live (default: the directory of ``engine_path``).
- ``marian_profiles_dir``: Marian's saved profiles (default: the profiles
  directory Marian itself uses); ``marian_objective``: ``"tg"`` (generation
  speed) or ``"pp"`` (prompt speed, for read-heavy work).
- ``strata_api_key_env``: the NAME of the environment variable holding a
  Strata API key, if Strata was started with one (default STRATA_API_KEY).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
import re
from typing import Any

from ..inference import TargetUnavailable
from ..inference.dgx_spark import DgxSparkTarget
from ..inference.llamacpp import LlamaCppTarget
from ..inference.marian import OBJECTIVES, MarianPlanner, MarianTarget
from ..inference.ollama import OllamaTarget
from ..inference.strata import DEFAULT_API_KEY_ENV, StrataConfig, StrataTarget
from ..inference.json_output import strict_loads
from .catalogue import Catalogue, ModelEntry
from .children import ChildRegistry
from .occupancy import OccupancyAwareReader, strata_holders
from .planning import LaunchPlanner
from .resources import ResourceReader
from .scheduler import Scheduler
from ..paths import app_data_dir, default_engine_path, default_models_dir


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    engine_path: Path = field(default_factory=default_engine_path)
    models_dir: Path = field(default_factory=default_models_dir)
    catalogue_file: Path | None = None
    data_dir: Path | None = None
    gpu_index: int = 0
    idle_minutes: float = 10
    queue_limit: int = 32
    queue_timeout_seconds: float = 900
    startup_timeout_seconds: float = 600
    cuda_retry_pause_seconds: float = 15
    exclusive_ollama: bool = False
    role_models: dict[str, str] | None = None
    engines_dir: Path | None = None
    marian_profiles_dir: Path | None = None
    marian_objective: str = "tg"
    strata_api_key_env: str = DEFAULT_API_KEY_ENV

    def __post_init__(self) -> None:
        if type(self.gpu_index) is not int or self.gpu_index < 0:
            raise ValueError("invalid runtime GPU index")
        if (
            type(self.queue_limit) is not int or self.queue_limit < 1
            or self.idle_minutes <= 0 or self.queue_timeout_seconds <= 0
            or self.startup_timeout_seconds <= 0
            or self.cuda_retry_pause_seconds < 0
            or type(self.exclusive_ollama) is not bool
        ):
            raise ValueError("invalid runtime settings")
        if self.role_models is not None and (
            not isinstance(self.role_models, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in self.role_models.items()
            )
        ):
            raise ValueError("invalid runtime role mapping")
        if self.marian_objective not in OBJECTIVES:
            raise ValueError("invalid Marian objective")
        if not isinstance(self.strata_api_key_env, str) or not re.fullmatch(
            r"[A-Z_][A-Z0-9_]{0,63}", self.strata_api_key_env,
        ):
            raise ValueError("invalid Strata API key variable name")

    @property
    def engines_root(self) -> Path:
        return self.engines_dir if self.engines_dir is not None else Path(self.engine_path).parent

    def engine_for(self, entry: ModelEntry) -> Path:
        """The engine binary for an entry: its alternative engine, or the default."""
        if not getattr(entry, "engine_path", None):
            return self.engine_path
        root = self.engines_root.resolve()
        engine = (root / entry.engine_path).resolve()
        if not engine.is_relative_to(root):
            raise TargetUnavailable("alternative engine outside the engines directory")
        return engine

    def marian_planner(self) -> MarianPlanner:
        return MarianPlanner(self.marian_profiles_dir, self.marian_objective)

    @property
    def storage_dir(self) -> Path:
        return self.data_dir if self.data_dir is not None else app_data_dir()

    @classmethod
    def load(cls, path: Path | None = None) -> RuntimeSettings:
        if path is None:
            return cls()
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("runtime settings exceed bound")
        data = strict_loads(path.read_bytes())
        allowed = {item.name for item in fields(cls)}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid runtime settings document")
        for key in (
            "engine_path", "models_dir", "catalogue_file", "data_dir",
            "engines_dir", "marian_profiles_dir",
        ):
            if key in data and data[key] is not None:
                if not isinstance(data[key], str):
                    raise ValueError("invalid runtime settings path")
                data[key] = Path(data[key]).expanduser()
        return cls(**data)


def build_runtime(
    settings: RuntimeSettings | None = None,
    catalogue: Catalogue | None = None,
) -> Scheduler:
    settings = settings or RuntimeSettings()
    catalogue = catalogue or Catalogue.load(settings.catalogue_file)
    storage = settings.storage_dir
    # Reap llama-server children left behind by a previous, hard-killed gate
    # (verified by pid, creation time and executable name), then track ours.
    children = ChildRegistry(storage / "state")
    children.reap_stale()

    def factory(entry: ModelEntry) -> Any:
        if entry.runtime == "llamacpp":
            return LlamaCppTarget(
                entry, engine_path=settings.engine_for(entry),
                models_dir=settings.models_dir, logs_dir=storage / "logs",
                gpu_index=settings.gpu_index,
                startup_timeout=settings.startup_timeout_seconds,
                retry_pause=settings.cuda_retry_pause_seconds,
                child_registry=children,
            )
        if entry.runtime == "strata":
            return StrataTarget(StrataConfig.from_entry(
                entry, api_key_env=settings.strata_api_key_env,
            ))
        if entry.runtime == "marian":
            return MarianTarget()
        if entry.runtime == "dgx-spark":
            return DgxSparkTarget()
        if entry.runtime == "ollama":
            return OllamaTarget(
                entry.id, exclusive_control=settings.exclusive_ollama,
                keep_alive_seconds=max(1, round(settings.idle_minutes * 60)),
            )
        raise ValueError("unsupported catalogue runtime")

    # A fixed per-user lease location prevents two differently configured
    # application instances from bypassing the same GPU ownership lock.
    lease_path = app_data_dir() / f"gpu-{settings.gpu_index}.lock"
    return Scheduler(
        catalogue, factory, resource_reader(settings, catalogue),
        lease_path=lease_path, role_models=settings.role_models,
        launch_planner=launch_planner(settings, catalogue),
        idle_seconds=settings.idle_minutes * 60,
        queue_limit=settings.queue_limit,
        queue_timeout=settings.queue_timeout_seconds,
    )


def resource_reader(settings: RuntimeSettings, catalogue: Catalogue) -> Any:
    """GPU/RAM reader that counts attached external runtimes as occupied."""
    base = ResourceReader(settings.gpu_index)
    holders = strata_holders(catalogue, api_key_env=settings.strata_api_key_env)
    return OccupancyAwareReader(base, holders) if holders else base


def launch_planner(settings: RuntimeSettings, catalogue: Catalogue) -> LaunchPlanner | None:
    """A Marian launch planner when any entry asks for one (imported lazily)."""
    if not any(entry.planner == "marian" for entry in catalogue):
        return None
    return LaunchPlanner(settings.marian_planner(), settings.models_dir)
