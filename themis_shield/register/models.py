"""Safe SQLite intake fields for the synthetic register rehearsal.

These are additional fields, not changes to the existing JobRecord contract.
References must be inventory aliases, never source filenames or local paths.
Validation is a guard, not proof of de-identification or endorsement.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
import re

from ..detectors.markings import MarkingClass, marking_policy
from ..routing import Route
from ..types import Label
from .client import JobRecord, SensitivePath, is_major


PILOT_ROLE = "PILOT-FIXTURE (synthetic demo; not an operator decision)"

# Decisions use fixed safe templates. Arbitrary explanation text is not stored.
REASONS = {
    "INTAKE": "Safe intake recorded.",
    "LOCAL_AUTHORISED": "Authorised local processing.",
    "SUBMITTED": "Complete clean submission received.",
    "MAJOR": "Major cloud submission requires the review board.",
    "OPERATOR_DOUBT": "Operator referred an uncertain decision.",
    "ROUTINE_APPROVED": "Routine scope approved.",
    "BOARD_APPROVED": "Delegated review-board decision recorded.",
    "PILOT_APPROVED": "Synthetic pilot fixture approval.",
    "REFUSED": "Requested processing refused.",
    "REFERRED": "Referral unresolved; no cloud permission.",
    "WITHDRAWN": "Requester cancelled the job.",
    "COMPLETED": "Processing finished.",
    "EXPIRED": "Approval validity elapsed.",
    "BINDING_CHANGED": "Approved scope changed.",
    "REVISED": "Revised clean scope recorded.",
    "OFFLINE": "Authoritative register or audit unavailable.",
    "RECONCILED": "Reconciled and freshly validated.",
    "MANDATORY_LOCAL": "New mandatory-local evidence.",
    "PEER_CHECKED": "Final clean revision independently checked.",
    "CONFIRMED": "Requester confirmed the final clean revision.",
    "SYNCED": "Local journal event reconciled.",
}

MARKING_REASONS = {"ASSESSED", "UNMARKED", "MANDATORY_LOCAL", "UNRESOLVED"}
CATEGORY_CODES = {
    "HEALTH", "DISABILITY", "ETHNICITY", "RELIGION", "POLITICAL",
    "SEXUAL", "CRIMINAL", "FINANCIAL", "OTHER_SENSITIVE",
}
COMPLETION_CODES = {"FINISHED", "NO_OUTPUT", "STOPPED"}
REVIEW_BOARD_OUTCOMES = {
    "Standard", "Normal", "Emergency", "Refer-Admin", "Refer-Governance",
}

_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_HASH = re.compile(r"[0-9a-f]{64}")
_LONG_DIGITS = re.compile(r"[0-9]{7,}")
# Conservative phone shape: eight or more digits with optional separators (spaces, dots, dashes, brackets, a leading
# "+"). A de-identified summary has no reason to carry such a run, so err towards refusing it. (A stricter
# pattern missed "+61 3 1234 5678" because it wanted two digits after the country code.)
_PHONE = re.compile(r"(?<![A-Za-z0-9])\+?(?:[0-9][ .()\-]*){8,}(?![A-Za-z0-9])")


def summary_guard(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 1000
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or "@" in value
        or _LONG_DIGITS.search(value)
        or _PHONE.search(value)
    ):
        raise ValueError("unsafe de-identified summary; use a generic safe stub")
    return value


def alias(value: str | None, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not _ALIAS.fullmatch(value)
        or "@" in value
        or _LONG_DIGITS.search(value)
    ):
        raise ValueError("invalid controlled reference")
    return value


def identity(value: str) -> str:
    """Opaque audit identity; never an Operator/reporting field."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid opaque identity")
    return value


def reason_code(value: str) -> str:
    if value in REASONS:
        return value
    for code, template in REASONS.items():
        if value == template:
            return code
    raise ValueError("reason must be a fixed safe reason code or template")


def canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _json(value: str) -> object:
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("invalid safe metadata JSON")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = item
        return result

    try:
        return json.loads(
            value, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, TypeError):
        raise ValueError("invalid safe metadata JSON") from None


@dataclass(frozen=True, slots=True)
class JobIntake(JobRecord):
    """Additive intake type accepted wherever JobRecord is expected.

    Evidence, decisions and deadlines are deliberately NOT intake fields.
    Plain JobRecord callers receive generic metadata, not inferred endorsement.
    Synthetic is an explicit assertion by the trusted rehearsal caller.
    """

    requester_role: str = "staff"
    requester_identity: str = ""  # Restricted; omitted from safe projections.
    client: str = "KILO_CODE"
    integration_mode: str = "handover"  # "managed-hooks" (Claude Code) or "handover"
    use_case_use_case_ref: str | None = None
    use_case_record_ref: str | None = None
    marking_reason_code: str = "ASSESSED"
    data_source_ids_json: str = "[]"
    pii_counts_json: str = "{}"
    sensitive_category_codes_json: str = "[]"
    problem_summary_deid: str = "Synthetic rehearsal; clean scope awaiting assessment."
    scope_json: str = (
        '{"schema_version":1,"release_profile":"single-release","tool_profile":"none"}'
    )
    overrides_count: int = 0
    gate_version: str = "synthetic-demo-v1"
    policy_version: str = "synthetic-demo-v1"
    local_authorisation_ref: str | None = None
    synthetic: bool = False
    interim_manual_assisted: bool = False
    confirmation_required: bool = False


