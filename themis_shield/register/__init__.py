"""Job register contracts and value-free offline journalling.

The central register is proposed; only the local SQLite register is implemented.
Imports do not contact a register, read credentials or open a journal.
"""

from .client import (
    JobRegisterClient,
    ApprovalRecord,
    HttpRegisterClient,
    JobRecord,
    JobStatus,
    JournalEntry,
    LocalJournal,
    Tier,
    approval_is_valid,
)

__all__ = [
    "JobRegisterClient",
    "ApprovalRecord",
    "HttpRegisterClient",
    "JobRecord",
    "JobStatus",
    "JournalEntry",
    "LocalJournal",
    "Tier",
    "approval_is_valid",
]
