"""Known-miss register and final sweep (v0.3). Synthetic values only."""

from __future__ import annotations

from datetime import timedelta
import json
import sqlite3

import pytest

from themis_shield.catalogues.cli import kmr_main
from themis_shield.catalogues.gate import KnownMissSweep, final_sweep, outbound_leaves
from themis_shield.catalogues.known_miss import (
    MIN_FINGERPRINT_BITS, KnownMissRegister, LowEntropyValue, check_fingerprint_value, check_pattern,
    estimate_bits, normalise_fingerprint_value,
)
from themis_shield.catalogues.store import CatalogueError, CatalogueUnavailable
from themis_shield.cloud.openai_compat import canonical
from themis_shield.dpapi import NullProtector
from themis_shield.types import Decision, DecisionResult, ReasonCode

from catalogue_fakes import MISSED, known_miss, protector, sweeping_harness
from router_fakes import NOW, request


def in_sweep(register, entry):
    register.transition(entry, "verified", actor_identity="synthetic-operator-b")
    register.transition(entry, "in_sweep", actor_identity="synthetic-operator-b")
    return entry


def add_missed(register, value=MISSED, entity="INTERNAL_ID"):
    return in_sweep(register, register.add_fingerprint(
        value, entity_type=entity, source="operator", proposed_by="synthetic-operator-a",
    ))


# -- fingerprints: entropy, normalisation, never storing the value ------------------------------------------------


@pytest.mark.parametrize("value", [
    "12/03/1984", "1984-03-12", "12 March 1984", "0412 345 678", "+61 2 9876 5432",
    "jane.example@example.org", "S1234567", "Bartholomew Smithfield", "CASE-2024-ABX-99812",
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
])
def test_low_entropy_values_are_refused_with_rule_advice(value):
    with pytest.raises(LowEntropyValue) as refused:
        check_fingerprint_value(value)
    message = str(refused.value)
    assert "rule" in message and "key" in message
    assert value not in message


@pytest.mark.parametrize("value", [MISSED, "k7q2m9x4p1z8w3n6", "ref 7f3e9a1c0b4d8e2f6a5c"])
def test_high_entropy_values_are_accepted(value):
    normalised = check_fingerprint_value(value)
    assert estimate_bits(normalised) >= MIN_FINGERPRINT_BITS


def test_normalisation_ignores_case_separators_and_invisible_characters():
    assert normalise_fingerprint_value("qx7z k9pw​4m2r-t8vb") == normalise_fingerprint_value(MISSED)
    assert normalise_fingerprint_value("ＱＸ７Ｚ-K9PW-4M2R-T8VB") == normalise_fingerprint_value(MISSED)


def test_fingerprint_value_is_never_stored(tmp_path):
    register = known_miss(tmp_path)
    add_missed(register)
    raw = (tmp_path / "known-miss.db").read_bytes()
    for form in (MISSED, normalise_fingerprint_value(MISSED), MISSED.lower(), "QX7Z", "T8VB"):
        assert form.encode() not in raw and form.encode("utf-16-le") not in raw
    assert all("QX7Z" not in json.dumps(row) for row in register.entries())


def test_fingerprint_needs_the_protected_key(tmp_path):
    register = known_miss(tmp_path, with_protector=False)
    with pytest.raises(CatalogueUnavailable):
        register.add_fingerprint(MISSED, entity_type="INTERNAL_ID", source="operator", proposed_by="a")


def test_null_protector_needs_the_insecure_flag(tmp_path):
    with pytest.raises(CatalogueError):
        KnownMissRegister(tmp_path / "k.db", protector=NullProtector(), create=True)
    KnownMissRegister(tmp_path / "k.db", protector=NullProtector(), create=True, insecure_test_only=True)


# -- patterns ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["Jane Citizen", "on the night shift", "", "a|", "(", "x" * 600])
def test_patterns_must_generalise_and_compile(pattern):
    with pytest.raises(CatalogueError):
        check_pattern(pattern)


def test_pattern_entries_store_shapes(tmp_path):
    register = known_miss(tmp_path)
    entry = register.add_pattern(
        "rule", r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b", entity_type="INTERNAL_ID", source="evaluation",
        proposed_by="synthetic-operator-a",
    )
    assert register.entry(entry)["pattern"].startswith(r"\bQX")
    with pytest.raises(CatalogueError):
        register.add_pattern("rule", r"\d{1,9}", entity_type="NOT_A_LABEL", source="operator", proposed_by="a")
    with pytest.raises(CatalogueError):
        register.add_pattern("rule", r"\d{1,9}", entity_type="PERSON", source="rumour", proposed_by="a")


