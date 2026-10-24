"""Encrypted, per-user SQLite token mappings.

Only ciphertext and protected/wrapped keys are persisted (Windows DPAPI or a
passphrase protector; see :mod:`themis_shield.protectors`). Keys are
unwrapped per operation, never cached between operations. DELETE journalling,
FULL synchronous writes and secure_delete reduce active-file key remnants;
they do not destroy backups, SSD snapshots or Python memory copies.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from .paths import app_data_dir
import secrets
import sqlite3
from threading import RLock
from typing import Callable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .dpapi import DPAPIProtector, KeyProtector
from .placeholders import parse_strict_token


AES_DATA_KEY_BITS = 256
DEFAULT_IDLE_EXPIRY_SECONDS = 4 * 60 * 60
PROPOSED_MAX_RETENTION_DAYS = 14
VERSION = 1


class VaultError(RuntimeError):
    pass


class VaultConflict(VaultError):
    pass


class VaultExpired(VaultError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware UTC time required")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _aad(conversation_id: str, purpose: str, token: str = "") -> bytes:
    return json.dumps(
        [VERSION, conversation_id, purpose, token],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")


def _validate(conversation_id: str, token: str | None = None) -> None:
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation identifier required")
    if token is not None:
        parse_strict_token(token)


def default_vault_path() -> Path:
    return app_data_dir() / "vault.db"


class Vault:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        protector: KeyProtector | None = None,
        idle_expiry_seconds: float = DEFAULT_IDLE_EXPIRY_SECONDS,
        clock: Callable[[], datetime] = _now,
        insecure_test_only: bool = False,
    ) -> None:
        if idle_expiry_seconds <= 0:
            raise ValueError("positive idle expiry required")
        # Fail before creating directories or opening SQLite: off Windows the
        # default DPAPI protector refuses, and the identity protector is never
        # accepted unless explicitly flagged as an insecure test setup.
        self._protector = protector if protector is not None else DPAPIProtector()
        if getattr(self._protector, "test_only", False) and insecure_test_only is not True:
            raise VaultError("NullProtector is not protection; it requires insecure_test_only=True")
        self._clock = clock
        self._idle = timedelta(seconds=idle_expiry_seconds)
        self.allocation_lock = RLock()
        self.path = Path(path) if path is not None else default_vault_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False, timeout=30
        )
        self._closed = False
        with self.allocation_lock:
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS user_key (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    protected_key BLOB NOT NULL,
                    version INTEGER NOT NULL CHECK(version = 1)
                );
                CREATE TABLE IF NOT EXISTS conversation_keys (
                    conversation_id TEXT PRIMARY KEY,
                    nonce BLOB NOT NULL,
                    wrapped_data_key BLOB NOT NULL,
                    created_utc TEXT NOT NULL,
                    last_used_utc TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version = 1)
                );
                CREATE TABLE IF NOT EXISTS mappings (
                    conversation_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    created_utc TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version = 1),
                    PRIMARY KEY(conversation_id, token)
                );
            """)
            with self._transaction():
                if self._db.execute(
                    "SELECT 1 FROM user_key WHERE singleton=1"
                ).fetchone() is None:
                    protected = self._protector.protect(secrets.token_bytes(32))
                    self._db.execute(
                        "INSERT INTO user_key VALUES (1, ?, ?)", (protected, VERSION)
                    )
                # Recovery after a crash between key deletion and row deletion.
                self._db.execute("""
                    DELETE FROM mappings WHERE conversation_id NOT IN
                    (SELECT conversation_id FROM conversation_keys)
                """)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise VaultError("vault is closed")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def verify_key(self) -> None:
        """Unwrap the user wrapping key once and discard it (startup check)."""
        with self.allocation_lock, self._transaction():
            self._user_key()

    def _user_key(self) -> bytes:
        row = self._db.execute(
            "SELECT protected_key, version FROM user_key WHERE singleton=1"
        ).fetchone()
        if row is None or row[1] != VERSION:
            raise VaultError("user wrapping key is unavailable")
        key = self._protector.unprotect(row[0])
        if len(key) != 32:
            raise VaultError("invalid user wrapping key")
        return key

    def _key(self, conversation_id: str, now: datetime, *, create: bool) -> bytes | None:
        row = self._db.execute("""
            SELECT nonce, wrapped_data_key, last_used_utc, version
            FROM conversation_keys WHERE conversation_id=?
        """, (conversation_id,)).fetchone()
        if row is not None:
            if row[3] != VERSION:
                raise VaultError("unsupported conversation key version")
            if now >= _utc(datetime.fromisoformat(row[2])) + self._idle:
                if create:
                    raise VaultExpired("conversation expired; purge or wipe before reuse")
                return None
            try:
                key = AESGCM(self._user_key()).decrypt(
                    row[0], row[1], _aad(conversation_id, "data-key")
                )
            except InvalidTag:
                raise VaultError("conversation key authentication failed") from None
            if len(key) != 32:
                raise VaultError("invalid conversation data key")
            self._db.execute(
                "UPDATE conversation_keys SET last_used_utc=? WHERE conversation_id=?",
                (_timestamp(now), conversation_id),
            )
            return key
        if not create:
            return None
        # Never inherit orphaned rows when an identifier is reused after wipe.
        self._db.execute(
            "DELETE FROM mappings WHERE conversation_id=?", (conversation_id,)
        )
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        wrapped = AESGCM(self._user_key()).encrypt(
            nonce, key, _aad(conversation_id, "data-key")
        )
        timestamp = _timestamp(now)
        self._db.execute(
            "INSERT INTO conversation_keys VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, nonce, wrapped, timestamp, timestamp, VERSION),
        )
        return key

    @staticmethod
    def _decrypt(
        key: bytes, conversation_id: str, token: str, row: tuple
    ) -> str:
        nonce, ciphertext, version = row
        if version != VERSION:
            raise VaultError("unsupported mapping version")
        try:
            raw = AESGCM(key).decrypt(
                nonce, ciphertext, _aad(conversation_id, "original", token)
            )
            return raw.decode("utf-8")
        except (InvalidTag, UnicodeError):
            raise VaultError("mapping authentication failed") from None

    def put(self, conversation_id: str, token: str, value: str) -> None:
        _validate(conversation_id, token)
        if not isinstance(value, str):
            raise TypeError("original value must be text")
        # Reject unsupported lone surrogates before changing storage.
        raw = value.encode("utf-8")
        with self.allocation_lock, self._transaction():
            now = _utc(self._clock())
            key = self._key(conversation_id, now, create=True)
            assert key is not None
            existing = self._db.execute("""
                SELECT nonce, ciphertext, version FROM mappings
                WHERE conversation_id=? AND token=?
            """, (conversation_id, token)).fetchone()
            if existing is not None:
                if self._decrypt(key, conversation_id, token, existing) != value:
                    raise VaultConflict("token is already bound to another value")
                return
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(key).encrypt(
                nonce, raw, _aad(conversation_id, "original", token)
            )
            self._db.execute(
                "INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, token, nonce, ciphertext, _timestamp(now), VERSION),
            )

    def get(self, conversation_id: str, token: str) -> str | None:
        _validate(conversation_id, token)
        with self.allocation_lock, self._transaction():
            key = self._key(conversation_id, _utc(self._clock()), create=False)
            if key is None:
                return None
            row = self._db.execute("""
                SELECT nonce, ciphertext, version FROM mappings
                WHERE conversation_id=? AND token=?
            """, (conversation_id, token)).fetchone()
            return None if row is None else self._decrypt(key, conversation_id, token, row)

    def mapping(self, conversation_id: str) -> dict[str, str]:
        _validate(conversation_id)
        with self.allocation_lock, self._transaction():
            key = self._key(conversation_id, _utc(self._clock()), create=False)
            if key is None:
                return {}
            rows = self._db.execute("""
                SELECT token, nonce, ciphertext, version FROM mappings
                WHERE conversation_id=? ORDER BY token
            """, (conversation_id,)).fetchall()
            return {
                token: self._decrypt(key, conversation_id, token, (nonce, ciphertext, version))
                for token, nonce, ciphertext, version in rows
            }

    def wipe(self, conversation_id: str) -> None:
        _validate(conversation_id)
        with self.allocation_lock:
            # Separate commits: a crash must not roll back key deletion together
            # with row deletion. No per-conversation key cache exists to clear.
            with self._transaction():
                self._db.execute(
                    "DELETE FROM conversation_keys WHERE conversation_id=?",
                    (conversation_id,),
                )
            with self._transaction():
                self._db.execute(
                    "DELETE FROM mappings WHERE conversation_id=?", (conversation_id,)
                )

    def wipe_all(self) -> None:
        with self.allocation_lock:
            with self._transaction():
                self._db.execute("DELETE FROM conversation_keys")
            with self._transaction():
                self._db.execute("DELETE FROM mappings")

    def purge_expired(self, now: datetime) -> tuple[str, ...]:
        now = _utc(now)
        with self.allocation_lock:
            if self._closed:
                raise VaultError("vault is closed")
            rows = self._db.execute(
                "SELECT conversation_id, last_used_utc FROM conversation_keys"
            ).fetchall()
            expired = tuple(
                conversation_id for conversation_id, timestamp in rows
                if now >= _utc(datetime.fromisoformat(timestamp)) + self._idle
            )
            for conversation_id in expired:
                self.wipe(conversation_id)
            return expired

    def close(self) -> None:
        with self.allocation_lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


