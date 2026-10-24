"""Register contracts, approval validation and an append-only offline journal.


The journal accepts only fixed metadata: no prompts, source text, original
values, paths, arbitrary reasons, credentials or de-identified summaries.
Aliases are controlled references, not user-supplied prose.

Use one writer on an administrator-protected local disk. fsync is requested before append
returns. This is not an authenticated or tamper-evident audit store. Sync must
be idempotent by entry_id; it never deletes rows. Cloud stays Held-Offline
until the central register and audit are reachable.

Version 2 persists duration, chosen sensitive path and assessed major inputs.
Version 1 remains readable with the original seven-day/default contract.
No transport, authentication, transition engine or synchroniser is implemented.
No component reads Claude credentials or transcripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import re
from typing import Protocol
from uuid import uuid4
from ..timezone import business_timezone

from ..detectors.markings import MarkingClass, marking_policy
from ..routing import Route


ROUTINE_MAX_DOCUMENTS = 5
ROUTINE_MAX_WORDS = 20_000
DEFAULT_VALIDITY_DAYS = 7
OPERATOR_BUSINESS_HOURS = 8
BUSINESS_START = time(9)
BUSINESS_END = time(17)

REPEAT_ESCALATION_THRESHOLD = 3
MAXIMUM_LINE_BYTES = 4096

# Compatibility alias for earlier callers; the default is now decided.
PROPOSED_APPROVAL_LIFETIME = timedelta(days=DEFAULT_VALIDITY_DAYS)


class JobStatus(StrEnum):
    LOGGED = "Logged"
    AUTO_ALLOWED_LOCAL = "Auto-Allowed-Local"
    AWAITING_OPERATOR = "Awaiting-Operator"
    ESCALATED_REVIEW = "Escalated-Review"
    APPROVED = "Approved"
    REFUSED = "Refused"
    EXPIRED = "Expired"
    WITHDRAWN = "Withdrawn"
    COMPLETED = "Completed"
    HELD_OFFLINE = "Held-Offline"


class Tier(StrEnum):
    OPERATOR = "OPERATOR"
    AI_CAB = "AI_CAB"


class SensitivePath(StrEnum):
    LOCAL = "LOCAL"
    CLOUD_CONFIRMED = "CLOUD_CONFIRMED"


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Minimal client contract, not the final central table schema.

    Counts/flags must be populated from a complete assessed submission; their
    defaults preserve older callers, not proof of endorsement or inspection.
    CLOUD_CONFIRMED asserts confirmation of this frozen manifest revision.
    Its actor/time/authenticity must be checked in the authoritative service.
    Legacy-marker routing must project marking_class to at least the profile's legacy floor.
    """

    job_id: str
    created_utc: datetime
    status: JobStatus
    route: Route
    marking_class: MarkingClass = MarkingClass.NONE
    manifest_hash: str | None = None
    destination_id: str | None = None
    model: str | None = None
    deployment_type: str | None = None
    source_document_count: int = 0
    source_word_count: int = 0
    requested_duration_days: int = DEFAULT_VALIDITY_DAYS
    sensitive_path: SensitivePath | None = None
    new_data_source: bool = False
    new_cloud_configuration: bool = False
    scheduled_or_repeated_batch: bool = False
    restricted_contract_scope: bool = False
    no_matching_approved_use_case: bool = False
    state_changing_register_act: bool = False
    repeated_overrides_or_refusals_30_days: int = 0
    sensitive_categories_present: bool = False
    operator_doubt: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    job_id: str
    manifest_hash: str
    destination_id: str
    approved_utc: datetime
    expiry_utc: datetime
    model: str
    deployment_type: str
    tier: Tier = Tier.OPERATOR
    status: JobStatus = JobStatus.APPROVED


@dataclass(frozen=True, slots=True)
class JournalEntry:
    entry_id: str
    occurred_utc: datetime
    job: JobRecord


