"""Describe Kilo Code API integration and Claude Code's direct-login hook pattern.

Kilo Code uses the local gate as a custom API provider; the gate holds the
cloud API key, never Claude subscription tokens. Claude Code managed-hooks mode
uses managed local hooks and its own direct Anthropic login: the gate never
reads, stores or forwards that login, credentials or transcripts.

A subscription-proxy path is deliberately not supported. API-key/provider API
profiles below describe the separate API mode, not managed-hooks mode's direct-login
credential path. This reading of the Claude Code terms is the project's own,
not Anthropic's ruling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class APIDialect(StrEnum):
    OPENAI_CHAT_COMPLETIONS = "openai-chat-completions"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"


class CredentialPath(StrEnum):
    GATE_CLOUD_API_KEY = "gate-cloud-api-key"
    GATE_ANTHROPIC_API_KEY = "gate-anthropic-api-key"
    # Retained only so legacy callers receive an explicit rejection.
    CLAUDE_SUBSCRIPTION_OAUTH = "claude-subscription-oauth"


class ClientName(StrEnum):
    KILO_CODE = "KILO_CODE"
    CLAUDE_CODE = "CLAUDE_CODE"


@dataclass(frozen=True, slots=True)
class ClientProfile:
    name: ClientName
    api_dialects: tuple[APIDialect, ...]
    credential_paths: tuple[CredentialPath, ...]
    allowed_destinations: tuple[str, ...]
    conditional_credential_paths: tuple[CredentialPath, ...] = ()


KILO_CODE = ClientProfile(
    ClientName.KILO_CODE,
    (APIDialect.OPENAI_CHAT_COMPLETIONS, APIDialect.ANTHROPIC_MESSAGES),
    (CredentialPath.GATE_CLOUD_API_KEY, CredentialPath.GATE_ANTHROPIC_API_KEY),
    ("gate-cloud", "gate-anthropic-api"),
)

CLAUDE_CODE = ClientProfile(
    ClientName.CLAUDE_CODE,
    (APIDialect.ANTHROPIC_MESSAGES,),
    (CredentialPath.GATE_CLOUD_API_KEY, CredentialPath.GATE_ANTHROPIC_API_KEY),
    ("gate-cloud", "gate-anthropic-api"),
)


def validate_credential_path(
    client: ClientProfile,
    credential_path: CredentialPath,
    *,
    claude_subscription_passthrough: bool = False,
) -> None:
    """Reject retired OAuth intermediation even if a legacy flag requests it."""

    if credential_path == CredentialPath.CLAUDE_SUBSCRIPTION_OAUTH:
        raise PermissionError("Claude subscription passthrough is retired")
    if credential_path not in client.credential_paths:
        raise PermissionError("client credential path is not supported")
