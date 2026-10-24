"""Strata attach adapter: an optional local answering model that the user runs.

Strata (MIT, https://github.com/Niko1221/Strata) is a mixture-of-experts-only
local runtime. It streams experts between GPU, RAM and SSD and serves an
OpenAI-compatible endpoint. themis-shield never starts, installs, updates or
configures Strata. The user starts it their own way, and themis *attaches* to
a configured loopback endpoint:

- The endpoint must be plain http on 127.0.0.1 ("localhost" is normalised to
  127.0.0.1). Anything else is refused at configuration time.
- ``GET /health`` must report ``service: "strata"``, ``loaded: true`` and the
  configured model. ``GET /v1/models`` must list the configured model id.
- Requests go through the OpenAI-compatible local client with Strata
  defaults: a ``max_tokens`` floor (a thinking model spends its reasoning out
  of ``max_tokens``), the model card's sampling, one request in flight, and a
  per-request timeout derived from the expected prefill and decode speeds.
- ``reasoning_content`` is never returned: only ``message.content`` is.

Role: answering only, for local-only work. Strata is not evaluated as a PII
finder, so structured (finder) output is refused. While Strata is attached,
its GPU memory counts as occupied (see ``themis_shield.runtime.occupancy``),
so the scheduler does not start a llama-server beside it that cannot fit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
import threading
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

from . import (
    InferenceCapabilities, InferenceRequest, InferenceResponse,
    RuntimeFailure, TargetUnavailable,
)
from .openai_local import OpenAILocalTarget

# Reference figures (8 GB laptop GPU, 64K context): decode ~12 tok/s,
# prefill ~100 tok/s. A catalogue entry's own figures take precedence.
REFERENCE_PROMPT_TOK_S = 100.0
REFERENCE_DECODE_TOK_S = 12.0
DEFAULT_MIN_MAX_TOKENS = 8192
DEFAULT_API_KEY_ENV = "STRATA_API_KEY"
# Sampling recommended by the model card of the Qwen thinking models Strata
# serves. Greedy decoding is discouraged for thinking models (it loops).
STRATA_SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.05}
# Timeout model: slack + factor x (estimated prompt / prefill + max_tokens / decode).
TIMEOUT_SLACK_SECONDS = 60.0
TIMEOUT_FACTOR = 1.5
TIMEOUT_FLOOR_SECONDS = 120.0
CHARS_PER_TOKEN = 2.0          # conservative: over-estimates tokens, so the timeout errs long
LOOPBACK_NAMES = {"127.0.0.1", "localhost"}
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")


def loopback_origin(endpoint: str) -> str:
    """Return ``http://127.0.0.1:<port>`` for a loopback endpoint, else refuse.

    Accepts ``http://127.0.0.1:<port>`` or ``.../v1`` (``localhost`` is
    normalised). Refuses other hosts, https, credentials, queries and paths.
    """
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 200:
        raise ValueError("Strata endpoint must be an http loopback URL")
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("invalid Strata endpoint port") from None
    if (
        parsed.scheme != "http"
        or (parsed.hostname or "").lower() not in LOOPBACK_NAMES
        or parsed.username or parsed.password
        or parsed.query or parsed.fragment
        or parsed.path.rstrip("/") not in ("", "/v1")
    ):
        raise ValueError("Strata endpoint must be http on 127.0.0.1 (loopback only)")
    if port is None or not 1 <= port <= 65535:
        raise ValueError("Strata endpoint needs an explicit port")
    return f"http://127.0.0.1:{port}"


@dataclass(frozen=True, slots=True)
class StrataConfig:
    """Where an already running Strata is, and how to talk to it.

    ``endpoint`` and ``model_id`` unset means "not configured": the target
    then never claims to be available.
    """

    endpoint: str | None = None
    model_id: str | None = None
    ctx: int = 65536
    min_max_tokens: int = DEFAULT_MIN_MAX_TOKENS
    expected_prompt_tok_s: float = REFERENCE_PROMPT_TOK_S
    expected_tok_s: float = REFERENCE_DECODE_TOK_S
    api_key_env: str | None = DEFAULT_API_KEY_ENV
    probe_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if self.endpoint is not None:
            loopback_origin(self.endpoint)
        if self.model_id is not None and (
            not isinstance(self.model_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.model_id)
        ):
            raise ValueError("invalid Strata model id")
        if (
            type(self.ctx) is not int or not 256 <= self.ctx <= 1_048_576
            or type(self.min_max_tokens) is not int
            or not 1 <= self.min_max_tokens <= self.ctx
        ):
            raise ValueError("invalid Strata context or max_tokens floor")
        for value in (self.expected_prompt_tok_s, self.expected_tok_s, self.probe_timeout_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid Strata speed or timeout")
        if self.api_key_env is not None and not _ENV_NAME.fullmatch(self.api_key_env):
            raise ValueError("invalid Strata API key variable name")

    @property
    def configured(self) -> bool:
        return self.endpoint is not None and self.model_id is not None

    @classmethod
    def from_entry(cls, entry: Any, *, api_key_env: str | None = DEFAULT_API_KEY_ENV) -> StrataConfig:
        """Build from a catalogue entry (runtime ``strata``)."""
        return cls(
            endpoint=getattr(entry, "endpoint", None),
            model_id=getattr(entry, "served_model", None),
            ctx=entry.ctx,
            min_max_tokens=min(
                getattr(entry, "min_max_tokens", None) or DEFAULT_MIN_MAX_TOKENS, entry.ctx,
            ),
            expected_prompt_tok_s=entry.expected_prompt_tok_s or REFERENCE_PROMPT_TOK_S,
            expected_tok_s=entry.expected_tok_s or REFERENCE_DECODE_TOK_S,
            api_key_env=api_key_env,
        )


class StrataTarget(OpenAILocalTarget):
    name = "strata"
    capabilities = InferenceCapabilities(streaming=True, structured_output=False)
    pid = None  # an externally owned process: never ours to sample or stop
    connect_timeout_seconds = 0.5  # loopback: a listening Strata accepts at once

    def __init__(self, config: StrataConfig | None = None) -> None:
        self.config = config or StrataConfig()
        origin = (
            loopback_origin(self.config.endpoint)
            if self.config.endpoint is not None else "http://127.0.0.1:1"
        )
        super().__init__(
            origin, self.config.model_id or "unconfigured", name="strata",
            timeout_seconds=self.config.probe_timeout_seconds,
            health_path="/health",
        )
        self.load_ms = 0.0
        self.attached = False
        self.attach_info: dict[str, Any] = {}
        self._inflight = threading.Lock()

    # -- transport -------------------------------------------------------

    def _refresh_auth(self) -> None:
        # Read at use time; the key is never logged, stored or returned.
        key = os.environ.get(self.config.api_key_env) if self.config.api_key_env else None
        if key and key.isascii() and key.isprintable() and " " not in key and len(key) >= 8:
            self._headers = {"Authorization": f"Bearer {key}"}
        else:
            self._headers = {}

    def _exchange(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self._refresh_auth()
        return super()._exchange(method, path, body)

    def _probe_get(self, path: str) -> Any:
        saved = self.timeout_seconds
        self.timeout_seconds = self.config.probe_timeout_seconds
        try:
            return self._exchange("GET", path)
        finally:
            self.timeout_seconds = saved

    # -- attach ------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Content-free readiness facts from ``/health``; never raises."""
        facts: dict[str, Any] = {
            "configured": self.config.configured, "reachable": False,
            "service_ok": False, "loaded": False, "model_ok": False,
            "max_context": None,
        }
        if not self.config.configured:
            return facts
        try:
            health = self._probe_get("/health")
        except (RuntimeFailure, ValueError):
            return facts
        facts["reachable"] = True
        if not isinstance(health, dict):
            return facts
        facts["service_ok"] = health.get("service") == "strata"
        facts["loaded"] = health.get("loaded") is True
        facts["model_ok"] = health.get("model") == self.config.model_id
        context = health.get("max_context")
        if type(context) is int and context > 0:
            facts["max_context"] = context
        return facts

    def health(self) -> bool:
        facts = self.probe()
        return facts["service_ok"] and facts["loaded"] and facts["model_ok"]

    def served_models(self) -> list[str]:
        result = self._probe_get("/v1/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise RuntimeFailure("Strata model list malformed")
        return [
            item["id"] for item in result["data"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

    def start(self) -> None:
        """Attach to the running Strata. Never starts, installs or updates it."""
        if not self.config.configured:
            raise TargetUnavailable(
                "Strata not configured: set endpoint and served_model in the catalogue entry"
            )
        began = time.monotonic()
        facts = self.probe()
        if not facts["reachable"]:
            raise TargetUnavailable("Strata not reachable: start Strata yourself, then retry")
        if not facts["service_ok"]:
            raise TargetUnavailable("configured endpoint is not a Strata server")
        if not facts["loaded"]:
            raise TargetUnavailable("Strata is running but its model is not loaded")
        try:
            served = self.served_models()
        except (RuntimeFailure, ValueError):
            raise TargetUnavailable("Strata model list unavailable") from None
        if not facts["model_ok"] or self.config.model_id not in served:
            raise TargetUnavailable("Strata serves a different model than configured")
        self.attach_info = {"max_context": facts["max_context"]}
        self.attached = True
        self.load_ms = (time.monotonic() - began) * 1000

    def stop(self) -> None:
        """Detach. Strata keeps running: it is the user's process, not ours."""
        self.attached = False
        self._headers = {}

    # -- requests ----------------------------------------------------------

    def effective_max_tokens(self, max_tokens: int) -> int:
        return min(max(max_tokens, self.config.min_max_tokens), self.config.ctx)

    def request_timeout(self, prompt_chars: int, max_tokens: int) -> float:
        """Seconds for one request, from the expected prefill and decode speeds."""
        prompt_tokens = math.ceil(max(0, prompt_chars) / CHARS_PER_TOKEN)
        decode = max_tokens / self.config.expected_tok_s
        estimate = TIMEOUT_SLACK_SECONDS + TIMEOUT_FACTOR * (
            prompt_tokens / self.config.expected_prompt_tok_s + decode
        )
        ceiling = TIMEOUT_SLACK_SECONDS + TIMEOUT_FACTOR * (
            self.config.ctx / self.config.expected_prompt_tok_s + decode
        )
        return max(TIMEOUT_FLOOR_SECONDS, min(estimate, ceiling))

    def _request_body(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = super()._request_body(messages, self.effective_max_tokens(max_tokens), None)
        # Strata keeps its own prefix cache; the llama.cpp-only key is dropped.
        body.pop("cache_prompt", None)
        body.update(STRATA_SAMPLING)
        return body

    def chat(
        self,
        messages: InferenceRequest | list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> InferenceResponse | dict[str, Any] | Iterator[dict[str, Any]]:
        legacy = isinstance(messages, InferenceRequest)
        payload = messages.payload if legacy else None
        if response_format is not None or (
            isinstance(payload, dict) and payload.get("response_format") is not None
        ):
            raise TargetUnavailable("Strata is not evaluated for structured output")
        if not self.attached or not self.health():
            raise TargetUnavailable("Strata not attached")
        if not self._inflight.acquire(blocking=False):
            raise TargetUnavailable("Strata busy: one request at a time")
        try:
            raw = payload.get("messages") if isinstance(payload, dict) else messages
            chars = sum(
                len(item["content"]) for item in raw
                if isinstance(item, dict) and isinstance(item.get("content"), str)
            ) if isinstance(raw, list) else 0
            requested = (
                payload.get("max_tokens", max_tokens) if isinstance(payload, dict) else max_tokens
            )
            if type(requested) is not int or requested <= 0:
                raise ValueError("invalid local chat limits")
            self.timeout_seconds = self.request_timeout(chars, self.effective_max_tokens(requested))
            return super().chat(messages, stream=stream, max_tokens=max_tokens)
        finally:
            self.timeout_seconds = self.config.probe_timeout_seconds
            self._inflight.release()

    def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TargetUnavailable(
            "Strata is an answering model; it is not evaluated as a PII finder"
        )

    # -- status ------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """A content-free subset of ``GET /metrics``: engine figures and live state."""
        try:
            result = self._probe_get("/metrics")
        except (RuntimeFailure, ValueError):
            return {}
        if not isinstance(result, dict):
            return {}
        engine = result.get("engine") if isinstance(result.get("engine"), dict) else {}
        live = result.get("live") if isinstance(result.get("live"), dict) else {}
        picked: dict[str, Any] = {}
        for key in ("vram_free_mib", "expert_cache_mib", "context", "max_context"):
            value = engine.get(key)
            if type(value) is int and value >= 0:
                picked[key] = value
        for key in ("kv", "version"):
            value = engine.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,32}", value):
                picked[key] = value
        state = live.get("state")
        if isinstance(state, str) and re.fullmatch(r"[a-z_-]{1,24}", state):
            picked["state"] = state
        queued = live.get("queued")
        if type(queued) is int and queued >= 0:
            picked["queued"] = queued
        return picked
