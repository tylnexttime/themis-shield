"""Specify complete-payload API walking, protection and local restoration.

Adapters inventory every supported content surface before release. Inspect
system/developer prompts, text, prior turns, tools, function arguments, tool
results and locally extracted files. Content-bearing nested strings and object
keys need inspection. Protocol identifiers are validated separately.

Unsupported images, audio, opaque files, external references or unknown blocks
must block unless an approved local conversion protects their complete content.
Never forward an uninspected original binary alongside extracted text.
"""

from __future__ import annotations

from typing import Protocol

from ..types import (
    ContentLeaf,
    JSONValue,
    LocalPayload,
    RedactedPayload,
    RedactedText,
    SSEEvent,
)
from ..restore import RestorationScope


class OutboundProtector(Protocol):
    def protect(self, leaf: ContentLeaf) -> RedactedText:
        """Run the common engine on this surface and retain its ledger evidence."""
        raise NotImplementedError("skeleton: not implemented")


class APIAdapter(Protocol):
    def walk_request(self, request: JSONValue) -> tuple[ContentLeaf, ...]:
        """Return complete supported-surface inventory or reject the request."""
        raise NotImplementedError("skeleton: not implemented")

    def walk_response(self, response: JSONValue) -> tuple[ContentLeaf, ...]:
        """Include every inbound text and executable argument surface."""
        raise NotImplementedError("skeleton: not implemented")

    def protect_request(
        self,
        request: JSONValue,
        protector: OutboundProtector,
    ) -> RedactedPayload:
        """Rebuild valid JSON with every outbound content surface protected."""
        raise NotImplementedError("skeleton: not implemented")

    def restore_response(
        self,
        response: JSONValue,
        scope: RestorationScope,
    ) -> LocalPayload:
        """Restore the local response only; retain redacted canonical content."""
        raise NotImplementedError("skeleton: not implemented")

    def handle_sse(
        self,
        event: SSEEvent,
        scope: RestorationScope,
    ) -> tuple[SSEEvent, ...]:
        """Preserve protocol ordering while protecting text and tool deltas."""
        raise NotImplementedError("skeleton: not implemented")