_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")
_HASH = re.compile(r"[0-9a-f]{64}")
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_ENTRY_ID = re.compile(r"[0-9a-f]{32}")
_MAJOR_FLAGS = (
    "new_data_source",
    "new_cloud_configuration",
    "scheduled_or_repeated_batch",
    "restricted_contract_scope",
    "no_matching_approved_use_case",
    "state_changing_register_act",
    "sensitive_categories_present",
    "operator_doubt",
)
_COUNT_FIELDS = (
    "source_document_count",
    "source_word_count",
    "repeated_overrides_or_refusals_30_days",
)
_BASE_JOB_FIELDS = frozenset({
    "job_id", "created_utc", "status", "route", "marking_class",
    "manifest_hash", "destination_id", "model", "deployment_type",
})
_EXTRA_JOB_FIELDS = frozenset(
    _MAJOR_FLAGS + _COUNT_FIELDS + ("requested_duration_days", "sensitive_path")
)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def operator_due_utc(
    submitted_utc: datetime,
    holidays: frozenset[date] = frozenset(),
) -> datetime:
    """Return eight business hours after a complete submission, in UTC.

    Count 09:00-17:00 Mon-Fri in the configured business timezone, excluding supplied local
    holiday dates. Outside hours, begin at the next opening. Work with naive
    local wall-clock intervals, then attach zoneinfo at the final boundary:
    a weekend DST change must not shift Monday's business closing time.

    Empty holidays means none supplied, not an authoritative holiday calendar.
    No I/O, clock reads, mutation or automatic holiday discovery occurs.
    """

    if any(type(day) is not date for day in holidays):
        raise ValueError("holidays must contain local dates")
    cursor = _utc(submitted_utc).astimezone(business_timezone()).replace(tzinfo=None)
    remaining = timedelta(hours=OPERATOR_BUSINESS_HOURS)
    while True:
        day = cursor.date()
        opening = datetime.combine(day, BUSINESS_START)
        closing = datetime.combine(day, BUSINESS_END)
        if day.weekday() >= 5 or day in holidays or cursor >= closing:
            cursor = datetime.combine(day + timedelta(days=1), BUSINESS_START)
            continue
        if cursor < opening:
            cursor = opening
        available = closing - cursor
        if remaining <= available:
            due_local = cursor + remaining
            return due_local.replace(tzinfo=business_timezone()).astimezone(timezone.utc)
        remaining -= available
        cursor = datetime.combine(day + timedelta(days=1), BUSINESS_START)


def _validate_major_inputs(job: JobRecord) -> None:
    if not isinstance(job, JobRecord):
        raise TypeError("JobRecord required")
    for name in _COUNT_FIELDS:
        value = getattr(job, name)
        if type(value) is not int or value < 0:
            raise ValueError("invalid job count")
    if type(job.requested_duration_days) is not int or job.requested_duration_days < 1:
        raise ValueError("invalid requested duration")
    for name in _MAJOR_FLAGS:
        if type(getattr(job, name)) is not bool:
            raise ValueError("invalid major flag")
    if job.sensitive_path is not None and not isinstance(job.sensitive_path, SensitivePath):
        raise ValueError("invalid sensitive path")


def is_major(job: JobRecord) -> bool:
    """Classify assessed scope; pure and independent of routing/status.

    Either size limit exceeded, duration above seven, repeat threshold or any
    available major flag suffices. A confirmation-tier marking itself is not major.
    Invalid counts/flags raise rather than silently becoming routine.
    This classification requires human escalation only for a proposed cloud leg.
    """

    _validate_major_inputs(job)
    return (
        job.source_document_count > ROUTINE_MAX_DOCUMENTS
        or job.source_word_count > ROUTINE_MAX_WORDS
        or job.requested_duration_days > DEFAULT_VALIDITY_DAYS
        or job.repeated_overrides_or_refusals_30_days >= REPEAT_ESCALATION_THRESHOLD
        or any(getattr(job, name) for name in _MAJOR_FLAGS)
    )


def _utc_text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _alias(value: str | None) -> bool:
    return isinstance(value, str) and _ALIAS.fullmatch(value) is not None


