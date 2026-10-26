"""Bounded, non-proxying OpenAI-compatible loopback client.

Streaming is a validated buffered burst, deliberately not incremental model
streaming. HTTP environment proxies and redirects are disabled. Exceptions
never include request content, response content or HTTP error bodies.
"""

from __future__ import annotations

import math
import ssl
import threading
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx

from . import (
    InferenceCapabilities, InferenceRequest, InferenceResponse,
    RuntimeFailure, TargetUnavailable,
)
from .json_output import normalise_schema, strict_loads, validate_output

TIMING_KEYS = {
    "cache_n", "prompt_n", "prompt_ms", "prompt_per_second",
    "predicted_n", "predicted_ms", "predicted_per_second",
}


# llama-server's native endpoints the typed-decision tier may call (never a proxy, never a file or admin endpoint).
NATIVE_PATHS = frozenset({"/apply-template", "/tokenize", "/completion", "/v1/systemone"})

_NO_TLS: ssl.SSLContext | None = None
_NO_TLS_LOCK = threading.Lock()


def _no_tls_context() -> ssl.SSLContext:
    """A TLS context with no trust roots: any TLS use fails closed.

    The endpoint is always plain http on loopback, so TLS is never needed.
    Passing this instead of the default avoids loading the CA bundle on
    every request (about 0.3 s each on Windows).
    """
    global _NO_TLS
    with _NO_TLS_LOCK:
        if _NO_TLS is None:
            _NO_TLS = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return _NO_TLS


def safe_timings(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number for key, number in value.items()
        if key in TIMING_KEYS
        and type(number) in (int, float)
        and math.isfinite(number) and number >= 0
    }


