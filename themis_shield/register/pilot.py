"""Explicit synthetic-only fixture; never a production Operator decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import identity
from .sqlite_store import SqliteRegister


@dataclass(frozen=True, slots=True)
class PilotAutoApprover:
    register: SqliteRegister
    pilot_auto_approve: bool = False
    pilot_identity: str = "named-synthetic-pilot-fixture"

    def __post_init__(self):
        if type(self.pilot_auto_approve) is not bool:
            raise ValueError("pilot_auto_approve must be a boolean")
        identity(self.pilot_identity)

    def try_approve(self, job_id: str, now: datetime | None = None):
        # Conditions and decision are rechecked inside ONE SQLite transaction.
        return self.register._pilot_approve(
            job_id, enabled=self.pilot_auto_approve,
            actor=self.pilot_identity, now=now,
        )
