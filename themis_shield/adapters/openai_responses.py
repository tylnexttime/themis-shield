"""Specify OpenAI Responses and Chat Completions walking and SSE handling.

Inspect instructions, system/developer/user/assistant turns, tool definitions,
function-call arguments, function outputs, tool messages and file text.
JSON-encoded argument strings are parsed before walking and re-encoded after
substitution/restoration. The gate, not the caller, holds the cloud API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from . import OutboundProtector
from ..restore import Restorer, RestorationScope
from ..types import (
    ContentLeaf,
    JSONValue,
    LocalPayload,
    RedactedPayload,
    SSEEvent,
)


class OpenAIAPIShape(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat-completions"


@dataclass(frozen=True, slots=True)
class OpenAIResponsesAdapter:
    restorer: Restorer
    shape: OpenAIAPIShape = OpenAIAPIShape.RESPONSES

    def walk_request(self, request: JSONValue) -> tuple[ContentLeaf, ...]:
        """Inventory Responses input/instructions or Chat messages and tools."""
        raise NotImplementedError("skeleton: not implemented")

    def walk_response(self, response: JSONValue) -> tuple[ContentLeaf, ...]:
        """Include output text, function arguments and tool-call structures."""
        raise NotImplementedError("skeleton: not implemented")

    def protect_request(
        self,
        request: JSONValue,
        protector: OutboundProtector,
    ) -> RedactedPayload:
        """Protect every supported leaf and reject uninspected file references."""
        raise NotImplementedError("skeleton: not implemented")

    def restore_response(
        self,
        response: JSONValue,
        scope: RestorationScope,
    ) -> LocalPayload:
        """Restore executable local arguments before tool dispatch."""
        raise NotImplementedError("skeleton: not implemented")

    def handle_sse(
        self,
        event: SSEEvent,
        scope: RestorationScope,
    ) -> tuple[SSEEvent, ...]:
        """Handle Responses text/function deltas and Chat tool-call deltas.

        Preserve indices, call IDs and completion ordering. Validate assembled
        JSON and escape restored values correctly before local execution.
        P1 may re-emit a completely validated answer as one SSE burst.
        """
        raise NotImplementedError("skeleton: not implemented")