# -- lifecycle and append-only history ---------------------------------------------------------------------------


def test_lifecycle_needs_a_second_person_and_a_regression_case(tmp_path):
    register = known_miss(tmp_path)
    entry = register.add_pattern(
        "context", r"the only \w{1,30} on the night shift", entity_type="QUASI_ID", source="peer_check",
        proposed_by="synthetic-operator-a", job_id="JOB-2026-0001",
    )
    with pytest.raises(CatalogueError, match="other than its proposer"):
        register.transition(entry, "verified", actor_identity="synthetic-operator-a")
    with pytest.raises(CatalogueError):
        register.transition(entry, "in_sweep", actor_identity="synthetic-operator-b")
    in_sweep(register, entry)
    with pytest.raises(CatalogueError, match="release tag"):
        register.transition(entry, "built_in", actor_identity="synthetic-operator-b")
    with pytest.raises(CatalogueError, match="regression case"):
        register.transition(entry, "built_in", actor_identity="synthetic-operator-b", release_tag="v0.3.0")
    stub = register.regression_stub(entry, actor_identity="synthetic-operator-b")
    assert stub["text"] == "" and stub["entry_id"] == entry and stub["synthetic"] is True
    register.transition(entry, "built_in", actor_identity="synthetic-operator-b", release_tag="v0.3.0")
    assert register.entry(entry)["status"] == "built_in"
    assert register.entry(entry)["regression_case_id"] == stub["case_id"]
    assert register.snapshot().rules == ()  # built_in entries are no longer swept
    with pytest.raises(CatalogueError):
        register.transition(entry, "rejected", actor_identity="synthetic-operator-b")


@pytest.mark.parametrize("table", ["kmr_entries", "kmr_events", "kmr_meta"])
def test_history_is_append_only(tmp_path, table):
    register = known_miss(tmp_path)
    add_missed(register)
    with sqlite3.connect(tmp_path / "known-miss.db") as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(f"UPDATE {table} SET rowid = rowid")


def test_only_in_sweep_entries_are_swept(tmp_path):
    register = known_miss(tmp_path)
    proposed = register.add_fingerprint(
        MISSED, entity_type="INTERNAL_ID", source="operator", proposed_by="synthetic-operator-a",
    )
    leaves = [(("messages", 0, "content"), f"Reference {MISSED} again.")]
    assert register.snapshot().sweep(leaves) == ()
    in_sweep(register, proposed)
    hits = register.snapshot().sweep(leaves)
    assert [hit.entry_id for hit in hits] == [proposed]
    assert leaves[0][1][hits[0].start:hits[0].end] == MISSED


def test_key_rotation_is_explicit_and_invalidates_fingerprints(tmp_path):
    register = known_miss(tmp_path)
    entry = add_missed(register)
    assert register.fingerprints_invalidated_by_rotation() == 1
    with pytest.raises(CatalogueError, match="confirm"):
        register.rotate_key(actor_identity="synthetic-operator-b", confirm_invalidates=0)
    assert register.rotate_key(actor_identity="synthetic-operator-b", confirm_invalidates=1) == 1
    assert register.entry(entry)["status"] == "rejected"
    assert register.snapshot().fingerprints == {}
    with sqlite3.connect(tmp_path / "known-miss.db") as db:
        assert db.execute("SELECT COUNT(*) FROM kmr_keys").fetchone()[0] == 1
    again = add_missed(register)
    assert again != entry and len(register.snapshot().fingerprints) == 1


# -- the sweep ---------------------------------------------------------------------------------------------------


def test_sweep_matches_windows_on_the_detection_view_and_masks_placeholders(tmp_path):
    register = known_miss(tmp_path)
    fingerprint_entry = add_missed(register)
    rule_entry = in_sweep(register, register.add_pattern(
        "rule", r"[0-9a-f]{6}", entity_type="INTERNAL_ID", source="operator",
        proposed_by="synthetic-operator-a",
    ))
    snapshot = register.snapshot()
    text = "See ⟦P:3fa9c1⟧ and ref:qx7z k9pw‍ 4m2r/t8vb, done."
    hits = snapshot.sweep([(("messages", 0, "content"), text)])
    assert [hit.entry_id for hit in hits] == [fingerprint_entry]
    assert text[hits[0].start:hits[0].end].startswith("qx7z")
    assert rule_entry not in {hit.entry_id for hit in hits}  # never a placeholder's own hex


