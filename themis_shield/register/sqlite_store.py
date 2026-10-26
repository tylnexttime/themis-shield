"""Transactional SQLite job register: synthetic rehearsal only.

The private core retains the original register contract. The public class
composes the two-stage commands BEFORE that core in its method resolution
order. Neither class performs import-time database or network operations.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import csv
import getpass
import hashlib
import json
import os
from pathlib import Path
from ..paths import app_data_dir
import sqlite3
from types import MappingProxyType

from ..detectors.markings import MarkingClass, marking_policy
from ..routing import Route
from .client import (
    ApprovalRecord, JobRecord, JobStatus, JournalEntry, SensitivePath, Tier,
    _entry_object, approval_is_valid, is_major, operator_due_utc,
)
from .models import (
    BASE_FIELDS, BOOL_FIELDS, REVIEW_BOARD_OUTCOMES, COMPLETION_CODES, EDITABLE_FIELDS,
    INTAKE_FIELDS, INT_FIELDS, PILOT_ROLE, REASONS, JobIntake,
    alias, canonical_json, identity, major_codes, reason_code, validate_intake,
)
from .reporting import build_report
from .two_stage import TwoStageCommands, STAGE_REASONS
from .gate_records import GateRecords


REASONS.update(STAGE_REASONS)
S = JobStatus
LEGAL_TRANSITIONS = MappingProxyType({
    S.LOGGED: frozenset({
        S.AUTO_ALLOWED_LOCAL, S.AWAITING_OPERATOR, S.WITHDRAWN,
        S.HELD_OFFLINE, S.REFUSED,
    }),
    S.AUTO_ALLOWED_LOCAL: frozenset({
        S.AWAITING_OPERATOR, S.COMPLETED, S.WITHDRAWN, S.REFUSED,
    }),
    S.AWAITING_OPERATOR: frozenset({
        S.ESCALATED_REVIEW, S.APPROVED, S.REFUSED, S.WITHDRAWN,
        S.HELD_OFFLINE, S.AUTO_ALLOWED_LOCAL,
    }),
    S.ESCALATED_REVIEW: frozenset({
        S.APPROVED, S.REFUSED, S.ESCALATED_REVIEW, S.WITHDRAWN,
        S.HELD_OFFLINE, S.AUTO_ALLOWED_LOCAL,
    }),
    S.APPROVED: frozenset({
        S.EXPIRED, S.COMPLETED, S.WITHDRAWN, S.HELD_OFFLINE,
        S.AUTO_ALLOWED_LOCAL, S.REFUSED,
    }),
    S.EXPIRED: frozenset({S.LOGGED}),
    S.HELD_OFFLINE: frozenset({
        S.LOGGED, S.APPROVED, S.WITHDRAWN, S.AUTO_ALLOWED_LOCAL, S.REFUSED,
    }),
    S.REFUSED: frozenset(),
    S.WITHDRAWN: frozenset(),
    S.COMPLETED: frozenset(),
})

OPERATOR_ROLES = {"operator", "security-admin"}
GATE_ROLES = {"gate", "process-custodian"}
TIME_FIELDS = {
    "created_utc", "approved_utc", "expiry_utc", "completed_utc",
    "received_utc", "due_utc",
}
OPERATIONS = {
    "approval_revision": "INTEGER NOT NULL",
    "major_reason_codes_json": "TEXT NOT NULL",
    "tier": "TEXT",
    "approver_role": "TEXT",
    "approved_utc": "TEXT",
    "expiry_utc": "TEXT",
    "decision_reason": "TEXT",
    "decision_outcome": "TEXT",
    "peer_checked_by_role": "TEXT",
    "board_decision_id": "TEXT",
    "completed_utc": "TEXT",
    "completion_outcome_code": "TEXT",
    "received_utc": "TEXT",
    "due_utc": "TEXT",
    "calendar_version": "TEXT",
}
JOB_COLUMNS = (
    tuple(name for name in INTAKE_FIELDS if name != "requester_identity")
    + tuple(OPERATIONS)
)


def utc(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def stamp(now: datetime) -> str:
    return utc(now).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_path() -> Path:
    return app_data_dir() / "register.db"


def _encode(name, value):
    if value is None:
        return None
    if name == "created_utc":
        return stamp(value)
    if name == "marking_class":
        return value.name
    if name in {"status", "route", "sensitive_path"}:
        return value.value
    if name in BOOL_FIELDS:
        return int(value)
    return value


def _base(job: JobRecord) -> JobRecord:
    return JobRecord(**{name: getattr(job, name) for name in BASE_FIELDS})


class _SqliteRegisterCore:
    """Original controlled commands; public callers use SqliteRegister below."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        requester_identity: str | None = None,
        holidays: frozenset[date] = frozenset(),
        calendar_version: str = "NO-HOLIDAYS-SUPPLIED",
        journal_local_authorisation_ref: str | None = None,
    ):
        self.path = Path(path) if path is not None else default_path()
        self.requester_identity = identity(
            getpass.getuser() if requester_identity is None else requester_identity
        )
        operator_due_utc(utc(), holidays)
        alias(calendar_version, required=True)
        if holidays and calendar_version == "NO-HOLIDAYS-SUPPLIED":
            raise ValueError("a supplied holiday calendar needs an explicit version")
        self.holidays = frozenset(holidays)
        self.calendar_version = calendar_version
        self.journal_local_authorisation_ref = alias(journal_local_authorisation_ref)
        if self.path.is_symlink() or any(
            parent.is_symlink() for parent in self.path.parents
        ):
            raise ValueError("database symlink paths are forbidden")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError(
                    "unsupported register schema; controlled migration required"
                )
            declarations = []
            for name in JOB_COLUMNS:
                if name == "job_id":
                    declaration = "TEXT PRIMARY KEY"
                elif name in OPERATIONS:
                    declaration = OPERATIONS[name]
                elif name in BOOL_FIELDS or name in INT_FIELDS:
                    declaration = "INTEGER"
                else:
                    declaration = "TEXT"
                declarations.append(f'"{name}" {declaration}')
            statements = [
                "CREATE TABLE IF NOT EXISTS jobs (" + ",".join(declarations) + ")",
                """CREATE TABLE IF NOT EXISTS requesters (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    requester_identity TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS yearly_counters (
                    year INTEGER PRIMARY KEY, last_number INTEGER NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS calendars (
                    version TEXT PRIMARY KEY, holidays_json TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS audit (
                    audit_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    event TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    occurred_utc TEXT NOT NULL,
                    sync_utc TEXT,
                    reason_code TEXT NOT NULL,
                    approval_revision INTEGER NOT NULL,
                    requested_duration_days INTEGER NOT NULL,
                    received_utc TEXT,
                    due_utc TEXT,
                    calendar_version TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS audit_actors (
                    audit_id INTEGER PRIMARY KEY REFERENCES audit(audit_id),
                    actor_identity TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    kind TEXT NOT NULL,
                    approval_revision INTEGER NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    occurred_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS evidence_actors (
                    evidence_id INTEGER PRIMARY KEY REFERENCES evidence(evidence_id),
                    actor_identity TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS approvals (
                    approval_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    manifest_hash TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    approved_utc TEXT NOT NULL,
                    expiry_utc TEXT NOT NULL,
                    model TEXT NOT NULL,
                    deployment_type TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_revision INTEGER NOT NULL,
                    binding_hash TEXT NOT NULL,
                    approver_role TEXT NOT NULL,
                    decision_reason_code TEXT NOT NULL,
                    board_decision_id TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS journal_receipts (
                    entry_id TEXT PRIMARY KEY,
                    entry_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    synced_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS journal_correlations (
                    local_ref TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id)
                )""",
                "CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)",
                "CREATE INDEX IF NOT EXISTS audit_job ON audit(job_id,audit_id)",
                """CREATE INDEX IF NOT EXISTS approvals_job
                   ON approvals(job_id,approval_id)""",
            ]
            for statement in statements:
                connection.execute(statement)
            for table in (
                "audit", "audit_actors", "evidence", "evidence_actors",
                "approvals", "requesters", "calendars", "journal_receipts",
                "journal_correlations",
            ):
                for action in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_denied "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT,'immutable register history'); END"
                    )
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS jobs_delete_denied "
                "BEFORE DELETE ON jobs BEGIN "
                "SELECT RAISE(ABORT,'jobs cannot be deleted'); END"
            )
            calendar_json = canonical_json(
                sorted(day.isoformat() for day in holidays)
            )
            existing = connection.execute(
                "SELECT holidays_json FROM calendars WHERE version=?",
                (calendar_version,),
            ).fetchone()
            if existing and existing[0] != calendar_json:
                raise ValueError(
                    "calendar version reused for different holiday dates"
                )
            connection.execute(
                "INSERT OR IGNORE INTO calendars VALUES (?,?)",
                (calendar_version, calendar_json),
            )
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(
            self.path, timeout=5, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _row(self, connection, job_id):
        alias(job_id, required=True)
        result = connection.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,),
        ).fetchone()
        if result is None:
            raise KeyError("job not found")
        return dict(result)

    def _intake(self, connection, row):
        requester = connection.execute(
            "SELECT requester_identity FROM requesters WHERE job_id=?",
            (row["job_id"],),
        ).fetchone()[0]
        values = {
            name: row[name] for name in INTAKE_FIELDS
            if name != "requester_identity"
        }
        values["requester_identity"] = requester
        values["created_utc"] = datetime.fromisoformat(values["created_utc"])
        values["status"] = S(values["status"])
        values["route"] = Route(values["route"])
        values["marking_class"] = MarkingClass[values["marking_class"]]
        values["sensitive_path"] = (
            SensitivePath(values["sensitive_path"]) if values["sensitive_path"] else None
        )
        for name in BOOL_FIELDS:
            values[name] = bool(values[name])
        return JobIntake(**values)

    def _update(self, connection, job_id, changes):
        if not changes or not set(changes) <= set(JOB_COLUMNS) - {"job_id"}:
            raise ValueError("invalid internal projection update")
        names = list(changes)
        connection.execute(
            "UPDATE jobs SET "
            + ",".join(f'"{name}"=?' for name in names)
            + " WHERE job_id=?",
            [changes[name] for name in names] + [job_id],
        )

    def _audit(
        self, connection, row, event, before, after, role, reason, now,
        actor_identity=None, sync_utc=None,
    ):
        code = reason_code(reason)
        if role != PILOT_ROLE:
            alias(role, required=True)
        cursor = connection.execute(
            """INSERT INTO audit (
                job_id,event,from_status,to_status,actor_role,occurred_utc,sync_utc,
                reason_code,approval_revision,requested_duration_days,
                received_utc,due_utc,calendar_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["job_id"], event, before.value if before else None,
                after.value, role, stamp(now),
                stamp(sync_utc) if sync_utc else None, code,
                row["approval_revision"], row["requested_duration_days"],
                row["received_utc"], row["due_utc"], row["calendar_version"],
            ),
        )
        if actor_identity is not None:
            connection.execute(
                "INSERT INTO audit_actors VALUES (?,?)",
                (cursor.lastrowid, identity(actor_identity)),
            )

    def _move(
        self, connection, row, target, role, code, now,
        actor_identity=None, event=None, sync_utc=None,
    ):
        before = S(row["status"])
        if target not in LEGAL_TRANSITIONS[before]:
            raise ValueError(
                f"illegal transition: {before.value} -> {target.value}"
            )
        self._update(connection, row["job_id"], {"status": target.value})
        row = self._row(connection, row["job_id"])
        self._audit(
            connection, row, event or target.name, before, target, role, code,
            now, actor_identity, sync_utc,
        )
        return row

    def _insert(self, connection, job, now, event_time=None, sync_time=None):
        year = now.year
        counter = connection.execute(
            "SELECT last_number FROM yearly_counters WHERE year=?", (year,),
        ).fetchone()
        number = (counter[0] if counter else 0) + 1
        if number > 9999:
            raise OverflowError("yearly four-digit JOB series exhausted")
        connection.execute(
            """INSERT INTO yearly_counters VALUES (?,?)
               ON CONFLICT(year) DO UPDATE
               SET last_number=excluded.last_number""",
            (year, number),
        )
        job = replace(
            job, job_id=f"JOB-{year:04d}-{number:04d}",
            created_utc=now, status=S.LOGGED,
        )
        validate_intake(job)
        if job.sensitive_path == SensitivePath.CLOUD_CONFIRMED:
            raise PermissionError(
                "confirmation must use the controlled evidence command"
            )
        values = {
            name: _encode(name, getattr(job, name))
            for name in INTAKE_FIELDS if name != "requester_identity"
        }
        values.update({name: None for name in OPERATIONS})
        values["approval_revision"] = 1
        values["major_reason_codes_json"] = canonical_json(major_codes(job))
        connection.execute(
            "INSERT INTO jobs (" + ",".join(JOB_COLUMNS) + ") VALUES ("
            + ",".join("?" for _ in JOB_COLUMNS) + ")",
            [values[name] for name in JOB_COLUMNS],
        )
        connection.execute(
            "INSERT INTO requesters VALUES (?,?)",
            (job.job_id, job.requester_identity),
        )
        row = self._row(connection, job.job_id)
        self._audit(
            connection, row, "LOGGED", None, S.LOGGED, "gate", "INTAKE",
            event_time or now, sync_utc=sync_time,
        )
        return row

    def log_job(self, job: JobRecord, *, now=None) -> JobRecord:
        if not isinstance(job, JobRecord):
            raise TypeError(
                "JobRecord required; arbitrary dictionaries are forbidden"
            )
        if job.status != S.LOGGED:
            raise ValueError("intake cannot set a lifecycle decision")
        if not isinstance(job, JobIntake):
            job = JobIntake(**{
                name: getattr(job, name) for name in BASE_FIELDS
            })
        if not job.requester_identity:
            job = replace(job, requester_identity=self.requester_identity)
        if not job.use_case_use_case_ref and not job.use_case_record_ref:
            job = replace(job, no_matching_approved_use_case=True)
        now = utc(now)
        with self._transaction() as connection:
            row = self._insert(connection, job, now)
            return _base(self._intake(connection, row))

    def get_job(self, job_id: str) -> JobRecord:
        with self._transaction() as connection:
            return _base(self._intake(
                connection, self._row(connection, job_id)
            ))

    def get_details(self, job_id: str) -> dict:
        with self._transaction() as connection:
            return self._row(connection, job_id)

    def list_jobs(self, status=None) -> tuple[dict, ...]:
        with self._transaction() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_utc,job_id"
                )
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE status=? "
                    "ORDER BY created_utc,job_id",
                    (S(status).value,),
                )
            return tuple(dict(row) for row in rows)

    def audit_rows(self, job_id: str) -> tuple[dict, ...]:
        with self._transaction() as connection:
            self._row(connection, job_id)
            return tuple(dict(row) for row in connection.execute(
                "SELECT * FROM audit WHERE job_id=? ORDER BY audit_id",
                (job_id,),
            ))

    def _binding(self, row):
        value = {
            name: row[name] for name in INTAKE_FIELDS
            if name not in {"requester_identity", "created_utc", "status"}
        }
        value["approval_revision"] = row["approval_revision"]
        return hashlib.sha256(
            canonical_json(value).encode("ascii")
        ).hexdigest()

    def _evidence(self, connection, row, kind):
        return connection.execute(
            """SELECT e.*,a.actor_identity FROM evidence e
               JOIN evidence_actors a USING(evidence_id)
               WHERE job_id=? AND kind=? AND approval_revision=?
               AND manifest_hash=?
               ORDER BY evidence_id DESC LIMIT 1""",
            (
                row["job_id"], kind,
                row["approval_revision"], row["manifest_hash"],
            ),
        ).fetchone()

    def _ready(self, connection, row):
        job = self._intake(connection, row)
        validate_intake(job)
        if (
            job.route != Route.CLOUD
            or job.marking_reason_code == "UNRESOLVED"
            or not job.manifest_hash or not job.destination_id
            or not job.model or not job.deployment_type
        ):
            raise ValueError("complete permitted cloud scope required")
        if job.interim_manual_assisted and not self._evidence(
            connection, row, "PEER"
        ):
            raise PermissionError(
                "manual-assisted B requires a current successful peer check"
            )
        if (
            marking_policy().needs_confirm(job.marking_class)
            or job.confirmation_required
        ):
            confirmation = self._evidence(connection, row, "CONFIRM")
            if not confirmation:
                raise PermissionError("current requester confirmation required")
            if (
                marking_policy().needs_confirm(job.marking_class)
                and job.sensitive_path != SensitivePath.CLOUD_CONFIRMED
            ):
                raise PermissionError("sensitive cloud path is not confirmed")

    def _clear_effective_decision(self, connection, job_id):
        self._update(connection, job_id, {
            "tier": None, "approver_role": None, "approved_utc": None,
            "expiry_utc": None, "decision_reason": None,
            "decision_outcome": None, "board_decision_id": None,
        })

    def _edit(
        self, connection, row, changes, now, expected_revision=None,
    ):
        if (
            expected_revision is not None
            and expected_revision != row["approval_revision"]
        ):
            raise ValueError("stale approval revision")
        if not set(changes) <= EDITABLE_FIELDS:
            raise PermissionError(
                "requester cannot edit decisions, identities or evidence"
            )
        old = self._intake(connection, row)
        candidate = replace(old, **changes)
        if candidate.sensitive_path == SensitivePath.CLOUD_CONFIRMED:
            if any(
                getattr(candidate, name) != getattr(old, name)
                for name in EDITABLE_FIELDS
            ):
                candidate = replace(candidate, sensitive_path=None)
        validate_intake(candidate)
        differences = {
            name: _encode(name, getattr(candidate, name))
            for name in EDITABLE_FIELDS
            if getattr(candidate, name) != getattr(old, name)
        }
        if not differences:
            return row
        differences.update({
            "approval_revision": row["approval_revision"] + 1,
            "peer_checked_by_role": None,
            "received_utc": None, "due_utc": None, "calendar_version": None,
            "major_reason_codes_json": canonical_json(major_codes(candidate)),
        })
        self._update(connection, row["job_id"], differences)
        self._clear_effective_decision(connection, row["job_id"])
        row = self._row(connection, row["job_id"])
        self._audit(
            connection, row, "REVISION", S(row["status"]), S(row["status"]),
            "requester", "REVISED", now, old.requester_identity,
        )
        return row

    def revise(
        self, job_id, *, changes, requester_identity,
        expected_revision, now=None,
    ) -> JobRecord:
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            old = self._intake(connection, row)
            if identity(requester_identity) != old.requester_identity:
                raise PermissionError("only this requester can revise the job")
            if expected_revision != row["approval_revision"]:
                raise ValueError("stale approval revision")
            state = S(row["status"])
            if state not in {
                S.APPROVED, S.EXPIRED, S.LOGGED, S.AUTO_ALLOWED_LOCAL,
            }:
                raise ValueError("this state requires withdrawal and a new job")
            if (
                state == S.AUTO_ALLOWED_LOCAL
                and changes.get("route", old.route) != Route.LOCAL_ONLY
            ):
                raise ValueError(
                    "handover route changes belong in complete submission"
                )
            if (
                not set(changes) <= EDITABLE_FIELDS
                or changes.get("sensitive_path") == SensitivePath.CLOUD_CONFIRMED
            ):
                raise PermissionError("forbidden requester edit")
            if state == S.APPROVED:
                row = self._move(
                    connection, row, S.EXPIRED, "requester",
                    "BINDING_CHANGED", now, requester_identity,
                )
            if S(row["status"]) == S.EXPIRED:
                row = self._move(
                    connection, row, S.LOGGED, "requester", "REVISED",
                    now, requester_identity,
                )
                self._update(connection, job_id, {
                    "approval_revision": row["approval_revision"] + 1,
                    "peer_checked_by_role": None,
                    "received_utc": None, "due_utc": None,
                    "calendar_version": None,
                    "sensitive_path": (
                        None if old.sensitive_path == SensitivePath.CLOUD_CONFIRMED
                        else row["sensitive_path"]
                    ),
                })
                self._clear_effective_decision(connection, job_id)
                row = self._row(connection, job_id)
            row = self._edit(connection, row, changes, now)
            return _base(self._intake(connection, row))

    def _complete_package_submission(self, connection, row, now):
        """In-transaction extension point; the public two-stage class overrides."""
        return row

    def submit_for_approval(
        self, job: JobRecord, *, now=None, expected_revision=None,
    ) -> JobRecord:
        if not isinstance(job, JobRecord):
            raise TypeError("JobRecord required")
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job.job_id)
            if S(row["status"]) not in {S.LOGGED, S.AUTO_ALLOWED_LOCAL}:
                raise ValueError(
                    "submission requires Logged or Auto-Allowed-Local"
                )
            if job.created_utc != datetime.fromisoformat(row["created_utc"]):
                raise ValueError("stale or mismatched intake")
            names = (
                EDITABLE_FIELDS if isinstance(job, JobIntake)
                else set(BASE_FIELDS) & EDITABLE_FIELDS
            )
            changes = {name: getattr(job, name) for name in names}
            if changes.get("sensitive_path") == SensitivePath.CLOUD_CONFIRMED:
                current = self._intake(connection, row)
                if current.sensitive_path != SensitivePath.CLOUD_CONFIRMED:
                    raise PermissionError(
                        "generic submission cannot create confirmation"
                    )
            row = self._edit(
                connection, row, changes, now, expected_revision
            )
            self._ready(connection, row)
            if now < datetime.fromisoformat(row["created_utc"]):
                raise ValueError("submission precedes intake")
            self._update(connection, job.job_id, {
                "received_utc": stamp(now),
                "due_utc": stamp(operator_due_utc(now, self.holidays)),
                "calendar_version": self.calendar_version,
                "major_reason_codes_json": canonical_json(
                    major_codes(self._intake(connection, row))
                ),
            })
            row = self._move(
                connection, self._row(connection, job.job_id),
                S.AWAITING_OPERATOR, "gate", "SUBMITTED", now,
                event="SUBMITTED",
            )
            row = self._complete_package_submission(connection, row, now)
            return _base(self._intake(connection, row))

    def _record_evidence(
        self, job_id, kind, actor, role, revision, manifest_hash, now,
    ):
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            if S(row["status"]) not in {
                S.LOGGED, S.AUTO_ALLOWED_LOCAL, S.AWAITING_OPERATOR,
                S.ESCALATED_REVIEW,
            }:
                raise ValueError("checks require an unclosed package revision")
            if (
                revision != row["approval_revision"]
                or manifest_hash != row["manifest_hash"]
                or not manifest_hash
            ):
                raise ValueError("stale check or confirmation")
            job = self._intake(connection, row)
            actor = identity(actor)
            if kind == "CONFIRM" and actor != job.requester_identity:
                raise PermissionError("confirmation must be by this requester")
            if kind == "PEER" and actor == job.requester_identity:
                raise PermissionError("peer checker must be independent")
            if now < job.created_utc:
                raise ValueError("evidence precedes intake")
            cursor = connection.execute(
                """INSERT INTO evidence (
                    job_id,kind,approval_revision,manifest_hash,
                    actor_role,occurred_utc
                ) VALUES (?,?,?,?,?,?)""",
                (job_id, kind, revision, manifest_hash, role, stamp(now)),
            )
            connection.execute(
                "INSERT INTO evidence_actors VALUES (?,?)",
                (cursor.lastrowid, actor),
            )
            changes = {}
            if kind == "PEER":
                changes["peer_checked_by_role"] = role
            elif marking_policy().needs_confirm(job.marking_class):
                changes["sensitive_path"] = SensitivePath.CLOUD_CONFIRMED.value
            if changes:
                self._update(connection, job_id, changes)
            row = self._row(connection, job_id)
            code = "PEER_CHECKED" if kind == "PEER" else "CONFIRMED"
            self._audit(
                connection, row, code, S(row["status"]), S(row["status"]),
                role, code, now, actor,
            )

    def record_peer_check(
        self, job_id, *, checker_identity, revision, manifest_hash, now=None,
    ):
        self._record_evidence(
            job_id, "PEER", checker_identity, "peer-checker",
            revision, manifest_hash, now,
        )

    def confirm_payload(
        self, job_id, *, requester_identity, revision, manifest_hash, now=None,
    ):
        self._record_evidence(
            job_id, "CONFIRM", requester_identity, "requester",
            revision, manifest_hash, now,
        )

    def _human(self, connection, row, role, actor):
        actor = identity(actor)
        if actor == self._intake(connection, row).requester_identity:
            raise PermissionError("self-approval/decision is refused")
        peer = self._evidence(connection, row, "PEER")
        if peer and actor == peer["actor_identity"]:
            raise PermissionError("peer checker cannot also decide")
        state = S(row["status"])
        if (
            state == S.AWAITING_OPERATOR
            and role not in OPERATOR_ROLES | {PILOT_ROLE}
        ):
            raise PermissionError("Operator authority required")
        if state == S.ESCALATED_REVIEW and role != "review-board":
            raise PermissionError("review board authority required")
        if state not in {S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}:
            raise ValueError("job is not awaiting a human decision")
        return actor

    def _approval_for(self, connection, row, now):
        stored = connection.execute(
            "SELECT * FROM approvals WHERE job_id=? "
            "ORDER BY approval_id DESC LIMIT 1",
            (row["job_id"],),
        ).fetchone()
        if stored is None:
            return None
        if (
            stored["approval_revision"] != row["approval_revision"]
            or stored["binding_hash"] != self._binding(row)
        ):
            return None
        approval = ApprovalRecord(
            stored["job_id"], stored["manifest_hash"], stored["destination_id"],
            datetime.fromisoformat(stored["approved_utc"]),
            datetime.fromisoformat(stored["expiry_utc"]),
            stored["model"], stored["deployment_type"],
            Tier(stored["tier"]), S(stored["status"]),
        )
        if not approval_is_valid(
            approval, _base(self._intake(connection, row)), now
        ):
            return None
        self._ready(connection, row)
        return approval

    def current_approval(self, job_id, now=None):
        with self._transaction() as connection:
            return self._approval_for(
                connection, self._row(connection, job_id), utc(now)
            )

    def get_approval(self, job_id):
        return self.current_approval(job_id)

    def _approve(
        self, connection, row, role, decision_reason, now, actor,
        board_decision_id=None, approved_duration_days=None,
        decision_outcome=None, pilot_enabled=False,
    ):
        code = reason_code(decision_reason)
        if role == PILOT_ROLE:
            job = self._intake(connection, row)
            if not pilot_enabled or not job.synthetic or is_major(job):
                raise PermissionError(
                    "pilot fixture conditions are not all satisfied"
                )
            assert not marking_policy().is_local_only(job.marking_class)
            if code != "PILOT_APPROVED":
                raise ValueError("pilot fixture requires its fixed reason")
        actor = self._human(connection, row, role, actor)
        self._ready(connection, row)
        if now < datetime.fromisoformat(row["received_utc"]):
            raise ValueError("decision precedes complete submission")
        job = self._intake(connection, row)
        if S(row["status"]) == S.AWAITING_OPERATOR and is_major(job):
            self._move(
                connection, row, S.ESCALATED_REVIEW, role, "MAJOR",
                now, actor, event="ESCALATED",
            )
            return None
        tier = (
            Tier.AI_CAB if S(row["status"]) == S.ESCALATED_REVIEW
            else Tier.OPERATOR
        )
        if tier == Tier.AI_CAB:
            alias(board_decision_id, required=True)
        elif board_decision_id is not None:
            raise ValueError(
                "routine decisions do not manufacture review-board references"
            )
        if (
            not job.use_case_use_case_ref and not job.use_case_record_ref
            or job.no_matching_approved_use_case
        ):
            raise PermissionError(
                "the review board cannot waive missing current use-case endorsement"
            )
        days = (
            job.requested_duration_days
            if approved_duration_days is None else approved_duration_days
        )
        if (
            type(days) is not int or days < 1
            or days > job.requested_duration_days
            or tier == Tier.OPERATOR and days > 7
        ):
            raise ValueError(
                "approved duration exceeds delegated/requested scope"
            )
        if (
            decision_outcome is not None
            and decision_outcome not in REVIEW_BOARD_OUTCOMES
        ):
            raise ValueError("invalid review-board outcome")
        if decision_outcome in {"Refer-Admin", "Refer-Governance"}:
            raise ValueError("a referral is not an approval")
        approval = ApprovalRecord(
            job.job_id, job.manifest_hash, job.destination_id,
            now, now + timedelta(days=days),
            job.model, job.deployment_type, tier,
        )
        if not approval_is_valid(
            approval, replace(_base(job), status=S.APPROVED), now
        ):
            raise ValueError("approval would not be valid")
        connection.execute(
            """INSERT INTO approvals (
                job_id,manifest_hash,destination_id,approved_utc,expiry_utc,
                model,deployment_type,tier,status,approval_revision,binding_hash,
                approver_role,decision_reason_code,board_decision_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.job_id, job.manifest_hash, job.destination_id,
                stamp(now), stamp(approval.expiry_utc), job.model,
                job.deployment_type, tier.value, S.APPROVED.value,
                row["approval_revision"], self._binding(row),
                role, code, board_decision_id,
            ),
        )
        self._update(connection, job.job_id, {
            "tier": tier.value, "approver_role": role,
            "approved_utc": stamp(now), "expiry_utc": stamp(approval.expiry_utc),
            "decision_reason": REASONS[code],
            "board_decision_id": board_decision_id,
            "decision_outcome": decision_outcome,
        })
        self._move(
            connection, self._row(connection, job.job_id),
            S.APPROVED, role, code, now, actor, event="APPROVED",
        )
        return approval

    def approve(
        self, job_id, approver_role, decision_reason, now=None, *,
        approver_identity, board_decision_id=None, approved_duration_days=None,
        decision_outcome=None,
    ):
        if approver_role == PILOT_ROLE:
            raise PermissionError(
                "use PilotAutoApprover; a role string is not a fixture"
            )
        with self._transaction() as connection:
            return self._approve(
                connection, self._row(connection, job_id), approver_role,
                decision_reason, utc(now), approver_identity,
                board_decision_id, approved_duration_days, decision_outcome,
            )

    def _pilot_approve(self, job_id, *, enabled, actor, now):
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            job = self._intake(connection, row)
            if marking_policy().is_local_only(job.marking_class):
                assert job.route == Route.LOCAL_ONLY
                return None
            if (
                enabled is not True or not job.synthetic or is_major(job)
                or job.status != S.AWAITING_OPERATOR
            ):
                return None
            return self._approve(
                connection, row, PILOT_ROLE, "PILOT_APPROVED",
                utc(now), actor, pilot_enabled=True,
            )

    def escalate(
        self, job_id, approver_role, decision_reason="MAJOR", now=None, *,
        approver_identity, board_decision_id=None, decision_outcome=None,
    ):
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            actor = self._human(
                connection, row, approver_role, approver_identity
            )
            code = reason_code(decision_reason)
            if S(row["status"]) == S.ESCALATED_REVIEW:
                if (
                    code != "REFERRED"
                    or decision_outcome not in {"Refer-Admin", "Refer-Governance"}
                ):
                    raise ValueError(
                        "review-board self-transition is an unresolved referral only"
                    )
                alias(board_decision_id, required=True)
                self._update(connection, job_id, {
                    "board_decision_id": board_decision_id,
                    "decision_outcome": decision_outcome,
                    "decision_reason": REASONS[code],
                })
                event = "REFERRED"
            else:
                if approver_role not in OPERATOR_ROLES:
                    raise PermissionError("only an Operator escalates")
                if not is_major(self._intake(connection, row)):
                    if code != "OPERATOR_DOUBT":
                        raise ValueError(
                            "routine escalation requires explicit Operator doubt"
                        )
                    self._update(connection, job_id, {"operator_doubt": 1})
                event = "ESCALATED"
            if now < datetime.fromisoformat(row["received_utc"]):
                raise ValueError("decision precedes submission")
            row = self._row(connection, job_id)
            self._update(connection, job_id, {
                "major_reason_codes_json": canonical_json(
                    major_codes(self._intake(connection, row))
                ),
            })
            row = self._move(
                connection, self._row(connection, job_id), S.ESCALATED_REVIEW,
                approver_role, code, now, actor, event=event,
            )
            return _base(self._intake(connection, row))

    def refuse(
        self, job_id, approver_role, decision_reason="REFUSED", now=None, *,
        approver_identity, board_decision_id=None, decision_outcome=None,
    ):
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            actor = self._human(
                connection, row, approver_role, approver_identity
            )
            code = reason_code(decision_reason)
            if now < datetime.fromisoformat(row["received_utc"]):
                raise ValueError("decision precedes submission")
            if (
                S(row["status"]) == S.AWAITING_OPERATOR
                and is_major(self._intake(connection, row))
            ):
                row = self._move(
                    connection, row, S.ESCALATED_REVIEW, approver_role,
                    "MAJOR", now, actor, event="ESCALATED",
                )
                return _base(self._intake(connection, row))
            if S(row["status"]) == S.ESCALATED_REVIEW:
                alias(board_decision_id, required=True)
            if (
                decision_outcome is not None
                and decision_outcome not in REVIEW_BOARD_OUTCOMES
            ):
                raise ValueError("invalid review-board outcome")
            self._update(connection, job_id, {
                "approver_role": approver_role,
                "decision_reason": REASONS[code],
                "tier": (
                    Tier.AI_CAB.value
                    if S(row["status"]) == S.ESCALATED_REVIEW
                    else Tier.OPERATOR.value
                ),
                "board_decision_id": board_decision_id,
                "decision_outcome": decision_outcome,
            })
            row = self._move(
                connection, self._row(connection, job_id), S.REFUSED,
                approver_role, code, now, actor, event="REFUSED",
            )
            return _base(self._intake(connection, row))

    def auto_allow_local(
        self, job_id, *, actor_role="gate", local_authorisation_ref=None,
        new_marking=None, now=None,
    ):
        now = utc(now)
        if actor_role not in GATE_ROLES:
            raise PermissionError("controlled Gate/Custodian procedure required")
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            job = self._intake(connection, row)
            if S(row["status"]) != S.LOGGED:
                if job.route != Route.CLOUD or new_marking is None:
                    raise ValueError(
                        "active-cloud rerouting requires new mandatory-local evidence"
                    )
            if job.route == Route.CLOUD:
                if (
                    not isinstance(new_marking, MarkingClass)
                    or not marking_policy().is_local_only(new_marking)
                ):
                    raise ValueError("mandatory-local marking evidence required")
                marking, code = new_marking, "MANDATORY_LOCAL"
            else:
                marking, code = job.marking_class, "LOCAL_AUTHORISED"
            authority = local_authorisation_ref or job.local_authorisation_ref
            alias(authority, required=True)
            self._update(connection, job_id, {
                "route": Route.LOCAL_ONLY.value,
                "marking_class": marking.name,
                "marking_reason_code": (
                    "MANDATORY_LOCAL" if code == "MANDATORY_LOCAL"
                    else row["marking_reason_code"]
                ),
                "local_authorisation_ref": authority,
                "sensitive_path": (
                    SensitivePath.LOCAL.value
                    if marking_policy().needs_confirm(marking) else None
                ),
            })
            self._clear_effective_decision(connection, job_id)
            row = self._move(
                connection, self._row(connection, job_id),
                S.AUTO_ALLOWED_LOCAL, actor_role, code, now,
            )
            return _base(self._intake(connection, row))

    def withdraw(
        self, job_id, approver_role="requester",
        decision_reason="WITHDRAWN", now=None, *, requester_identity,
    ):
        if approver_role != "requester":
            raise PermissionError(
                "withdrawal is a requester-controlled procedure"
            )
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            if identity(requester_identity) != self._intake(
                connection, row
            ).requester_identity:
                raise PermissionError("only this requester can withdraw")
            row = self._move(
                connection, row, S.WITHDRAWN, "requester",
                reason_code(decision_reason), utc(now), requester_identity,
            )
            return _base(self._intake(connection, row))

    def complete(
        self, job_id, actor_role="gate", decision_reason="COMPLETED",
        now=None, *, completion_outcome_code="FINISHED",
    ):
        if (
            actor_role not in GATE_ROLES
            or completion_outcome_code not in COMPLETION_CODES
        ):
            raise PermissionError(
                "controlled completion and safe outcome required"
            )
        now = utc(now)
        with self._transaction() as connection:
            self._row(connection, job_id)
            self._update(connection, job_id, {
                "completed_utc": stamp(now),
                "completion_outcome_code": completion_outcome_code,
            })
            row = self._move(
                connection, self._row(connection, job_id), S.COMPLETED,
                actor_role, reason_code(decision_reason), now,
            )
            return _base(self._intake(connection, row))

    def hold_offline(self, job_id, *, actor_role="gate", now=None):
        if actor_role not in GATE_ROLES:
            raise PermissionError("controlled Gate/Custodian procedure required")
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            if row["route"] != Route.CLOUD.value:
                raise ValueError("Held-Offline is a cloud state")
            row = self._move(
                connection, row, S.HELD_OFFLINE, actor_role, "OFFLINE", utc(now)
            )
            return _base(self._intake(connection, row))

    def reconcile(
        self, job_id, *, target=S.LOGGED, audit_receipt_ref=None,
        actor_role="gate", now=None,
    ):
        if actor_role not in GATE_ROLES:
            raise PermissionError("controlled reconciliation required")
        now = utc(now)
        with self._transaction() as connection:
            row = self._row(connection, job_id)
            if S(row["status"]) != S.HELD_OFFLINE:
                raise ValueError("job is not Held-Offline")
            target = S(target)
            if target == S.APPROVED:
                alias(audit_receipt_ref, required=True)
                prospective = dict(row, status=S.APPROVED.value)
                if self._approval_for(connection, prospective, now) is None:
                    raise PermissionError(
                        "fresh unchanged unexpired approval required"
                    )
            elif target == S.LOGGED:
                self._clear_effective_decision(connection, job_id)
                self._update(connection, job_id, {
                    "approval_revision": row["approval_revision"] + 1,
                    "peer_checked_by_role": None,
                    "received_utc": None, "due_utc": None,
                    "calendar_version": None,
                    "sensitive_path": (
                        None
                        if row["sensitive_path"] == SensitivePath.CLOUD_CONFIRMED.value
                        else row["sensitive_path"]
                    ),
                })
                row = self._row(connection, job_id)
            else:
                raise ValueError(
                    "reconciliation target must be Logged or Approved"
                )
            row = self._move(
                connection, row, target, actor_role, "RECONCILED", now
            )
            return _base(self._intake(connection, row))

    def expire_due(self, now=None) -> tuple[str, ...]:
        now = utc(now)
        expired = []
        with self._transaction() as connection:
            found_rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status=? ORDER BY job_id",
                (S.APPROVED.value,),
            ).fetchall()
            for found in found_rows:
                row = self._row(connection, found[0])
                if self._approval_for(connection, row, now) is None:
                    if now < datetime.fromisoformat(row["approved_utc"]):
                        continue
                    self._move(
                        connection, row, S.EXPIRED, "gate", "EXPIRED", now
                    )
                    expired.append(row["job_id"])
        return tuple(expired)

    def transition(
        self, job_id, target, *, actor_role, reason, now=None, **kwargs,
    ):
        target = S(target)
        before = self.get_job(job_id).status
        if target not in LEGAL_TRANSITIONS[before]:
            raise ValueError(
                f"illegal transition: {before.value} -> {target.value}"
            )
        if target == S.APPROVED:
            if before == S.HELD_OFFLINE:
                return self.reconcile(
                    job_id, target=target, actor_role=actor_role,
                    now=now, **kwargs,
                )
            return self.approve(job_id, actor_role, reason, now, **kwargs)
        if target == S.REFUSED:
            return self.refuse(job_id, actor_role, reason, now, **kwargs)
        if target == S.ESCALATED_REVIEW:
            return self.escalate(job_id, actor_role, reason, now, **kwargs)
        if target == S.AUTO_ALLOWED_LOCAL:
            return self.auto_allow_local(
                job_id, actor_role=actor_role, now=now, **kwargs
            )
        if target == S.AWAITING_OPERATOR:
            if actor_role not in GATE_ROLES:
                raise PermissionError("controlled submission required")
            return self.submit_for_approval(
                kwargs.pop("job", self.get_job(job_id)), now=now, **kwargs
            )
        if target == S.WITHDRAWN:
            return self.withdraw(job_id, actor_role, reason, now, **kwargs)
        if target == S.COMPLETED:
            return self.complete(job_id, actor_role, reason, now, **kwargs)
        if target == S.HELD_OFFLINE:
            return self.hold_offline(job_id, actor_role=actor_role, now=now)
        if target == S.LOGGED:
            if before == S.HELD_OFFLINE:
                return self.reconcile(
                    job_id, actor_role=actor_role, now=now, **kwargs
                )
            if actor_role != "requester":
                raise PermissionError("reassessment is requester-controlled")
            return self.revise(job_id, now=now, **kwargs)
        if target == S.EXPIRED:
            if actor_role not in GATE_ROLES:
                raise PermissionError(
                    "expiry is a controlled Gate/Custodian transition"
                )
            when = utc(now)
            with self._transaction() as connection:
                row = self._row(connection, job_id)
                if when < datetime.fromisoformat(row["expiry_utc"]):
                    raise ValueError("approval has not reached expiry")
                row = self._move(
                    connection, row, target, actor_role, "EXPIRED", when
                )
                return _base(self._intake(connection, row))
        raise ValueError("unsupported controlled command")

    def sync_journal(self, entries: tuple[JournalEntry, ...]):
        if not isinstance(entries, tuple):
            raise TypeError("tuple of JournalEntry required")
        now = utc()
        acknowledged = []
        with self._transaction() as connection:
            for entry in entries:
                if not isinstance(entry, JournalEntry):
                    raise TypeError("JournalEntry required")
                payload = canonical_json(_entry_object(entry))
                digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
                received = connection.execute(
                    "SELECT entry_hash FROM journal_receipts WHERE entry_id=?",
                    (entry.entry_id,),
                ).fetchone()
                if received:
                    if received[0] != digest:
                        raise ValueError("conflicting journal entry replay")
                    acknowledged.append(entry.entry_id)
                    continue
                if entry.job.route != Route.LOCAL_ONLY:
                    raise PermissionError(
                        "cloud journal replay is not approval authority"
                    )
                alias(self.journal_local_authorisation_ref, required=True)
                linked = connection.execute(
                    "SELECT job_id FROM journal_correlations WHERE local_ref=?",
                    (entry.job.job_id,),
                ).fetchone()
                if linked is None:
                    if entry.job.status not in {
                        S.LOGGED, S.AUTO_ALLOWED_LOCAL,
                    }:
                        raise ValueError("journal sequence lacks intake")
                    job = JobIntake(
                        **{
                            name: getattr(entry.job, name)
                            for name in BASE_FIELDS
                        },
                        requester_identity=self.requester_identity,
                        local_authorisation_ref=(
                            self.journal_local_authorisation_ref
                        ),
                    )
                    row = self._insert(
                        connection, replace(job, status=S.LOGGED), now,
                        utc(entry.occurred_utc), now,
                    )
                    connection.execute(
                        "INSERT INTO journal_correlations VALUES (?,?)",
                        (entry.job.job_id, row["job_id"]),
                    )
                else:
                    row = self._row(connection, linked[0])
                    current = self._intake(connection, row)
                    if any(
                        getattr(current, name) != getattr(entry.job, name)
                        for name in BASE_FIELDS
                        if name not in {"job_id", "created_utc", "status"}
                    ):
                        raise ValueError(
                            "journal scope changed; explicit reconciliation required"
                        )
                target = entry.job.status
                if target != S(row["status"]):
                    if target not in {
                        S.AUTO_ALLOWED_LOCAL, S.COMPLETED, S.WITHDRAWN,
                    }:
                        raise ValueError(
                            "unsupported local journal transition"
                        )
                    if target == S.COMPLETED:
                        self._update(connection, row["job_id"], {
                            "completed_utc": stamp(utc(entry.occurred_utc)),
                            "completion_outcome_code": "FINISHED",
                        })
                    row = self._move(
                        connection, self._row(connection, row["job_id"]),
                        target, "process-custodian", "SYNCED",
                        utc(entry.occurred_utc), event="SYNCED", sync_utc=now,
                    )
                else:
                    self._audit(
                        connection, row, "SYNC_RECEIPT", target, target,
                        "process-custodian", "SYNCED", utc(entry.occurred_utc),
                        sync_utc=now,
                    )
                connection.execute(
                    "INSERT INTO journal_receipts VALUES (?,?,?,?)",
                    (entry.entry_id, digest, row["job_id"], stamp(now)),
                )
                acknowledged.append(entry.entry_id)
        return tuple(acknowledged)

    def report(self, *, unsuppressed_synthetic=False):
        with self._transaction() as connection:
            rows = [
                dict(row) for row in connection.execute("SELECT * FROM jobs")
            ]
            if unsuppressed_synthetic and any(
                not row["synthetic"] for row in rows
            ):
                raise PermissionError(
                    "unsuppressed reports require every job to be labelled synthetic"
                )
            events = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM audit ORDER BY audit_id"
                )
            ]
            calendars = {
                row["version"]: frozenset(
                    date.fromisoformat(day)
                    for day in json.loads(row["holidays_json"])
                )
                for row in connection.execute("SELECT * FROM calendars")
            }
            return build_report(
                rows, events, calendars, 1 if unsuppressed_synthetic else 5
            )

    def export_interim(self, output):
        rows = self.list_jobs()
        destination = Path(output)
        if destination.is_symlink():
            raise ValueError("export symlinks are forbidden")

        def cell(value):
            if value is None:
                return ""
            text = str(value)
            if text.lstrip().startswith(("=", "+", "-", "@")):
                return "'" + text
            return text

        columns = ("record_kind", "interim_reference") + JOB_COLUMNS
        with destination.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                values = {
                    "record_kind": "SYNTHETIC SNAPSHOT — NOT APPROVAL AUTHORITY",
                    "interim_reference": row["job_id"].replace(
                        "JOB-", "DEMO-INTERIM-", 1
                    ),
                    **row,
                }
                writer.writerow({
                    name: cell(values[name]) for name in columns
                })
            stream.flush()
            os.fsync(stream.fileno())
        return len(rows)


class SqliteRegister(GateRecords, TwoStageCommands, _SqliteRegisterCore):
    """Authoritative synthetic register with cooperative two-stage commands.

    Required MRO:
        SqliteRegister -> GateRecords -> TwoStageCommands -> _SqliteRegisterCore -> object

    ``GateRecords`` (v0.3) only adds append-only decision and trip-wire record tables and commands.

    The minimal JobRecord/JobRegisterClient return contract is unchanged.
    Extended API fields are obtained from safe register row projections.
    """