def _job_metadata(job: JobRecord) -> dict:
    _validate_major_inputs(job)
    if not isinstance(job.job_id, str) or not _ID.fullmatch(job.job_id):
        raise ValueError("invalid job identifier")
    if not isinstance(job.status, JobStatus) or not isinstance(job.route, Route):
        raise ValueError("invalid job state or route")
    if not isinstance(job.marking_class, MarkingClass):
        raise ValueError("invalid marking class")
    if job.manifest_hash is not None and (
        not isinstance(job.manifest_hash, str)
        or not _HASH.fullmatch(job.manifest_hash)
    ):
        raise ValueError("invalid manifest hash")
    for value in (job.destination_id, job.model, job.deployment_type):
        if value is not None and not _alias(value):
            raise ValueError("invalid scope alias")
    if job.route == Route.CLOUD and (
        job.manifest_hash is None
        or job.destination_id is None
        or job.model is None
        or job.deployment_type is None
    ):
        raise ValueError("cloud scope is incomplete")
    if (
        job.sensitive_path == SensitivePath.LOCAL and job.route != Route.LOCAL_ONLY
        or job.sensitive_path == SensitivePath.CLOUD_CONFIRMED and job.route != Route.CLOUD
    ):
        raise ValueError("sensitive path and route disagree")
    row = {
        "job_id": job.job_id,
        "created_utc": _utc_text(job.created_utc),
        "status": job.status.value,
        "route": job.route.value,
        "marking_class": job.marking_class.name,
        "manifest_hash": job.manifest_hash,
        "destination_id": job.destination_id,
        "model": job.model,
        "deployment_type": job.deployment_type,
        "requested_duration_days": job.requested_duration_days,
        "sensitive_path": job.sensitive_path.value if job.sensitive_path is not None else None,
    }
    for name in _MAJOR_FLAGS + _COUNT_FIELDS:
        row[name] = getattr(job, name)
    return row


def approval_is_valid(
    approval: ApprovalRecord | None,
    job: JobRecord,
    now: datetime,
) -> bool:
    """Pure, fail-closed validation; no reads, network or state mutation.

    Match Approved records and exact job/manifest/destination/model/deployment.
    Reject local-only tiers, local, future, expired or incorrectly tiered approvals.
    Cap duration at the explicit request; requests above seven require review board.
    Sensitive cloud requires CLOUD_CONFIRMED for this manifest.

    Authenticity, revocation freshness, explicit-request evidence, confirmation,
    peer-check evidence, endorsements and destination authority remain service/
    release-gate responsibilities; possession of these dataclasses proves none.
    """

    try:
        if not isinstance(approval, ApprovalRecord):
            return False
        _job_metadata(job)
        if (
            job.status != JobStatus.APPROVED
            or approval.status != JobStatus.APPROVED
            or not isinstance(approval.status, JobStatus)
            or not isinstance(approval.tier, Tier)
            or job.route != Route.CLOUD
            or marking_policy().is_local_only(job.marking_class)
            or (
                marking_policy().needs_confirm(job.marking_class)
                and job.sensitive_path != SensitivePath.CLOUD_CONFIRMED
            )
            or (is_major(job) and approval.tier != Tier.AI_CAB)
        ):
            return False
        if (
            approval.job_id != job.job_id
            or approval.manifest_hash != job.manifest_hash
            or approval.destination_id != job.destination_id
            or approval.model != job.model
            or approval.deployment_type != job.deployment_type
        ):
            return False
        approved = _utc(approval.approved_utc)
        expiry = _utc(approval.expiry_utc)
        current = _utc(now)
        allowed_lifetime = timedelta(days=job.requested_duration_days)
        return (
            approved <= current < expiry
            and approved < expiry <= approved + allowed_lifetime
        )
    except (TypeError, ValueError, AttributeError, OverflowError):
        return False


