"""Authenticated loopback hook transport, with an absolute client wait budget.

No engine, vault, Claude credential or transcript access exists here.
The only credential file read is themis-shield/gate-token.

An HTTP operation runs in a daemon thread. The caller stops waiting at the
absolute deadline even if a server drips bytes or an injected transport hangs.
Late server work cannot auto-send: these APIs never invoke a cloud connector.
"""

from __future__ import annotations

from themis_shield.paths import app_data_dir
from queue import Empty, Queue
import re
from threading import Thread
from urllib.parse import urlsplit

import httpx

from themis_shield.cloud.openai_compat import canonical, strict_json
from .protocol import EVENT_PATHS, MAX_BYTES, response_for


DEFAULT_BASE_URL = "http://127.0.0.1:8742"
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")


def token_path():
    return app_data_dir() / "gate-token"


def read_token():
    path = token_path()
    if (
        path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents)
        or not path.is_file()
        or path.stat().st_size > 4096
    ):
        raise ValueError("LOCAL_TOKEN_UNAVAILABLE")
    token = path.read_text(encoding="ascii").strip()
    if not TOKEN.fullmatch(token):
        raise ValueError("LOCAL_TOKEN_INVALID")
    return token


def budget(event):
    return 8.0 if event == "MessageDisplay" else 25.0


class HookClient:
    def __init__(
        self, base_url=DEFAULT_BASE_URL, *, exchange=None, wait_seconds=None,
    ):
        # Python-only injection for tests/managed launchers. No project/env URL.
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port is None or not 1 <= parsed.port <= 65535
        ):
            raise ValueError("LOOPBACK_URL_REQUIRED")
        self.base_url = base_url.rstrip("/")
        self.exchange = exchange
        self.wait_seconds = wait_seconds

    def _http(self, url, raw, headers, seconds):
        with httpx.Client(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(seconds),
        ) as client:
            with client.stream(
                "POST", url, content=raw, headers=headers,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";", 1)[0]
                    != "application/json"
                ):
                    raise ValueError("HOOK_HTTP_FAILURE")
                result = bytearray()
                for part in response.iter_bytes():
                    result.extend(part)
                    if len(result) > MAX_BYTES:
                        raise ValueError("HOOK_REPLY_BOUND")
                return bytes(result)

    def call(self, event, payload):
        raw = canonical(payload)
        if len(raw) > MAX_BYTES:
            raise ValueError("HOOK_BODY_BOUND")
        token = read_token()
        maximum = budget(event)
        seconds = maximum if self.wait_seconds is None else self.wait_seconds
        # Tests may shorten a deadline, never lengthen the managed budget.
        if not 0 < seconds <= maximum:
            raise ValueError("INVALID_HOOK_DEADLINE")
        result = Queue(maxsize=1)

        def worker():
            try:
                exchange = self.exchange or self._http
                reply = exchange(
                    self.base_url + "/api/hooks/" + EVENT_PATHS[event],
                    raw,
                    {
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    seconds,
                )
                result.put((True, reply))
            except Exception:
                result.put((False, None))

        Thread(target=worker, name="themis-hook-http", daemon=True).start()
        try:
            succeeded, reply = result.get(timeout=seconds)
        except Empty:
            raise TimeoutError("HOOK_DEADLINE") from None
        if not succeeded or not isinstance(reply, bytes) or len(reply) > MAX_BYTES:
            raise ValueError("HOOK_TRANSPORT_FAILED")
        value = strict_json(reply)
        return response_for(event, value, tool=payload.get("tool_name"))
