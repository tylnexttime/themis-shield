"""Specify local-first audit rows without text, values or value hashes.

No prompts, responses, source paths, content paths, token IDs, entity IDs, URLs,
exception messages or hashes of values are logged. Governance aliases are not
request-supplied text. Routing has its own schema and needs no cloud destination.

Count bands remain provisional. Any central feed needs administrator and privacy approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .config import Cloud, DeploymentType
from .routing import RouteAuditFields
from .types import Decision, IntegrationMode, ReasonCode


class CountBand(StrEnum):
    ZERO = "0"
    ONE_TO_NINE = "1-9"
    TEN_TO_NINETY_NINE = "10-99"
    HUNDRED_PLUS = "100+"


class AskOutcome(StrEnum):
    NOT_ASKED = "not-asked"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class RouteAuditRow:
    occurred_at: datetime
    request_id: str
    fields: RouteAuditFields


@dataclass(frozen=True, slots=True)
class AuditRow:
    occurred_at: datetime
    request_id: str
    integration_mode: IntegrationMode
    cloud: Cloud
    destination_alias: str
    deployment_type: DeploymentType
    decision: Decision
    reasons: tuple[ReasonCode, ...]
    ask_outcome: AskOutcome
    finding_count: CountBand
    placeholders_sent: CountBand
    tokens_restored: CountBand
    tokens_unknown: CountBand
    tokens_dropped: CountBand
    tokens_invented: CountBand
    gate_version: str
    profile_version: str
    policy_version: str
    artefact_manifest_id: str


def coarsen_count(count: int) -> CountBand:
    """Reject negative counts and apply the approved, frozen count bands."""
    raise NotImplementedError("skeleton: not implemented")


class AuditSink(Protocol):
    def append(self, row: AuditRow) -> None:
        """Write only the cloud release schema, without diagnostic context."""
        raise NotImplementedError("skeleton: not implemented")

    def append_route(self, row: RouteAuditRow) -> None:
        """Write canonical routing fields only; never include finding offsets."""
        raise NotImplementedError("skeleton: not implemented")
