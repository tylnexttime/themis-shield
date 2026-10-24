"""Describe locally loaded deployment, use-case and destination policies.

Configuration is local, schema-validated and contains no secrets. Destination
aliases resolve through administrator-controlled configuration, never a supplied URL.
Resource location is not an inference-residency guarantee.

Confirmation-tier and legacy-marker routing offers local or cloud with
mandatory confirm; there is no classification-specific cloud-authorisation
switch. Ordinary
use-case and destination approvals remain required.

The legacy subscription-passthrough flag is retained for migration only:
True is rejected. Claude Code managed-hooks mode uses direct login and managed hooks,
not this API credential configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping


class Cloud(StrEnum):
    CLAUDE = "claude"
    OPENAI_COMPATIBLE = "openai-compatible"


class ClaudePlan(StrEnum):
    TEAM = "team"
    ENTERPRISE = "enterprise"


class DeploymentType(StrEnum):
    CLAUDE_MANAGED = "claude-managed"
    REGIONAL_STANDARD = "regional-standard"
    REGIONAL_PROVISIONED = "regional-provisioned"
    GLOBAL_STANDARD = "global-standard"
    GLOBAL_PROVISIONED = "global-provisioned"
    DATA_ZONE_STANDARD = "data-zone-standard"
    DATA_ZONE_PROVISIONED = "data-zone-provisioned"


class FinderPlacement(StrEnum):
    LAPTOP = "laptop"
    APPROVED_ON_PREM = "approved-on-prem"


class LocalTargetName(StrEnum):
    LAPTOP = "laptop"
    STRATA = "strata"
    DGX_SPARK = "dgx-spark"


class StreamingMode(StrEnum):
    BUFFERED_BURST = "buffered-burst"
    LIVE_VALIDATED = "live-validated"


@dataclass(frozen=True, slots=True)
class Destination:
    """Identify an approved destination without storing its credential."""

    destination_id: str
    cloud: Cloud
    deployment_type: DeploymentType
    endpoint_alias: str
    organisation_alias: str | None = None
    plan: ClaudePlan | None = None
    resource_alias: str | None = None
    deployment_alias: str | None = None
    resource_region: str | None = None
    data_zone: str | None = None


@dataclass(frozen=True, slots=True)
class UseCasePolicy:
    """Apply governance approval, routing and required-stage coverage."""

    use_case_id: str
    allowed_destination_ids: tuple[str, ...]
    approved_for_cloud: bool = False
    synthetic_only: bool = True
    requires_in_region_inference: bool = True
    allow_on_prem_finder: bool = False
    required_detectors: tuple[str, ...] = (
        "rules",
        "pii-tracer",
        "gemma4-e4b",
        "gemma4-26b-a4b",
    )
    replace_whole_sensitive_statement: bool = True
    streaming_mode: StreamingMode = StreamingMode.BUFFERED_BURST
    local_only: bool = False
    local_inference_target: LocalTargetName = LocalTargetName.LAPTOP


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    """Hold institution exemptions and administrator-controlled deployment choices.

    S-M cannot be removed through required_detectors. Additional marking
    vocabularies remain unimplemented and are rejected by the server.
    """

    profile_id: str
    profile_version: str
    destinations: tuple[Destination, ...]
    use_cases: tuple[UseCasePolicy, ...]
    finder_placement: FinderPlacement = FinderPlacement.LAPTOP
    finder_endpoint_alias: str | None = None
    allowlist_literals: tuple[str, ...] = ()
    allowlist_patterns: tuple[str, ...] = ()
    public_date_literals: tuple[str, ...] = ()
    security_marking_patterns: tuple[str, ...] = ()
    contract_caveat_patterns: tuple[str, ...] = ()
    vault_idle_seconds: int = 4 * 60 * 60
    vault_max_retention_days: int = 14
    central_audit_enabled: bool = False
    claude_subscription_passthrough: bool = False
    institution: Mapping | None = None

    def __post_init__(self) -> None:
        if self.claude_subscription_passthrough is not False:
            raise PermissionError("Claude subscription passthrough is retired")


def load_local_config(path: Path) -> DeploymentProfile:
    """Validate local JSON, controlled aliases and cross-field constraints.

    Reject unknown fields, credentials, missing approvals, invalid destinations
    and the retired subscription-passthrough mode. Spark requires an approved
    on-site boundary. Sensitive routing cannot be toggled by configuration.
    """
    raise NotImplementedError("skeleton: not implemented")


def validate_destination(
    destination: Destination,
    use_case: UseCasePolicy,
    profile: DeploymentProfile,
) -> None:
    """Reject unapproved destinations and incompatible inference boundaries.

    Global and multi-region data zones are not in-region inference boundaries.
    Provider-managed global inference must not satisfy an in-region requirement.
    """
    raise NotImplementedError("skeleton: not implemented")