def test_rule_sees_through_invisible_characters(tmp_path):
    register = known_miss(tmp_path)
    entry = in_sweep(register, register.add_pattern(
        "rule", r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b", entity_type="INTERNAL_ID", source="operator",
        proposed_by="synthetic-operator-a",
    ))
    text = "Reference QX7​Z-K9PW."
    hits = register.snapshot().sweep([(("stop",), text)])
    assert [(hit.entry_id, text[hit.start:hit.end]) for hit in hits] == [(entry, "QX7​Z-K9PW")]


def test_outbound_leaves_cover_messages_tools_and_decoded_arguments():
    body = {
        "model": "example-cloud-model",
        "messages": [{"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "write_local", "arguments": canonical({"name": MISSED}).decode()},
        }]}],
    }
    leaves = dict(outbound_leaves(body))
    assert MISSED in leaves[("messages", 0, "tool_calls", 0, "function", "arguments", "decoded", "name")]
    assert "example-cloud-model" not in leaves.values()


class _Router:
    def __init__(self, sweeper, now=NOW):
        self.known_miss, self.clock = sweeper, lambda: now


def test_final_sweep_only_moves_release_to_ask(tmp_path):
    register = known_miss(tmp_path)
    add_missed(register)
    raw = canonical({"messages": [{"role": "user", "content": f"Ref {MISSED}"}]})
    sweeper = KnownMissSweep(register, timedelta(days=7))
    result = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert result.decision == Decision.ASK and ReasonCode.KNOWN_MISS_HIT in result.reasons
    asked = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.ASK, (ReasonCode.SENSITIVE,)))
    assert asked.decision == Decision.ASK and asked.reasons[0] == ReasonCode.SENSITIVE
    clean = canonical({"messages": [{"role": "user", "content": "Nothing to see."}]})
    unchanged = final_sweep(_Router(sweeper), clean, DecisionResult(Decision.RELEASE, ()))
    assert unchanged.decision == Decision.RELEASE and unchanged.sweep.status == "clear"
    assert final_sweep(_Router(None), raw, DecisionResult(Decision.RELEASE, ())) == DecisionResult(
        Decision.RELEASE, (),
    )


def central_export(tmp_path, days_old, *, name="central"):
    """A register elsewhere (the source), exported ``days_old`` days ago with one in-sweep rule."""
    source = KnownMissRegister(
        tmp_path / f"{name}-{days_old}.db", protector=protector(),
        clock=lambda: NOW - timedelta(days=days_old), create=True,
    )
    in_sweep(source, source.add_pattern(
        "rule", r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b", entity_type="INTERNAL_ID", source="evaluation",
        proposed_by="synthetic-operator-a",
    ))
    add_missed(source)  # a fingerprint: never exported
    return source.export_snapshot(source_id="central-register")


def test_a_local_register_is_never_stale(tmp_path):
    register = known_miss(tmp_path, clock=lambda: NOW - timedelta(days=400))
    add_missed(register)
    raw = canonical({"messages": [{"role": "user", "content": "Nothing to see."}]})
    sweeper = KnownMissSweep(register, timedelta(days=7))
    result = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert result.decision == Decision.RELEASE and not result.sweep.stale
    assert not hasattr(register, "refresh")


def test_export_carries_rules_only_and_imports_are_swept(tmp_path):
    document = central_export(tmp_path, 1)
    assert [item["entry_kind"] for item in document["entries"]] == ["rule"]
    assert "fingerprint" not in json.dumps(document)
    local = known_miss(tmp_path)
    assert local.import_snapshot(document, actor_identity="synthetic-operator-b") == 1
    raw = canonical({"messages": [{"role": "user", "content": "Reference QX7Z-K9PW."}]})
    result = final_sweep(_Router(KnownMissSweep(local, timedelta(days=7))), raw, DecisionResult(Decision.RELEASE, ()))
    assert [hit.entry_id for hit in result.sweep.hits] == ["central-register:KMR-0001"]
    assert result.reasons == (ReasonCode.KNOWN_MISS_HIT,)
    assert result.sweep.as_record()["imports"][0]["source_id"] == "central-register"


