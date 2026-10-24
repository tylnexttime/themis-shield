"""Specify the loopback front door and implement routing orchestration.

Pipeline: S-1 boundary -> S-M marking gate -> route ->
(CLOUD) PII pipeline / (LOCAL_ONLY) local inference.

Bind 127.0.0.1 only, as the signed-in user. Check the per-install local token,
Host and browser Origin. Reject redirects, request-supplied upstream URLs and
unsupported surfaces. There are no Workbench routes.

Expose /v1/messages, /v1/responses and /v1/chat/completions. LOCAL_ONLY answers
come from the configured target and return to the same client, labelled local.
No local failure, confirmation or lack of PII permits a cloud fallback.

claude_subscription_passthrough = False by default. Subscription OAuth and
anthropic-beta forwarding remain disabled. Cloud API-key credentials stay
under gate custody. Never forward the
local gate token upstream or credentials across redirects.

S-M and routing are implemented. HTTP serving, complete-payload boundary
inspection, local request conversion, PII preparation, custody, audit storage
and cloud release remain contracts. This is not an operational real-data gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import time
from typing import Mapping, Protocol
from uuid import uuid4

from .audit import AuditSink, RouteAuditRow
from .config import (
    Cloud,
    DeploymentProfile,
    Destination,
    LocalTargetName,
    UseCasePolicy,
    validate_destination,
)
from .detectors.markings import detect_markings, summarise_markings
from .history import CanonicalHistory
from .inference import (
    InferenceCapabilities,
    InferenceRequest,
    InferenceResponse,
    LocalInferenceTarget,
)
from .ledger import EvidenceLedger
from .routing import Route, RouteDecision, route_request, select_route
from .types import (
    ContentLeaf,
    Decision,
    DecisionResult,
    IntegrationMode,
    JSONValue,
    LocalPayload,
    RedactedPayload,
    SSEEvent,
)


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8742
MESSAGES_ROUTE = "/v1/messages"
RESPONSES_ROUTE = "/v1/responses"
CHAT_COMPLETIONS_ROUTE = "/v1/chat/completions"
_API_MODES = {
    MESSAGES_ROUTE: IntegrationMode.API_ANTHROPIC,
    RESPONSES_ROUTE: IntegrationMode.API_RESPONSES,
    CHAT_COMPLETIONS_ROUTE: IntegrationMode.API_CHAT_COMPLETIONS,
}


@dataclass(frozen=True, slots=True)
class SecretHeader:
    """Ephemeral credential material; never a configuration or audit field."""

    name: str
    value: str = field(repr=False)


class CredentialCustody(Protocol):
    def cloud_headers(self, destination: Destination) -> tuple[SecretHeader, ...]:
        """Obtain the configured cloud API key without exposing it to workers."""
        raise NotImplementedError("skeleton: not implemented")

    def claude_api_headers(self, destination: Destination) -> tuple[SecretHeader, ...]:
        """Obtain organisation API-key credentials held by the gate."""
        raise NotImplementedError("skeleton: not implemented")

    def claude_headers(
        self,
        destination: Destination,
        incoming: tuple[SecretHeader, ...],
    ) -> tuple[SecretHeader, ...]:
        """Forward approved subscription OAuth/capability headers to Claude only.

        The server must reject this path unless passthrough is explicitly
        authorised following Anthropic's written confirmation.
        """
        raise NotImplementedError("skeleton: not implemented")

    def check_local_token(self, incoming: SecretHeader | None) -> None:
        """Check the local client token without logging or forwarding it."""
        raise NotImplementedError("skeleton: not implemented")


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Local evidence references, not an audit row or value-derived fingerprint."""

    request_id: str
    conversation_id: str
    candidate_revision_id: str
    ledger_last_sequence: int
    use_case_id: str
    destination_id: str
    cloud: Cloud
    profile_version: str
    policy_version: str
    artefact_manifest_id: str
    authorised_tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class PreparedRelease:
    payload: RedactedPayload
    ledger: EvidenceLedger
    decision: DecisionResult
    manifest: ReleaseManifest


