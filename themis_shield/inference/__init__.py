"""Common local inference contracts.

Imports do not start engines, load weights or contact endpoints. Targets have
no cloud credentials or vault access. Readiness is not deployment approval.

The original InferenceRequest -> InferenceResponse interface is retained.
The messages interface returns an OpenAI Chat response, or buffered-burst
chunks when stream=True. Required structured output fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, overload

from ..types import JSONValue


class RuntimeFailure(RuntimeError):
    """Content-free local-runtime failure; never triggers cloud fallback."""


class TargetUnavailable(RuntimeFailure):
    pass


class NotEnoughMemory(RuntimeFailure):
    pass


@dataclass(frozen=True, slots=True)
class InferenceCapabilities:
    text_chat: bool = True
    tool_calls: bool = False
    streaming: bool = False
    structured_output: bool = False


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    payload: JSONValue = field(repr=False)


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    text: str = field(repr=False)
    timings: dict[str, Any] = field(default_factory=dict, repr=False)
    finish_reason: str = "stop"


class LocalInferenceTarget(Protocol):
    name: str
    capabilities: InferenceCapabilities
    last_timings: dict[str, Any]
    load_ms: float
    pid: int | None

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def health(self) -> bool: ...

    @overload
    def chat(self, messages: InferenceRequest) -> InferenceResponse: ...

    @overload
    def chat(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> dict[str, Any] | Iterator[dict[str, Any]]: ...

    def chat(
        self,
        messages: InferenceRequest | list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> InferenceResponse | dict[str, Any] | Iterator[dict[str, Any]]: ...

    def chat_json(
        self,
        model_role: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> dict[str, Any]: ...
