"""OpenAI-compatible Chat Completions with three independently checked release conditions.

The endpoint, model and the *name* of the environment variable holding the API
key come from local configuration, never from a request. The key itself is read
from the environment at send time and is never written to configuration,
captures, logs or exceptions.

Capture files contain tokenised JSON, not HTTP headers or credentials.
Redirects, environment proxies and automatic inference retries are disabled.

Any provider that speaks the OpenAI Chat Completions streaming protocol can be
used (OpenAI, Azure OpenAI, vLLM or llama.cpp servers, gateways). See
``config/themis.example.toml`` for examples.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import Lock
import time
from typing import Iterator, Mapping
from urllib.parse import urlencode, urlsplit

import httpx

from ..detectors.markings import MarkingClass, marking_policy
from ..substitute import verify_no_originals


JOB_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")


class CloudFailure(RuntimeError):
    """A fixed code only; never include provider bodies or command output."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def strict_json(raw: str | bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate-json-key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-json-number")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)

    def check(item, depth=0):
        if depth > 32:
            raise ValueError("json-depth")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite-number")
        if isinstance(item, dict):
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)

    check(value)
    return value


def verify_json(raw: bytes, originals: tuple[str, ...], *, decode_arguments: bool = True) -> bool:
    """Check exact wire JSON, decoded leaves and decoded function arguments.

    The extra decoded checks close JSON-escape gaps in a raw lexical check.
    This remains a known-original check, not a detector-completeness claim.
    """
    verify_no_originals(raw.decode("utf-8"), originals)

    def walk(value):
        if isinstance(value, str):
            verify_no_originals(value, originals)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                verify_no_originals(key, originals)
                walk(item)
                # Streamed tool-call deltas carry partial JSON fragments: those get the raw-text check above, and
                # the assembled arguments are parsed strictly when the tool call is restored (ToolCallAccumulator).
                if key == "arguments" and isinstance(item, str) and decode_arguments:
                    walk(strict_json(item))

    walk(strict_json(raw))
    return True


AUTH_STYLES = {"bearer", "api-key"}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True, slots=True)
class CloudSettings:
    """Where and how to reach the one configured cloud model.

    ``base_url`` is the API root that ``/chat/completions`` is appended to,
    for example ``https://api.openai.com/v1``. HTTPS is required except for a
    loopback address, which is allowed for local OpenAI-compatible servers.
    ``api_key_env`` names the environment variable that holds the key.
    ``auth_style`` is ``bearer`` (``Authorization: Bearer``) or ``api-key``
    (an ``api-key`` header, as Azure OpenAI uses). ``api_version`` adds an
    ``api-version`` query parameter when set.
    """

    base_url: str
    model: str
    api_key_env: str = "THEMIS_CLOUD_API_KEY"
    auth_style: str = "bearer"
    api_version: str | None = None
    destination_id: str = "cloud"
    deployment_type: str = "global-standard"
    max_tokens: int = 32768
    timeout_seconds: float = 1800
    maximum_response_bytes: int = 8 * 1024 * 1024
    capture_dir: Path | None = None

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        loopback = parsed.hostname in _LOOPBACK
        if (
            parsed.scheme not in ("https", "http") or not parsed.hostname
            or (parsed.scheme == "http" and not loopback)
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError("cloud-base-url-must-be-https")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", self.model):
            raise ValueError("invalid-cloud-model")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.api_key_env):
            raise ValueError("invalid-api-key-env-name")
        if self.auth_style not in AUTH_STYLES:
            raise ValueError("invalid-auth-style")
        if self.api_version is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.api_version):
            raise ValueError("invalid-api-version")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.destination_id):
            raise ValueError("invalid-destination-id")
        if (
            type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 128000
            or self.timeout_seconds <= 0 or self.maximum_response_bytes <= 0
        ):
            raise ValueError("invalid-cloud-bounds")

    @property
    def url(self):
        url = self.base_url.rstrip("/") + "/chat/completions"
        if self.api_version:
            url += "?" + urlencode({"api-version": self.api_version})
        return url

    @property
    def captures(self) -> Path:
        if self.capture_dir is not None:
            return self.capture_dir
        from ..runtime.settings import app_data_dir
        return app_data_dir() / "cloud-payloads"