@dataclass(frozen=True, slots=True)
class AskConfirmation:
    """A local approval bound to one gate-held revision, not a general override."""

    request_id: str
    candidate_revision_id: str
    confirmed: bool


class GateEngine(Protocol):
    def inspect_boundary(
        self,
        request: JSONValue,
        integration_mode: IntegrationMode,
        use_case: UseCasePolicy,
    ) -> tuple[ContentLeaf, ...]:
        """S-1: inventory every supported surface, or reject the whole request.

        Validate schema, bounds and synthetic-only scope. Include system,
        history, tools, JSON keys/arguments and extracted file text. Unsupported
        or opaque content must fail closed; do not return a partial inventory.
        Keep original offsets for S-M. This does not run the PII pipeline.
        """
        raise NotImplementedError("skeleton: not implemented")

    def prepare_local(
        self,
        request: JSONValue,
        integration_mode: IntegrationMode,
        capabilities: InferenceCapabilities,
    ) -> InferenceRequest:
        """Convert the complete supported request to local Chat format.

        Reject unsupported tools, modalities or streaming. Do not silently
        discard content. Local conversion does not authorise cloud release.
        """
        raise NotImplementedError("skeleton: not implemented")

    def prepare(
        self,
        request: JSONValue,
        integration_mode: IntegrationMode,
        conversation_id: str,
        use_case: UseCasePolicy,
        destination: Destination,
        *,
        forced_ask: bool = False,
    ) -> PreparedRelease:
        """CLOUD only: normalise, detect, add evidence, substitute and rescan.

        Append mandatory routing ASK when forced_ask is True. Required-stage
        keys cover every outbound surface. Validate the exact final payload
        and retain the redacted history lane.
        """
        raise NotImplementedError("skeleton: not implemented")

    def validate_release(
        self,
        prepared: PreparedRelease,
        confirmation: AskConfirmation | None,
    ) -> None:
        """Recheck exact bytes, latest lineage, destination and any ASK approval.

        Reject BLOCK, stale revisions, incomplete rescans and non-convergence.
        No endpoint may send before this validation succeeds.
        """
        raise NotImplementedError("skeleton: not implemented")

    def restore_inbound(
        self,
        response: JSONValue,
        prepared: PreparedRelease,
    ) -> LocalPayload:
        """Restore locally and retain the independent canonical redacted answer."""
        raise NotImplementedError("skeleton: not implemented")


class UpstreamTransport(Protocol):
    def send(
        self,
        destination: Destination,
        prepared: PreparedRelease,
        credential_headers: tuple[SecretHeader, ...],
    ) -> JSONValue:
        """Send only a validated candidate; no redirects or raw-body lane."""
        raise NotImplementedError("skeleton: not implemented")

    def send_stream(
        self,
        destination: Destination,
        prepared: PreparedRelease,
        credential_headers: tuple[SecretHeader, ...],
    ) -> tuple[SSEEvent, ...]:
        """Represent the deferred streaming contract."""
        raise NotImplementedError("skeleton: not implemented")


