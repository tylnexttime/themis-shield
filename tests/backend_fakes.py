"""Fakes for the Strata and Marian back-end tests. No GPU, no models.

- FakeStrata: a loopback HTTP server that answers like Strata's /health,
  /v1/models, /metrics and /v1/chat/completions, and records what it got.
- install_fake_marian: puts a tiny planner package called ``marian`` into
  sys.modules with the same plan API (load, choose, to_config) and the same
  choice rule (smallest measured context that fits, then fastest).
- write_fake_engine: an executable "llama-server" in a directory, which
  records its argv and then serves like tests/fake_llama_server.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import types
from typing import Any

CANARY = "SYNTHETIC-STRATA-PROMPT-CANARY"


class FakeStrata:
    def __init__(self, *, model: str = "fake-moe-model", api_key: str | None = None) -> None:
        self.model = model
        self.service = "strata"
        self.loaded = True
        self.health_model: str | None = None      # None: same as model
        self.listed: list[str] | None = None      # None: [model]
        self.api_key = api_key
        self.content = "Synthetic local answer."
        self.reasoning = "SYNTHETIC-REASONING-NOT-FOR-OUTPUT"
        self.finish = "stop"
        self.delay = 0.0
        self.requests: list[dict[str, Any]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.chat_headers: list[dict[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *unused: Any) -> None:
                pass

            def reply(self, code: int, value: Any) -> None:
                raw = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorised(self) -> bool:
                if fake.api_key is None:
                    return True
                if self.headers.get("Authorization") == f"Bearer {fake.api_key}":
                    return True
                self.reply(401, {"error": {"message": "unauthorised"}})
                return False

            def do_GET(self) -> None:
                fake.requests.append({"method": "GET", "path": self.path})
                if self.path in ("/health", "/api/health"):
                    self.reply(200, {
                        "status": "ok", "max_context": 65536,
                        "model": fake.health_model or fake.model, "images": False,
                        "api_key": fake.api_key is not None, "image_generation": False,
                        "loaded": fake.loaded, "service": fake.service,
                    })
                elif self.path in ("/v1/models", "/models"):
                    if not self.authorised():
                        return
                    ids = fake.listed if fake.listed is not None else [fake.model]
                    self.reply(200, {"object": "list", "data": [
                        {"id": item, "object": "model", "status": {"value": "loaded"}}
                        for item in ids
                    ]})
                elif self.path.startswith("/metrics"):
                    if not self.authorised():
                        return
                    self.reply(200, {
                        "engine": {
                            "model": fake.model, "context": 65536, "kv": "int8",
                            "vram_free_mib": 427, "expert_cache_mib": 1127,
                            "version": "0.1.46", "note": CANARY,
                        },
                        "live": {"state": "idle", "queued": 0, "phase": CANARY},
                        "requests": [{"id": "r1", "prompt": CANARY}],
                    })
                else:
                    self.reply(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                fake.requests.append({"method": "POST", "path": self.path})
                if not self.authorised():
                    return
                if self.path != "/v1/chat/completions":
                    self.reply(404, {"error": {"message": "not found"}})
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.chat_bodies.append(body)
                fake.chat_headers.append(dict(self.headers))
                if fake.delay:
                    time.sleep(fake.delay)
                self.reply(200, {
                    "id": "chatcmpl-fake", "object": "chat.completion",
                    "model": fake.model,
                    "choices": [{
                        "index": 0, "finish_reason": fake.finish,
                        "message": {
                            "role": "assistant", "content": fake.content,
                            "reasoning_content": fake.reasoning,
                        },
                    }],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42},
                })

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> FakeStrata:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# -- Marian ----------------------------------------------------------------

@dataclass
class FakeConfig:
    ctx: int = 4096
    n_gpu_layers: int = 999
    n_cpu_moe: int | None = None
    cache_type: str = "f16"
    threads: int | None = None
    load_mode: str = "none"
    flash_attn: str = "on"
    moe_cache_mib: int | None = None
    extra: list[str] = field(default_factory=list)


@dataclass
class MarianCalls:
    load: int = 0
    last_need: int | None = None
    last_objective: str | None = None
    last_dir: Path | None = None


def install_fake_marian(
    monkeypatch: Any,
    rows: list[dict[str, Any]],
    *,
    profiles_dir: Path | None = None,
    raise_on_load: bool = False,
    config_overrides: dict[str, Any] | None = None,
) -> MarianCalls:
    calls = MarianCalls()
    package = types.ModuleType("marian")
    package.__path__ = []  # a package
    plan = types.ModuleType("marian.plan")
    cli = types.ModuleType("marian.cli")
    cli.PROFILES = profiles_dir

    def load(profile_dir: Path, model: Path) -> list[dict[str, Any]]:
        calls.load += 1
        calls.last_dir = Path(profile_dir)
        if raise_on_load:
            raise RuntimeError(f"synthetic failure reading {model}")
        return [dict(row) for row in rows]

    def choose(found: list[dict[str, Any]], need: int, objective: str = "tg") -> dict | None:
        calls.last_need, calls.last_objective = need, objective
        key = "tg_tps" if objective == "tg" else "pp_tps"
        fits = [row for row in found if row.get("status") == "ok" and row["ctx"] >= need]
        if not fits:
            return None
        smallest = min(row["ctx"] for row in fits)
        return max((row for row in fits if row["ctx"] == smallest), key=lambda row: row.get(key) or 0)

    def to_config(row: dict[str, Any]) -> FakeConfig:
        config = FakeConfig(ctx=row["ctx"], cache_type=row["kv"], threads=row.get("threads"))
        if "n_cpu_moe" in row:
            config.n_gpu_layers, config.n_cpu_moe = 999, row["n_cpu_moe"]
        else:
            config.n_gpu_layers = row["n_gpu_layers"]
        for key, value in (config_overrides or {}).items():
            setattr(config, key, value)
        return config

    plan.load, plan.choose, plan.to_config = load, choose, to_config
    package.plan, package.cli = plan, cli
    monkeypatch.setitem(sys.modules, "marian", package)
    monkeypatch.setitem(sys.modules, "marian.plan", plan)
    monkeypatch.setitem(sys.modules, "marian.cli", cli)
    return calls


def profile_rows() -> list[dict[str, Any]]:
    """Rows shaped like a real Marian profile (8 GB GPU, MoE model)."""
    common = {"threads": 8, "_engine": "llama.cpp", "_file": "abc123-def456-llama.cpp.json"}
    return [
        {"ctx": 8192, "kv": "f16", "n_cpu_moe": 26, "pp_tps": 600.0, "tg_tps": 24.7,
         "status": "ok", "vram_peak_mib": 4868, **common},
        {"ctx": 16384, "kv": "f16", "n_cpu_moe": 22, "pp_tps": 640.0, "tg_tps": 25.9,
         "status": "ok", "vram_peak_mib": 6900, **common},
        {"ctx": 16384, "kv": "q8_0", "n_cpu_moe": 14, "pp_tps": 700.0, "tg_tps": 31.0,
         "status": "ok", "vram_peak_mib": 7300, **common},
        {"ctx": 16384, "kv": "q8_0", "n_cpu_moe": 10, "pp_tps": 720.0, "tg_tps": 6.0,
         "status": "overflow", "vram_peak_mib": 8000, **common},
        {"ctx": 32768, "kv": "q8_0", "n_cpu_moe": 20, "pp_tps": 500.0, "tg_tps": 20.0,
         "status": "ok", "vram_peak_mib": 7600, **common},
    ]


# -- engine ----------------------------------------------------------------

def write_fake_engine(directory: Path, name: str = "llama-server") -> Path:
    """An executable engine stand-in. It records argv to THEMIS_FAKE_ENGINE_ARGV."""
    directory.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("backend_fake_engine.py")
    if os.name == "nt":
        path = directory / f"{name}.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="ascii")
    else:
        path = directory / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="ascii")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