def test_stale_import_asks_until_a_current_export_is_imported(tmp_path):
    local = known_miss(tmp_path)
    local.import_snapshot(central_export(tmp_path, 8), actor_identity="synthetic-operator-b")
    raw = canonical({"messages": [{"role": "user", "content": "Nothing to see."}]})
    sweeper = KnownMissSweep(local, timedelta(days=7))
    stale = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert stale.decision == Decision.ASK and stale.reasons == (ReasonCode.KNOWN_MISS_STALE,)
    assert stale.sweep.stale_sources == ("central-register",)
    local.import_snapshot(central_export(tmp_path, 1), actor_identity="synthetic-operator-b")
    fresh = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert fresh.decision == Decision.RELEASE and fresh.sweep.status == "clear"
    with pytest.raises(CatalogueError, match="older"):
        local.import_snapshot(central_export(tmp_path, 3), actor_identity="synthetic-operator-b")


@pytest.mark.parametrize("change", [
    {"schema": "something-else"}, {"source_id": "bad id!"}, {"exported_utc": "2099-01-01T00:00:00Z"},
    {"entries": [{"source_entry_id": "KMR-0001", "entry_kind": "rule", "entity_type": "PERSON",
                  "source": "operator", "pattern": "(a+)+$", "regression_case_id": None}]},
    {"entries": [{"source_entry_id": "KMR-0001", "entry_kind": "fingerprint", "entity_type": "PERSON",
                  "source": "operator", "pattern": None, "regression_case_id": None}]},
])
def test_imports_are_validated(tmp_path, change):
    document = dict(central_export(tmp_path, 1), **change)
    with pytest.raises(CatalogueError):
        known_miss(tmp_path).import_snapshot(document, actor_identity="synthetic-operator-b")


@pytest.mark.parametrize("damage", ["missing", "corrupt", "no-key"])
def test_unreadable_register_blocks(tmp_path, damage):
    from themis_shield.router import GateFailure
    register = known_miss(tmp_path)
    add_missed(register)
    path = tmp_path / "known-miss.db"
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"not a database" * 100)
    else:
        register = KnownMissRegister(path)  # no protector: the in-sweep fingerprint cannot be checked
    sweeper = KnownMissSweep(register, timedelta(days=7))
    raw = canonical({"messages": [{"role": "user", "content": "Nothing to see."}]})
    with pytest.raises(GateFailure) as blocked:
        final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert blocked.value.code == "BLOCK:known-miss-unreadable"


# -- through the router ------------------------------------------------------------------------------------------


def test_router_hit_holds_with_the_finding_shown_and_records_the_version(tmp_path):
    h = sweeping_harness(tmp_path)
    try:
        entry = add_missed(h.kmr)
        version = h.kmr.snapshot().version
        body = request(f"Mara Lindahl asked about reference qx7z k9pw 4m2r t8vb today.")
        held = h.router.handle(body)
        assert held.route == "held" and held.reason == "KNOWN_MISS_HIT"
        text = held.body["choices"][0]["message"]["content"]
        assert entry in text and "fingerprint" in text and "INTERNAL_ID" in text
        assert "qx7z" not in text.lower()  # the finding is located, never echoed
        assert h.fake.requests == []
        (record,) = h.register.gate_decisions(held.job_id)
        assert record["known_miss_version"] == version
        assert record["record"]["known_miss"]["status"] == "hit"
        assert record["record"]["model_decision"] == "RELEASE"
        assert record["record"]["final_decision"] == "ASK"
        assert "qx7z" not in record["record_json"].lower() and "Mara" not in record["record_json"]
        assert h.register.get_details(held.job_id)["confirmation_required"] == 1
        # ASK, never silent redaction: a person confirms, the Operator approves, then it goes as it stands.
        headers = {"X-Themis-Confirm": held.job_id}
        assert h.router.handle(body, headers).reason == "OPERATOR_APPROVAL_REQUIRED"
        h.register.approve(held.job_id)
        sent = h.router.handle(body, headers)
        assert sent.route == "cloud-tokenised"
        assert "qx7z k9pw" in json.dumps(h.fake.requests[0])
    finally:
        h.cloud.close()


def test_router_without_hits_is_unchanged_but_records_the_version(tmp_path):
    h = sweeping_harness(tmp_path)
    try:
        add_missed(h.kmr)
        response = h.router.handle(request())
        assert response.reason == "OPERATOR_APPROVAL_REQUIRED"
        (record,) = h.register.gate_decisions(response.job_id)
        assert record["record"]["known_miss"]["status"] == "clear"
        assert record["known_miss_version"] == h.kmr.snapshot().version
    finally:
        h.cloud.close()


