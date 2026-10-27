"""The reviewed false-positive catalogue (v0.3; off unless ``[false_positives] enabled = true``).

The mirror image of the known-miss register. Look-alikes the gate keeps flagging (a product code, a switchboard, a
committee name) can be proposed from runs and **accepted by a person** other than the proposer. Nothing enters
unreviewed, and text under test can never switch the gate off: there are no in-document ignore markers of any kind.

An entry is scoped to a profile (the deployment's organisation scope), one label, and a phrase and/or a context
pattern; it expires. Two strengths:

- ``no_hold`` (default): a matching finding no longer holds the document for a human. It is **still substituted**,
  so friction falls while the cloud still sees a placeholder.
- ``allow``: a matching finding is not substituted at all. Stronger and rarer: it needs a phrase, it is refused for
  every label in the allow-list's ``NEVER_TRIM`` set (secrets, sensitive statements, dates of birth,
  quasi-identifiers) and it never applies to a checksum-valid identifier.

Neither strength can touch a ``SECRET`` finding (a secret blocks; it is not a hold), a security marking or the
injection signal: those are not findings, and the decision is recomputed from the same evidence with only the
matched findings set aside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Callable

from ..detectors.allowlist import NEVER_TRIM
from ..detectors.rules import validate_identifier
from ..normalise import detection_view
from .known_miss import check_pattern_safety
from ..types import Label
from .store import (
    CatalogueError, CatalogueUnavailable, actor, deny_changes, parse_stamp, stamp, transaction, utc,
    utc_now,
)


STRENGTHS = ("no_hold", "allow")
STATUSES = ("proposed", "accepted", "expired")
TRANSITIONS = {
    "proposed": frozenset({"accepted", "expired"}),
    "accepted": frozenset({"expired"}),
    "expired": frozenset(),
}
ALLOW_FORBIDDEN = NEVER_TRIM | {Label.SECRET}
CONTEXT_WINDOW = 80
MAX_PHRASE_CHARS = 200
MAX_CONTEXT_CHARS = 256
MAX_EXPIRY_DAYS = 365
DEFAULT_EXPIRY_DAYS = 90
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_JOB_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")


def phrase_key(text: str) -> str:
    """Detection view, case-folded, whitespace collapsed: how phrases and finding values are compared."""
    return " ".join(detection_view(text).text.casefold().split())


def _label(value) -> Label:
    try:
        label = Label(value)
    except ValueError:
        raise CatalogueError("unknown label (markings and the injection signal are not catalogue labels)") from None
    if label == Label.SECRET:
        raise CatalogueError("a SECRET finding blocks release; it can never be catalogued")
    return label


def _context(pattern: str | None) -> re.Pattern | None:
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_CONTEXT_CHARS:
        raise CatalogueError(f"a context pattern has 1 to {MAX_CONTEXT_CHARS} characters")
    try:
        compiled = re.compile(pattern)
    except re.error:
        raise CatalogueError("the context pattern does not compile") from None
    if compiled.search("") is not None:
        raise CatalogueError("the context pattern matches empty text, so it would match everywhere")
    # The same backtracking limits as known-miss patterns (it runs on untrusted text around each finding).
    check_pattern_safety(pattern)
    return compiled


def validate(label, strength: str, phrase: str | None, context: str | None) -> tuple[Label, str | None]:
    label = _label(label)
    if strength not in STRENGTHS:
        raise CatalogueError("strength must be no_hold or allow")
    key = None
    if phrase is not None:
        if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > MAX_PHRASE_CHARS:
            raise CatalogueError(f"a phrase has 1 to {MAX_PHRASE_CHARS} characters")
        key = phrase_key(phrase)
        if not key:
            raise CatalogueError("the phrase is empty once invisible characters are removed")
    _context(context)
    if key is None and context is None:
        raise CatalogueError("an entry needs a phrase, a context pattern, or both")
    if strength == "allow":
        if label in ALLOW_FORBIDDEN:
            raise CatalogueError(
                "allow is refused for secrets, sensitive statements, dates of birth and quasi-identifiers; "
                "use no_hold (still substituted)"
            )
        if key is None:
            raise CatalogueError("allow needs a phrase")
        has_letters = any(character.isalpha() for character in key)
        has_digits = any(character.isdigit() for character in key)
        if len(key.split()) < 2 and not (has_letters and has_digits):
            raise CatalogueError(
                "allow needs a phrase of two or more words, or a code with letters and digits: a bare word "
                "allow-listed everywhere is how a suburb becomes a home address"
            )
    return label, key


@dataclass(frozen=True, slots=True)
class ActiveEntry:
    entry_id: str
    label: Label
    strength: str
    phrase: str | None = field(repr=False)
    context: re.Pattern | None = field(repr=False)
    expires_utc: datetime


class FalsePositiveCatalogue:
    """The local false-positive store (SQLite; entries and history append-only)."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] = utc_now, create: bool = False):
        self.path = Path(path)
        self._clock = clock
        if create:
            self._initialise()

    def _now(self) -> datetime:
        return utc(self._clock())

    def _initialise(self) -> None:
        with transaction(self.path, create=True) as db:
            db.execute(f"""CREATE TABLE IF NOT EXISTS fpc_entries (
                entry_id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                label TEXT NOT NULL,
                phrase TEXT,
                context TEXT,
                strength TEXT NOT NULL CHECK(strength IN ({",".join(repr(s) for s in STRENGTHS)})),
                created_utc TEXT NOT NULL,
                expires_utc TEXT NOT NULL,
                proposed_by TEXT NOT NULL,
                source_job_id TEXT,
                CHECK(phrase IS NOT NULL OR context IS NOT NULL)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS fpc_events (
                event_id INTEGER PRIMARY KEY,
                entry_id TEXT NOT NULL REFERENCES fpc_entries(entry_id),
                event TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                actor TEXT NOT NULL,
                occurred_utc TEXT NOT NULL
            )""")
            for table in ("fpc_entries", "fpc_events"):
                deny_changes(db, table)

    def _open(self):
        return transaction(self.path, create=False)

    @staticmethod
    def _status(db, entry_id) -> str:
        row = db.execute(
            "SELECT to_status FROM fpc_events WHERE entry_id=? ORDER BY event_id DESC LIMIT 1", (entry_id,),
        ).fetchone()
        if row is None:
            raise KeyError("entry not found")
        return row[0]

    def propose(
        self, *, profile: str, label, strength: str = "no_hold", phrase: str | None = None,
        context: str | None = None, expires_days: int = DEFAULT_EXPIRY_DAYS, proposed_by: str,
        source_job_id: str | None = None,
    ) -> str:
        if not isinstance(profile, str) or not _SCOPE.fullmatch(profile):
            raise CatalogueError("invalid profile scope")
        label, key = validate(label, strength, phrase, context)
        if type(expires_days) is not int or not 1 <= expires_days <= MAX_EXPIRY_DAYS:
            raise CatalogueError(f"an entry expires within 1 to {MAX_EXPIRY_DAYS} days")
        if source_job_id is not None and (not isinstance(source_job_id, str) or not _JOB_ID.fullmatch(source_job_id)):
            raise CatalogueError("invalid job id")
        now = self._now()
        with self._open() as db:
            number = db.execute("SELECT COUNT(*) FROM fpc_entries").fetchone()[0] + 1
            entry_id = f"FPC-{number:04d}"
            db.execute(
                "INSERT INTO fpc_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                (entry_id, profile, label.value, key, context, strength, stamp(now),
                 stamp(now + timedelta(days=expires_days)), actor(proposed_by), source_job_id),
            )
            db.execute(
                "INSERT INTO fpc_events (entry_id,event,from_status,to_status,actor,occurred_utc) "
                "VALUES (?,?,?,?,?,?)", (entry_id, "PROPOSED", None, "proposed", proposed_by, stamp(now)),
            )
            return entry_id

    def _move(self, entry_id: str, target: str, who: str) -> None:
        if not isinstance(entry_id, str) or not re.fullmatch(r"FPC-[0-9]{4,}", entry_id):
            raise KeyError("entry not found")
        now = self._now()
        with self._open() as db:
            row = db.execute("SELECT * FROM fpc_entries WHERE entry_id=?", (entry_id,)).fetchone()
            if row is None:
                raise KeyError("entry not found")
            before = self._status(db, entry_id)
            if target not in TRANSITIONS[before]:
                raise CatalogueError(f"an entry that is {before} cannot become {target}")
            if target == "accepted":
                if actor(who) == row["proposed_by"]:
                    raise CatalogueError("an entry is accepted by a person other than its proposer")
                if now >= parse_stamp(row["expires_utc"]):
                    raise CatalogueError("the proposal has already expired")
                # Re-check the rules at acceptance: they may have tightened since the proposal.
                validate(row["label"], row["strength"], row["phrase"], row["context"])
            db.execute(
                "INSERT INTO fpc_events (entry_id,event,from_status,to_status,actor,occurred_utc) "
                "VALUES (?,?,?,?,?,?)", (entry_id, target.upper(), before, target, actor(who), stamp(now)),
            )

    def accept(self, entry_id: str, *, actor_identity: str) -> None:
        self._move(entry_id, "accepted", actor_identity)

    def expire(self, entry_id: str, *, actor_identity: str) -> None:
        self._move(entry_id, "expired", actor_identity)

    def entries(self) -> tuple[dict, ...]:
        now = self._now()
        with self._open() as db:
            result = []
            for row in db.execute("SELECT * FROM fpc_entries ORDER BY entry_id"):
                status = self._status(db, row["entry_id"])
                if status != "expired" and now >= parse_stamp(row["expires_utc"]):
                    status = "expired"
                result.append({
                    "entry_id": row["entry_id"], "profile": row["profile"], "label": row["label"],
                    "strength": row["strength"], "status": status, "phrase": row["phrase"],
                    "context": row["context"], "expires_utc": row["expires_utc"],
                    "source_job_id": row["source_job_id"],
                })
            return tuple(result)

    def active(self, profile: str, now: datetime | None = None) -> tuple[ActiveEntry, ...]:
        now = self._now() if now is None else utc(now)
        try:
            with self._open() as db:
                result = []
                for row in db.execute(
                    "SELECT * FROM fpc_entries WHERE profile=? ORDER BY entry_id", (profile,),
                ).fetchall():
                    if self._status(db, row["entry_id"]) != "accepted":
                        continue
                    expires = parse_stamp(row["expires_utc"])
                    if now >= expires:
                        continue
                    label, key = validate(row["label"], row["strength"], row["phrase"], row["context"])
                    result.append(ActiveEntry(
                        row["entry_id"], label, row["strength"], key, _context(row["context"]), expires,
                    ))
                return tuple(result)
        except CatalogueUnavailable:
            raise
        except Exception:
            raise CatalogueUnavailable("false-positive catalogue unreadable") from None


