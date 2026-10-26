"""Append-only gate records in the job register (v0.3): decision records and cloud trip-wire results.

Written only when a v0.3 catalogue feature is enabled; with all of them off these tables stay empty.

- ``gate_decisions``: per job and clean package, the model's decision (detection plus policy) and the catalogue
  override, kept apart, with the known-miss register version and age used by the final sweep.
- ``tripwire_records`` / ``tripwire_reviews``: what the cloud trip-wire said (spans and placeholder ids only, never
  text) and the Operator's verdict on each flag, so flag precision can be tracked.

Like the rest of the register's history these tables refuse UPDATE and DELETE.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import re

from ..routing import Route
from .client import JobStatus as S
from .models import REASONS, canonical_json, identity


GATE_RECORD_REASONS = {
    "TRIPWIRE_SENT": "The approved clean package was sent to the cloud trip-wire.",
    "TRIPWIRE_CLEAR": "The cloud trip-wire reported nothing (no evidence either way).",
    "TRIPWIRE_FLAGGED": "The cloud trip-wire flagged possible residual personal information; the job stopped.",
    "TRIPWIRE_MALFORMED": "The cloud trip-wire reply was malformed; the job stopped.",
    "TRIPWIRE_CONFIRMED": "An Operator confirmed the trip-wire flag; the job is refused.",
    "TRIPWIRE_REJECTED": "An Operator rejected the trip-wire flag as a false flag.",
}
REASONS.update(GATE_RECORD_REASONS)
OUTCOMES = ("clear", "flagged", "malformed", "failed")
VERDICTS = ("confirmed", "rejected")
OPERATOR_ROLES = {"operator", "security-admin"}
_HASH = re.compile(r"[0-9a-f]{64}")


class GateRecords:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS gate_decisions (
                decision_id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                manifest_hash TEXT NOT NULL,
                record_json TEXT NOT NULL,
                known_miss_version INTEGER,
                occurred_utc TEXT NOT NULL
            )""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS tripwire_records (
                record_id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                manifest_hash TEXT NOT NULL,
                instruction_version TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ({",".join(repr(item) for item in OUTCOMES)})),
                flags_json TEXT NOT NULL,
                occurred_utc TEXT NOT NULL
            )""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS tripwire_reviews (
                review_id INTEGER PRIMARY KEY,
                record_id INTEGER NOT NULL UNIQUE REFERENCES tripwire_records(record_id),
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                verdict TEXT NOT NULL CHECK(verdict IN ({",".join(repr(item) for item in VERDICTS)})),
                reviewer_role TEXT NOT NULL,
                occurred_utc TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS tripwire_review_actors (
                review_id INTEGER PRIMARY KEY REFERENCES tripwire_reviews(review_id),
                actor_identity TEXT NOT NULL
            )""")
            for table in ("gate_decisions", "tripwire_records", "tripwire_reviews", "tripwire_review_actors"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_denied "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT,'immutable register history'); END"
                    )

    # -- decision records ---------------------------------------------------------------------------------------

    def record_gate_decision(self, job_id, manifest_hash, record, *, now=None):
        """Append the decision record once per job and clean package (idempotent for a re-send)."""
        from .sqlite_store import stamp, utc
        if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash) or type(record) is not dict:
            raise ValueError("invalid decision record")
        text = canonical_json(record)
        version = (record.get("known_miss") or {}).get("version")
        with self._transaction() as db:
            self._row(db, job_id)
            if db.execute(
                "SELECT 1 FROM gate_decisions WHERE job_id=? AND manifest_hash=?", (job_id, manifest_hash),
            ).fetchone() is None:
                db.execute(
                    "INSERT INTO gate_decisions (job_id,manifest_hash,record_json,known_miss_version,occurred_utc) "
                    "VALUES (?,?,?,?,?)",
                    (job_id, manifest_hash, text, version if type(version) is int else None, stamp(utc(now))),
                )

    def gate_decisions(self, job_id):
        with self._transaction() as db:
            self._row(db, job_id)
            return tuple(
                dict(row, record=json.loads(row["record_json"])) for row in db.execute(
                    "SELECT * FROM gate_decisions WHERE job_id=? ORDER BY decision_id", (job_id,),
                )
            )

    # -- cloud trip-wire ----------------------------------------------------------------------------------------

    @contextmanager
    def tripwire_guard(self, job_id, manifest_hash, *, now=None):
        """Like ``release_guard``: the approval must still hold for exactly this package."""
        from .sqlite_store import utc
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            if (
                row["route"] != Route.CLOUD.value or row["manifest_hash"] != manifest_hash
                or not self._approval_for(db, row, now)
            ):
                raise PermissionError("trip-wire send not authorised")
            self._stage_event(db, row, "TRIPWIRE_SENT", now)
            yield

    def record_tripwire(self, job_id, manifest_hash, outcome, flags, *, instruction_version, now=None):
        """Append a trip-wire result. A flag stops the job (Held-Offline) until an Operator reviews it."""
        from .sqlite_store import stamp, utc
        now = utc(now)
        if outcome not in OUTCOMES or not isinstance(flags, (list, tuple)):
            raise ValueError("invalid trip-wire result")
        if outcome != "flagged" and flags:
            raise ValueError("only a flag carries spans")
        if not isinstance(instruction_version, str) or not re.fullmatch(r"[a-z0-9.-]{1,32}", instruction_version):
            raise ValueError("invalid instruction version")
        flags_json = canonical_json([dict(item) for item in flags])
        with self._transaction() as db:
            row = self._row(db, job_id)
            cursor = db.execute(
                "INSERT INTO tripwire_records (job_id,manifest_hash,instruction_version,outcome,flags_json,"
                "occurred_utc) VALUES (?,?,?,?,?,?)",
                (job_id, manifest_hash, instruction_version, outcome, flags_json, stamp(now)),
            )
            if outcome == "flagged":
                self._move(
                    db, row, S.HELD_OFFLINE, "gate", "TRIPWIRE_FLAGGED", now, event="TRIPWIRE_FLAGGED",
                )
            elif outcome == "clear":
                self._stage_event(db, row, "TRIPWIRE_CLEAR", now)
            else:
                self._stage_event(db, row, "TRIPWIRE_MALFORMED", now)
            return cursor.lastrowid

    def tripwire_state(self, job_id, manifest_hash=None):
        """The latest trip-wire record for the job (and package, if given), with its review, or None."""
        with self._transaction() as db:
            self._row(db, job_id)
            query = "SELECT * FROM tripwire_records WHERE job_id=?"
            arguments = [job_id]
            if manifest_hash is not None:
                query += " AND manifest_hash=?"
                arguments.append(manifest_hash)
            row = db.execute(query + " ORDER BY record_id DESC LIMIT 1", arguments).fetchone()
            if row is None:
                return None
            review = db.execute(
                "SELECT verdict, reviewer_role, occurred_utc FROM tripwire_reviews WHERE record_id=?",
                (row["record_id"],),
            ).fetchone()
            return dict(
                row, flags=json.loads(row["flags_json"]),
                verdict=review["verdict"] if review else None,
            )

    def review_tripwire(self, job_id, verdict, *, reviewer_identity, reviewer_role="operator", now=None):
        """An Operator's verdict on the job's pending flag.

        ``confirmed``: a real residual; the job is refused. ``rejected``: a false flag; the job returns to Approved
        if its approval still holds for the same package (it is never re-checked by the trip-wire), otherwise to
        Logged for a fresh assessment. Either verdict counts towards the trip-wire's flag precision.
        """
        from .sqlite_store import stamp, utc
        now = utc(now)
        if verdict not in VERDICTS or reviewer_role not in OPERATOR_ROLES:
            raise PermissionError("an Operator verdict of confirmed or rejected is required")
        reviewer = identity(reviewer_identity)
        with self._transaction() as db:
            row = self._row(db, job_id)
            if reviewer == self._intake(db, row).requester_identity:
                raise PermissionError("the requester cannot review their own flag")
            record = db.execute(
                "SELECT r.* FROM tripwire_records r LEFT JOIN tripwire_reviews v USING(record_id) "
                "WHERE r.job_id=? AND r.outcome='flagged' AND v.review_id IS NULL "
                "ORDER BY r.record_id DESC LIMIT 1", (job_id,),
            ).fetchone()
            if record is None or S(row["status"]) != S.HELD_OFFLINE:
                raise ValueError("no pending trip-wire flag for this job")
            cursor = db.execute(
                "INSERT INTO tripwire_reviews (record_id,job_id,verdict,reviewer_role,occurred_utc) "
                "VALUES (?,?,?,?,?)", (record["record_id"], job_id, verdict, reviewer_role, stamp(now)),
            )
            db.execute("INSERT INTO tripwire_review_actors VALUES (?,?)", (cursor.lastrowid, reviewer))
            if verdict == "confirmed":
                row = self._move(
                    db, row, S.REFUSED, reviewer_role, "TRIPWIRE_CONFIRMED", now, reviewer, event="REFUSED",
                )
            elif (
                row["manifest_hash"] == record["manifest_hash"]
                and self._approval_for(db, dict(row, status=S.APPROVED.value), now) is not None
            ):
                row = self._move(
                    db, row, S.APPROVED, reviewer_role, "TRIPWIRE_REJECTED", now, reviewer, event="RECONCILED",
                )
            else:
                self._clear_effective_decision(db, job_id)
                self._update(db, job_id, {
                    "approval_revision": row["approval_revision"] + 1, "peer_checked_by_role": None,
                    "received_utc": None, "due_utc": None, "calendar_version": None,
                })
                row = self._move(
                    db, self._row(db, job_id), S.LOGGED, reviewer_role, "TRIPWIRE_REJECTED", now, reviewer,
                    event="RECONCILED",
                )
            return S(row["status"])

    def tripwire_stats(self):
        """Counts by outcome and verdict, and flag precision (confirmed / reviewed flags), value-free."""
        with self._transaction() as db:
            outcomes = dict(db.execute(
                "SELECT outcome, COUNT(*) FROM tripwire_records GROUP BY outcome"
            ).fetchall())
            verdicts = dict(db.execute(
                "SELECT verdict, COUNT(*) FROM tripwire_reviews GROUP BY verdict"
            ).fetchall())
        confirmed, rejected = verdicts.get("confirmed", 0), verdicts.get("rejected", 0)
        flagged = outcomes.get("flagged", 0)
        return {
            "runs": sum(outcomes.values()),
            "outcomes": {name: outcomes.get(name, 0) for name in OUTCOMES},
            "flags_confirmed": confirmed, "flags_rejected": rejected,
            "flags_pending": flagged - confirmed - rejected,
            "flag_precision": (confirmed / (confirmed + rejected)) if confirmed + rejected else None,
        }

    def tripwire_records(self, job_id=None):
        with self._transaction() as db:
            query = (
                "SELECT r.record_id, r.job_id, r.outcome, r.flags_json, r.occurred_utc, v.verdict "
                "FROM tripwire_records r LEFT JOIN tripwire_reviews v USING(record_id)"
            )
            arguments = []
            if job_id is not None:
                query += " WHERE r.job_id=?"
                arguments.append(job_id)
            return tuple(
                dict(row, flags=json.loads(row["flags_json"]))
                for row in db.execute(query + " ORDER BY r.record_id", arguments)
            )
