"""Append-only records of the Operator's per-point review of a held job (0.4.x, Operator review).

For every clean package the router records, value-free, where the gate placed a placeholder by itself
(``settled_by = 'gate'``) and where it held the job for a person (``settled_by = 'operator'``): a fixed hold reason,
a label, a kind (``placeholder``, ``text`` or ``document``) and character positions in the de-identified text. The
Operator then settles each held point once: ``not-personal`` (leave it), ``replaced`` (a placeholder the Operator
added to the job's local codebook) or ``stopped`` (the document is refused).

Never stored here: any original value, any de-identified text, any placeholder. Positions, categories, decisions and
the deciding identity only. Like the rest of the register's history these tables refuse UPDATE and DELETE.

Release rule: while a held point of the current package has no resolution, no stage is approved and an earlier
approval no longer completes (``_decide_stage`` and ``_stages_valid`` below). Nothing an Operator decides here
changes the gate itself: "not personal" verdicts are counted per hold reason (``operator_review_stats``), and turning
them into false-positive catalogue entries is a separate, deliberate step that this module never takes.
"""

from __future__ import annotations

import re

from ..routing import Route
from ..types import ReasonCode
from .client import JobStatus as S, Tier
from .models import REASONS, identity


REVIEW_REASONS = {
    "POINT_NOT_PERSONAL": "An Operator marked a highlighted point as not personal; the text stays as shown.",
    "POINT_REPLACED": (
        "An Operator replaced a highlighted point with a placeholder kept in the job's local codebook."
    ),
    "STOPPED_BY_OPERATOR": "An Operator stopped the document at a highlighted point; nothing is sent.",
    "PACKAGE_REOPENED": (
        "An Operator replacement changes the clean package; it is rebuilt when the requester sends again."
    ),
    "REVIEW_POINTS_UNRESOLVED": (
        "Every highlighted point must be settled (not personal, replaced, or stopped) before approval."
    ),
}
REASONS.update(REVIEW_REASONS)

GATE_REASON = "REPLACED_BY_GATE"
POINT_REASONS = frozenset(item.value for item in ReasonCode if item.value.startswith("ASK:")) | {GATE_REASON}
KINDS = ("placeholder", "text", "document")
SETTLERS = ("gate", "operator")
ACTIONS = ("not-personal", "replaced", "stopped")
CATEGORIES = ("person", "contact", "address", "identifier", "date", "statement", "quasi-identifier")
_POINT_ID = re.compile(r"pt-[0-9a-f]{12}")
_HASH = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[A-Z_]{1,32}")
_OPEN = {S.LOGGED, S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}
MAX_POINTS = 20_000
MAX_POSITION = 10_000_000


def _position(value, *, document):
    if document:
        if value is not None:
            raise ValueError("a document-level point has no position")
        return None
    if type(value) is not int or not 0 <= value <= MAX_POSITION:
        raise ValueError("invalid point position")
    return value


def validate_point(point):
    """A value-free point: fixed keys, a fixed reason, a label of capitals, positions only."""
    if type(point) is not dict or set(point) != {"id", "reason", "label", "kind", "start", "end", "settled_by"}:
        raise ValueError("invalid review point")
    if not isinstance(point["id"], str) or not _POINT_ID.fullmatch(point["id"]):
        raise ValueError("invalid point id")
    if point["reason"] not in POINT_REASONS or point["kind"] not in KINDS or point["settled_by"] not in SETTLERS:
        raise ValueError("invalid point reason, kind or settler")
    if point["label"] is not None and (not isinstance(point["label"], str) or not _LABEL.fullmatch(point["label"])):
        raise ValueError("invalid point label")
    document = point["kind"] == "document"
    start, end = _position(point["start"], document=document), _position(point["end"], document=document)
    if not document and start >= end:
        raise ValueError("empty point span")
    if (point["settled_by"] == "gate") != (point["reason"] == GATE_REASON):
        raise ValueError("only gate placeholders are settled by the gate")
    return point


