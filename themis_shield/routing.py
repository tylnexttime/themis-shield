"""Immutable marking-based routing policy, applied before any PII decision.

Tiers at or above the policy's ``local_only_at`` (and recognised caveats,
through the profile's caveat floor) have no cloud path or user override.
Tiers from ``confirm_at`` up to ``local_only_at`` and legacy markers offer
CHOOSE_LOCAL_OR_CLOUD_CONFIRMED: a CLOUD candidate with mandatory confirmation
and a local alternative. This is a choice of boundary, never permission to
send. Use-case policy can further restrict cloud.

mandatory_ask is retained for the existing engine contract; mandatory_confirm
is its explicit routing alias. Handling-marker-only candidates also require
confirmation. All cloud candidates still need PII, destination, human job
approval and exact release validation. HYBRID is reserved and cannot be
selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterator, Mapping

from .config import UseCasePolicy
from .detectors.markings import (
    MarkingClass, MarkingResult, active_marking_profile, marking_policy,
)


class Route(StrEnum):
    CLOUD = "CLOUD"
    LOCAL_ONLY = "LOCAL_ONLY"
    HYBRID = "HYBRID"


class RouteReasonCode(StrEnum):
    ROUTE_LOCAL_MARKING = "ROUTE_LOCAL_MARKING"
    ROUTE_SENSITIVE_LOCAL_OR_CONFIRM = "ROUTE_SENSITIVE_LOCAL_OR_CONFIRM"
    ROUTE_CLOUD_HANDLING_ASK = "ROUTE_CLOUD_HANDLING_ASK"
    ROUTE_CLOUD_PII = "ROUTE_CLOUD_PII"
    ROUTE_LOCAL_POLICY = "ROUTE_LOCAL_POLICY"


@dataclass(frozen=True, slots=True)
class RouteRule:
    route: Route
    mandatory_ask: bool
    cloud_offered: bool
    no_override: bool
    reason_code: RouteReasonCode
    local_alternative_offered: bool = False

    @property
    def mandatory_confirm(self) -> bool:
        return self.mandatory_ask


_CLOUD = RouteRule(Route.CLOUD, False, True, False, RouteReasonCode.ROUTE_CLOUD_PII)
_SENSITIVE_CHOICE = RouteRule(
    Route.CLOUD, True, True, False,
    RouteReasonCode.ROUTE_SENSITIVE_LOCAL_OR_CONFIRM, True,
)
_MARKED_LOCAL = RouteRule(
    Route.LOCAL_ONLY, False, False, True,
    RouteReasonCode.ROUTE_LOCAL_MARKING, True,
)

def _rule_for(classification: MarkingClass) -> RouteRule:
    policy = marking_policy()
    if policy.is_local_only(classification):
        return _MARKED_LOCAL
    if policy.needs_confirm(classification):
        return _SENSITIVE_CHOICE
    return _CLOUD


class _PolicyTable(Mapping):
    """Read-only view of the active policy: tier -> rule. No item assignment."""

    def __getitem__(self, key: MarkingClass) -> RouteRule:
        return _rule_for(MarkingClass(key))

    def __iter__(self) -> Iterator[MarkingClass]:
        return iter(MarkingClass)

    def __len__(self) -> int:
        return len(MarkingClass)


ROUTING_TABLE: Mapping[MarkingClass, RouteRule] = _PolicyTable()


@dataclass(frozen=True, slots=True)
class RouteAuditFields:
    """The entire routing audit schema; no excerpts, offsets or marker values."""

    route: Route
    marking_class: MarkingClass
    reason_code: RouteReasonCode
    mandatory_ask: bool
    cloud_offered: bool
    no_override: bool
    local_alternative_offered: bool = False

    @property
    def mandatory_confirm(self) -> bool:
        return self.mandatory_ask


@dataclass(frozen=True, slots=True)
class RouteDecision:
    audit_fields: RouteAuditFields
    user_message: str = field(repr=False)

    @property
    def route(self) -> Route:
        return self.audit_fields.route

    @property
    def mandatory_ask(self) -> bool:
        return self.audit_fields.mandatory_ask

    @property
    def mandatory_confirm(self) -> bool:
        return self.audit_fields.mandatory_confirm

    @property
    def local_alternative_offered(self) -> bool:
        return self.audit_fields.local_alternative_offered

    @property
    def reason_code(self) -> RouteReasonCode:
        return self.audit_fields.reason_code

    @property
    def decision(self) -> str:
        """Expose the choice without adding an executable route to the server."""

        if (
            self.route == Route.CLOUD
            and self.mandatory_confirm
            and self.local_alternative_offered
        ):
            return "CHOOSE_LOCAL_OR_CLOUD_CONFIRMED"
        return self.route.value


def select_route(route: Route) -> Route:
    """Reject the research route, including its string enum value."""

    selected = Route(route)
    if selected == Route.HYBRID:
        raise ValueError("HYBRID is deferred to P3")
    return selected


def route_request(
    marking: MarkingResult,
    use_case: UseCasePolicy,
) -> RouteDecision:
    """Offer a compute boundary, not RELEASE/ASK/BLOCK authorisation.

    Legacy markers are normalised to at least the profile's legacy floor in
    the safe audit projection. Local-only tiers always win, including when
    legacy or handling signals coexist.
    """

    profile = active_marking_profile()
    policy = profile.policy
    classification = marking.classification
    if marking.caveats:
        classification = max(classification, profile.caveat_floor)
    if marking.legacy:
        classification = max(classification, profile.legacy_floor)

    rule = ROUTING_TABLE[classification]
    if policy.is_local_only(classification):
        message = (
            f"This stays on your computer because it is marked {profile.label(classification)}. "
            "It will be processed by the local model; this is slower."
        )
    elif policy.needs_confirm(classification):
        message = (
            "Choose local-only processing, or cloud processing after "
            "de-identification. Cloud requires your confirmation of the final "
            "clean payload and an operator decision, or the review board for a "
            "major job. Local processing is always available."
        )
    elif marking.handling:
        rule = RouteRule(
            Route.CLOUD, True, True, False, RouteReasonCode.ROUTE_CLOUD_HANDLING_ASK
        )
        message = (
            "This has a handling marker. Confirmation is "
            "required after the PII check before any cloud send."
        )
    else:
        message = "This must pass the PII gate before any approved cloud send."

    # A use-case policy can strengthen a cloud candidate, never weaken a hard route.
    if rule.route == Route.CLOUD and use_case.local_only:
        rule = RouteRule(
            Route.LOCAL_ONLY, False, False, True,
            RouteReasonCode.ROUTE_LOCAL_POLICY, True,
        )
        message = (
            "This stays local under the use-case policy. "
            "It will be processed by the local model; this is slower."
        )

    select_route(rule.route)
    return RouteDecision(
        RouteAuditFields(
            rule.route,
            classification,
            rule.reason_code,
            rule.mandatory_ask,
            rule.cloud_offered,
            rule.no_override,
            rule.local_alternative_offered,
        ),
        message,
    )