@dataclass(frozen=True, slots=True)
class Override:
    """Value-free record of one catalogue entry setting findings aside."""

    entry_id: str
    strength: str
    label: str
    count: int

    def as_record(self) -> dict:
        return {"entry_id": self.entry_id, "strength": self.strength, "label": self.label, "count": self.count}


def _allow_forbidden(item, text: str) -> bool:
    """``allow`` never applies to a checksum-valid identifier, whatever label it now carries.

    The finding's own checksum result counts, and the value is validated again: a number the checksum-context fix
    relabelled as a generic identifier is still a checksum-valid TFN, Medicare number or ABN.
    """
    if item.checksum_valid is True or item.labels & ALLOW_FORBIDDEN:
        return True
    value = text[item.span.start:item.span.end]
    return any(validate_identifier(label, value).checksum_valid for label in (Label.TFN, Label.MEDICARE, Label.ABN))


def match(entries, text: str, findings) -> tuple[frozenset[int], frozenset[int], tuple[Override, ...]]:
    """Indexes of findings set aside for holds (any strength) and removed from substitution (``allow``).

    A finding matches an entry only when its labels are exactly the entry's one label, the phrase (if any) equals
    the finding's value after normalisation, and the context pattern (if any) occurs within ``CONTEXT_WINDOW``
    characters around it on the detection view. ``allow`` never applies to a checksum-valid identifier or a
    ``NEVER_TRIM`` label, whatever the entry says.
    """
    no_hold, allowed, counts = set(), set(), {}
    direct: list[tuple[int, int, Label, ActiveEntry, str]] = []
    for index, item in enumerate(findings):
        if len(item.labels) != 1:
            continue
        value = phrase_key(text[item.span.start:item.span.end])
        window = None
        best = None
        for entry in entries:
            if item.labels != frozenset({entry.label}):
                continue
            if entry.phrase is not None and value != entry.phrase:
                continue
            if entry.context is not None:
                if window is None:
                    window = detection_view(
                        text[max(0, item.span.start - CONTEXT_WINDOW):item.span.end + CONTEXT_WINDOW]
                    ).text
                if entry.context.search(window) is None:
                    continue
            strength = entry.strength
            if strength == "allow" and _allow_forbidden(item, text):
                strength = "no_hold"
            if best is None or (strength == "allow" and best[1] != "allow"):
                best = (entry, strength)
        if best is None:
            continue
        entry, strength = best
        direct.append((item.span.start, item.span.end, entry.label, entry, strength))
        no_hold.add(index)
        if strength == "allow":
            allowed.add(index)
        key = (entry.entry_id, strength, entry.label.value)
        counts[key] = counts.get(key, 0) + 1
    # Parts of a matched occurrence (propagation splits "Harbour Committee" into "Harbour" and "Committee") are
    # covered by the same entry, but only inside that occurrence's span: the same word elsewhere still counts.
    for index, item in enumerate(findings):
        if index in no_hold or len(item.labels) != 1:
            continue
        for start, end, label, entry, strength in direct:
            if item.labels == frozenset({label}) and start <= item.span.start and item.span.end <= end:
                if strength == "allow" and _allow_forbidden(item, text):
                    strength = "no_hold"
                no_hold.add(index)
                if strength == "allow":
                    allowed.add(index)
                key = (entry.entry_id, strength, label.value)
                counts[key] = counts.get(key, 0) + 1
                break
    overrides = tuple(Override(*key, count) for key, count in sorted(counts.items()))
    return frozenset(no_hold), frozenset(allowed), overrides