def _entry_object(entry: JournalEntry) -> dict:
    return {
        "schema_version": 2,
        "entry_id": entry.entry_id,
        "occurred_utc": _utc_text(entry.occurred_utc),
        "job": _job_metadata(entry.job),
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal field")
        result[key] = value
    return result


def _decode_entry(raw: bytes) -> JournalEntry:
    row = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(row, dict) or set(row) != {
        "schema_version", "entry_id", "occurred_utc", "job"
    }:
        raise ValueError("invalid journal schema")
    version = row["schema_version"]
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("invalid journal version")
    if not isinstance(row["entry_id"], str) or not _ENTRY_ID.fullmatch(row["entry_id"]):
        raise ValueError("invalid journal entry identifier")
    job = row["job"]
    expected = _BASE_JOB_FIELDS if version == 1 else _BASE_JOB_FIELDS | _EXTRA_JOB_FIELDS
    if not isinstance(job, dict) or set(job) != expected:
        raise ValueError("invalid journal job schema")
    extras = {}
    if version == 2:
        extras = {name: job[name] for name in _EXTRA_JOB_FIELDS}
        if extras["sensitive_path"] is not None:
            extras["sensitive_path"] = SensitivePath(extras["sensitive_path"])
    record = JobRecord(
        job_id=job["job_id"],
        created_utc=datetime.fromisoformat(job["created_utc"]),
        status=JobStatus(job["status"]),
        route=Route(job["route"]),
        marking_class=MarkingClass[job["marking_class"]],
        manifest_hash=job["manifest_hash"],
        destination_id=job["destination_id"],
        model=job["model"],
        deployment_type=job["deployment_type"],
        **extras,
    )
    entry = JournalEntry(
        row["entry_id"], datetime.fromisoformat(row["occurred_utc"]), record
    )
    _entry_object(entry)
    return entry


@dataclass(frozen=True, slots=True)
class LocalJournal:
    path: Path

    def read_entries(self) -> tuple[JournalEntry, ...]:
        """Read validated metadata for sync; never acknowledge or truncate.

        Missing means empty. Corruption, unexpected fields and incomplete tails
        raise a value-free error; they are not silently skipped.
        """

        path = Path(self.path)
        if path.is_symlink():
            raise ValueError("journal symlinks are forbidden")
        entries = []
        seen = set()
        try:
            with path.open("rb") as stream:
                while raw := stream.readline(MAXIMUM_LINE_BYTES + 1):
                    if len(raw) > MAXIMUM_LINE_BYTES or not raw.endswith(b"\n"):
                        raise ValueError("invalid journal line")
                    entry = _decode_entry(raw)
                    if entry.entry_id in seen:
                        raise ValueError("duplicate journal entry")
                    seen.add(entry.entry_id)
                    entries.append(entry)
        except FileNotFoundError:
            return ()
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise ValueError("journal is malformed; recovery required") from None
        return tuple(entries)

    def append(self, job: JobRecord, occurred_utc: datetime) -> JournalEntry:
        """Append one fixed-schema JSON line and request disk synchronisation.

        Caller supplies a single-writer, protected directory. An interrupted
        write may require recovery; never repair or rewrite a log here.
        """

        entry = JournalEntry(uuid4().hex, _utc(occurred_utc), job)
        raw = (
            json.dumps(
                _entry_object(entry),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
        if len(raw) > MAXIMUM_LINE_BYTES:
            raise ValueError("journal entry exceeds bound")
        self.read_entries()
        path = Path(self.path)
        if path.is_symlink():
            raise ValueError("journal symlinks are forbidden")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if os.write(descriptor, raw) != len(raw):
                raise OSError("journal append incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return entry


class JobRegisterClient(Protocol):
    def log_job(self, job: JobRecord) -> JobRecord:
        """Log local/cloud jobs; central and extension transitions are deferred."""
        raise NotImplementedError("register transport is a stub")

    def get_job(self, job_id: str) -> JobRecord:
        raise NotImplementedError("register transport is a stub")

    def submit_for_approval(self, job: JobRecord) -> JobRecord:
        """Submit complete revision; service records received/due time and tier."""
        raise NotImplementedError("register transport is a stub")

    def get_approval(self, job_id: str) -> ApprovalRecord | None:
        raise NotImplementedError("register transport is a stub")

    def sync_journal(self, entries: tuple[JournalEntry, ...]) -> tuple[str, ...]:
        """Return acknowledged entry IDs; server must deduplicate by entry_id."""
        raise NotImplementedError("register synchronisation is a stub")


@dataclass(frozen=True, slots=True)
class HttpRegisterClient:
    """Non-networking stub. endpoint_alias is not a credential or supplied URL."""

    endpoint_alias: str

    def log_job(self, job: JobRecord) -> JobRecord:
        raise NotImplementedError("register HTTP client is a stub")

    def get_job(self, job_id: str) -> JobRecord:
        raise NotImplementedError("register HTTP client is a stub")

    def submit_for_approval(self, job: JobRecord) -> JobRecord:
        raise NotImplementedError("register HTTP client is a stub")

    def get_approval(self, job_id: str) -> ApprovalRecord | None:
        raise NotImplementedError("register HTTP client is a stub")

    def sync_journal(self, entries: tuple[JournalEntry, ...]) -> tuple[str, ...]:
        raise NotImplementedError("register HTTP client is a stub")