def test_router_unreadable_register_blocks_and_never_calls_the_cloud(tmp_path):
    h = sweeping_harness(tmp_path)
    try:
        (tmp_path / "known-miss.db").unlink()
        response = h.router.handle(request())
        assert response.route == "blocked" and response.reason == "BLOCK:known-miss-unreadable"
        assert h.fake.requests == []
        assert h.register.get_job(response.job_id).status.value == "Refused"
    finally:
        h.cloud.close()


def test_router_stale_import_asks(tmp_path):
    register = known_miss(tmp_path)
    register.import_snapshot(central_export(tmp_path, 30), actor_identity="synthetic-operator-b")
    h = sweeping_harness(tmp_path, register)
    try:
        response = h.router.handle(request())
        assert response.reason == "KNOWN_MISS_STALE"
        text = response.body["choices"][0]["message"]["content"]
        assert "older than the configured limit" in text and "themis kmr import" in text
    finally:
        h.cloud.close()


def test_entry_added_before_approval_asks_and_records_the_change(tmp_path):
    h = sweeping_harness(tmp_path)
    try:
        body = request(f"Mara Lindahl asked about reference {MISSED}.")
        held = h.router.handle(body)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        add_missed(h.kmr)
        again = h.router.handle(body)
        assert again.route == "held" and again.reason == "KNOWN_MISS_HIT"
        assert h.register.get_job(held.job_id).status.value != "Refused"
        assert h.register.get_details(held.job_id)["confirmation_required"] == 1
        records = h.register.gate_decisions(held.job_id)
        assert [item["record"]["known_miss"]["status"] for item in records] == ["clear", "hit"]
        assert h.fake.requests == []
    finally:
        h.cloud.close()


def test_import_going_stale_after_approval_refuses_with_its_own_reason(tmp_path):
    register = known_miss(tmp_path)
    register.import_snapshot(central_export(tmp_path, 1), actor_identity="synthetic-operator-b")
    h = sweeping_harness(tmp_path, register, max_age_days=1)
    try:
        held = h.router.handle(request())
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        h.register.approve(held.job_id)
        h.router.clock = lambda: NOW + timedelta(hours=1)
        refused = h.router.handle(request())
        assert refused.route == "blocked" and refused.reason == "KNOWN_MISS_STALE"
        assert h.fake.requests == []
    finally:
        h.cloud.close()


def test_entry_added_after_approval_refuses_the_bound_package(tmp_path):
    h = sweeping_harness(tmp_path)
    try:
        body = request(f"Mara Lindahl asked about reference {MISSED}.")
        held = h.router.handle(body)
        assert held.reason == "OPERATOR_APPROVAL_REQUIRED"
        h.register.approve(held.job_id)
        add_missed(h.kmr)
        response = h.router.handle(body)
        assert response.route == "blocked" and response.reason == "KNOWN_MISS_HIT"
        assert h.fake.requests == []
    finally:
        h.cloud.close()


# -- CLI ---------------------------------------------------------------------------------------------------------


def test_cli_reads_values_through_a_hidden_prompt_only(tmp_path, capsys):
    db = tmp_path / "cli.db"
    prompts = []

    def hidden(text):
        prompts.append(text)
        return MISSED

    argv = ["--db", str(db), "--create", "add", "--kind", "fingerprint", "--entity-type", "INTERNAL_ID",
            "--identity", "synthetic-operator-a", "--job", "JOB-2026-0007"]
    assert kmr_main(argv, protector=protector(), prompt=hidden) == 0
    assert len(prompts) == 2 and all("hidden" in item for item in prompts)
    assert MISSED not in " ".join(argv)
    assert "KMR-0001: proposed" in capsys.readouterr().out
    assert kmr_main(["--db", str(db), "verify", "KMR-0001", "--identity", "synthetic-operator-b"]) == 0
    assert kmr_main(["--db", str(db), "promote", "KMR-0001", "--to", "in_sweep",
                     "--identity", "synthetic-operator-b"]) == 0
    assert kmr_main(["--db", str(db), "export-regression-stub", "KMR-0001", "--out", str(tmp_path / "stubs"),
                     "--identity", "synthetic-operator-b"]) == 0
    (stub,) = (tmp_path / "stubs").iterdir()
    assert "QX7Z" not in stub.read_text(encoding="utf-8")
    capsys.readouterr()
    assert kmr_main(["--db", str(db), "list", "--show-patterns"]) == 0
    listing = capsys.readouterr().out
    assert "in_sweep" in listing and "(fingerprint)" in listing and "QX7Z" not in listing