class OpenAILocalTarget:
    capabilities = InferenceCapabilities(
        streaming=True, structured_output=True,
    )
    pid: int | None = None
    load_ms = 0.0
    # Optional shorter connect timeout. A loopback listener accepts at once;
    # a refused loopback connect can take seconds on Windows.
    connect_timeout_seconds: float | None = None

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        name: str = "laptop",
        api_key: str | None = None,
        timeout_seconds: float = 900,
        maximum_response_bytes: int = 4 * 1024 * 1024,
        maximum_request_bytes: int = 8 * 1024 * 1024,
        health_path: str = "/health",
        cache_prompt: bool = True,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") not in ("", "/v1")
        ):
            raise ValueError("local inference endpoint must be IPv4 loopback")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("invalid local inference port") from None
        if port is None or not 1 <= port <= 65535:
            raise ValueError("explicit local inference port required")
        if (
            timeout_seconds <= 0 or maximum_response_bytes <= 0
            or maximum_request_bytes <= 0
        ):
            raise ValueError("invalid local inference bounds")
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.maximum_response_bytes = maximum_response_bytes
        self.maximum_request_bytes = maximum_request_bytes
        self.health_path = health_path
        if type(cache_prompt) is not bool:
            raise ValueError("invalid prompt-cache setting")
        # Reuse the server's KV cache for a shared prompt prefix (the finder's fixed system message). The slot keeps
        # the previous prompt in local GPU memory either way; this only skips recomputing the common prefix.
        # llama.cpp notes that logits may then differ slightly between batch sizes (not bit-for-bit reproducible).
        self.cache_prompt = cache_prompt
        self.last_timings: dict[str, Any] = {}
        self._headers = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )

    def _exchange(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, root: str | None = None,
    ) -> Any:
        import json

        raw: bytes | None = None
        if body is not None:
            try:
                raw = json.dumps(
                    body, ensure_ascii=False, allow_nan=False,
                ).encode("utf-8")
            except (ValueError, TypeError, RecursionError):
                raise ValueError("invalid local inference request") from None
            if len(raw) > self.maximum_request_bytes:
                raise ValueError("local inference request exceeds bound")
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            timeout: float | httpx.Timeout = self.timeout_seconds
            if self.connect_timeout_seconds is not None:
                timeout = httpx.Timeout(
                    self.timeout_seconds,
                    connect=min(self.timeout_seconds, self.connect_timeout_seconds),
                )
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=timeout,
                verify=_no_tls_context(),
            ) as client:
                with client.stream(
                    method, (self.base_url if root is None else root) + path, content=raw, headers=headers,
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeFailure("local inference HTTP failure")
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > self.maximum_response_bytes:
                            raise RuntimeFailure("local inference response exceeds bound")
        except (httpx.HTTPError, OSError):
            raise RuntimeFailure("local inference transport failed") from None
        return strict_loads(bytes(data))

    def native_json(self, path: str, body: dict[str, Any]) -> Any:
        """POST to one of llama-server's native endpoints (typed-decision tier only): template rendering,
        tokenisation, a one-token completion with top log-probabilities, or a decision model. Fixed allow-list; the
        same loopback, size and no-proxy bounds as chat; bodies and replies are never logged."""
        if path not in NATIVE_PATHS or not isinstance(body, dict):
            raise ValueError("unsupported native local endpoint")
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        return self._exchange("POST", path, body, root=root)

    @property
    def completion_path(self) -> str:
        return (
            "/chat/completions"
            if self.base_url.endswith("/v1") else "/v1/chat/completions"
        )

    def start(self) -> None:
        if not self.health():
            raise TargetUnavailable("configured local endpoint unavailable")

    def stop(self) -> None:
        """An external endpoint is not an owned process."""

    def health(self) -> bool:
        try:
            self._exchange("GET", self.health_path)
            return True
        except RuntimeFailure:
            return False

    def chat(
        self,
        messages: InferenceRequest | list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> InferenceResponse | dict[str, Any] | Iterator[dict[str, Any]]:
        legacy = isinstance(messages, InferenceRequest)
        if legacy:
            payload = messages.payload
            if not isinstance(payload, dict):
                raise ValueError("local chat request must be an object")
            allowed = {
                "messages", "stream", "response_format", "max_tokens",
                "temperature", "model",
            }
            if set(payload) - allowed or payload.get("stream") is True:
                raise ValueError("unsupported legacy local chat request")
            response_format = payload.get("response_format")
            max_tokens = payload.get("max_tokens", max_tokens)
            messages = payload.get("messages")
        if type(stream) is not bool or type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("invalid local chat limits")
        if not isinstance(messages, list) or not messages:
            raise ValueError("local chat messages required")
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise ValueError("unsupported local chat message")
        if response_format is not None:
            if (
                not isinstance(response_format, dict)
                or set(response_format) != {"type", "json_schema"}
                or response_format["type"] != "json_schema"
            ):
                raise ValueError("unsupported local response format")
            response_format = {
                "type": "json_schema",
                "json_schema": normalise_schema(response_format["json_schema"]),
            }
        body = self._request_body(messages, max_tokens, response_format)
        self.last_timings = {}
        result = self._exchange("POST", self.completion_path, body)
        try:
            if not isinstance(result, dict) or "error" in result:
                raise ValueError
            choices = result["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            finish = choice["finish_reason"]
            message = choice["message"]
            content = message["content"]
            if (
                finish not in {"stop", "length"}
                or not isinstance(content, str)
                or message.get("tool_calls") or message.get("function_call")
                or (legacy and finish != "stop")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise RuntimeFailure("local inference output incomplete or malformed") from None
        self.last_timings = safe_timings(result.get("timings"))
        # Return only the supported envelope; do not pass opaque server fields.
        output: dict[str, Any] = {
            "object": "chat.completion",
            "model": self.model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }],
            "timings": dict(self.last_timings),
        }
        usage = result.get("usage")
        if isinstance(usage, dict):
            output["usage"] = {
                key: val for key, val in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(val) is int and val >= 0
            }
        if legacy:
            return InferenceResponse(content, dict(self.last_timings), finish)
        if stream:
            return self._burst(output)
        return output

    def _request_body(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The validated wire body. Subclasses may adjust sampling or limits."""
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "max_tokens": max_tokens,
            "cache_prompt": self.cache_prompt,
        }
        if response_format is not None:
            body["response_format"] = response_format
        return body

    @staticmethod
    def _burst(output: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """OpenAI chunk dictionaries. An HTTP adapter adds SSE and [DONE]."""
        common = {"object": "chat.completion.chunk", "model": output["model"]}
        choice = output["choices"][0]
        yield {
            **common,
            "choices": [{
                "index": 0, "delta": {"role": "assistant"},
                "finish_reason": None,
            }],
        }
        yield {
            **common,
            "choices": [{
                "index": 0, "delta": {"content": choice["message"]["content"]},
                "finish_reason": None,
            }],
        }
        yield {
            **common,
            "choices": [{
                "index": 0, "delta": {},
                "finish_reason": choice["finish_reason"],
            }],
            "timings": output["timings"],
            **({"usage": output["usage"]} if "usage" in output else {}),
        }

    def chat_json(
        self,
        model_role: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        if not isinstance(model_role, str) or not model_role:
            raise ValueError("model role required")
        wrapper = normalise_schema(schema)
        output = self.chat(
            messages, response_format={
                "type": "json_schema", "json_schema": wrapper,
            }, max_tokens=max_tokens,
        )
        assert isinstance(output, dict)
        if output["choices"][0]["finish_reason"] != "stop":
            raise RuntimeFailure("local JSON output truncated")
        value = strict_loads(output["choices"][0]["message"]["content"])
        validate_output(value, wrapper["schema"])
        if not isinstance(value, dict):
            raise RuntimeFailure("local JSON output must be an object")
        return value
