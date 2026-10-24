"""Owned llama.cpp server lifecycle.

Default engine and weights locations come from :mod:`themis_shield.paths`
and are overridden by runtime settings.

No shell, downloads, prompt logging, wildcard listener or cloud fallback.
Only numeric/cache tuning arguments are configurable. An entry may name an
alternative engine binary (for example a llama-server built with expert-cache
patches) below the runtime's engines directory; such an engine may receive a
small allow-list of extra numeric or enumerated flags (``ENGINE_EXTRA_*``).
The runtime log records which planner chose the arguments (see
``themis_shield.inference.marian``), as fixed codes and validated numbers. The engine's log
verbosity is restricted; stdout/stderr are drained through a fixed-code
filter, never copied verbatim to disk. One CUDA-race retry is permitted.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Sequence

import psutil

from . import RuntimeFailure, TargetUnavailable
from .openai_local import OpenAILocalTarget
from ..paths import default_engine_path, default_models_dir

CUDA_RACE = "shared object initialization failed"

NUMERIC_ARGS = {
    "-ngl": (0, 999),
    "-ncmoe": (0, 999),
    "-t": (1, 256),
    "-c": (256, 1_048_576),
}
CACHE_ARGS = {"-ctk", "-ctv"}
CACHE_TYPES = {"f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl"}


# Extra flags an alternative engine (engine_path) may receive: expert-cache
# controls of Marian-patched llama-server builds. Never endpoint, logging,
# file or network flags.
ENGINE_EXTRA_NUMERIC = {
    "--moe-cache-mib": (0, 1_048_576),
    "--moe-tier-mib": (0, 1_048_576),
    "--moe-ring-mib": (1, 65_536),
}
ENGINE_EXTRA_CHOICES = {
    "--moe-miss-source": {"mmap", "ring", "tier"},
}


def engine_extra_args(args: Sequence[str]) -> list[str]:
    """Validate extra flags for an alternative engine; same pair rules as tuning."""
    if (
        not isinstance(args, (list, tuple))
        or len(args) % 2
        or not all(isinstance(item, str) for item in args)
    ):
        raise ValueError("engine args must be flag/value pairs")
    seen: set[str] = set()
    result: list[str] = []
    for flag, value in zip(args[::2], args[1::2]):
        if flag in seen:
            raise ValueError("duplicate engine flag")
        seen.add(flag)
        if flag in ENGINE_EXTRA_NUMERIC:
            if not value.isascii() or not value.isdecimal():
                raise ValueError("invalid numeric engine argument")
            low, high = ENGINE_EXTRA_NUMERIC[flag]
            if not low <= int(value) <= high:
                raise ValueError("engine argument out of range")
        elif flag in ENGINE_EXTRA_CHOICES:
            if value not in ENGINE_EXTRA_CHOICES[flag]:
                raise ValueError("unsupported engine argument value")
        else:
            raise ValueError("unsupported or protected engine argument")
        result += [flag, value]
    return result


def tuning_args(args: Sequence[str], ctx: int) -> list[str]:
    """Normalise protected defaults and reject endpoint/logging overrides."""
    if type(ctx) is not int or not 256 <= ctx <= 1_048_576:
        raise ValueError("invalid model context")
    if (
        not isinstance(args, (list, tuple))
        or len(args) % 2
        or not all(isinstance(item, str) for item in args)
    ):
        raise ValueError("model args must be flag/value pairs")
    values: dict[str, str] = {"-t": "8", "-c": str(ctx)}
    seen: set[str] = set()
    for flag, value in zip(args[::2], args[1::2]):
        if flag in seen:
            raise ValueError("duplicate model tuning flag")
        seen.add(flag)
        if flag in NUMERIC_ARGS:
            if not value.isascii() or not value.isdecimal():
                raise ValueError("invalid numeric model argument")
            low, high = NUMERIC_ARGS[flag]
            if not low <= int(value) <= high:
                raise ValueError("model argument out of range")
            if flag == "-c" and int(value) != ctx:
                raise ValueError("model context and -c disagree")
        elif flag in CACHE_ARGS:
            if value not in CACHE_TYPES:
                raise ValueError("unsupported model cache type")
        else:
            raise ValueError("unsupported or protected model argument")
        values[flag] = value
    return [item for pair in values.items() for item in pair]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LlamaCppTarget(OpenAILocalTarget):
    def __init__(
        self,
        entry: Any,
        *,
        engine_path: Path | None = None,
        models_dir: Path | None = None,
        logs_dir: Path,
        gpu_index: int = 0,
        startup_timeout: float = 600,
        retry_pause: float = 15,
        stop_timeout: float = 10,
        poll_interval: float = 0.2,
        command_prefix: Sequence[str] | None = None,
        child_registry: Any = None,
        extra_args: Sequence[str] | None = None,
        **client_options: Any,
    ) -> None:
        if (
            gpu_index < 0 or startup_timeout <= 0 or retry_pause < 0
            or stop_timeout <= 0 or poll_interval <= 0
        ):
            raise ValueError("invalid llama.cpp lifecycle settings")
        self.entry = entry
        self.mode = getattr(entry, "mode", "full")
        if self.mode not in {"full", "mmap"}:
            raise ValueError("invalid llama.cpp load mode")
        self.engine_path = Path(engine_path) if engine_path is not None else default_engine_path()
        self.models_dir = Path(models_dir) if models_dir is not None else default_models_dir()
        self.logs_dir = Path(logs_dir)
        self.gpu_index = gpu_index
        self.startup_timeout = startup_timeout
        self.retry_pause = retry_pause
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        # Test/deployment injection is Python API only, not catalogue JSON.
        self.command_prefix = tuple(command_prefix) if command_prefix else None
        # Records owned children so a hard-killed gate's servers can be reaped.
        self.child_registry = child_registry
        self._tuning = tuning_args(entry.args, entry.ctx)
        self._extra = engine_extra_args(
            tuple(extra_args) if extra_args is not None
            else tuple(getattr(entry, "engine_args", ()) or ())
        )
        self.alternative_engine = bool(getattr(entry, "engine_path", None))
        # Which planner chose the arguments; set by the scheduler before start.
        self.args_source = "catalogue"
        self.plan_reason = "static"
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._cuda_race = threading.Event()
        self._lifecycle = threading.RLock()
        self._log_lock = threading.Lock()
        self._key_path: Path | None = None
        self.log_path: Path | None = None
        super().__init__(
            "http://127.0.0.1:1", entry.id, **client_options,
        )

    def set_launch_plan(self, source: str, reason: str) -> None:
        from .marian import PlanDecision

        PlanDecision(source, reason)  # validates both codes
        self.args_source = source
        self.plan_reason = reason

    def command(self, model: Path, port: int) -> list[str]:
        """The exact argv; shell is never used."""
        return [
            *(self.command_prefix or (str(self.engine_path),)),
            "-m", str(model),
            "-fa", "on",
            *(("-lm", "none") if self.mode == "full" else ()),
            "--reasoning", "off",
            "-np", "1", *self._tuning, *self._extra,
            "--host", "127.0.0.1", "--port", str(port),
            "--log-verbosity", "0",
            "--api-key-file", str(self._key_path),
        ]

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _log(self, code: str) -> None:
        if self.log_path is not None:
            with self._log_lock:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.time():.3f} {code}\n")

    def _drain(self, pipe: Any) -> None:
        # Fixed-sized reads also bound unterminated diagnostic lines.
        tail = b""
        try:
            while chunk := pipe.read(4096):
                text = (tail + chunk).decode("utf-8", errors="replace").lower()
                if CUDA_RACE in text:
                    self._cuda_race.set()
                tail = (tail + chunk)[-128:]
        finally:
            pipe.close()

    def _owned_listener(self, port: int) -> bool:
        if not self.running or self.pid is None:
            return False
        try:
            root = psutil.Process(self.pid)
            # The listener may belong to a child of the launched process (for example a venv python.exe launcher
            # stub that starts the real interpreter), so accept any process in the tree we started, and nothing else.
            owned = [root, *root.children(recursive=True)]
        except (psutil.Error, OSError):
            return False
        for process in owned:
            try:
                if any(
                    connection.status == psutil.CONN_LISTEN
                    and connection.laddr.ip == "127.0.0.1"
                    and connection.laddr.port == port
                    for connection in process.net_connections(kind="inet")
                ):
                    return True
            except (psutil.Error, OSError):
                continue
        return False

    def _wait_ready(self, port: int) -> bool:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if not self.running:
                return False
            if self._owned_listener(port) and super().health() and self.running:
                return True
            time.sleep(self.poll_interval)
        return False

    def health(self) -> bool:
        if not self.running:
            return False
        port = int(self.base_url.rsplit(":", 1)[1])
        return self._owned_listener(port) and super().health()

    def _stop_process(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=self.stop_timeout)
            except (OSError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=self.stop_timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=self.stop_timeout)
        if self.child_registry is not None:
            self.child_registry.untrack(proc.pid)
        if self._reader is not None:
            self._reader.join(timeout=self.stop_timeout)
            if self._reader.is_alive():
                raise RuntimeFailure("llama.cpp log reader did not stop")
        self._reader = None
        self._proc = None

    def start(self) -> None:
        with self._lifecycle:
            if self.running:
                if self.health():
                    return
                raise TargetUnavailable("owned llama.cpp server unhealthy")
            root = self.models_dir.resolve()
            model = (root / self.entry.file).resolve()
            if not model.is_relative_to(root) or not model.is_file():
                raise TargetUnavailable("configured GGUF unavailable")
            if self.command_prefix is None and not self.engine_path.is_file():
                raise TargetUnavailable("configured llama.cpp engine unavailable")
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            nonce = secrets.token_hex(12)
            self.log_path = self.logs_dir / f"server-{nonce}.log"
            self.log_path.touch(mode=0o600, exist_ok=False)
            self._key_path = self.logs_dir / f"key-{nonce}.txt"
            key = secrets.token_urlsafe(32)
            fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key)
            self._headers = {"Authorization": f"Bearer {key}"}
            # Fixed codes and allow-listed numbers only: never paths or content.
            self._log(f"ARGS_SOURCE {self.args_source} {self.plan_reason}")
            self._log("ARGS " + " ".join([*self._tuning, *self._extra]))
            if self.alternative_engine:
                self._log("ENGINE_ALTERNATIVE")
            began = time.monotonic()
            try:
                for attempt in range(2):
                    self._cuda_race.clear()
                    port = _free_port()
                    self.base_url = f"http://127.0.0.1:{port}"
                    cmd = self.command(model, port)
                    env = dict(os.environ)
                    env["CUDA_VISIBLE_DEVICES"] = str(self.gpu_index)
                    self._log(f"START_ATTEMPT_{attempt + 1}")
                    try:
                        self._proc = subprocess.Popen(
                            cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, shell=False,
                            creationflags=(
                                subprocess.CREATE_NEW_PROCESS_GROUP
                                if os.name == "nt" else 0
                            ),
                        )
                    except OSError:
                        raise TargetUnavailable("llama.cpp launch failed") from None
                    if self.child_registry is not None:
                        self.child_registry.track(self._proc.pid)
                    assert self._proc.stdout is not None
                    self._reader = threading.Thread(
                        target=self._drain, args=(self._proc.stdout,),
                        name="themis-llama-log", daemon=True,
                    )
                    self._reader.start()
                    if self._wait_ready(port):
                        self.load_ms = (time.monotonic() - began) * 1000
                        self._log("READY")
                        return
                    self._stop_process()  # Join before inspecting the error flag.
                    if attempt == 0 and self._cuda_race.is_set():
                        self._log("CUDA_RACE_RETRY")
                        time.sleep(self.retry_pause)
                        continue
                    raise TargetUnavailable("llama.cpp startup failed")
            except BaseException:
                self.stop()
                raise
            raise TargetUnavailable("llama.cpp startup failed")

    def stop(self) -> None:
        with self._lifecycle:
            self._stop_process()
            if self._key_path is not None:
                self._key_path.unlink(missing_ok=True)
                self._key_path = None
            self._headers = {}
            self._log("STOPPED")

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        if not self.health():
            raise TargetUnavailable("owned llama.cpp server unavailable")
        return super().chat(*args, **kwargs)
