"""The register's history is append-only at the database level.

SQLite triggers refuse UPDATE/DELETE on the audit, approval and stage-decision tables, and DELETE on jobs. This is
append-only, not tamper-evident: anyone with write access to the file can drop triggers. Tamper evidence (hash chain
or a central copy) is a separate, later control.
"""
from __future__ import annotations

import sqlite3

import pytest

from router_fakes import harness, request


@pytest.mark.parametrize("statement", [
    "DELETE FROM audit",
    "UPDATE audit SET occurred_utc = 'x'",
    "DELETE FROM approvals",
    "DELETE FROM stage_decisions",
    "DELETE FROM jobs",
])
def test_register_history_cannot_be_rewritten(tmp_path, statement):
    h = harness(tmp_path)
    try:
        response = h.router.handle(request("What is a synthetic retention schedule?"))
        h.register.approve(response.job_id)
    finally:
        h.cloud.close()
    connection = sqlite3.connect(tmp_path / "register.db")
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
            connection.commit()
    finally:
        connection.close()
