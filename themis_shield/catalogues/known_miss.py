"""The known-miss register and the final sweep (v0.3; off unless ``[known_miss] enabled = true``).

Anything the gate missed, once a person confirms it, can be added here. Every later cloud-bound job is checked
against the entries that are ``in_sweep``, on the outbound de-identified text exactly as it will be sent, after
substitution. A hit sends the job to ASK with the finding shown; it is never silently redacted. "The same miss
cannot pass twice", not "nothing is ever missed again".

**The register never stores the values it protects.** An entry is one of:

- ``rule``: a pattern (a format, such as a reference-number shape), matched on the shared detection view;
- ``context``: a phrase shape that carried a quasi-identifier, matched the same way;
- ``fingerprint``: an HMAC-SHA-256 of one specific *high-entropy* string, under a key wrapped by the vault's key
  protector (DPAPI on Windows, a passphrase with scrypt elsewhere). Matching is exact, after normalisation.

Fingerprints are refused for low-entropy values (dates, phone numbers, short identifiers, e-mail addresses, words
and names): a keyed hash of a low-entropy value can be reversed by anyone holding the key, simply by hashing every
candidate, and the key has to sit on the gate. Such misses become rules. Because values are never stored, rotating
the fingerprint key invalidates every fingerprint: rotation is an explicit command that names how many entries it
invalidates, and those entries must be added again.

Entry lifecycle: ``proposed`` -> ``verified`` (by a person other than the proposer) -> ``in_sweep`` -> ``built_in``
(the fix shipped in a release, with its synthetic regression case) or ``rejected``. Only ``in_sweep`` entries are
swept. Entries and their history are append-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import math
from pathlib import Path
import re
import secrets
from typing import Callable, Iterable, Mapping

from ..normalise import detection_view
from ..placeholders import STRICT_TOKEN_PATTERN
from ..types import Label
from .store import (
    CatalogueError, CatalogueUnavailable, actor, deny_changes, integrity_ok, parse_stamp,
    stamp, transaction, utc, utc_now,
)


ENTRY_KINDS = ("rule", "context", "fingerprint")
SOURCES = ("operator", "cloud_tripwire", "peer_check", "evaluation")
STATUSES = ("proposed", "verified", "in_sweep", "built_in", "rejected")
TRANSITIONS = {
    "proposed": frozenset({"verified", "rejected"}),
    "verified": frozenset({"in_sweep", "rejected"}),
    "in_sweep": frozenset({"built_in", "rejected"}),
    "built_in": frozenset(),
    "rejected": frozenset(),
}
SCHEMA_VERSION = 1
MIN_FINGERPRINT_BITS = 64
MAX_WINDOW_RUNS = 8
MAX_PATTERN_CHARS = 512
MAX_HITS_PER_ENTRY = 20
DEFAULT_MAX_SNAPSHOT_AGE_DAYS = 7
_FINGERPRINT_CONTEXT = b"themis-shield/known-miss/v1\0"

_JOB_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}")
_TOKEN = re.compile(STRICT_TOKEN_PATTERN)
_RUN = re.compile(r"[^\W_]+")
# A pattern must generalise: a character class, an escape class or a quantifier. A bare literal would be a stored
# value or quoted surrounding text.
_GENERALISES = re.compile(r"\\[dDwWsSbBpP]|\[|[*+?]|\{[0-9]")
_DATE = re.compile(
    r"(?<![0-9])[0-9]{1,4}[-/. ][0-9]{1,2}[-/. ][0-9]{1,4}(?![0-9])"
    r"|(?i:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*[0-9]{1,4}\b)"
    r"|(?i:\b[0-9]{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec))"
)
_PHONE_SHAPE = re.compile(r"\+?[0-9 ().\-]{6,24}")


class LowEntropyValue(CatalogueError):
    """A fingerprint was refused: the value is guessable, so it must become a rule entry."""


def _fold(view_text: str) -> str:
    return "".join(character for character in view_text.casefold() if character.isalnum())


def normalise_fingerprint_value(value: str) -> str:
    """The form a fingerprint is computed over: detection view, case-folded, letters and digits only.

    ``AB12-CD34`` and ``ab12 cd34`` fingerprint the same; the sweep folds candidate windows the same way.
    """
    if not isinstance(value, str):
        raise TypeError("text required")
    return _fold(detection_view(value).text)


def estimate_bits(normalised: str) -> float:
    """A deliberately conservative entropy estimate for a normalised (letters and digits) string.

    Runs of digits count log2(10) bits per digit. Runs of three or more letters count 2 bits per letter: they are
    word-like (names, words, codes such as ``case``), and English text carries well under 2 bits per letter. Runs
    of one or two letters between digits count log2(26). Structure is therefore not credited: ``CASE-2024-ABX-99812``
    estimates at about 44 bits, a random 16-character letters-and-digits string at about 64, a 32-character hex
    token at about 100. Underestimating only refuses a fingerprint (the operator writes a rule instead); it never
    stores something guessable.
    """
    total = 0.0
    for run in re.finditer(r"[0-9]+|[^0-9]+", normalised):
        text = run.group()
        if text.isdigit():
            total += len(text) * math.log2(10)
        elif len(text) >= 3:
            total += len(text) * 2.0
        else:
            total += len(text) * math.log2(26)
    return total


def check_fingerprint_value(value: str, *, min_bits: float = MIN_FINGERPRINT_BITS) -> str:
    """Return the normalised value, or refuse it with an instruction to add a rule instead."""
    if not isinstance(value, str):
        raise TypeError("text required")
    view = detection_view(value).text.strip()
    advice = (
        " A keyed hash of a guessable value can be reversed by anyone holding the key, and the key sits on the "
        "gate. Add a rule entry (a pattern) for this miss instead."
    )
    if not view:
        raise CatalogueError("empty value")
    if "@" in view:
        raise LowEntropyValue("An e-mail address is low-entropy." + advice)
    digits = sum(character.isdigit() for character in view)
    if _PHONE_SHAPE.fullmatch(view) and 6 <= digits <= 15:
        raise LowEntropyValue("A phone-number shape is low-entropy." + advice)
    if _DATE.search(view):
        raise LowEntropyValue("A date is low-entropy." + advice)
    if len(_RUN.findall(view)) > MAX_WINDOW_RUNS:
        raise CatalogueError(
            f"A fingerprint covers at most {MAX_WINDOW_RUNS} words or codes; use a context entry for a phrase."
        )
    normalised = _fold(view)
    if len(set(normalised)) < 8:
        raise LowEntropyValue("The value has fewer than eight distinct characters." + advice)
    bits = estimate_bits(normalised)
    if bits < min_bits:
        raise LowEntropyValue(
            f"Estimated entropy {bits:.0f} bits is below the minimum of {min_bits:.0f}." + advice
        )
    return normalised


def fingerprint(key: bytes, normalised: str) -> str:
    return hmac.new(key, _FINGERPRINT_CONTEXT + normalised.encode("utf-8"), hashlib.sha256).hexdigest()


def check_pattern(pattern: str) -> re.Pattern:
    """A rule or context pattern: bounded, compiles, generalises, and cannot match empty text."""
    if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN_CHARS:
        raise CatalogueError(f"a pattern of 1 to {MAX_PATTERN_CHARS} characters is required")
    if any(ord(character) < 32 and character not in "\t" for character in pattern):
        raise CatalogueError("control characters are not allowed in a pattern")
    try:
        compiled = re.compile(pattern)
    except re.error:
        raise CatalogueError("the pattern does not compile") from None
    if compiled.fullmatch("") is not None or compiled.search("") is not None:
        raise CatalogueError("the pattern matches empty text, so it would match everywhere")
    if not _GENERALISES.search(pattern):
        raise CatalogueError(
            "a rule or context entry is a pattern, never a stored value or quoted text: generalise it with "
            "character classes or quantifiers, or use a fingerprint for one high-entropy string"
        )
    return compiled


def _entity_type(value: str) -> str:
    try:
        return Label(value).value
    except ValueError:
        raise CatalogueError("unknown entity type; use one of the gate's labels") from None


@dataclass(frozen=True, slots=True)
class SweepHit:
    """Value-free: which entry, and where in the outbound text (path and character offsets)."""

    entry_id: str
    entry_kind: str
    entity_type: str
    path: tuple
    start: int
    end: int

    def as_record(self) -> dict:
        return {
            "entry_id": self.entry_id, "entry_kind": self.entry_kind, "entity_type": self.entity_type,
            "path": list(self.path), "start": self.start, "end": self.end,
        }

    def describe(self) -> str:
        where = ".".join(str(part) for part in self.path) or "text"
        return (
            f"{self.entry_id} ({self.entry_kind}, {self.entity_type}) in {where}, "
            f"characters {self.start}-{self.end}"
        )


@dataclass(frozen=True, slots=True)
class SweepRule:
    entry_id: str
    kind: str
    entity_type: str
    regex: re.Pattern = field(repr=False)


@dataclass(frozen=True, slots=True)
class SweepSnapshot:
    """The in-sweep entries at one register version, with the unwrapped key held only for the sweep."""

    version: int
    as_of_utc: datetime
    rules: tuple[SweepRule, ...]
    fingerprints: Mapping[str, tuple[str, str]] = field(repr=False)
    key: bytes | None = field(default=None, repr=False)

    def is_stale(self, now: datetime, max_age: timedelta) -> bool:
        return utc(now) - self.as_of_utc > max_age

    def sweep(self, leaves: Iterable[tuple[tuple, str]]) -> tuple[SweepHit, ...]:
        hits: dict[tuple, SweepHit] = {}
        per_entry: dict[str, int] = {}

        def add(entry_id, kind, entity_type, path, span):
            key = (entry_id, path, span)
            if key in hits or per_entry.get(entry_id, 0) >= MAX_HITS_PER_ENTRY:
                return
            per_entry[entry_id] = per_entry.get(entry_id, 0) + 1
            hits[key] = SweepHit(entry_id, kind, entity_type, path, span[0], span[1])

        for path, text in leaves:
            # The gate's own placeholders are masked (same length), so no entry can match a token's letters.
            masked = _TOKEN.sub(lambda match: " " * len(match.group()), text)
            view = detection_view(masked)
            for rule in self.rules:
                for match in rule.regex.finditer(view.text):
                    if match.end() > match.start():
                        add(rule.entry_id, rule.kind, rule.entity_type, path,
                            view.to_source(match.start(), match.end()))
            if not self.fingerprints or self.key is None:
                continue
            runs = list(_RUN.finditer(view.text))
            for first in range(len(runs)):
                folded = ""
                for last in range(first, min(first + MAX_WINDOW_RUNS, len(runs))):
                    folded += _fold(runs[last].group())
                    found = self.fingerprints.get(fingerprint(self.key, folded))
                    if found is not None:
                        add(found[0], "fingerprint", found[1], path,
                            view.to_source(runs[first].start(), runs[last].end()))
        return tuple(sorted(hits.values(), key=lambda hit: (hit.path, hit.start, hit.entry_id)))


class KnownMissRegister:
    """The local known-miss store (SQLite, append-only history; values never stored).

    ``protector`` wraps and unwraps the fingerprint key (a vault :class:`~themis_shield.dpapi.KeyProtector`); it is
    needed only to add fingerprints and to sweep them. ``create=True`` initialises a missing store (the gate does
    this at start-up when the register is enabled); otherwise a missing file is *unreadable*.
    """

    def __init__(
        self, path: Path | str, *, protector=None, clock: Callable[[], datetime] = utc_now,
        create: bool = False, insecure_test_only: bool = False,
    ):
        if getattr(protector, "test_only", False) and insecure_test_only is not True:
            raise CatalogueError("the null protector is not protection; it requires insecure_test_only")
        self.path = Path(path)
        self._protector = protector
        self._clock = clock
        if create:
            self._initialise()

    # -- schema -------------------------------------------------------------------------------------------------

    def _initialise(self) -> None:
        with transaction(self.path, create=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS kmr_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                schema_version INTEGER NOT NULL,
                created_utc TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS kmr_keys (
                key_id INTEGER PRIMARY KEY,
                protected_key BLOB NOT NULL,
                created_utc TEXT NOT NULL
            )""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS kmr_entries (
                entry_id TEXT PRIMARY KEY,
                entry_kind TEXT NOT NULL CHECK(entry_kind IN ({",".join(repr(k) for k in ENTRY_KINDS)})),
                entity_type TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ({",".join(repr(s) for s in SOURCES)})),
                created_utc TEXT NOT NULL,
                job_id TEXT,
                regression_case_id TEXT,
                pattern TEXT,
                fingerprint TEXT,
                key_id INTEGER,
                proposed_by TEXT NOT NULL,
                CHECK((entry_kind = 'fingerprint') = (fingerprint IS NOT NULL AND pattern IS NULL))
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS kmr_events (
                event_id INTEGER PRIMARY KEY,
                entry_id TEXT REFERENCES kmr_entries(entry_id),
                event TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT NOT NULL,
                occurred_utc TEXT NOT NULL,
                regression_case_id TEXT,
                release_tag TEXT,
                note TEXT
            )""")
            for table in ("kmr_meta", "kmr_entries", "kmr_events"):
                deny_changes(db, table)
            # The key table is key custody, not history: rotation deletes the old wrapped key.
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS kmr_keys_update_denied BEFORE UPDATE ON kmr_keys "
                "BEGIN SELECT RAISE(ABORT,'wrapped keys are replaced, never edited'); END"
            )
            now = self._now()
            if db.execute("SELECT 1 FROM kmr_meta").fetchone() is None:
                db.execute("INSERT INTO kmr_meta VALUES (1, ?, ?)", (SCHEMA_VERSION, stamp(now)))
                db.execute(
                    "INSERT INTO kmr_events (entry_id,event,to_status,actor,occurred_utc) VALUES (NULL,?,?,?,?)",
                    ("INITIALISED", None, "gate", stamp(now)),
                )
            elif db.execute("SELECT schema_version FROM kmr_meta").fetchone()[0] != SCHEMA_VERSION:
                raise CatalogueUnavailable("unsupported known-miss register schema")
            if self._protector is not None and db.execute("SELECT 1 FROM kmr_keys").fetchone() is None:
                db.execute(
                    "INSERT INTO kmr_keys (protected_key, created_utc) VALUES (?, ?)",
                    (self._protector.protect(secrets.token_bytes(32)), stamp(now)),
                )

    def _now(self) -> datetime:
        return utc(self._clock())

    def _open(self):
        return transaction(self.path, create=False)

    def _key(self, db) -> tuple[int, bytes]:
        row = db.execute("SELECT key_id, protected_key FROM kmr_keys ORDER BY key_id DESC LIMIT 1").fetchone()
        if row is None or self._protector is None:
            raise CatalogueUnavailable("fingerprint key unavailable")
        try:
            key = self._protector.unprotect(row["protected_key"])
        except Exception:
            raise CatalogueUnavailable("fingerprint key cannot be unwrapped") from None
        if len(key) != 32:
            raise CatalogueUnavailable("invalid fingerprint key")
        return row["key_id"], key

    @staticmethod
    def _status(db, entry_id: str) -> str:
        row = db.execute(
            "SELECT to_status FROM kmr_events WHERE entry_id=? AND to_status IS NOT NULL "
            "ORDER BY event_id DESC LIMIT 1", (entry_id,),
        ).fetchone()
        if row is None:
            raise KeyError("entry not found")
        return row[0]

    @staticmethod
    def _entry(db, entry_id: str):
        if not isinstance(entry_id, str) or not re.fullmatch(r"KMR-[0-9]{4,}", entry_id):
            raise KeyError("entry not found")
        row = db.execute("SELECT * FROM kmr_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError("entry not found")
        return row

    def _event(self, db, entry_id, event, before, after, who, now, *, case=None, release=None, note=None):
        db.execute(
            "INSERT INTO kmr_events (entry_id,event,from_status,to_status,actor,occurred_utc,"
            "regression_case_id,release_tag,note) VALUES (?,?,?,?,?,?,?,?,?)",
            (entry_id, event, before, after, actor(who), stamp(now), case, release, note),
        )

    # -- commands -----------------------------------------------------------------------------------------------

    def _insert(self, db, *, kind, entity_type, source, proposed_by, job_id, regression_case_id,
                pattern=None, digest=None, key_id=None) -> str:
        entity_type = _entity_type(entity_type)
        if source not in SOURCES:
            raise CatalogueError("unknown source")
        if job_id is not None and (not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id)):
            raise CatalogueError("invalid job id")
        if regression_case_id is not None and (
            not isinstance(regression_case_id, str) or not _CASE_ID.fullmatch(regression_case_id)
        ):
            raise CatalogueError("invalid regression case id")
        now = self._now()
        number = db.execute("SELECT COUNT(*) FROM kmr_entries").fetchone()[0] + 1
        entry_id = f"KMR-{number:04d}"
        db.execute(
            "INSERT INTO kmr_entries (entry_id,entry_kind,entity_type,source,created_utc,job_id,"
            "regression_case_id,pattern,fingerprint,key_id,proposed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, kind, entity_type, source, stamp(now), job_id, regression_case_id,
             pattern, digest, key_id, actor(proposed_by)),
        )
        self._event(db, entry_id, "PROPOSED", None, "proposed", proposed_by, now, case=regression_case_id)
        return entry_id

    def add_pattern(
        self, kind: str, pattern: str, *, entity_type: str, source: str, proposed_by: str,
        job_id: str | None = None, regression_case_id: str | None = None,
    ) -> str:
        """Propose a ``rule`` or ``context`` entry."""
        if kind not in {"rule", "context"}:
            raise CatalogueError("kind must be rule or context")
        check_pattern(pattern)
        with self._open() as db:
            return self._insert(
                db, kind=kind, entity_type=entity_type, source=source, proposed_by=proposed_by,
                job_id=job_id, regression_case_id=regression_case_id, pattern=pattern,
            )

    def add_fingerprint(
        self, value: str, *, entity_type: str, source: str, proposed_by: str,
        job_id: str | None = None, regression_case_id: str | None = None,
        min_bits: float = MIN_FINGERPRINT_BITS,
    ) -> str:
        """Propose a ``fingerprint`` entry. The value itself is hashed and discarded, never stored."""
        normalised = check_fingerprint_value(value, min_bits=max(min_bits, MIN_FINGERPRINT_BITS))
        with self._open() as db:
            key_id, key = self._key(db)
            digest = fingerprint(key, normalised)
            if db.execute(
                "SELECT 1 FROM kmr_entries WHERE fingerprint=? AND key_id=?", (digest, key_id),
            ).fetchone() is not None:
                raise CatalogueError("this value is already fingerprinted in the register")
            return self._insert(
                db, kind="fingerprint", entity_type=entity_type, source=source, proposed_by=proposed_by,
                job_id=job_id, regression_case_id=regression_case_id, digest=digest, key_id=key_id,
            )

    def transition(
        self, entry_id: str, to_status: str, *, actor_identity: str,
        regression_case_id: str | None = None, release_tag: str | None = None,
    ) -> str:
        if to_status not in STATUSES:
            raise CatalogueError("unknown status")
        if regression_case_id is not None and (
            not isinstance(regression_case_id, str) or not _CASE_ID.fullmatch(regression_case_id)
        ):
            raise CatalogueError("invalid regression case id")
        with self._open() as db:
            entry = self._entry(db, entry_id)
            before = self._status(db, entry_id)
            if to_status not in TRANSITIONS[before]:
                raise CatalogueError(f"an entry that is {before} cannot become {to_status}")
            if to_status == "verified" and actor(actor_identity) == entry["proposed_by"]:
                raise CatalogueError("an entry is verified by a person other than its proposer")
            if to_status == "in_sweep" and entry["entry_kind"] == "fingerprint":
                current = db.execute("SELECT MAX(key_id) FROM kmr_keys").fetchone()[0]
                if entry["key_id"] != current:
                    raise CatalogueError("this fingerprint was made under a retired key; add it again")
            if to_status == "built_in":
                if not isinstance(release_tag, str) or not _RELEASE.fullmatch(release_tag):
                    raise CatalogueError("built_in needs the release tag that carries the fix")
                if regression_case_id is None and self._case(db, entry) is None:
                    raise CatalogueError("built_in needs a linked synthetic regression case")
            self._event(
                db, entry_id, to_status.upper(), before, to_status, actor_identity, self._now(),
                case=regression_case_id, release=release_tag if to_status == "built_in" else None,
            )
            return to_status

    @staticmethod
    def _case(db, entry) -> str | None:
        row = db.execute(
            "SELECT regression_case_id FROM kmr_events WHERE entry_id=? AND regression_case_id IS NOT NULL "
            "ORDER BY event_id DESC LIMIT 1", (entry["entry_id"],),
        ).fetchone()
        return row[0] if row is not None else entry["regression_case_id"]

    def link_regression_case(self, entry_id: str, case_id: str, *, actor_identity: str) -> None:
        if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
            raise CatalogueError("invalid regression case id")
        with self._open() as db:
            self._entry(db, entry_id)
            self._event(db, entry_id, "LINKED", None, None, actor_identity, self._now(), case=case_id)

    def refresh(self, *, actor_identity: str) -> int:
        """Attest that the register is current (resets the snapshot age). Returns the new version."""
        with self._open() as db:
            self._event(db, None, "REFRESHED", None, None, actor_identity, self._now())
            return db.execute("SELECT MAX(event_id) FROM kmr_events").fetchone()[0]

    def fingerprints_invalidated_by_rotation(self) -> int:
        with self._open() as db:
            return len(self._live_fingerprints(db))

    def _live_fingerprints(self, db) -> list[str]:
        rows = db.execute("SELECT entry_id FROM kmr_entries WHERE entry_kind='fingerprint'").fetchall()
        return [row[0] for row in rows if self._status(db, row[0]) not in {"built_in", "rejected"}]

    def rotate_key(self, *, actor_identity: str, confirm_invalidates: int) -> int:
        """Replace the fingerprint key; the old wrapped key is deleted. Every live fingerprint becomes rejected.

        ``confirm_invalidates`` must equal the number of fingerprint entries this invalidates, so a rotation is
        never accidental. Returns that number.
        """
        if self._protector is None:
            raise CatalogueUnavailable("a key protector is required to rotate the key")
        with self._open() as db:
            live = self._live_fingerprints(db)
            if confirm_invalidates != len(live):
                raise CatalogueError(
                    f"rotation invalidates {len(live)} fingerprint entries; confirm that number"
                )
            now = self._now()
            db.execute("PRAGMA secure_delete=ON")
            db.execute(
                "INSERT INTO kmr_keys (protected_key, created_utc) VALUES (?, ?)",
                (self._protector.protect(secrets.token_bytes(32)), stamp(now)),
            )
            current = db.execute("SELECT MAX(key_id) FROM kmr_keys").fetchone()[0]
            db.execute("DELETE FROM kmr_keys WHERE key_id<>?", (current,))
            for entry_id in live:
                self._event(
                    db, entry_id, "REJECTED", self._status(db, entry_id), "rejected", actor_identity, now,
                    note="KEY_ROTATED",
                )
            self._event(db, None, "KEY_ROTATED", None, None, actor_identity, now)
            return len(live)

    # -- reads --------------------------------------------------------------------------------------------------

    def entries(self, status: str | None = None) -> tuple[dict, ...]:
        """Safe listing rows. Fingerprints are never listed; patterns are included (they are shapes)."""
        with self._open() as db:
            result = []
            for row in db.execute("SELECT * FROM kmr_entries ORDER BY entry_id"):
                current = self._status(db, row["entry_id"])
                if status is not None and current != status:
                    continue
                result.append({
                    "entry_id": row["entry_id"], "entry_kind": row["entry_kind"],
                    "entity_type": row["entity_type"], "source": row["source"], "status": current,
                    "created_utc": row["created_utc"], "job_id": row["job_id"],
                    "regression_case_id": self._case(db, row), "pattern": row["pattern"],
                })
            return tuple(result)

    def entry(self, entry_id: str) -> dict:
        for row in self.entries():
            if row["entry_id"] == entry_id:
                return row
        raise KeyError("entry not found")

    def version(self) -> tuple[int, datetime]:
        with self._open() as db:
            row = db.execute("SELECT MAX(event_id), MAX(occurred_utc) FROM kmr_events").fetchone()
            if row is None or row[0] is None:
                raise CatalogueUnavailable("register has no history")
            return row[0], parse_stamp(row[1])

    def snapshot(self) -> SweepSnapshot:
        """Load everything ``in_sweep``. Any problem raises :class:`CatalogueUnavailable` (the gate BLOCKs)."""
        try:
            with self._open() as db:
                if not integrity_ok(db):
                    raise CatalogueUnavailable("register integrity check failed")
                meta = db.execute("SELECT schema_version FROM kmr_meta").fetchone()
                if meta is None or meta[0] != SCHEMA_VERSION:
                    raise CatalogueUnavailable("unsupported known-miss register schema")
                version, latest = db.execute(
                    "SELECT MAX(event_id), MAX(occurred_utc) FROM kmr_events"
                ).fetchone()
                if version is None:
                    raise CatalogueUnavailable("register has no history")
                rules, prints = [], {}
                current_key = db.execute("SELECT MAX(key_id) FROM kmr_keys").fetchone()[0]
                for row in db.execute("SELECT * FROM kmr_entries ORDER BY entry_id").fetchall():
                    if self._status(db, row["entry_id"]) != "in_sweep":
                        continue
                    if row["entry_kind"] == "fingerprint":
                        if row["key_id"] != current_key:
                            raise CatalogueUnavailable("an in-sweep fingerprint has no key")
                        prints[row["fingerprint"]] = (row["entry_id"], row["entity_type"])
                    else:
                        rules.append(SweepRule(
                            row["entry_id"], row["entry_kind"], row["entity_type"], re.compile(row["pattern"]),
                        ))
                key = self._key(db)[1] if prints else None
                return SweepSnapshot(version, parse_stamp(latest), tuple(rules), dict(prints), key)
        except CatalogueUnavailable:
            raise
        except Exception:
            raise CatalogueUnavailable("known-miss register unreadable") from None

    def regression_stub(self, entry_id: str, *, actor_identity: str, case_id: str | None = None) -> dict:
        """A value-free stub for a freshly written synthetic regression case; records the link."""
        row = self.entry(entry_id)
        case_id = case_id or f"KMR-REG-{entry_id[4:]}-{secrets.token_hex(3)}"
        self.link_regression_case(entry_id, case_id, actor_identity=actor_identity)
        return {
            "schema": "themis-shield/known-miss-regression-stub/v1",
            "case_id": case_id,
            "entry_id": entry_id,
            "entry_kind": row["entry_kind"],
            "entity_type": row["entity_type"],
            "source": row["source"],
            "synthetic": True,
            "text": "",
            "expected": {
                "decision_at_least": "ASK",
                "or_substituted_label": row["entity_type"],
            },
            "instructions": (
                "Write a NEW synthetic document, in your own words, that contains a value of the same shape as "
                "this miss. Never copy the original document or the missed value. Regression cases train and "
                "regress; they never enter a held-out test split."
            ),
        }


def max_age(days: int | float) -> timedelta:
    if type(days) not in (int, float) or not 0 < days <= 365:
        raise ValueError("max_snapshot_age_days must be between 0 and 365")
    return timedelta(days=days)
