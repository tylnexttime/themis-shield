"""python -m themis_shield.runtime: catalogue, calibration and foreground control.

start <id> owns the scheduler in the foreground until stop or Ctrl+C.
A second CLI process can query status or request shutdown through an
authenticated, loopback-only control endpoint. It exposes no inference API.

State: %LOCALAPPDATA%/themis-shield/runtime-<gpu>.json
This contains a local control credential, never prompts or model outputs.

status also reports the optional back ends (Strata attach, Marian planner,
alternative engines) under "backends"; see runtime/backends.py.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from typing import Any

import httpx
import psutil

from ..inference import RuntimeFailure
from ..inference.json_output import strict_loads
from .backends import backend_status
from .calibration import CalibrationStore, calibrate
from .catalogue import Catalogue
from .resources import read_identity
from .settings import RuntimeSettings, app_data_dir, build_runtime, resource_reader


def _state_path(settings: RuntimeSettings) -> Path:
    return app_data_dir() / f"runtime-{settings.gpu_index}.json"


def _read_state(settings: RuntimeSettings) -> dict[str, Any] | None:
    path = _state_path(settings)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 4096:
            raise ValueError
        state = strict_loads(path.read_bytes())
        if (
            not isinstance(state, dict)
            or set(state) != {"pid", "created", "port", "key"}
            or type(state["pid"]) is not int
            or type(state["port"]) is not int
            or not 1 <= state["port"] <= 65535
            or not isinstance(state["key"], str)
            or len(state["key"]) < 32
            or abs(psutil.Process(state["pid"]).create_time() - state["created"]) > 0.01
        ):
            raise ValueError
        return state
    except (OSError, psutil.Error, RuntimeFailure, ValueError, TypeError, KeyError):
        raise RuntimeFailure("runtime control state stale or malformed") from None


def _control(settings: RuntimeSettings, action: str) -> dict[str, Any] | None:
    state = _read_state(settings)
    if state is None:
        return None
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=10,
        ) as client:
            response = client.request(
                "POST" if action == "stop" else "GET",
                f"http://127.0.0.1:{state['port']}/{action}",
                headers={"Authorization": f"Bearer {state['key']}"},
            )
            if response.status_code != 200 or len(response.content) > 1024 * 1024:
                raise RuntimeFailure("local runtime control unavailable")
            result = strict_loads(response.content)
            if not isinstance(result, dict):
                raise RuntimeFailure("local runtime control malformed")
            return result
    except httpx.HTTPError:
        raise RuntimeFailure("local runtime control unavailable") from None


def _serve(settings: RuntimeSettings, catalogue: Catalogue, model_id: str) -> None:
    scheduler = build_runtime(settings, catalogue)
    server: ThreadingHTTPServer | None = None
    path = _state_path(settings)
    wrote_state = False
    key = secrets.token_urlsafe(32)
    try:
        with scheduler.acquire(model_id):
            pass

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, code: int, value: dict[str, Any]) -> None:
                body = json.dumps(value, allow_nan=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorised(self) -> bool:
                return hmac.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {key}",
                ) and self.headers.get("Host") == f"127.0.0.1:{server.server_port}"

            def do_GET(self) -> None:
                if not self._authorised():
                    self._reply(403, {"status": "denied"})
                elif self.path == "/status":
                    self._reply(200, scheduler.status())
                else:
                    self._reply(404, {"status": "not found"})

            def do_POST(self) -> None:
                if not self._authorised():
                    self._reply(403, {"status": "denied"})
                elif self.path == "/stop":
                    self._reply(200, {"status": "stopping"})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    self._reply(404, {"status": "not found"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "pid": os.getpid(),
            "created": psutil.Process().create_time(),
            "port": server.server_port,
            "key": key,
        }
        # The device lease prevents another valid owner. A stale state file
        # can therefore be safely replaced after ownership was obtained.
        temporary = path.with_name(f".runtime-{secrets.token_hex(8)}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            wrote_state = True
        finally:
            temporary.unlink(missing_ok=True)
        print(json.dumps({
            "status": "started; foreground owner",
            "model_id": model_id,
            "control": "loopback authenticated",
        }), flush=True)
        server.serve_forever(poll_interval=0.2)
    finally:
        if server is not None:
            server.server_close()
        scheduler.close()
        if wrote_state:
            path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="themis runtime", description="Local model runtime control.")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--catalogue", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="catalogue entries with speed and memory labels")
    sub.add_parser(
        "status",
        help="scheduler, resources and back ends (Strata attach, Marian planner, alternative engines)",
    )
    sub.add_parser("stop", help="stop a foreground runtime owner")
    sub.add_parser("start", help="own the scheduler in the foreground with one model").add_argument("id")
    sub.add_parser("calibrate", help="measure one llama.cpp entry on this device").add_argument("id")
    args = parser.parse_args(argv)
    try:
        settings = RuntimeSettings.load(args.settings)
        if args.catalogue is not None:
            settings = replace(settings, catalogue_file=args.catalogue)
        catalogue = Catalogue.load(settings.catalogue_file)
        store = CalibrationStore(settings.storage_dir / "calibration.json")
        if args.command == "list":
            try:
                fingerprint = read_identity(settings.gpu_index).fingerprint
            except RuntimeFailure:
                fingerprint = None
            view = catalogue.with_calibration(store, fingerprint)
            print(json.dumps(
                [entry.display_record() for entry in view], indent=2,
            ))
        elif args.command == "status":
            status = _control(settings, "status")
            if status is None:
                status = {
                    "status": "not running", "loaded_models": [],
                    "queue": 0,
                    "resources": resource_reader(settings, catalogue)().record(),
                }
            status["backends"] = backend_status(settings, catalogue)
            print(json.dumps(status, indent=2))
        elif args.command == "stop":
            print(json.dumps(
                _control(settings, "stop") or {"status": "not running"},
            ))
        elif args.command == "start":
            catalogue.get(args.id)
            _serve(settings, catalogue, args.id)
        elif args.command == "calibrate":
            identity = read_identity(settings.gpu_index)
            with build_runtime(settings, catalogue) as scheduler:
                print(json.dumps(
                    calibrate(scheduler, args.id, store, identity), indent=2,
                ))
        return 0
    except KeyboardInterrupt:
        return 130
    except (RuntimeFailure, ValueError, OSError, TypeError):
        # Do not print arbitrary exceptions, paths, server bodies or credentials.
        print(
            "local runtime command held: check configuration, ownership and memory",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