class ReviewRecords:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self._transaction() as db:
            db.execute(f"""CREATE TABLE IF NOT EXISTS review_points (
                row_id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                manifest_hash TEXT NOT NULL,
                point_id TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                label TEXT,
                kind TEXT NOT NULL CHECK(kind IN ({",".join(repr(item) for item in KINDS)})),
                start_pos INTEGER,
                end_pos INTEGER,
                settled_by TEXT NOT NULL CHECK(settled_by IN ({",".join(repr(item) for item in SETTLERS)})),
                occurred_utc TEXT NOT NULL,
                UNIQUE(job_id, manifest_hash, point_id)
            )""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS review_resolutions (
                resolution_id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                point_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ({",".join(repr(item) for item in ACTIONS)})),
                category TEXT,
                start_pos INTEGER,
                end_pos INTEGER,
                actor_role TEXT NOT NULL,
                occurred_utc TEXT NOT NULL,
                UNIQUE(job_id, point_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_resolution_actors (
                resolution_id INTEGER PRIMARY KEY REFERENCES review_resolutions(resolution_id),
                actor_identity TEXT NOT NULL
            )""")
            for table in ("review_points", "review_resolutions", "review_resolution_actors"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_denied "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT,'immutable register history'); END"
                    )

    # -- the release rule -------------------------------------------------------------------------------------------

    def _unresolved_ids(self, db, row):
        if not row.get("manifest_hash"):
            return ()
        return tuple(item[0] for item in db.execute(
            "SELECT p.point_id FROM review_points p WHERE p.job_id=? AND p.manifest_hash=? "
            "AND p.settled_by='operator' AND NOT EXISTS (SELECT 1 FROM review_resolutions r "
            "WHERE r.job_id=p.job_id AND r.point_id=p.point_id) ORDER BY p.row_id",
            (row["job_id"], row["manifest_hash"]),
        ))

    def _stopped(self, db, job_id):
        return db.execute(
            "SELECT 1 FROM review_resolutions WHERE job_id=? AND action='stopped'", (job_id,),
        ).fetchone() is not None

    def _decide_stage(self, db, row, stage, role, actor, reason, now, **kwargs):
        if self._unresolved_ids(db, row) or self._stopped(db, row["job_id"]):
            from .models import PILOT_ROLE
            if role == PILOT_ROLE:
                return None  # the synthetic pilot fixture never settles a held point for a person
            raise PermissionError("REVIEW_POINTS_UNRESOLVED")
        return super()._decide_stage(db, row, stage, role, actor, reason, now, **kwargs)

    def _stages_valid(self, db, row):
        return (
            super()._stages_valid(db, row)
            and not self._unresolved_ids(db, row) and not self._stopped(db, row["job_id"])
        )

    # -- commands ---------------------------------------------------------------------------------------------------

    def record_review_points(self, job_id, manifest_hash, points, *, now=None):
        """Record the value-free points of a clean package once (a re-send of the same package adds nothing)."""
        from .sqlite_store import stamp, utc
        if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash):
            raise ValueError("invalid manifest hash")
        if not isinstance(points, (list, tuple)) or len(points) > MAX_POINTS:
            raise ValueError("invalid review points")
        points = [validate_point(dict(point)) for point in points]
        if len({point["id"] for point in points}) != len(points):
            raise ValueError("duplicate point id")
        with self._transaction() as db:
            row = self._row(db, job_id)
            if row["route"] != Route.CLOUD.value or row["manifest_hash"] != manifest_hash:
                raise PermissionError("points belong to the current clean cloud package")
            for point in points:
                db.execute(
                    "INSERT OR IGNORE INTO review_points (job_id,manifest_hash,point_id,reason_code,label,kind,"
                    "start_pos,end_pos,settled_by,occurred_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job_id, manifest_hash, point["id"], point["reason"], point["label"], point["kind"],
                     point["start"], point["end"], point["settled_by"], stamp(utc(now))),
                )

    def review_state(self, job_id):
        """The current package's points, each with its resolution (who settled it, how, when) or None."""
        with self._transaction() as db:
            row = self._row(db, job_id)
            resolutions = {
                item["point_id"]: {
                    "action": item["action"], "category": item["category"],
                    "start": item["start_pos"], "end": item["end_pos"], "role": item["actor_role"],
                    "identity": item["actor_identity"], "at": item["occurred_utc"],
                    "manifest_hash": item["manifest_hash"],
                } for item in db.execute(
                    "SELECT r.*, a.actor_identity FROM review_resolutions r LEFT JOIN review_resolution_actors a "
                    "ON a.resolution_id=r.resolution_id WHERE r.job_id=? ORDER BY r.resolution_id", (job_id,),
                )
            }
            points = [] if not row["manifest_hash"] else [{
                "id": item["point_id"], "reason": item["reason_code"], "label": item["label"],
                "kind": item["kind"], "start": item["start_pos"], "end": item["end_pos"],
                "settled_by": item["settled_by"],
                "resolution": resolutions.get(item["point_id"]) if item["settled_by"] == "operator" else None,
            } for item in db.execute(
                "SELECT * FROM review_points WHERE job_id=? AND manifest_hash=? ORDER BY row_id",
                (job_id, row["manifest_hash"]),
            )]
            return {
                "manifest_hash": row["manifest_hash"], "points": points,
                "unresolved": list(self._unresolved_ids(db, row)), "stopped": self._stopped(db, job_id),
            }

    def unresolved_points(self, job_id):
        with self._transaction() as db:
            return self._unresolved_ids(db, self._row(db, job_id))

    def _held_point(self, db, row, point_id, manifest_hash):
        if not isinstance(point_id, str) or not _POINT_ID.fullmatch(point_id):
            raise KeyError("unknown point")
        if manifest_hash is not None and row["manifest_hash"] != manifest_hash:
            raise PermissionError("the clean package changed; reload the review")
        point = db.execute(
            "SELECT * FROM review_points WHERE job_id=? AND manifest_hash=? AND point_id=? AND settled_by='operator'",
            (row["job_id"], row["manifest_hash"], point_id),
        ).fetchone()
        if point is None:
            raise KeyError("unknown point")
        return point

    def _resolve(self, db, row, point_id, action, actor, now, *, category=None, start=None, end=None):
        from .sqlite_store import stamp
        if db.execute(
            "SELECT 1 FROM review_resolutions WHERE job_id=? AND point_id=?", (row["job_id"], point_id),
        ).fetchone() is not None:
            raise PermissionError("point already settled")
        cursor = db.execute(
            "INSERT INTO review_resolutions (job_id,point_id,manifest_hash,action,category,start_pos,end_pos,"
            "actor_role,occurred_utc) VALUES (?,?,?,?,?,?,?,?,?)",
            (row["job_id"], point_id, row["manifest_hash"], action, category, start, end, "operator", stamp(now)),
        )
        db.execute("INSERT INTO review_resolution_actors VALUES (?,?)", (cursor.lastrowid, actor))

    def _actor(self, db, row, actor_identity):
        actor = identity(actor_identity)
        if actor == self._intake(db, row).requester_identity:
            raise PermissionError("self-approval/decision is refused")
        if S(row["status"]) not in _OPEN or row["route"] != Route.CLOUD.value:
            raise PermissionError("only an open cloud job is reviewed")
        return actor

    def resolve_point(
        self, job_id, point_id, action, *, actor_identity, manifest_hash=None, category=None,
        start=None, end=None, reopen=False, now=None,
    ):
        """Settle one held point: ``not-personal`` or ``replaced``. A replacement that changes the clean package
        (``reopen``) also sends the package back to "building", so the next send rebuilds and re-binds it."""
        from .sqlite_store import utc
        now = utc(now)
        if action not in {"not-personal", "replaced"}:
            raise ValueError("invalid point action")
        if action == "replaced":
            if category not in CATEGORIES:
                raise ValueError("invalid category")
            for value in (start, end):
                if type(value) is not int or not 0 <= value <= MAX_POSITION:
                    raise ValueError("invalid replacement span")
            if start >= end:
                raise ValueError("invalid replacement span")
        elif category is not None or start is not None or end is not None or reopen:
            raise ValueError("not personal takes no category or span")
        with self._transaction() as db:
            row = self._row(db, job_id)
            actor = self._actor(db, row, actor_identity)
            self._held_point(db, row, point_id, manifest_hash)
            if self._approval_for(db, row, now) is not None:
                raise PermissionError("an approved package is not reviewed again")
            self._resolve(db, row, point_id, action, actor, now, category=category, start=start, end=end)
            # The record of the point (how, by whom, when) is the review_resolutions row itself; the job's status does
            # not change, so no audit row is added (a package reopened by a replacement adds one below).
            if reopen and row["package"] == "ready":
                self._stage_update(db, job_id, package="building", stage2="pending", stage2_manifest_hash=None)
                row = self._row(db, job_id)
                self._audit(
                    db, row, "PACKAGE_REOPENED", S(row["status"]), S(row["status"]), "operator",
                    "PACKAGE_REOPENED", now, actor,
                )
            return {"unresolved": list(self._unresolved_ids(db, row)), "package": row["package"]}

    def stop_document(self, job_id, point_id, *, actor_identity, now=None):
        """The Operator stops the document at a held point: the point is recorded as ``stopped`` (if it was not
        settled yet) and the job is refused in the same transaction. Allowed in any open state, review included:
        stopping never sends anything."""
        from .sqlite_store import utc
        now = utc(now)
        with self._transaction() as db:
            row = self._row(db, job_id)
            actor = self._actor(db, row, actor_identity)
            self._held_point(db, row, point_id, None)
            if db.execute(
                "SELECT 1 FROM review_resolutions WHERE job_id=? AND point_id=?", (job_id, point_id),
            ).fetchone() is None:
                self._resolve(db, row, point_id, "stopped", actor, now)
            escalated = S(row["status"]) == S.ESCALATED_REVIEW
            self._update(db, job_id, {
                "approver_role": "operator", "decision_reason": REASONS["STOPPED_BY_OPERATOR"],
                "tier": (Tier.AI_CAB if escalated else Tier.OPERATOR).value,
            })
            row = self._move(
                db, self._row(db, job_id), S.REFUSED, "operator", "STOPPED_BY_OPERATOR", now, actor,
                event="REFUSED",
            )
            return S(row["status"])

    def operator_review_stats(self):
        """Value-free counts per hold reason: points held, and how Operators settled them.

        A high "not personal" share marks a reason that wastes checks. Nothing here changes the gate: promoting a
        reason's verdicts into the false-positive catalogue is a deliberate, separate step (``themis fpc``).
        """
        with self._transaction() as db:
            held = {}
            for reason, count in db.execute(
                "SELECT reason_code, COUNT(*) FROM (SELECT DISTINCT job_id, point_id, reason_code FROM review_points "
                "WHERE settled_by='operator') GROUP BY reason_code"
            ):
                held[reason] = {"held": count, "not-personal": 0, "replaced": 0, "stopped": 0, "open": count}
            for reason, action, count in db.execute(
                "SELECT p.reason_code, r.action, COUNT(*) FROM review_resolutions r JOIN "
                "(SELECT DISTINCT job_id, point_id, reason_code FROM review_points WHERE settled_by='operator') p "
                "ON p.job_id=r.job_id AND p.point_id=r.point_id GROUP BY p.reason_code, r.action"
            ):
                entry = held.setdefault(reason, {"held": 0, "not-personal": 0, "replaced": 0, "stopped": 0, "open": 0})
                entry[action] += count
                entry["open"] -= count
            gate = db.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT job_id, point_id FROM review_points WHERE settled_by='gate')"
            ).fetchone()[0]
            return {
                "by_reason": dict(sorted(held.items())), "gate_settled_placeholders": gate,
                "promotion": "manual only: no verdict changes the gate by itself",
            }
