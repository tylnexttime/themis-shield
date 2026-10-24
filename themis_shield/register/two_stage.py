"""Cooperative two-stage commands, safe local projections and stale intake closure."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib

from ..detectors.markings import MarkingClass, marking_policy
from ..routing import Route
from .client import JobStatus as S, is_major, operator_due_utc
from .models import (
    PILOT_ROLE, JobIntake, canonical_json, identity, major_codes,
    reason_code, validate_intake,
)


STAGE_REASONS = {
    "STAGE1_REQUESTED": "De-identified summary approval requested.",
    "STAGE1_APPROVED": "De-identified summary approved.",
    "PACKAGE_READY": "Immutable clean package prepared.",
    "PACKAGE_FAILED": "Required package preparation failed.",
    "STAGE2_APPROVED": "Final clean package hash approved.",
    "STAGE1_REASSESS": "Changed assessed scope requires summary reassessment.",
    "GATE_REFUSED": "Required processing or release control failed.",
    "SEND_AUTHORISED": "Both approvals and the immutable release were validated.",
    "ABANDONED_SYNTHETIC": "Inactive synthetic intake withdrawn after 30 minutes.",
}
PACKAGE_FIELDS = {
    "manifest_hash", "pii_counts_json", "sensitive_categories_present",
    "sensitive_category_codes_json", "confirmation_required", "problem_summary_deid",
}


class TwoStageCommands:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self._transaction() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            additions = {
                # Nullable: these stages do not exist for local-only jobs.
                "stage1": "TEXT DEFAULT 'requested'",
                "package": "TEXT DEFAULT 'building'",
                "stage2": "TEXT DEFAULT 'pending'",
                "stage2_manifest_hash": "TEXT",
                "timings_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN "{name}" {declaration}')
            # Older installations declared these three columns NOT NULL.
            # Rebuild only the jobs table, retaining IDs and all history tables.
            info = list(db.execute("PRAGMA table_info(jobs)"))
            if any(row[3] for row in info if row[1] in {"stage1", "package", "stage2"}):
                declarations = []
                for row in info:
                    name, kind, required, default, primary = row[1:6]
                    declaration = f'"{name}" {kind}'
                    if primary:
                        declaration += " PRIMARY KEY"
                    if required and name not in {"stage1", "package", "stage2"}:
                        declaration += " NOT NULL"
                    if default is not None:
                        declaration += f" DEFAULT {default}"
                    declarations.append(declaration)
                # Keep the referenced table name unchanged during the rebuild.
                # No foreign-key-bearing table is renamed.
                db.execute("CREATE TABLE jobs_m7 (" + ",".join(declarations) + ")")
                names = ",".join(f'"{row[1]}"' for row in info)
                db.execute(f"INSERT INTO jobs_m7 ({names}) SELECT {names} FROM jobs")
                # SQLite cannot DROP a referenced table with foreign_keys=ON.
                # A controlled connection rebuild is handled below instead.
                db.execute("DROP TABLE jobs_m7")
        self._migrate_nullable_stages()
        with self._transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS stage_decisions (
                    decision_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    stage INTEGER NOT NULL CHECK(stage IN (1,2)),
                    approval_revision INTEGER NOT NULL,
                    binding_hash TEXT NOT NULL,
                    manifest_hash TEXT,
                    actor_role TEXT NOT NULL,
                    actor_identity TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    occurred_utc TEXT NOT NULL
                )
            """)
            for action in ("UPDATE", "DELETE"):
                db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS stage_decisions_{action.lower()}_denied "
                    f"BEFORE {action} ON stage_decisions BEGIN "
                    "SELECT RAISE(ABORT,'immutable stage decision'); END"
                )
            db.execute(
                "UPDATE jobs SET stage1=NULL,package=NULL,stage2=NULL,"
                "stage2_manifest_hash=NULL WHERE route=?",
                (Route.LOCAL_ONLY.value,),
            )

    def _migrate_nullable_stages(self):
        """Transactional schema-only rebuild for the M4c NOT NULL columns.

        Foreign keys are disabled on this dedicated connection before BEGIN,
        checked before commit, then restored. History rows are not rewritten.
        """
        import sqlite3
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            info = list(db.execute("PRAGMA table_info(jobs)"))
            if any(row[3] for row in info if row[1] in {"stage1", "package", "stage2"}):
                objects = list(db.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name='jobs' "
                    "AND type IN ('index','trigger') AND sql IS NOT NULL"
                ))
                declarations = []
                for row in info:
                    name, kind, required, default, primary = row[1:6]
                    declaration = f'"{name}" {kind}'
                    if primary:
                        declaration += " PRIMARY KEY"
                    if required and name not in {"stage1", "package", "stage2"}:
                        declaration += " NOT NULL"
                    if default is not None:
                        declaration += f" DEFAULT {default}"
                    declarations.append(declaration)
                db.execute("CREATE TABLE jobs_m7 (" + ",".join(declarations) + ")")
                names = ",".join(f'"{row[1]}"' for row in info)
                db.execute(f"INSERT INTO jobs_m7 ({names}) SELECT {names} FROM jobs")
                db.execute("DROP TABLE jobs")
                db.execute("ALTER TABLE jobs_m7 RENAME TO jobs")
                for (sql,) in objects:
                    db.execute(sql)
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("register migration foreign-key failure")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
            db.close()

    def _stage_update(self, db, job_id, **changes):
        allowed = {"stage1", "package", "stage2", "stage2_manifest_hash", "timings_json"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("invalid internal stage update")
        local = self._row(db, job_id)["route"] == Route.LOCAL_ONLY.value
        for name, choices in (
            ("stage1", {"requested", "approved", "refused"}),
            ("package", {"building", "ready", "failed"}),
            ("stage2", {"pending", "approved", "refused"}),
        ):
            if name in changes and changes[name] not in choices and not (local and changes[name] is None):
                raise ValueError("invalid stage state")
        db.execute(
            "UPDATE jobs SET " + ",".join(f'"{key}"=?' for key in changes) + " WHERE job_id=?",
            [*changes.values(), job_id],
        )

    def _stage_event(self, db, row, event, now, role="gate", actor=None):
        self._audit(
            db, row, event, S(row["status"]), S(row["status"]), role, event, now, actor,
        )

    def _summary_binding(self, row):
        names = (
            "job_id", "approval_revision", "problem_summary_deid", "route",
            "marking_class", "destination_id", "model", "deployment_type",
            "source_document_count", "source_word_count", "requested_duration_days",
            "scope_json", "policy_version", "gate_version",
            "use_case_use_case_ref", "use_case_record_ref",
        )
        return hashlib.sha256(canonical_json({
            name: row[name] for name in names
        }).encode("ascii")).hexdigest()

    def _stage2_binding(self, row):
        return self._binding(dict(row, sensitive_path=None))

    def _summary_receipt(self, db, row, now):
        from .sqlite_store import stamp
        if row["received_utc"] is None:
            self._update(db, row["job_id"], {
                "received_utc": stamp(now),
                "due_utc": stamp(operator_due_utc(now, self.holidays)),
                "calendar_version": self.calendar_version,
            })
            row = self._row(db, row["job_id"])
        return row

    def _insert(self, db, job, now, event_time=None, sync_time=None):
        row = super()._insert(db, job, now, event_time, sync_time)
        if job.route == Route.CLOUD:
            self._stage_update(db, row["job_id"], stage1="requested", package="building", stage2="pending")
            row = self._summary_receipt(db, self._row(db, row["job_id"]), now)
            self._stage_event(db, row, "STAGE1_REQUESTED", now)
        else:
            self._stage_update(db, row["job_id"], stage1=None, package=None, stage2=None)
            row = self._row(db, row["job_id"])
        return row

    def _clear_effective_decision(self, db, job_id):
        super()._clear_effective_decision(db, job_id)
        local = self._row(db, job_id)["route"] == Route.LOCAL_ONLY.value
        self._stage_update(
            db, job_id, stage1=None if local else "requested",
            package=None if local else "building", stage2=None if local else "pending",
            stage2_manifest_hash=None, timings_json="{}",
        )

    def _edit(self, db, row, changes, now, expected_revision=None):
        revision = row["approval_revision"]
        row = super()._edit(db, row, changes, now, expected_revision)
        if row["approval_revision"] != revision and row["route"] == Route.CLOUD.value:
            row = self._summary_receipt(db, row, now)
            self._stage_event(db, row, "STAGE1_REQUESTED", now)
        return row

    def _move(
        self, db, row, target, role, code, now,
        actor_identity=None, event=None, sync_utc=None,
    ):
        row = super()._move(db, row, target, role, code, now, actor_identity, event, sync_utc)
        if target == S.REFUSED and row["route"] == Route.CLOUD.value:
            field = "stage1" if row["stage1"] != "approved" else "stage2"
            self._stage_update(db, row["job_id"], **{field: "refused"})
            row = self._row(db, row["job_id"])
        return row

    def withdraw_abandoned(self, *, now=None, active_ids=frozenset(), activity=None):
        from .sqlite_store import utc
        now = utc(now)
        cutoff = now - timedelta(minutes=30)
        activity = {} if activity is None else activity
        withdrawn = []
        with self._transaction() as db:
            rows = list(db.execute(
                "SELECT job_id FROM jobs WHERE status=? AND synthetic=1 ORDER BY job_id",
                (S.LOGGED.value,),
            ))
            for (identifier,) in rows:
                if identifier in active_ids:
                    continue
                row = self._row(db, identifier)
                times = [datetime.fromisoformat(row["created_utc"])]
                times.extend(datetime.fromisoformat(item[0]) for item in db.execute(
                    "SELECT occurred_utc FROM audit WHERE job_id=?", (identifier,),
                ))
                times.extend(datetime.fromisoformat(item[0]) for item in db.execute(
                    "SELECT occurred_utc FROM evidence WHERE job_id=?", (identifier,),
                ))
                if identifier in activity:
                    times.append(utc(activity[identifier]))
                if max(times) >= cutoff:
                    continue
                self._move(
                    db, row, S.WITHDRAWN, "gate", "ABANDONED_SYNTHETIC",
                    now, event="WITHDRAWN",
                )
                withdrawn.append(identifier)
        return tuple(withdrawn)

    def request_stage1(self, job_id, *, now=None):
        from .sqlite_store import utc, _base
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            job = self._intake(db, row)
            if job.route != Route.CLOUD or marking_policy().is_local_only(job.marking_class):
                raise PermissionError("summary approval is a permitted-cloud command")
            if now < job.created_utc:
                raise ValueError("summary receipt precedes intake")
            row = self._summary_receipt(db, row, now)
            if S(row["status"]) == S.LOGGED:
                row = self._move(
                    db, row, S.AWAITING_OPERATOR, "gate", "STAGE1_REQUESTED",
                    now, event="SUMMARY_SUBMITTED",
                )
            elif S(row["status"]) not in {S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}:
                raise ValueError("job is not awaiting preparation or approval")
            return _base(self._intake(db, row))

    def set_package(self, job, *, timings=None, now=None):
        from .sqlite_store import utc, _base, _encode
        if not isinstance(job, JobIntake):
            raise TypeError("typed safe package metadata required")
        now = utc(now)
        timings = {} if timings is None else timings
        if (
            type(timings) is not dict or set(timings) - {"tiers_ms", "runtime_modes"}
            or type(timings.get("tiers_ms", {})) is not dict
            or type(timings.get("runtime_modes", {})) is not dict
        ):
            raise ValueError("invalid content-free timings")
        if any(
            str(key) not in {"1", "2", "3"} or type(value) not in (int, float)
            or not 0 <= value < float("inf")
            for key, value in timings.get("tiers_ms", {}).items()
        ) or any(
            not isinstance(key, str) or value not in {"full", "mmap"}
            for key, value in timings.get("runtime_modes", {}).items()
        ):
            raise ValueError("invalid content-free timings")
        with self._transaction() as db:
            row = self._row(db, job.job_id)
            current = self._intake(db, row)
            if current.route != Route.CLOUD:
                raise PermissionError("local-only jobs have no cloud package")
            if S(row["status"]) not in {S.LOGGED, S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}:
                raise ValueError("package cannot replace active or closed work")
            if now < current.created_utc:
                raise ValueError("package precedes intake")
            if row["package"] == "ready":
                if any(getattr(current, name) != getattr(job, name) for name in PACKAGE_FIELDS):
                    raise PermissionError("ready package is immutable")
                return _base(current)
            if row["package"] != "building":
                raise PermissionError("failed package cannot become ready")
            candidate = replace(current, **{
                name: getattr(job, name) for name in PACKAGE_FIELDS
            })
            validate_intake(candidate)
            if not candidate.manifest_hash:
                raise ValueError("clean manifest hash required")
            previous_summary_binding = self._summary_binding(row)
            self._update(db, job.job_id, {
                name: _encode(name, getattr(candidate, name)) for name in PACKAGE_FIELDS
            } | {"major_reason_codes_json": canonical_json(major_codes(candidate))})
            self._stage_update(db, job.job_id, package="ready", timings_json=canonical_json(timings))
            row = self._row(db, job.job_id)
            first = self._latest_stage(db, row, 1)
            reassess = (
                previous_summary_binding != self._summary_binding(row)
                or is_major(candidate) and first and first["actor_role"] != "review-board"
            )
            if reassess:
                # Never carry approval of the provisional summary to the real
                # summary. Immutable historical decisions remain untouched.
                self._stage_update(db, job.job_id, stage1="requested")
                row = self._row(db, job.job_id)
                self._stage_event(db, row, "STAGE1_REASSESS", now)
            self._stage_event(db, row, "PACKAGE_READY", now)
            return _base(self._intake(db, row))

    def _complete_package_submission(self, db, row, now):
        if row["package"] == "failed":
            raise PermissionError("failed package cannot be submitted")
        self._stage_update(db, row["job_id"], package="ready")
        row = self._row(db, row["job_id"])
        self._stage_event(db, row, "PACKAGE_READY", now)
        return row

    def _latest_stage(self, db, row, stage):
        return db.execute(
            "SELECT * FROM stage_decisions WHERE job_id=? AND stage=? "
            "AND approval_revision=? ORDER BY decision_id DESC LIMIT 1",
            (row["job_id"], stage, row["approval_revision"]),
        ).fetchone()

    def _stages_valid(self, db, row):
        if (
            row["route"] != Route.CLOUD.value
            or row["stage1"] != "approved" or row["package"] != "ready"
            or row["stage2"] != "approved" or not row["manifest_hash"]
            or row["stage2_manifest_hash"] != row["manifest_hash"]
        ):
            return False
        first, second = self._latest_stage(db, row, 1), self._latest_stage(db, row, 2)
        if not (
            first and second and first["binding_hash"] == self._summary_binding(row)
            and second["manifest_hash"] == row["manifest_hash"]
            and second["binding_hash"] == self._stage2_binding(row)
        ):
            return False
        job = self._intake(db, row)
        if is_major(job) and any(item["actor_role"] != "review-board" for item in (first, second)):
            return False
        peer = self._evidence(db, row, "PEER")
        return all(
            item["actor_identity"] != job.requester_identity
            and not (peer and item["actor_identity"] == peer["actor_identity"])
            for item in (first, second)
        )

    def _approval_for(self, db, row, now):
        if not self._stages_valid(db, row):
            return None
        return super()._approval_for(db, row, now)

    def _finish_stages(self, db, row, now, **approval_kwargs):
        if not self._stages_valid(db, row):
            return None
        if S(row["status"]) == S.APPROVED:
            return self._approval_for(db, row, now)
        try:
            self._ready(db, row)
        except PermissionError:
            return None
        second = self._latest_stage(db, row, 2)
        return super()._approve(
            db, row, second["actor_role"], second["reason_code"], now,
            second["actor_identity"], pilot_enabled=second["actor_role"] == PILOT_ROLE,
            **approval_kwargs,
        )

    def _decide_stage(
        self, db, row, stage, role, actor, reason, now, *,
        manifest_hash=None, expected_revision=None, pilot_enabled=False, **approval_kwargs,
    ):
        from .sqlite_store import stamp
        if type(stage) is not int or stage not in {1, 2}:
            raise ValueError("stage must be one or two")
        if expected_revision is not None and expected_revision != row["approval_revision"]:
            raise PermissionError("stale stage decision")
        if role == PILOT_ROLE and not pilot_enabled:
            raise PermissionError("pilot role requires its controlled fixture")
        actor = self._human(db, row, role, identity(actor))
        code = reason_code(reason)
        job = self._intake(db, row)
        if job.route != Route.CLOUD or marking_policy().is_local_only(job.marking_class):
            raise PermissionError("no stage approval for this route or marking")
        if stage == 2 and (
            row["package"] != "ready" or manifest_hash != row["manifest_hash"] or not manifest_hash
        ):
            raise PermissionError("stage two requires the current ready package hash")
        received = row["received_utc"]
        if not isinstance(received, str):
            raise PermissionError("summary receipt evidence is missing")
        received_time = datetime.fromisoformat(received)
        if received_time.utcoffset() is None:
            raise PermissionError("summary receipt evidence is invalid")
        if now < received_time:
            raise ValueError("decision precedes summary receipt")
        if role == PILOT_ROLE and (not job.synthetic or is_major(job)):
            raise PermissionError("pilot scope refused")
        if S(row["status"]) == S.AWAITING_OPERATOR and is_major(job):
            self._move(db, row, S.ESCALATED_REVIEW, role, "MAJOR", now, actor, event="ESCALATED")
            return None
        field = f"stage{stage}"
        if row[field] == "refused":
            raise PermissionError("refused stage is closed")
        if row[field] != "approved":
            binding = self._summary_binding(row) if stage == 1 else self._stage2_binding(row)
            db.execute(
                "INSERT INTO stage_decisions "
                "(job_id,stage,approval_revision,binding_hash,manifest_hash,actor_role,"
                "actor_identity,reason_code,occurred_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (row["job_id"], stage, row["approval_revision"], binding,
                 manifest_hash if stage == 2 else None, role, actor, code, stamp(now)),
            )
            self._stage_update(db, row["job_id"], **{
                field: "approved",
                **({"stage2_manifest_hash": manifest_hash} if stage == 2 else {}),
            })
            row = self._row(db, row["job_id"])
            self._stage_event(db, row, f"STAGE{stage}_APPROVED", now, role, actor)
        return self._finish_stages(db, row, now, **approval_kwargs)

    def approve_stage(
        self, job_id, stage, approver_role, decision_reason, *,
        approver_identity, manifest_hash=None, expected_revision=None, now=None, **approval_kwargs,
    ):
        from .sqlite_store import utc
        with self._transaction() as db:
            return self._decide_stage(
                db, self._row(db, job_id), stage, approver_role, approver_identity,
                decision_reason, utc(now), manifest_hash=manifest_hash,
                expected_revision=expected_revision, **approval_kwargs,
            )

    def approve(self, job_id, approver_role, decision_reason, now=None, *, approver_identity, **approval_kwargs):
        from .sqlite_store import utc
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            result = self._decide_stage(
                db, row, 1, approver_role, approver_identity, decision_reason, now, **approval_kwargs,
            )
            row = self._row(db, job_id)
            if S(row["status"]) == S.ESCALATED_REVIEW and approver_role != "review-board":
                return None
            if row["package"] == "ready" and S(row["status"]) != S.APPROVED:
                result = self._decide_stage(
                    db, row, 2, approver_role, approver_identity, decision_reason,
                    now, manifest_hash=row["manifest_hash"], **approval_kwargs,
                )
            return result

    def _pilot_approve(self, job_id, *, enabled, actor, now):
        from .sqlite_store import utc
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            job = self._intake(db, row)
            if (
                enabled is not True or not job.synthetic or is_major(job)
                or job.route != Route.CLOUD or job.status != S.AWAITING_OPERATOR
            ):
                return None
            result = self._decide_stage(
                db, row, 1, PILOT_ROLE, actor, "PILOT_APPROVED", now, pilot_enabled=True,
            )
            row = self._row(db, job_id)
            if row["package"] == "ready" and S(row["status"]) != S.APPROVED:
                result = self._decide_stage(
                    db, row, 2, PILOT_ROLE, actor, "PILOT_APPROVED", now,
                    manifest_hash=row["manifest_hash"], pilot_enabled=True,
                )
            return result

    def finalize_stages(self, job_id, *, now=None):
        from .sqlite_store import utc
        with self._transaction() as db:
            return self._finish_stages(db, self._row(db, job_id), utc(now))

    def gate_refuse(self, job_id, *, now=None):
        from .sqlite_store import utc, _base
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            if S(row["status"]) in {S.REFUSED, S.WITHDRAWN, S.COMPLETED, S.EXPIRED}:
                return _base(self._intake(db, row))
            if row["package"] == "building" and row["route"] == Route.CLOUD.value:
                self._stage_update(db, job_id, package="failed")
                row = self._row(db, job_id)
                self._stage_event(db, row, "PACKAGE_FAILED", now)
            row = self._move(db, row, S.REFUSED, "gate", "GATE_REFUSED", now, event="REFUSED")
            return _base(self._intake(db, row))

    @contextmanager
    def release_guard(self, job_id, manifest_hash, *, now=None):
        from .sqlite_store import utc
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            if row["manifest_hash"] != manifest_hash or not self._approval_for(db, row, now):
                raise PermissionError("release no longer authorised")
            self._stage_event(db, row, "SEND_AUTHORISED", now)
            yield
