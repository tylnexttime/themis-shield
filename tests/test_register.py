"""Synthetic tests for journal, approval, major classification and business SLA."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from zoneinfo import ZoneInfo

import pytest

from themis_shield.detectors.markings import MarkingClass
from themis_shield.register import (
    ApprovalRecord,
    HttpRegisterClient,
    JobRecord,
    JobStatus,
    LocalJournal,
    approval_is_valid,
)
from themis_shield.register.client import (
    DEFAULT_VALIDITY_DAYS,
    OPERATOR_BUSINESS_HOURS,
    SensitivePath,
    ROUTINE_MAX_DOCUMENTS,
    ROUTINE_MAX_WORDS,
    Tier,
    is_major,
    operator_due_utc,
)
from themis_shield.routing import Route

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
BUSINESS_TZ = ZoneInfo("Europe/Lisbon")


def _job(**changes):
    return replace(
        JobRecord(
            "JOB-2026-0001", NOW, JobStatus.APPROVED, Route.CLOUD,
            MarkingClass.INTERNAL, "a" * 64, "anthropic-direct",
            "approved-model", "claude-managed",
        ),
        **changes,
    )


def _approval(**changes):
    return replace(
        ApprovalRecord(
            "JOB-2026-0001", "a" * 64, "anthropic-direct",
            NOW, NOW + timedelta(days=7), "approved-model", "claude-managed",
        ),
        **changes,
    )


def _local(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BUSINESS_TZ)


def test_decided_constants():
    assert ROUTINE_MAX_DOCUMENTS == 5
    assert ROUTINE_MAX_WORDS == 20_000
    assert DEFAULT_VALIDITY_DAYS == 7
    assert OPERATOR_BUSINESS_HOURS == 8


@pytest.mark.parametrize(
    "submitted, expected",
    [
        (_local(2026, 10, 23, 16), _local(2026, 10, 26, 16)),
        (_local(2026, 10, 24, 12), _local(2026, 10, 26, 17)),
        (_local(2026, 10, 22, 8), _local(2026, 10, 22, 17)),
    ],
    ids=["friday-1600", "saturday", "weekday-0800"],
)
def test_operator_due_business_hour_examples(submitted, expected):
    submitted_utc = submitted.astimezone(timezone.utc)
    before = submitted_utc
    due = operator_due_utc(submitted_utc)
    assert due == expected.astimezone(timezone.utc)
    assert due.tzinfo is timezone.utc
    assert submitted_utc == before


def test_operator_due_skips_supplied_holiday():
    holidays = frozenset({date(2026, 10, 26)})
    due = operator_due_utc(
        _local(2026, 10, 23, 16).astimezone(timezone.utc), holidays
    )
    assert due == _local(2026, 10, 27, 16).astimezone(timezone.utc)
    assert holidays == frozenset({date(2026, 10, 26)})


def test_operator_due_skips_holiday_submission_and_weekend():
    due = operator_due_utc(
        _local(2026, 10, 23, 10).astimezone(timezone.utc),
        frozenset({date(2026, 10, 23)}),
    )
    assert due == _local(2026, 10, 26, 17).astimezone(timezone.utc)


@pytest.mark.parametrize(
    "submitted, expected",
    [
        (_local(2026, 10, 22, 9), _local(2026, 10, 22, 17)),
        (_local(2026, 10, 22, 17), _local(2026, 10, 23, 17)),
        (_local(2026, 10, 22, 18), _local(2026, 10, 23, 17)),
        (_local(2026, 10, 23, 16, 30), _local(2026, 10, 26, 16, 30)),
    ],
)
def test_operator_due_open_close_and_partial_hours(submitted, expected):
    assert operator_due_utc(submitted) == expected.astimezone(timezone.utc)


@pytest.mark.parametrize(
    "submitted, expected, elapsed_hours",
    [
        (_local(2026, 3, 27, 16), _local(2026, 3, 30, 16), 71),
        (_local(2026, 10, 23, 16), _local(2026, 10, 26, 16), 73),
    ],
    ids=["dst-start", "dst-end"],
)
def test_dst_changes_utc_elapsed_not_wall_clock_business_hours(
    submitted, expected, elapsed_hours,
):
    # No holiday calendar is supplied: isolate the synthetic DST contract.
    assert submitted.utcoffset() != expected.utcoffset()
    submitted_utc = submitted.astimezone(timezone.utc)
    due = operator_due_utc(submitted_utc)
    assert due == expected.astimezone(timezone.utc)
    assert due.astimezone(BUSINESS_TZ).hour == 16
    assert due - submitted_utc == timedelta(hours=elapsed_hours)


def test_operator_due_rejects_naive_submission():
    with pytest.raises(ValueError, match="timezone-aware"):
        operator_due_utc(NOW.replace(tzinfo=None))


def test_operator_due_rejects_non_date_holiday():
    with pytest.raises(ValueError, match="local dates"):
        operator_due_utc(NOW, frozenset({NOW}))


@pytest.mark.parametrize(
    "documents, words, expected",
    [
        (0, 0, False),
        (5, 20_000, False),
        (6, 20_000, True),
        (5, 20_001, True),
        (6, 20_001, True),
    ],
)
def test_major_size_boundaries(documents, words, expected):
    job = _job(source_document_count=documents, source_word_count=words)
    assert is_major(job) is expected
    assert job.source_document_count == documents
    assert job.source_word_count == words


@pytest.mark.parametrize("days, expected", [(1, False), (7, False), (8, True)])
def test_major_explicit_duration(days, expected):
    assert is_major(_job(requested_duration_days=days)) is expected


@pytest.mark.parametrize(
    "flag",
    [
        "new_data_source",
        "new_cloud_configuration",
        "scheduled_or_repeated_batch",
        "restricted_contract_scope",
        "no_matching_approved_use_case",
        "state_changing_register_act",
        "sensitive_categories_present",
        "operator_doubt",
    ],
)
def test_each_available_major_flag(flag):
    assert is_major(_job(**{flag: True}))


@pytest.mark.parametrize("count, expected", [(0, False), (2, False), (3, True)])
def test_repeat_override_refusal_threshold(count, expected):
    assert is_major(
        _job(repeated_overrides_or_refusals_30_days=count)
    ) is expected


def test_official_sensitive_itself_is_not_major():
    job = _job(
        marking_class=MarkingClass.SENSITIVE,
        sensitive_path=SensitivePath.CLOUD_CONFIRMED,
        source_document_count=5,
        source_word_count=20_000,
    )
    assert not is_major(job)
    assert approval_is_valid(_approval(), job, NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_document_count": -1},
        {"source_document_count": True},
        {"source_word_count": 1.5},
        {"requested_duration_days": 0},
        {"requested_duration_days": True},
        {"requested_duration_days": "8"},
        {"repeated_overrides_or_refusals_30_days": -1},
        {"operator_doubt": "false"},
    ],
)
def test_invalid_major_inputs_are_not_silently_routine(changes):
    with pytest.raises(ValueError):
        is_major(_job(**changes))
    assert not approval_is_valid(_approval(), _job(**changes), NOW)


def test_valid_approval_is_pure():
    job, approval = _job(), _approval()
    assert approval_is_valid(approval, job, NOW)
    assert job == _job() and approval == _approval()


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": "JOB-2026-0002"},
        {"manifest_hash": "b" * 64},
        {"destination_id": "other-destination"},
        {"model": "other-model"},
        {"deployment_type": "other-deployment"},
    ],
)
def test_scope_change_invalidates_approval(changes):
    assert not approval_is_valid(_approval(), _job(**changes), NOW)


@pytest.mark.parametrize("status", list(JobStatus))
def test_only_approved_job_status_is_valid(status):
    assert approval_is_valid(_approval(), _job(status=status), NOW) is (
        status == JobStatus.APPROVED
    )


def test_refused_approval_is_invalid():
    assert not approval_is_valid(
        _approval(status=JobStatus.REFUSED), _job(), NOW
    )


@pytest.mark.parametrize(
    "current",
    [NOW - timedelta(microseconds=1), NOW + timedelta(days=7)],
)
def test_approval_time_boundaries(current):
    assert not approval_is_valid(_approval(), _job(), current)


def test_excessive_default_lifetime_is_invalid_even_for_cab():
    for tier in Tier:
        assert not approval_is_valid(
            _approval(expiry_utc=NOW + timedelta(days=8), tier=tier),
            _job(),
            NOW,
        )


def test_eight_day_request_requires_board_and_caps_expiry():
    job = _job(requested_duration_days=8)
    longer = _approval(expiry_utc=NOW + timedelta(days=8))
    assert is_major(job)
    assert not approval_is_valid(longer, job, NOW)
    assert approval_is_valid(replace(longer, tier=Tier.AI_CAB), job, NOW)
    assert not approval_is_valid(
        replace(longer, tier=Tier.AI_CAB, expiry_utc=NOW + timedelta(days=9)),
        job,
        NOW,
    )


def test_board_may_grant_shorter_than_requested():
    assert approval_is_valid(
        _approval(tier=Tier.AI_CAB),
        _job(requested_duration_days=8),
        NOW,
    )


def test_shorter_request_caps_approval():
    job = _job(requested_duration_days=3)
    assert not approval_is_valid(_approval(), job, NOW)
    assert approval_is_valid(
        _approval(expiry_utc=NOW + timedelta(days=3)), job, NOW
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_document_count": 6},
        {"source_word_count": 20_001},
        {"scheduled_or_repeated_batch": True},
        {"operator_doubt": True},
    ],
)
def test_major_approval_requires_cab(changes):
    job = _job(**changes)
    assert not approval_is_valid(_approval(), job, NOW)
    assert approval_is_valid(_approval(tier=Tier.AI_CAB), job, NOW)


def test_sensitive_cloud_without_confirm_is_invalid():
    assert not approval_is_valid(
        _approval(), _job(marking_class=MarkingClass.SENSITIVE), NOW
    )


def test_sensitive_confirmation_is_still_bound_to_manifest():
    job = _job(
        marking_class=MarkingClass.SENSITIVE,
        sensitive_path=SensitivePath.CLOUD_CONFIRMED,
        manifest_hash="b" * 64,
    )
    assert not approval_is_valid(_approval(), job, NOW)


def test_local_sensitive_path_cannot_authorise_cloud():
    assert not approval_is_valid(
        _approval(),
        _job(marking_class=MarkingClass.SENSITIVE, sensitive_path=SensitivePath.LOCAL),
        NOW,
    )


def test_naive_time_is_invalid():
    assert not approval_is_valid(_approval(), _job(), NOW.replace(tzinfo=None))


def test_missing_approval_is_invalid():
    assert not approval_is_valid(None, _job(), NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"route": Route.LOCAL_ONLY},
        {"marking_class": MarkingClass.RESTRICTED},
        {"marking_class": MarkingClass.SECRET},
        {"marking_class": MarkingClass.TOP_SECRET},
    ],
)
def test_local_or_protected_cannot_use_cloud_approval(changes):
    assert not approval_is_valid(_approval(), _job(**changes), NOW)


def test_journal_is_append_only_and_readable_for_sync(tmp_path):
    journal = LocalJournal(tmp_path / "offline.jsonl")
    assert journal.read_entries() == ()
    local = _job(
        status=JobStatus.AUTO_ALLOWED_LOCAL, route=Route.LOCAL_ONLY,
        manifest_hash=None, destination_id=None, model=None, deployment_type=None,
    )
    first = journal.append(local, NOW)
    prefix = journal.path.read_bytes()
    second = journal.append(_job(status=JobStatus.HELD_OFFLINE), NOW)
    assert journal.path.read_bytes().startswith(prefix)
    assert journal.read_entries() == (first, second)
    assert first.entry_id != second.entry_id
    assert journal.path.read_bytes().count(b"\n") == 2
    unchanged = journal.path.read_bytes()
    journal.read_entries()
    assert journal.path.read_bytes() == unchanged


def test_journal_schema_has_no_source_or_summary(tmp_path):
    journal = LocalJournal(tmp_path / "offline.jsonl")
    journal.append(_job(), NOW)
    row = json.loads(journal.path.read_text())
    assert set(row) == {"schema_version", "entry_id", "occurred_utc", "job"}
    assert row["schema_version"] == 2
    assert set(row["job"]) == {
        "job_id", "created_utc", "status", "route", "marking_class",
        "manifest_hash", "destination_id", "model", "deployment_type",
        "source_document_count", "source_word_count", "requested_duration_days",
        "sensitive_path", "new_data_source", "new_cloud_configuration",
        "scheduled_or_repeated_batch", "restricted_contract_scope",
        "no_matching_approved_use_case", "state_changing_register_act",
        "repeated_overrides_or_refusals_30_days",
        "sensitive_categories_present", "operator_doubt",
    }
    with pytest.raises(TypeError):
        journal.append({"source_text": "SYNTHETIC_SOURCE_SENTINEL"}, NOW)
    assert b"SYNTHETIC_SOURCE_SENTINEL" not in journal.path.read_bytes()


def test_journal_round_trips_duration_path_and_major_flags(tmp_path):
    journal = LocalJournal(tmp_path / "offline.jsonl")
    job = _job(
        marking_class=MarkingClass.SENSITIVE,
        sensitive_path=SensitivePath.CLOUD_CONFIRMED,
        requested_duration_days=8,
        source_document_count=6,
        source_word_count=20_001,
        new_data_source=True,
        scheduled_or_repeated_batch=True,
        repeated_overrides_or_refusals_30_days=3,
    )
    entry = journal.append(job, NOW)
    restored = journal.read_entries()[0]
    assert restored == entry
    assert restored.job == job
    assert is_major(restored.job)


def test_version_one_journal_remains_readable_without_rewrite(tmp_path):
    path = tmp_path / "offline.jsonl"
    row = {
        "schema_version": 1,
        "entry_id": "1" * 32,
        "occurred_utc": NOW.isoformat(),
        "job": {
            "job_id": "JOB-2026-0001",
            "created_utc": NOW.isoformat(),
            "status": JobStatus.APPROVED.value,
            "route": Route.CLOUD.value,
            "marking_class": MarkingClass.INTERNAL.name,
            "manifest_hash": "a" * 64,
            "destination_id": "anthropic-direct",
            "model": "approved-model",
            "deployment_type": "claude-managed",
        },
    }
    raw = (json.dumps(row) + "\n").encode("ascii")
    path.write_bytes(raw)
    journal = LocalJournal(path)
    assert journal.read_entries()[0].job == _job()
    assert path.read_bytes() == raw
    journal.append(_job(requested_duration_days=8), NOW)
    assert path.read_bytes().startswith(raw)
    assert len(journal.read_entries()) == 2


def test_journal_rejects_prose_in_alias(tmp_path):
    journal = LocalJournal(tmp_path / "offline.jsonl")
    with pytest.raises(ValueError):
        journal.append(_job(destination_id="synthetic source text"), NOW)
    assert not journal.path.exists()


@pytest.mark.parametrize("raw", [b"{", b"{}\n", b'{"source_text":"synthetic"}\n'])
def test_corrupt_journal_is_not_skipped_or_overwritten(tmp_path, raw):
    path = tmp_path / "offline.jsonl"
    path.write_bytes(raw)
    journal = LocalJournal(path)
    with pytest.raises(ValueError):
        journal.read_entries()
    with pytest.raises(ValueError):
        journal.append(_job(), NOW)
    assert path.read_bytes() == raw


def test_journal_rejects_unknown_fields(tmp_path):
    journal = LocalJournal(tmp_path / "offline.jsonl")
    journal.append(_job(), NOW)
    row = json.loads(journal.path.read_text())
    row["job"]["source_text"] = "SYNTHETIC_ONLY"
    journal.path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="recovery"):
        journal.read_entries()


def test_journal_requests_fsync(tmp_path, monkeypatch):
    calls = []
    original = os.fsync

    def tracked(descriptor):
        calls.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", tracked)
    LocalJournal(tmp_path / "offline.jsonl").append(_job(), NOW)
    assert len(calls) == 1


def test_http_register_client_is_non_networking_stub():
    with pytest.raises(NotImplementedError):
        HttpRegisterClient("proposed-register").get_job("JOB-2026-0001")