def _local_payload(
    response: InferenceResponse,
    mode: IntegrationMode,
    target_name: LocalTargetName,
    routing: RouteDecision,
) -> LocalPayload:
    """Encode a text-only answer with visible and structural local labelling."""

    text = f"[LOCAL · {target_name.value}]\n{response.text}"
    model = f"local-{target_name.value}"
    identifier = uuid4().hex
    if mode == IntegrationMode.API_ANTHROPIC:
        payload = {
            "id": f"msg_{identifier}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    elif mode == IntegrationMode.API_RESPONSES:
        payload = {
            "id": f"resp_{identifier}",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": model,
            "output": [{
                "id": f"msg_{identifier}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                }],
            }],
        }
    else:
        payload = {
            "id": f"chatcmpl-{identifier}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
        }
    # Local UI metadata, not an audit field. Usage zero means unmeasured here.
    payload["_themis"] = {
        "route": Route.LOCAL_ONLY.value,
        "target": target_name.value,
        "notice": routing.user_message,
        "usage_measured": False,
    }
    return LocalPayload(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class LocalGateServer:
    profile: DeploymentProfile
    engine: GateEngine
    credentials: CredentialCustody
    upstream: UpstreamTransport
    history: CanonicalHistory
    audit: AuditSink
    inference_targets: Mapping[str, LocalInferenceTarget] = field(
        default_factory=dict, repr=False
    )

    def serve(self, port: int = DEFAULT_PORT) -> None:
        """Start authenticated loopback routes with Host/Origin and body bounds.

        HTTP serving is not implemented. Do not expose handle_request directly
        to unvalidated HTTP input.
        """
        raise NotImplementedError("skeleton: not implemented")

    def handle_request(
        self,
        route: str,
        request: JSONValue,
        destination_id: str,
        use_case_id: str,
        conversation_id: str,
        local_token: SecretHeader | None,
        confirmation: AskConfirmation | None = None,
        incoming_claude_headers: tuple[SecretHeader, ...] = (),
    ) -> LocalPayload:
        """Authenticate, inspect S-1, mark, route, then execute only that branch."""

        self.credentials.check_local_token(local_token)
        if route not in _API_MODES:
            raise ValueError("unsupported API route")
        mode = _API_MODES[route]
        use_cases = [
            policy for policy in self.profile.use_cases
            if policy.use_case_id == use_case_id
        ]
        if len(use_cases) != 1:
            raise PermissionError("unknown or ambiguous use case")
        use_case = use_cases[0]
        if (
            self.profile.security_marking_patterns
            or self.profile.contract_caveat_patterns
        ):
            raise NotImplementedError("configure reviewed additional marking vocabulary")

        leaves = self.engine.inspect_boundary(request, mode, use_case)
        marking = summarise_markings(
            tuple(detect_markings(leaf.text) for leaf in leaves)
        )
        routing = route_request(marking, use_case)
        selected = select_route(routing.route)
        self.audit.append_route(RouteAuditRow(
            datetime.now(timezone.utc), uuid4().hex, routing.audit_fields
        ))

        if selected == Route.LOCAL_ONLY:
            target_name = LocalTargetName(use_case.local_inference_target)
            target = self.inference_targets.get(target_name.value)
            if target is None or target.name != target_name.value:
                raise RuntimeError("configured local inference target unavailable")
            if target_name == LocalTargetName.DGX_SPARK and not use_case.allow_on_prem_finder:
                raise PermissionError("on-site inference boundary is not approved")
            if not target.health():
                raise RuntimeError("local inference target is not ready")
            local_request = self.engine.prepare_local(
                request, mode, target.capabilities
            )
            response = target.chat(local_request)
            return _local_payload(response, mode, target_name, routing)

        destinations = [
            destination for destination in self.profile.destinations
            if destination.destination_id == destination_id
        ]
        if len(destinations) != 1:
            raise PermissionError("unknown or ambiguous destination")
        destination = destinations[0]
        validate_destination(destination, use_case, self.profile)
        if incoming_claude_headers and (
            destination.cloud != Cloud.CLAUDE
            or not self.profile.claude_subscription_passthrough
        ):
            raise PermissionError("Claude subscription passthrough is not authorised")

        prepared = self.engine.prepare(
            request,
            mode,
            conversation_id,
            use_case,
            destination,
            forced_ask=routing.mandatory_ask,
        )
        if prepared.decision.decision == Decision.BLOCK:
            raise PermissionError("cloud release blocked")
        if routing.mandatory_ask and prepared.decision.decision != Decision.ASK:
            raise RuntimeError("mandatory routing ASK evidence missing")
        self.engine.validate_release(prepared, confirmation)

        if destination.cloud == Cloud.OPENAI_COMPATIBLE:
            headers = self.credentials.cloud_headers(destination)
        elif incoming_claude_headers:
            headers = self.credentials.claude_headers(
                destination, incoming_claude_headers
            )
        else:
            headers = self.credentials.claude_api_headers(destination)
        response = self.upstream.send(destination, prepared, headers)
        return self.engine.restore_inbound(response, prepared)
