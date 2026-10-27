"""Single-process loopback launcher with a one-use browser bootstrap code."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys
from threading import Thread
import time
import webbrowser

import uvicorn

from themis_shield import startup
from themis_shield.integration import build_router, default_config_path
from .app import create_app, default_token_path, read_gate_token


# Retain the earlier integration entry point.
load_router = build_router


def provision_token():
    path = default_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_gate_token(path)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(secrets.token_urlsafe(32))
        stream.flush()
        os.fsync(stream.fileno())
    return read_gate_token(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="themis serve")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--port", type=int, default=8742)
    parser.add_argument("--open", action="store_true",
                        help="provision the gate token if needed and open the browser (port 8742)")
    parser.add_argument("--provision-token", action="store_true",
                        help="create the local gate token for hook clients and API callers, then exit")
    parser.add_argument("--skip-model-check", action="store_true",
                        help="start without the local detection stack (and without the start-up detector "
                             "canary); every request that needs it is blocked until the canary has passed")
    args = parser.parse_args(argv)
    if args.open and args.port != 8742:
        parser.error("--open uses the contract port")
    if args.provision_token:
        provision_token()
        print("Local gate token provisioned or already present.")
        return
    router = None
    try:
        startup.check_config(args.config)
        token = (
            provision_token() if args.open
            else startup.check_token(default_token_path(), read_gate_token)
        )
        startup.check_port(args.port)
        router = startup.build(args.config, build_router)
        startup.check_vault(router)
        if not args.skip_model_check:
            startup.check_models(router)
            # Known-answer check of every detector before serving (v0.3 detector canary).
            startup.check_canary(router)
        code = secrets.token_urlsafe(32) if args.open else None
        app = create_app(router, token=token, port=args.port, launcher_code=code)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=args.port,
            workers=1, access_log=False, log_level="warning",
        ))
        if args.open:
            def open_when_ready():
                deadline = time.monotonic() + 60
                while not server.started and not server.should_exit and time.monotonic() < deadline:
                    time.sleep(0.05)
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:8742/?t={code}")
            Thread(target=open_when_ready, name="themis-browser", daemon=True).start()
        server.run()
    except startup.StartupRefused as refusal:
        print(refusal.message, file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as error:
        print(startup.StartupRefused("STARTUP_FAILED", type(error).__name__).message, file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        if router is not None:
            try:
                router.cloud.close()
            finally:
                try:
                    router.vault.close()
                finally:
                    router.runtime.close()


if __name__ == "__main__":
    main()