def test_cli_refuses_low_entropy_fingerprints_with_advice(tmp_path, capsys):
    argv = ["--db", str(tmp_path / "cli.db"), "--create", "add", "--kind", "fingerprint", "--entity-type", "PHONE"]
    assert kmr_main(argv, protector=protector(), prompt=lambda _: "0412 345 678") == 2
    error = capsys.readouterr().err
    assert "rule" in error and "0412" not in error


def test_cli_rule_pattern_comes_from_a_prompt(tmp_path, capsys):
    argv = ["--db", str(tmp_path / "cli.db"), "--create", "add", "--kind", "rule", "--entity-type", "INTERNAL_ID"]
    assert kmr_main(argv, read=lambda _: r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b") == 0
    assert "KMR-0001" in capsys.readouterr().out


# -- pattern safety ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", [
    r"(a+)+$", r"(a|aa)*b", r"(\d+-)+x", r"(?:ab|cd){2,}", r"(\w)\1", r"(?P<x>a)(?P=x)", r"(a)?(?(1)b|c)",
    r"(?=QX)\w+", r"(?<!x)\d+", r"(?!a)\w+", r"\w*\w*x", r"\w+\d+x", r"[a-z]+[a-z]{1,16}x",
    r"\d+", r"\w{2,}", r"\w{1,65}", r"\w{1,30}\w{1,30}", r"\w?\w?\w?\w?x", r"\w{1,7}\w{1,8}x",
])
def test_catastrophic_patterns_are_refused(pattern):
    with pytest.raises(CatalogueError):
        check_pattern(pattern)


@pytest.mark.parametrize("pattern", [
    r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b", r"the only \w{1,30} on the night shift", r"\d{1,20}",
    r"(?:ref|case)[ :][0-9]{4,8}", r"[A-Z]{2}[0-9]{6}", r"\w{1,7}\w{1,7}x",
    r"\d{3}-\d{3}-\d{4}", r"(?i)\bward [0-9]{1,2}[a-d]\b",
])
def test_ordinary_patterns_pass(pattern):
    check_pattern(pattern)


def test_unsafe_stored_pattern_blocks_at_load(tmp_path):
    register = known_miss(tmp_path)
    entry = in_sweep(register, register.add_pattern(
        "rule", r"[0-9]{4}", entity_type="INTERNAL_ID", source="operator", proposed_by="synthetic-operator-a",
    ))
    with sqlite3.connect(tmp_path / "known-miss.db") as db:  # an entry written around the validator
        db.execute(
            "INSERT INTO kmr_entries (entry_id,entry_kind,entity_type,source,created_utc,pattern,proposed_by) "
            "VALUES ('KMR-0002','rule','PERSON','operator','2026-10-22T00:00:00Z','(a+)+$','x')"
        )
        for status in ("proposed", "verified", "in_sweep"):
            db.execute(
                "INSERT INTO kmr_events (entry_id,event,to_status,actor,occurred_utc) "
                "VALUES ('KMR-0002',?,?,'x','2026-10-22T00:00:00Z')", (status.upper(), status),
            )
    assert entry == "KMR-0001"
    with pytest.raises(CatalogueUnavailable):
        register.snapshot()


