"""Small shared SQLite plumbing for the catalogues: per-call transactions and append-only tables.

The same discipline as the job register: history tables refuse UPDATE and DELETE through triggers, so an
application bug cannot rewrite what was decided. That guards against mistakes, not against someone with access to
the file.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class CatalogueError(RuntimeError):
    """A catalogue command was refused. Messages are fixed text, never caller input."""


class CatalogueUnavailable(CatalogueError):
    """The catalogue file is missing, unreadable or fails its integrity check."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_stamp(value: str) -> datetime:
    return utc(datetime.fromisoformat(value))


def actor(value: str) -> str:
    """An opaque, printable actor identity (the same rule as the job register)."""
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CatalogueError("invalid actor identity")
    return value


def safe_path(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise CatalogueError("catalogue symlink paths are forbidden")
    return path


@contextmanager
def transaction(path: Path, *, create: bool):
    """One IMMEDIATE transaction on a fresh connection; never creates the file unless ``create``."""
    path = safe_path(path)
    if not create and not path.is_file():
        raise CatalogueUnavailable("catalogue file missing")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    except sqlite3.Error:
        raise CatalogueUnavailable("catalogue file unreadable") from None
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


def deny_changes(connection: sqlite3.Connection, table: str, *, deletes: bool = True) -> None:
    for action in ("UPDATE", "DELETE") if deletes else ("UPDATE",):
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_denied BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'immutable catalogue history'); END"
        )


def integrity_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA quick_check").fetchone()
    return row is not None and row[0] == "ok"
