"""Specify an append-only evidence ledger for each protected request.

Carries forward v0.2 §4. New evidence extends the ledger; an allow-list creates a
projection, not deletion. Mandatory SUBSTITUTE, ASK and BLOCK actions survive
exemptions, clean later stages and staff confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import ContentPath, Finding, LedgerEntry, StageKey


@dataclass(frozen=True, slots=True)
class EvidenceLedger:
    """Represent immutable snapshots of one add-only request history.

    Appending returns a new snapshot. A future service must retain the current
    lineage and reject attempts to authorise release from an older snapshot.
    """

    request_id: str
    required_stages: frozenset[StageKey]
    entries: tuple[LedgerEntry, ...] = ()

    def append(self, entry: LedgerEntry) -> EvidenceLedger:
        """Require the next sequence and valid provenance; never remove evidence."""
        raise NotImplementedError("skeleton: not implemented")

    def effective_findings(self, block_path: ContentPath) -> tuple[Finding, ...]:
        """Project exemptions while retaining mandatory source-span protection."""
        raise NotImplementedError("skeleton: not implemented")

    def assert_complete(self) -> None:
        """Require successful, complete evidence for every expected stage key.

        A stage missing on one system, tool or history block is not covered by
        that stage succeeding on another block.
        """
        raise NotImplementedError("skeleton: not implemented")

    def assert_extension_of(self, previous: EvidenceLedger) -> None:
        """Reject rollback, changed entries, changed inventory or another request."""
        raise NotImplementedError("skeleton: not implemented")
