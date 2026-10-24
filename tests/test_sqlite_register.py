"""Synthetic-only SQLite, lifecycle, CLI and reporting tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import csv
import itertools
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

from themis_shield.detectors.markings import MarkingClass
from themis_shield.register.client import (
    JobRegisterClient, JobRecord, JobStatus, JournalEntry, SensitivePath,
    Tier, approval_is_valid,
)
from themis_shield.register.cli import display_time, main
from themis_shield.register.models import JobIntake, PILOT_ROLE, summary_guard
from themis_shield.register.pilot import PilotAutoApprover
from themis_shield.register.reporting import business_hours_between, latency_stats
from themis_shield.register.sqlite_store import LEGAL_TRANSITIONS, SqliteRegister
from themis_shield.routing import Route

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


S = JobStatus
UTC = timezone.utc
BUSINESS_TZ = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 10, 22, 10, tzinfo=UTC)  # 11:00 local (WEST) in the pinned test timezone.
ACTOR = "synthetic-requester"
OPERATOR = "synthetic-operator"


def intake(**changes):
    return replace(
        JobIntake(
            "", NOW, S.LOGGED, Route.CLOUD, MarkingClass.INTERNAL,
            "a" * 64, "approved-destination", "approved-model", "cloud-demo",
            requester_identity=ACTOR,
            use_case_record_ref="SYNTHETIC-ENDORSEMENT",
            problem_summary_deid="Summarise the synthetic clean example.",
            synthetic=True,
            local_authorisation_ref="SYNTHETIC-LOCAL-AUTHORITY",
        ),
        **changes,
    )


@pytest.fixture
def store(tmp_path):
    return SqliteRegister(tmp_path / "register.db", requester_identity=ACTOR)


def logged(store, **changes):
    return store.log_job(intake(**changes), now=NOW)


def awaiting(store, **changes):
    job = logged(store, **changes)
    return store.submit_for_approval(job, now=NOW)


def approved(store, **changes):
    job = awaiting(store, **changes)
    decision = store.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", NOW,
        approver_identity=OPERATOR,
    )
    assert decision is not None
    return store.get_job(job.job_id)


def state_job(store, state):
    if state == S.LOGGED:
        return logged(store)
    if state == S.AUTO_ALLOWED_LOCAL:
        job = logged(store, route=Route.LOCAL_ONLY)
        return store.auto_allow_local(job.job_id, now=NOW)
    if state == S.AWAITING_OPERATOR:
        return awaiting(store)
    if state == S.ESCALATED_REVIEW:
        job = awaiting(store, source_document_count=6)
        return store.escalate(
            job.job_id, "operator", "MAJOR", NOW,
            approver_identity=OPERATOR,
        )
    if state == S.APPROVED:
        return approved(store)
    if state == S.HELD_OFFLINE:
        job = approved(store)
        return store.hold_offline(job.job_id, now=NOW)
    if state == S.EXPIRED:
        job = approved(store)
        store.expire_due(NOW + timedelta(days=7))
        return store.get_job(job.job_id)
    if state == S.REFUSED:
        job = awaiting(store)
        return store.refuse(
            job.job_id, "operator", "REFUSED", NOW,
            approver_identity=OPERATOR,
        )
    if state == S.WITHDRAWN:
        job = logged(store)
        return store.withdraw(job.job_id, requester_identity=ACTOR, now=NOW)
    if state == S.COMPLETED:
        job = approved(store)
        return store.complete(job.job_id, now=NOW)
    raise AssertionError(state)


def test_unchanged_protocol_shape_and_minimal_return(store):
    # Protocol is not runtime_checkable; exercise every structural member.
    client: JobRegisterClient = store
    assert all(callable(getattr(client, name)) for name in (
        "log_job", "get_job", "submit_for_approval", "get_approval", "sync_journal",
    ))
    job = logged(store)
    assert type(job) is JobRecord
    assert not hasattr(client.get_job(job.job_id), "requester_identity")


def test_gap_free_year_boundary_and_failed_mint(store):
    before = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
    after = datetime(2027, 1, 1, tzinfo=UTC)
    assert store.log_job(intake(), now=before).job_id == "JOB-2026-0001"
    with pytest.raises(ValueError):
        store.log_job(intake(problem_summary_deid="SYNTHETIC@example.invalid"), now=before)
    assert store.log_job(intake(), now=before).job_id == "JOB-2026-0002"
    assert store.log_job(intake(), now=after).job_id == "JOB-2027-0001"
    assert store.log_job(intake(), now=after).job_id == "JOB-2027-0002"


def test_audit_failure_rolls_back_mint_and_decision(store, monkeypatch):
    original = store._audit

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic audit failure")

    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(sqlite3.Error):
        logged(store)
    monkeypatch.setattr(store, "_audit", original)
    job = awaiting(store)
    assert job.job_id == "JOB-2026-0001"
    monkeypatch.setattr(store, "_audit", fail)
    with pytest.raises(sqlite3.Error):
        store.approve(
            job.job_id, "operator", "ROUTINE_APPROVED", NOW,
            approver_identity=OPERATOR,
        )
    monkeypatch.setattr(store, "_audit", original)
    assert store.get_job(job.job_id).status == S.AWAITING_OPERATOR
    assert store.current_approval(job.job_id, NOW) is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_concurrent_connections_mint_once_each(store):
    def create(_):
        other = SqliteRegister(store.path, requester_identity=ACTOR)
        return other.log_job(intake(), now=NOW).job_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(12)))
    assert sorted(ids) == [f"JOB-2026-{number:04d}" for number in range(1, 13)]


def test_yearly_series_exhaustion_does_not_widen_or_skip(store):
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO yearly_counters VALUES (2026,9999)")
    with pytest.raises(OverflowError):
        logged(store)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT last_number FROM yearly_counters").fetchone()[0] == 9999
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def expected_edges():
    return {
        S.LOGGED: {S.AUTO_ALLOWED_LOCAL, S.AWAITING_OPERATOR, S.WITHDRAWN, S.HELD_OFFLINE, S.REFUSED},
        S.AUTO_ALLOWED_LOCAL: {S.AWAITING_OPERATOR, S.COMPLETED, S.WITHDRAWN, S.REFUSED},
        S.AWAITING_OPERATOR: {
            S.ESCALATED_REVIEW, S.APPROVED, S.REFUSED, S.WITHDRAWN,
            S.HELD_OFFLINE, S.AUTO_ALLOWED_LOCAL,
        },
        S.ESCALATED_REVIEW: {
            S.APPROVED, S.REFUSED, S.ESCALATED_REVIEW, S.WITHDRAWN,
            S.HELD_OFFLINE, S.AUTO_ALLOWED_LOCAL,
        },
        S.APPROVED: {S.EXPIRED, S.COMPLETED, S.WITHDRAWN, S.HELD_OFFLINE, S.AUTO_ALLOWED_LOCAL, S.REFUSED},
        S.EXPIRED: {S.LOGGED},
        S.HELD_OFFLINE: {S.LOGGED, S.APPROVED, S.WITHDRAWN, S.AUTO_ALLOWED_LOCAL, S.REFUSED},
        S.REFUSED: set(), S.WITHDRAWN: set(), S.COMPLETED: set(),
    }


def test_transition_matrix_matches_proposal():
    assert dict(LEGAL_TRANSITIONS) == expected_edges()


LEGAL_PAIRS = [(source, target) for source, targets in expected_edges().items() for target in targets if target != S.REFUSED or source in {S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}]
ILLEGAL_PAIRS = [
    pair for pair in itertools.product(S, S)
    if pair[1] not in expected_edges()[pair[0]]
]


@pytest.mark.parametrize("source,target", LEGAL_PAIRS)
def test_every_legal_transition(store, source, target):
    job = state_job(store, source)
    when = NOW + timedelta(days=7) if source == S.EXPIRED or target == S.EXPIRED else NOW
    arguments = {}
    role = "gate"
    reason = "RECONCILED"
    if target == S.AUTO_ALLOWED_LOCAL:
        arguments.update(
            new_marking=MarkingClass.RESTRICTED,
            local_authorisation_ref="SYNTHETIC-LOCAL-AUTHORITY",
        )
    elif target == S.AWAITING_OPERATOR:
        arguments["job"] = replace(store.get_job(job.job_id), route=Route.CLOUD)
    elif target == S.APPROVED and source != S.HELD_OFFLINE:
        role = "review-board" if source == S.ESCALATED_REVIEW else "operator"
        reason = "BOARD_APPROVED" if role == "review-board" else "ROUTINE_APPROVED"
        arguments["approver_identity"] = OPERATOR
        if role == "review-board":
            arguments["board_decision_id"] = "SYNTHETIC-BOARD"
    elif target == S.APPROVED:
        arguments["audit_receipt_ref"] = "SYNTHETIC-FRESH-RECEIPT"
    elif target == S.REFUSED:
        role = "review-board" if source == S.ESCALATED_REVIEW else "operator"
        reason = "REFUSED"
        arguments["approver_identity"] = OPERATOR
        if role == "review-board":
            arguments["board_decision_id"] = "SYNTHETIC-BOARD"
    elif target == S.ESCALATED_REVIEW:
        role = "review-board" if source == S.ESCALATED_REVIEW else "operator"
        reason = "REFERRED" if role == "review-board" else "OPERATOR_DOUBT"
        arguments["approver_identity"] = OPERATOR
        if role == "review-board":
            arguments.update(board_decision_id="SYNTHETIC-BOARD", decision_outcome="Refer-Admin")
    elif target == S.WITHDRAWN:
        role, reason = "requester", "WITHDRAWN"
        arguments["requester_identity"] = ACTOR
    elif target == S.COMPLETED:
        reason = "COMPLETED"
    elif target == S.LOGGED and source == S.EXPIRED:
        role = "requester"
        arguments.update(
            changes={}, requester_identity=ACTOR,
            expected_revision=store.get_details(job.job_id)["approval_revision"],
        )
    before = len(store.audit_rows(job.job_id))
    store.transition(
        job.job_id, target, actor_role=role, reason=reason, now=when, **arguments,
    )
    assert store.get_job(job.job_id).status == target
    events = store.audit_rows(job.job_id)[before:]
    assert any(event["from_status"] == source.value and event["to_status"] == target.value for event in events)


@pytest.mark.parametrize("source,target", ILLEGAL_PAIRS)
def test_every_illegal_transition_raises(store, source, target):
    job = state_job(store, source)
    before = store.audit_rows(job.job_id)
    with pytest.raises(ValueError, match="illegal transition"):
        store.transition(
            job.job_id, target, actor_role="gate", reason="RECONCILED", now=NOW,
        )
    assert store.audit_rows(job.job_id) == before
    assert store.get_job(job.job_id).status == source


@pytest.mark.parametrize("change", [
    {"manifest_hash": "b" * 64},
    {"destination_id": "other-destination"},
    {"model": "other-model"},
    {"deployment_type": "other-deployment"},
    {"requested_duration_days": 8},
])
def test_approval_binding_and_controlled_revision(store, change):
    job = approved(store)
    decision = store.current_approval(job.job_id, NOW)
    assert decision is not None
    assert not approval_is_valid(decision, replace(job, **change), NOW)
    revision = store.get_details(job.job_id)["approval_revision"]
    revised = store.revise(
        job.job_id, changes=change, requester_identity=ACTOR,
        expected_revision=revision, now=NOW,
    )
    assert revised.status == S.LOGGED
    assert store.current_approval(job.job_id, NOW) is None
    assert any(row["to_status"] == S.EXPIRED.value for row in store.audit_rows(job.job_id))


@pytest.mark.parametrize("change", [
    {"scope_json": '{"schema_version":1,"release_profile":"other","tool_profile":"none"}'},
    {"policy_version": "different-policy"},
    {"gate_version": "different-profile"},
])
def test_store_binds_fields_beyond_existing_pure_contract(store, change):
    job = approved(store)
    # Simulate an external projection/configuration change. No release permission
    # may survive even though these fields are absent from the minimal Protocol.
    with sqlite3.connect(store.path) as db:
        name, value = next(iter(change.items()))
        db.execute(f"UPDATE jobs SET {name}=? WHERE job_id=?", (value, job.job_id))
    assert store.current_approval(job.job_id, NOW) is None
    assert store.expire_due(NOW) == (job.job_id,)


def test_expiry_exact_boundary_and_no_silent_extension(store):
    job = approved(store, requested_duration_days=3)
    assert store.current_approval(job.job_id, NOW + timedelta(days=3, microseconds=-1))
    assert store.current_approval(job.job_id, NOW + timedelta(days=3)) is None
    assert store.expire_due(NOW + timedelta(days=3)) == (job.job_id,)
    assert store.expire_due(NOW + timedelta(days=4)) == ()
    with pytest.raises(PermissionError):
        store.revise(
            job.job_id, changes={"expiry_utc": NOW + timedelta(days=10)},
            requester_identity=ACTOR,
            expected_revision=store.get_details(job.job_id)["approval_revision"],
            now=NOW + timedelta(days=4),
        )


def test_major_attempt_escalates_and_board_reference_is_required(store):
    job = awaiting(store, requested_duration_days=8)
    assert store.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", NOW,
        approver_identity=OPERATOR,
    ) is None
    assert store.get_job(job.job_id).status == S.ESCALATED_REVIEW
    with pytest.raises(ValueError):
        store.approve(
            job.job_id, "review-board", "BOARD_APPROVED", NOW,
            approver_identity=OPERATOR,
        )
    result = store.approve(
        job.job_id, "review-board", "BOARD_APPROVED", NOW,
        approver_identity=OPERATOR, board_decision_id="SYNTHETIC-BOARD",
        approved_duration_days=8,
    )
    assert result.tier == Tier.AI_CAB
    assert result.expiry_utc == NOW + timedelta(days=8)


def test_missing_endorsement_is_major_and_cannot_be_waived(store):
    job = awaiting(store, use_case_record_ref=None)
    store.escalate(job.job_id, "operator", "MAJOR", NOW, approver_identity=OPERATOR)
    with pytest.raises(PermissionError):
        store.approve(
            job.job_id, "review-board", "BOARD_APPROVED", NOW,
            approver_identity=OPERATOR, board_decision_id="SYNTHETIC-BOARD",
        )


def test_self_approval_and_wrong_role_refused(store):
    job = awaiting(store)
    for role, actor in (("operator", ACTOR), ("gate", OPERATOR), ("review-board", OPERATOR)):
        with pytest.raises(PermissionError):
            store.approve(
                job.job_id, role, "ROUTINE_APPROVED", NOW,
                approver_identity=actor,
            )
    assert store.get_job(job.job_id).status == S.AWAITING_OPERATOR


@pytest.mark.parametrize("enabled,synthetic,major,allowed", [
    (False, True, False, False),
    (True, False, False, False),
    (True, True, True, False),
    (True, True, False, True),
])
def test_pilot_all_three_conditions_and_label(store, enabled, synthetic, major, allowed):
    job = awaiting(store, synthetic=synthetic, source_document_count=6 if major else 5)
    result = PilotAutoApprover(store, enabled).try_approve(job.job_id, NOW)
    assert (result is not None) is allowed
    row = store.get_details(job.job_id)
    if allowed:
        assert row["approver_role"] == PILOT_ROLE
        assert store.audit_rows(job.job_id)[-1]["actor_role"] == PILOT_ROLE
    else:
        assert row["status"] == S.AWAITING_OPERATOR.value


def test_pilot_default_off_and_protected_never_approved(store):
    job = awaiting(store)
    assert PilotAutoApprover(store).try_approve(job.job_id, NOW) is None
    protected = logged(store, route=Route.LOCAL_ONLY, marking_class=MarkingClass.RESTRICTED)
    assert PilotAutoApprover(store, True).try_approve(protected.job_id, NOW) is None
    with pytest.raises(PermissionError):
        logged(store, marking_class=MarkingClass.RESTRICTED)
    with pytest.raises(PermissionError):
        store.approve(
            job.job_id, PILOT_ROLE, "PILOT_APPROVED", NOW,
            approver_identity=OPERATOR,
        )


@pytest.mark.parametrize("changes", [
    {"requested_duration_days": 8}, {"source_word_count": 20_001},
    {"scheduled_or_repeated_batch": True}, {"sensitive_categories_present": True},
    {"operator_doubt": True},
])
def test_pilot_never_approves_other_major_work(store, changes):
    job = awaiting(store, **changes)
    assert PilotAutoApprover(store, True).try_approve(job.job_id, NOW) is None


@pytest.mark.parametrize("summary", [
    "synthetic@example.invalid", "Synthetic reference 1234567",
    "Call 03 1234 5678", "Call +61 3 1234 5678", "Call 555-123-4567",
    "\x1b[31mSynthetic", "", " " * 10,
])
def test_summary_guard_rejects(store, summary):
    with pytest.raises(ValueError):
        summary_guard(summary)
    with pytest.raises(ValueError):
        logged(store, problem_summary_deid=summary)
    assert store.list_jobs() == ()


def test_summary_guard_is_not_claimed_to_certify_context(store):
    assert summary_guard("A synthetic account-holder has a complex request.")
    job = logged(store)
    assert store.get_details(job.job_id)["problem_summary_deid"] == intake().problem_summary_deid


def test_counts_and_scope_cannot_hide_values(store):
    for changes in (
        {"pii_counts_json": '{"PERSON":"SYNTHETIC_VALUE_SENTINEL"}'},
        {"scope_json": '{"source_text":"SYNTHETIC_SOURCE_SENTINEL"}'},
        {"data_source_ids_json": '["inputs/source.txt"]'},
    ):
        with pytest.raises(ValueError):
            logged(store, **changes)
    with pytest.raises(TypeError):
        store.log_job({"source_text": "SYNTHETIC_SOURCE_SENTINEL"})


def test_audit_no_content_or_identity_and_history_immutable(store):
    summary = "SYNTHETIC_SUMMARY_SENTINEL"
    job = approved(store, problem_summary_deid=summary)
    serialized = json.dumps(store.audit_rows(job.job_id))
    for forbidden in (
        summary, ACTOR, OPERATOR, "problem_summary_deid", "pii_counts_json",
        "source_text", "token", "requester_identity", "decision_reason",
    ):
        assert forbidden not in serialized
    with sqlite3.connect(store.path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        assert "requester_identity" not in columns
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM audit")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE approvals SET expiry_utc='changed'")
    assert "requester_identity" not in store.get_details(job.job_id)


def test_manual_peer_and_sensitive_confirmation_bound_to_revision(store):
    job = logged(
        store, marking_class=MarkingClass.SENSITIVE,
        interim_manual_assisted=True,
    )
    with pytest.raises(PermissionError):
        store.submit_for_approval(job, now=NOW)
    store.record_peer_check(
        job.job_id, checker_identity="synthetic-peer", revision=1,
        manifest_hash="a" * 64, now=NOW,
    )
    with pytest.raises(PermissionError):
        store.submit_for_approval(job, now=NOW)
    store.confirm_payload(
        job.job_id, requester_identity=ACTOR, revision=1,
        manifest_hash="a" * 64, now=NOW,
    )
    job = store.submit_for_approval(store.get_job(job.job_id), now=NOW)
    with pytest.raises(PermissionError):
        store.approve(
            job.job_id, "operator", "ROUTINE_APPROVED", NOW,
            approver_identity="synthetic-peer",
        )
    result = store.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", NOW,
        approver_identity=OPERATOR,
    )
    assert result is not None
    revised = store.revise(
        job.job_id, changes={"manifest_hash": "b" * 64},
        requester_identity=ACTOR, expected_revision=1, now=NOW,
    )
    assert revised.sensitive_path is None
    with pytest.raises(PermissionError):
        store.submit_for_approval(revised, now=NOW)
    with pytest.raises(ValueError):
        store.confirm_payload(
            job.job_id, requester_identity=ACTOR, revision=1,
            manifest_hash="a" * 64, now=NOW,
        )


def test_generic_confirm_and_generic_decision_edits_refused(store):
    with pytest.raises(PermissionError):
        logged(store, sensitive_path=SensitivePath.CLOUD_CONFIRMED)
    job = logged(store)
    for field in ("expiry_utc", "status", "approver_role", "peer_checked_by_role"):
        with pytest.raises(PermissionError):
            store.revise(
                job.job_id, changes={field: "SYNTHETIC"},
                requester_identity=ACTOR, expected_revision=1, now=NOW,
            )


def test_offline_invalidates_effective_permission_and_stale_restore_fails(store):
    job = approved(store)
    store.hold_offline(job.job_id, now=NOW)
    assert store.current_approval(job.job_id, NOW) is None
    with pytest.raises(ValueError):
        store.reconcile(job.job_id, target=S.APPROVED, now=NOW)
    with pytest.raises(PermissionError):
        store.reconcile(
            job.job_id, target=S.APPROVED, audit_receipt_ref="SYNTHETIC-RECEIPT",
            now=NOW + timedelta(days=7),
        )


def test_new_protected_evidence_closes_cloud_permission(store):
    job = approved(store)
    result = store.auto_allow_local(
        job.job_id, new_marking=MarkingClass.RESTRICTED,
        local_authorisation_ref="SYNTHETIC-LOCAL-AUTHORITY", now=NOW,
    )
    assert result.route == Route.LOCAL_ONLY
    assert result.status == S.AUTO_ALLOWED_LOCAL
    assert store.current_approval(job.job_id, NOW) is None


def test_local_journal_sync_is_idempotent_and_cloud_replay_denied(tmp_path):
    store = SqliteRegister(
        tmp_path / "register.db", requester_identity=ACTOR,
        journal_local_authorisation_ref="SYNTHETIC-LOCAL-AUTHORITY",
    )
    local = replace(
        JobRecord("JOB-2025-0042", NOW, S.AUTO_ALLOWED_LOCAL, Route.LOCAL_ONLY),
        marking_class=MarkingClass.INTERNAL,
    )
    entry = JournalEntry("1" * 32, NOW, local)
    assert store.sync_journal((entry,)) == (entry.entry_id,)
    rows = store.list_jobs()
    assert len(rows) == 1
    assert rows[0]["job_id"] != local.job_id
    before = store.audit_rows(rows[0]["job_id"])
    assert store.sync_journal((entry,)) == (entry.entry_id,)
    assert store.audit_rows(rows[0]["job_id"]) == before
    with pytest.raises(ValueError):
        store.sync_journal((replace(entry, occurred_utc=NOW + timedelta(seconds=1)),))
    cloud = JournalEntry("2" * 32, NOW, JobRecord(
        "JOB-2026-9999", NOW, S.APPROVED, Route.CLOUD,
        MarkingClass.INTERNAL, "a" * 64, "approved-destination", "model", "cloud-demo",
    ))
    with pytest.raises(PermissionError):
        store.sync_journal((cloud,))


def local(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BUSINESS_TZ)


@pytest.mark.parametrize("start,end,hours", [
    (local(2026, 10, 23, 16), local(2026, 10, 26, 16), 8),
    (local(2026, 10, 24, 12), local(2026, 10, 26, 17), 8),
    (local(2026, 10, 22, 8), local(2026, 10, 22, 17), 8),
    (local(2026, 10, 2, 16), local(2026, 10, 5, 16), 8),
    (local(2026, 4, 3, 16), local(2026, 4, 6, 16), 8),
    (local(2026, 10, 23, 16, 30), local(2026, 10, 26, 10), 1.5),
])
def test_business_latency_math(start, end, hours):
    assert business_hours_between(start, end) == hours


def test_latency_holiday_and_nearest_rank():
    assert business_hours_between(
        local(2026, 10, 23, 16), local(2026, 10, 27, 16),
        frozenset({date(2026, 10, 26)}),
    ) == 8
    assert latency_stats([1, 2, 3, 4], 1) == {"n": 4, "median": 2.5, "p90": 4}
    assert latency_stats(list(range(1, 11)), 1)["p90"] == 9
    assert latency_stats([1], 5)["suppressed"]


def test_store_report_starts_at_complete_submission_not_creation(store):
    submitted = local(2026, 10, 23, 16).astimezone(UTC)
    decision = local(2026, 10, 26, 16).astimezone(UTC)
    job = logged(store)
    store.submit_for_approval(job, now=submitted)
    store.approve(
        job.job_id, "operator", "ROUTINE_APPROVED", decision,
        approver_identity=OPERATOR,
    )
    report = store.report(unsuppressed_synthetic=True)
    month = report["months"][0]
    assert month["approval_latency_business_hours"] == {"n": 1, "median": 8, "p90": 8}
    assert month["operator_sla_breaches"] == 0
    assert month["counts"] == [{"route": Route.CLOUD.value, "status": S.APPROVED.value, "count": 1}]
    assert store.report()["months"][0]["counts"][0]["count"] is None


def test_report_escalation_is_operator_decision_not_board_latency(store):
    job = awaiting(store, source_document_count=6)
    escalated = NOW + timedelta(hours=1)
    decided = NOW + timedelta(hours=3)
    store.escalate(
        job.job_id, "operator", "MAJOR", escalated,
        approver_identity=OPERATOR,
    )
    store.approve(
        job.job_id, "review-board", "BOARD_APPROVED", decided,
        approver_identity="synthetic-board-member", board_decision_id="SYNTHETIC-BOARD",
    )
    month = store.report(unsuppressed_synthetic=True)["months"][0]
    assert month["approval_latency_business_hours"]["median"] == 3
    assert month["operator_decision_latency_business_hours"]["median"] == 1
    assert month["board_decision_latency_business_hours"]["median"] == 2


def test_calendar_version_cannot_change_meaning(store):
    with pytest.raises(ValueError):
        SqliteRegister(
            store.path, holidays=frozenset({date(2026, 10, 26)}),
            calendar_version="NO-HOLIDAYS-SUPPLIED",
        )


def test_cli_happy_path_safe_show_and_export(store, tmp_path, capsys):
    # Current real UTC intake ensures main()'s current-time decision is valid.
    job = store.log_job(intake(), now=datetime.now(UTC))
    store.submit_for_approval(job)
    common = ["--db", str(store.path)]
    assert main(common + ["list", "--status", "Awaiting-Operator"]) == 0
    assert job.job_id in capsys.readouterr().out
    assert main(common + ["show", job.job_id]) == 0
    shown = capsys.readouterr().out
    assert "Summary" in shown and "Operator due" in shown
    assert ACTOR not in shown and "requester_identity" not in shown
    assert main(common + [
        "approve", job.job_id, "--as-role", "operator",
        "--identity", OPERATOR, "--reason", "ROUTINE_APPROVED",
    ]) == 0
    output = capsys.readouterr().out
    assert "Approved" in output and "explicitly re-run" in output
    assert main(common + ["report", "--unsuppressed-synthetic"]) == 0
    assert "approval_latency_business_hours" in capsys.readouterr().out
    export = tmp_path / "controlled.csv"
    assert main(common + ["export-interim", "--output", str(export)]) == 0
    capsys.readouterr()
    with export.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["interim_reference"].startswith("DEMO-INTERIM-")
    assert "requester_identity" not in rows[0]
    assert ACTOR not in export.read_text(encoding="utf-8")


def test_cli_unsafe_reason_not_echoed(store, capsys):
    job = awaiting(store)
    assert main([
        "--db", str(store.path), "approve", job.job_id,
        "--reason", "SYNTHETIC_UNSAFE@example.invalid",
    ]) == 2
    captured = capsys.readouterr()
    assert "SYNTHETIC_UNSAFE" not in captured.out + captured.err


def test_day_first_local_display():
    assert display_time("2026-10-22T10:00:00.000000Z") == "22/10/2026 11:00:00 WEST"
    assert display_time("2026-12-22T10:00:00.000000Z") == "22/12/2026 10:00:00 WET"