class ApiKeyCredential:
    """Read the API key from a named environment variable at send time.

    The value is never cached beyond the call, logged, or included in an
    exception. A missing or malformed key fails with a fixed code.
    """

    def __init__(self, env_name: str, *, environ: Mapping[str, str] | None = None):
        self.env_name = env_name
        self._environ = environ

    def secret(self) -> str:
        source = os.environ if self._environ is None else self._environ
        value = source.get(self.env_name, "")
        if (
            not isinstance(value, str) or len(value) < 8 or len(value) > 4096
            or any(char.isspace() or ord(char) < 33 for char in value)
        ):
            raise CloudFailure("CREDENTIAL_UNAVAILABLE")
        return value


@dataclass(frozen=True, slots=True)
class Outbound:
    job_id: str
    marking_class: MarkingClass
    payload: bytes = field(repr=False)
    manifest_hash: str
    verify_passed: bool
    originals: tuple[str, ...] = field(repr=False)


class Capture:
    def __init__(self, directory: Path, job_id: str):
        if not JOB_ID.fullmatch(job_id):
            raise CloudFailure("INVALID_JOB_ID")
        self.path = directory / f"{job_id}.jsonl"
        self._lock = Lock()

    def append(self, value: dict):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise OSError
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with self._lock:
                descriptor = os.open(self.path, flags, 0o600)
                try:
                    raw = canonical(value) + b"\n"
                    with os.fdopen(descriptor, "ab", closefd=False) as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    os.close(descriptor)
        except Exception:
            raise CloudFailure("CAPTURE_UNAVAILABLE") from None


class CloudStream:
    def __init__(self, response, context, outbound, capture, settings):
        self.response = response
        self.context = context
        self.outbound = outbound
        self.capture = capture
        self.settings = settings
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.context.__exit__(None, None, None)

    def __iter__(self) -> Iterator[dict]:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        pending = ""
        event_lines = []
        total = 0
        done = False
        finished = False
        began = time.monotonic()

        def event():
            nonlocal done, finished
            if not event_lines:
                return None
            text = "\n".join(event_lines)
            event_lines.clear()
            if text == "[DONE]":
                if not finished:
                    raise CloudFailure("CLOUD_INCOMPLETE")
                done = True
                return None
            try:
                value = strict_json(text)
                if not isinstance(value, dict) or "error" in value:
                    raise ValueError
                choices = value.get("choices")
                if not isinstance(choices, list) or len(choices) > 1:
                    raise ValueError
                # Some providers send an initial annotation chunk with no choices.
                if not choices:
                    usage = value.get("usage")
                    return {"choices": [], **({"usage": usage} if isinstance(usage, dict) else {})}
                choice = choices[0]
                if choice.get("index") != 0:
                    raise ValueError
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    raise ValueError
                if set(delta) - {"role", "content", "tool_calls", "refusal"}:
                    raise ValueError
                if delta.get("refusal"):
                    raise CloudFailure("CLOUD_REFUSED")
                if delta.get("role", "assistant") != "assistant":
                    raise ValueError
                if "content" in delta and delta["content"] is not None and not isinstance(delta["content"], str):
                    raise ValueError
                finish = choice.get("finish_reason")
                if finished and delta:
                    raise ValueError
                if finish is not None:
                    if finish not in {"stop", "tool_calls"} or finished:
                        raise CloudFailure("CLOUD_INCOMPLETE")
                    finished = True
                clean = {
                    "choices": [{
                        "index": 0,
                        "delta": {key: val for key, val in delta.items() if key != "refusal"},
                        "finish_reason": finish,
                    }]
                }
                usage = value.get("usage")
                if isinstance(usage, dict):
                    clean["usage"] = usage
                # Do not capture a known original accidentally generated by
                # the model. Such a chunk is rejected before local delivery.
                verify_json(canonical(clean), self.outbound.originals, decode_arguments=False)
                self.capture.append({"kind": "answer-tokenised", "chunk": clean})
                return clean
            except CloudFailure:
                raise
            except Exception:
                raise CloudFailure("CLOUD_PROTOCOL") from None

        def line(value):
            if value == "":
                return event()
            if value.startswith("data:"):
                event_lines.append(value[5:].lstrip(" "))
            elif value.startswith(":") or value.startswith("event:"):
                pass
            else:
                raise CloudFailure("CLOUD_PROTOCOL")
            return None

        try:
            for part in self.response.iter_bytes():
                total += len(part)
                if (
                    total > self.settings.maximum_response_bytes
                    or time.monotonic() - began > self.settings.timeout_seconds
                ):
                    raise CloudFailure("CLOUD_RESPONSE_BOUND")
                pending += decoder.decode(part)
                if len(pending) > 1024 * 1024:
                    raise CloudFailure("CLOUD_EVENT_BOUND")
                while "\n" in pending:
                    current, pending = pending.split("\n", 1)
                    value = line(current.rstrip("\r"))
                    if value is not None:
                        yield value
                    if done:
                        self.capture.append({"kind": "transport-complete"})
                        return
            pending += decoder.decode(b"", final=True)
            if pending:
                value = line(pending.rstrip("\r"))
                if value is not None:
                    yield value
            value = event()
            if value is not None:
                yield value
            if not done:
                raise CloudFailure("CLOUD_INCOMPLETE")
        except CloudFailure:
            raise
        except Exception:
            raise CloudFailure("CLOUD_TRANSPORT") from None
        finally:
            self.close()