def test_scanned_text_is_bounded_per_entry_and_windows_overlap(tmp_path):
    from themis_shield.catalogues.known_miss import MAX_SCAN_PER_ENTRY, SCAN_WINDOW
    register = known_miss(tmp_path)
    in_sweep(register, register.add_pattern(
        "rule", r"\bQX[0-9A-Z]{2}-[0-9A-Z]{4}\b", entity_type="INTERNAL_ID", source="operator",
        proposed_by="synthetic-operator-a",
    ))
    snapshot = register.snapshot()
    for position in range(SCAN_WINDOW - 20, SCAN_WINDOW + 5):  # every placement across a window boundary
        text = "x " * (position // 2) + ("" if position % 2 == 0 else "y") + " QX7Z-K9PW " + "z " * 900
        hits = snapshot.sweep([(("stop",), text)])
        assert [text[hit.start:hit.end] for hit in hits] == ["QX7Z-K9PW"]
    with pytest.raises(CatalogueUnavailable):
        snapshot.sweep([(("stop",), "a " * (MAX_SCAN_PER_ENTRY // 2 + 1))])


# The costliest shapes the budget accepts (measured on the reference laptop: \w{1,7}\w{1,7}x is the worst,
# about 0.5 s per entry at the per-entry bound). Each must stay well under one second per entry on the largest
# text the sweep will scan for one entry.
WORST_ACCEPTED = [r"\w{1,7}\w{1,7}x", r"\w{0,6}\w{0,6}x", r"\w{1,63}x", r"\w{1,3}\w{1,3}\w{1,3}x"]


def largest_leaf():
    from themis_shield.catalogues.known_miss import MAX_SCAN_PER_ENTRY, _windows
    size = MAX_SCAN_PER_ENTRY
    while sum(len(window) for _, window in _windows("a" * size)) > MAX_SCAN_PER_ENTRY:
        size -= 256
    return "a" * size


@pytest.mark.parametrize("pattern", WORST_ACCEPTED)
def test_worst_accepted_patterns_cost_under_a_second_at_the_scan_bound(tmp_path, pattern):
    import time
    from themis_shield.catalogues.known_miss import MAX_SCAN_PER_ENTRY, _windows
    check_pattern(pattern)
    register = known_miss(tmp_path)
    in_sweep(register, register.add_pattern(
        "rule", pattern, entity_type="INTERNAL_ID", source="operator", proposed_by="synthetic-operator-a",
    ))
    leaf = largest_leaf()
    assert sum(len(window) for _, window in _windows(leaf)) > MAX_SCAN_PER_ENTRY - 1024
    snapshot = register.snapshot()
    started = time.perf_counter()
    assert snapshot.sweep([(("stop",), leaf)]) == ()
    assert time.perf_counter() - started < 1.0


def test_sweep_deadline_sends_the_job_to_ask(tmp_path):
    register = known_miss(tmp_path)
    for _ in range(3):
        in_sweep(register, register.add_pattern(
            "rule", r"\w{1,7}\w{1,7}x", entity_type="INTERNAL_ID", source="operator",
            proposed_by="synthetic-operator-a",
        ))
    raw = canonical({"model": "m", "messages": [{"role": "user", "content": "a" * 150000}]})
    sweeper = KnownMissSweep(register, timedelta(days=7), deadline_seconds=0.05)
    result = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.RELEASE, ()))
    assert result.decision == Decision.ASK and result.reasons == (ReasonCode.KNOWN_MISS_DEADLINE,)
    assert result.sweep.status == "deadline" and result.sweep.reason == "KNOWN_MISS_DEADLINE"
    blocked = final_sweep(_Router(sweeper), raw, DecisionResult(Decision.ASK, (ReasonCode.SENSITIVE,)))
    assert blocked.decision == Decision.ASK


def test_cli_never_creates_a_register_by_accident(tmp_path, capsys):
    missing = tmp_path / "typo.db"
    assert kmr_main(["--db", str(missing), "list"]) == 2
    assert kmr_main(["--db", str(missing), "verify", "KMR-0001", "--identity", "synthetic-operator-b"]) == 2
    assert not missing.exists()
    assert "--create" in capsys.readouterr().err


def test_cli_export_and_import(tmp_path, capsys):
    source, local = str(tmp_path / "central.db"), str(tmp_path / "local.db")
    assert kmr_main(["--db", source, "--create", "add", "--kind", "rule", "--entity-type", "INTERNAL_ID",
                     "--identity", "synthetic-operator-a"], read=lambda _: r"[A-Z]{2}[0-9]{6}") == 0
    for command in (["verify", "KMR-0001"], ["promote", "KMR-0001", "--to", "in_sweep"]):
        assert kmr_main(["--db", source, *command, "--identity", "synthetic-operator-b"]) == 0
    out = tmp_path / "export.json"
    assert kmr_main(["--db", source, "export", "--source-id", "central-register", "--out", str(out)]) == 0
    assert kmr_main(["--db", local, "--create", "import", str(out), "--identity", "synthetic-operator-b"]) == 0
    capsys.readouterr()
    assert kmr_main(["--db", local, "imports"]) == 0
    assert "central-register" in capsys.readouterr().out
