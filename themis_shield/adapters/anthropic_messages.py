"""Specify API-key Anthropic Messages content walking and SSE restoration.

Walk system strings/blocks, every message content block, tool definitions,
tool_use input and nested tool_result content, including prior assistant turns.
Restore executable local arguments under the approved API-mode boundary.

This is not the Claude Code managed-hooks mode implementation. Subscription
OAuth/capability forwarding is retired; no Claude subscription credentials
belong in this adapter. Unknown content types fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import OutboundProtector
from ..restore import Restorer, RestorationScope
from ..types import (
    ContentLeaf,
    JSONValue,
    LocalPayload,
    RedactedPayload,
    SSEEvent,
)


@dataclass(frozen=True, slots=True)
class AnthropicMessagesAdapter:
    restorer: Restorer

    def walk_request(self, request: JSONValue) -> tuple[ContentLeaf, ...]:
        """Inventory every Messages outbound content block and nested surface."""
        raise NotImplementedError("skeleton: not implemented")

    def walk_response(self, response: JSONValue) -> tuple[ContentLeaf, ...]:
        """Include assistant text and complete tool_use input structures."""
        raise NotImplementedError("skeleton: not implemented")

    def protect_request(
        self,
        request: JSONValue,
        protector: OutboundProtector,
    ) -> RedactedPayload:
        """Protect all leaves and reject unsupported original attachments."""
        raise NotImplementedError("skeleton: not implemented")

    def restore_response(
        self,
        response: JSONValue,
        scope: RestorationScope,
    ) -> LocalPayload:
        """Restore local text/tool inputs with valid structured serialization."""
        raise NotImplementedError("skeleton: not implemented")

    def handle_sse(
        self,
        event: SSEEvent,
        scope: RestorationScope,
    ) -> tuple[SSEEvent, ...]:
        """Track block indices and tool identifiers across starts/deltas/stops.

        Never permit unresolved token-bearing calls to reach executable
        completion. Preserve non-content events only after schema validation.
        """
        raise NotImplementedError("skeleton: not implemented")