class CloudClient:
    def __init__(
        self, settings: CloudSettings, *,
        credential: ApiKeyCredential | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.credential = credential or ApiKeyCredential(settings.api_key_env)
        self.client = httpx.Client(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=30),
        )

    def _auth_headers(self, secret: str) -> dict[str, str]:
        if self.settings.auth_style == "api-key":
            return {"api-key": secret}
        return {"Authorization": f"Bearer {secret}"}

    def open(self, outbound: Outbound) -> CloudStream:
        """Third lock is enforced here, even if the router is bypassed."""
        try:
            if (
                not isinstance(outbound.marking_class, MarkingClass)
                or marking_policy().is_local_only(outbound.marking_class)
                or outbound.verify_passed is not True
                or hashlib.sha256(outbound.payload).hexdigest() != outbound.manifest_hash
                or not JOB_ID.fullmatch(outbound.job_id)
            ):
                raise CloudFailure("CLOUD_RELEASE_LOCK")
            verify_json(outbound.payload, outbound.originals)
        except CloudFailure:
            raise
        except Exception:
            raise CloudFailure("CLOUD_RELEASE_LOCK") from None

        capture = Capture(self.settings.captures, outbound.job_id)
        capture.append({
            "kind": "outbound",
            "manifest_hash": outbound.manifest_hash,
            "verify_no_originals": True,
            "payload": strict_json(outbound.payload),
            "exact_json": outbound.payload.decode("utf-8"),
        })
        secret = self.credential.secret()
        # A loaded credential must not itself appear in work content.
        if secret in outbound.payload.decode("utf-8"):
            raise CloudFailure("CREDENTIAL_IN_PAYLOAD")
        context = self.client.stream(
            "POST", self.settings.url, content=outbound.payload,
            headers={
                **self._auth_headers(secret),
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        try:
            response = context.__enter__()
            if (
                response.status_code != 200
                or response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream"
            ):
                context.__exit__(None, None, None)
                raise CloudFailure("CLOUD_HTTP")
            return CloudStream(response, context, outbound, capture, self.settings)
        except CloudFailure:
            raise
        except Exception:
            raise CloudFailure("CLOUD_TRANSPORT") from None

    def close(self):
        self.client.close()