EncryptedVault = Vault


class InMemoryVault:
    """Synthetic-only mapping store, with the same public lifecycle interface."""

    def __init__(
        self,
        *,
        idle_expiry_seconds: float = DEFAULT_IDLE_EXPIRY_SECONDS,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if idle_expiry_seconds <= 0:
            raise ValueError("positive idle expiry required")
        self.allocation_lock = RLock()
        self._clock = clock
        self._idle = timedelta(seconds=idle_expiry_seconds)
        self._values: dict[str, dict[str, str]] = {}
        self._last_used: dict[str, datetime] = {}
        self._closed = False

    def _active(self, conversation_id: str, *, create: bool) -> bool:
        if self._closed:
            raise VaultError("vault is closed")
        now = _utc(self._clock())
        last = self._last_used.get(conversation_id)
        if last is not None and now >= last + self._idle:
            if create:
                raise VaultExpired("conversation expired; purge or wipe before reuse")
            return False
        if last is None and not create:
            return False
        self._last_used[conversation_id] = now
        self._values.setdefault(conversation_id, {})
        return True

    def put(self, conversation_id: str, token: str, value: str) -> None:
        _validate(conversation_id, token)
        if not isinstance(value, str):
            raise TypeError("original value must be text")
        value.encode("utf-8")
        with self.allocation_lock:
            self._active(conversation_id, create=True)
            values = self._values[conversation_id]
            if token in values and values[token] != value:
                raise VaultConflict("token is already bound to another value")
            values[token] = value

    def get(self, conversation_id: str, token: str) -> str | None:
        _validate(conversation_id, token)
        with self.allocation_lock:
            if not self._active(conversation_id, create=False):
                return None
            return self._values[conversation_id].get(token)

    def mapping(self, conversation_id: str) -> dict[str, str]:
        _validate(conversation_id)
        with self.allocation_lock:
            if not self._active(conversation_id, create=False):
                return {}
            return dict(self._values[conversation_id])

    def wipe(self, conversation_id: str) -> None:
        _validate(conversation_id)
        with self.allocation_lock:
            self._last_used.pop(conversation_id, None)
            self._values.pop(conversation_id, None)

    def wipe_all(self) -> None:
        with self.allocation_lock:
            self._last_used.clear()
            self._values.clear()

    def purge_expired(self, now: datetime) -> tuple[str, ...]:
        now = _utc(now)
        with self.allocation_lock:
            expired = tuple(
                conversation_id for conversation_id, last in self._last_used.items()
                if now >= last + self._idle
            )
            for conversation_id in expired:
                self.wipe(conversation_id)
            return expired

    def close(self) -> None:
        with self.allocation_lock:
            self.wipe_all()
            self._closed = True

    def __enter__(self) -> InMemoryVault:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
