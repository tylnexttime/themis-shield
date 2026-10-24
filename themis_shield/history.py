"""Canonical redacted history, separate from hash-only local-view recognition.

Hashes of whole local views exist only in private process memory. Original
local view payloads are not retained, persisted, serialized or logged here.
This store is not authentication; M4 must bind callers to conversations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from threading import RLock

from .types import LocalPayload, RedactedPayload


@dataclass(frozen=True, slots=True)
class CanonicalTurn:
    turn_id: str
    role: str
    payload: RedactedPayload


@dataclass(frozen=True, slots=True)
class LocalTurnView:
    turn_id: str
    payload: LocalPayload


@dataclass(frozen=True, slots=True)
class ClientTurn:
    turn_id: str
    role: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class HistoryResolution:
    outbound: tuple[CanonicalTurn, ...]
    matched_turns: tuple[CanonicalTurn, ...]
    new_turns: tuple[ClientTurn, ...]

    @property
    def needs_detection(self) -> bool:
        return bool(self.new_turns)


class HistoryError(ValueError):
    pass


def _hash(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


class CanonicalHistory:
    def __init__(self) -> None:
        self._lock = RLock()
        self._turns: dict[str, list[CanonicalTurn]] = {}
        self._local_hashes: dict[tuple[str, str], set[bytes]] = {}

    def append_redacted(self, conversation_id: str, turn: CanonicalTurn) -> None:
        if (
            not isinstance(conversation_id, str) or not conversation_id
            or not isinstance(turn, CanonicalTurn)
            or not turn.turn_id or not turn.role
            or not isinstance(turn.payload, RedactedPayload)
        ):
            raise HistoryError("invalid canonical history turn")
        with self._lock:
            turns = self._turns.setdefault(conversation_id, [])
            for previous in turns:
                if previous.turn_id == turn.turn_id:
                    if previous == turn:
                        return
                    raise HistoryError("canonical turn is immutable")
            turns.append(turn)

    def record(
        self,
        conversation_id: str,
        turn_id: str,
        role: str,
        tokenised_text: str,
        *,
        local_text: str | None = None,
    ) -> CanonicalTurn:
        """Text convenience API. Only already-protected text may be supplied."""

        turn = CanonicalTurn(
            turn_id, role, RedactedPayload(tokenised_text.encode("utf-8"), turn_id)
        )
        self.append_redacted(conversation_id, turn)
        if local_text is not None:
            self.record_local_view(
                conversation_id, LocalTurnView(
                    turn_id, LocalPayload(local_text.encode("utf-8"))
                )
            )
        return turn

    def record_local_view(self, conversation_id: str, view: LocalTurnView) -> None:
        if not isinstance(view, LocalTurnView) or not isinstance(view.payload, LocalPayload):
            raise HistoryError("invalid local history view")
        with self._lock:
            if not any(
                turn.turn_id == view.turn_id
                for turn in self._turns.get(conversation_id, [])
            ):
                raise HistoryError("local view has no canonical turn")
            self._local_hashes.setdefault(
                (conversation_id, view.turn_id), set()
            ).add(_hash(view.payload.body))

    def outbound(self, conversation_id: str) -> tuple[CanonicalTurn, ...]:
        with self._lock:
            return tuple(self._turns.get(conversation_id, []))

    def reconcile(
        self, conversation_id: str, client_turns: tuple[ClientTurn, ...]
    ) -> HistoryResolution:
        """Accept only an ordered, hash-matching canonical prefix.

        On any mismatch, outbound is empty: the caller must detect/reconcile
        new_turns before constructing any send. matched_turns remain redacted.
        """

        with self._lock:
            canonical = tuple(self._turns.get(conversation_id, []))
            matched = []
            new = []
            seen: set[str] = set()
            for index, client in enumerate(client_turns):
                if not isinstance(client, ClientTurn):
                    raise HistoryError("typed client history required")
                candidate = canonical[index] if index < len(canonical) else None
                valid = (
                    client.turn_id not in seen
                    and candidate is not None
                    and candidate.turn_id == client.turn_id
                    and candidate.role == client.role
                )
                seen.add(client.turn_id)
                if valid:
                    digest = _hash(client.text.encode("utf-8"))
                    allowed = self._local_hashes.get(
                        (conversation_id, client.turn_id), set()
                    )
                    valid = (
                        digest == _hash(candidate.payload.body) or digest in allowed
                    )
                if valid:
                    matched.append(candidate)
                else:
                    new.append(client)
            return HistoryResolution(
                () if new else canonical, tuple(matched), tuple(new)
            )

    def wipe(self, conversation_id: str) -> None:
        with self._lock:
            self._turns.pop(conversation_id, None)
            for key in tuple(self._local_hashes):
                if key[0] == conversation_id:
                    del self._local_hashes[key]

    def wipe_all(self) -> None:
        with self._lock:
            self._turns.clear()
            self._local_hashes.clear()