INTAKE_FIELDS = tuple(item.name for item in fields(JobIntake))
BASE_FIELDS = tuple(item.name for item in fields(JobRecord))
BOOL_FIELDS = {
    "new_data_source", "new_cloud_configuration", "scheduled_or_repeated_batch",
    "restricted_contract_scope", "no_matching_approved_use_case",
    "state_changing_register_act", "sensitive_categories_present",
    "operator_doubt", "synthetic", "interim_manual_assisted",
    "confirmation_required",
}
INT_FIELDS = {
    "source_document_count", "source_word_count", "requested_duration_days",
    "repeated_overrides_or_refusals_30_days", "overrides_count",
}
EDITABLE_FIELDS = set(INTAKE_FIELDS) - {
    "job_id", "created_utc", "status", "requester_identity", "requester_role", "synthetic",
}


def validate_intake(job: JobIntake) -> None:
    is_major(job)  # Validates all inherited counts and assessed flags.
    if not isinstance(job.route, Route) or job.route not in {
        Route.CLOUD, Route.LOCAL_ONLY,
    }:
        raise ValueError("unsupported route")
    if not isinstance(job.marking_class, MarkingClass):
        raise ValueError("invalid marking class")
    if job.route == Route.CLOUD and marking_policy().is_local_only(job.marking_class):
        raise PermissionError("local-only marking tier has no cloud route")
    if job.manifest_hash is not None and (
        not isinstance(job.manifest_hash, str)
        or not _HASH.fullmatch(job.manifest_hash)
    ):
        raise ValueError("invalid clean manifest hash")
    for name in BOOL_FIELDS:
        if type(getattr(job, name)) is not bool:
            raise ValueError("invalid boolean metadata")
    if type(job.overrides_count) is not int or job.overrides_count < 0:
        raise ValueError("invalid override count")
    if job.client not in {"KILO_CODE", "CLAUDE_CODE"} or job.integration_mode not in {
        "managed-hooks", "handover",
    }:
        raise ValueError("unsupported client or integration mode")
    if job.interim_manual_assisted and job.integration_mode != "handover":
        raise ValueError("manual-assisted operation is handover mode")
    if job.marking_reason_code not in MARKING_REASONS:
        raise ValueError("invalid marking reason")
    summary_guard(job.problem_summary_deid)
    for name in (
        "requester_role", "destination_id", "model", "deployment_type",
        "use_case_use_case_ref", "use_case_record_ref", "gate_version",
        "policy_version", "local_authorisation_ref",
    ):
        alias(getattr(job, name), required=name in {
            "requester_role", "gate_version", "policy_version",
        })
    identity(job.requester_identity)
    if (
        job.sensitive_path == SensitivePath.LOCAL and job.route != Route.LOCAL_ONLY
        or job.sensitive_path == SensitivePath.CLOUD_CONFIRMED and job.route != Route.CLOUD
    ):
        raise ValueError("sensitive path disagrees with route")

    sources = _json(job.data_source_ids_json)
    if not isinstance(sources, list) or len(sources) > 100:
        raise ValueError("sources must be controlled references")
    for item in sources:
        alias(item, required=True)

    counts = _json(job.pii_counts_json)
    if not isinstance(counts, dict) or any(
        key not in Label.__members__ or type(count) is not int or count < 0
        for key, count in counts.items()
    ):
        raise ValueError("PII metadata must contain entity counts only")

    categories = _json(job.sensitive_category_codes_json)
    if (
        not isinstance(categories, list)
        or any(not isinstance(item, str) or item not in CATEGORY_CODES for item in categories)
        or len(categories) != len(set(categories))
        or bool(categories) and not job.sensitive_categories_present
    ):
        raise ValueError("invalid sensitive category codes")

    scope = _json(job.scope_json)
    if (
        not isinstance(scope, dict)
        or set(scope) != {"schema_version", "release_profile", "tool_profile"}
        or type(scope["schema_version"]) is not int
        or scope["schema_version"] != 1
    ):
        raise ValueError("unsupported bounded scope schema")
    alias(scope["release_profile"], required=True)
    alias(scope["tool_profile"], required=True)


def major_codes(job: JobRecord) -> tuple[str, ...]:
    is_major(job)
    result = []
    if job.source_document_count > 5 or job.source_word_count > 20_000:
        result.append("VOLUME")
    if job.requested_duration_days > 7:
        result.append("LONG_DURATION")
    if job.repeated_overrides_or_refusals_30_days >= 3:
        result.append("REPEAT_SIGNAL")
    names = {
        "new_data_source": "NEW_SOURCE",
        "new_cloud_configuration": "NEW_CONFIGURATION",
        "scheduled_or_repeated_batch": "BATCH",
        "restricted_contract_scope": "CONTRACT_SCOPE",
        "no_matching_approved_use_case": "NO_ENDORSEMENT",
        "state_changing_register_act": "STATE_CHANGE",
        "sensitive_categories_present": "SENSITIVE_STATEMENT",
        "operator_doubt": "OPERATOR_DOUBT",
    }
    result.extend(code for name, code in names.items() if getattr(job, name))
    return tuple(result)
