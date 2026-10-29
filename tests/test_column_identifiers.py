"""v0.4 fix "column-identifiers": a column headed as a personal identifier is protected cell by cell. Synthetic only."""

from __future__ import annotations

import pytest

from themis_shield.detectors.propagate import propagate, table_identifier_spans
from themis_shield.detectors.settings import configure_detection_fixes
from themis_shield.types import Label


def found(text):
    return [(f.primary_label, text[f.start:f.end]) for f in table_identifier_spans(text)]


def test_identifier_columns_are_protected_with_their_label():
    text = ("worker,dob,passport,national_id,licence,staff_no,bsb,account,card,medicare,tfn\n"
            '"ROE, Jane",1990-01-02,E1234567,QQ123456C,47227820,S-0042,062-000,12345678,4111 1111 1111 1111,'
            "2123 45670 1,123 456 782\n")
    got = dict((value, label) for label, value in found(text))
    assert got["E1234567"] == Label.PASSPORT and got["QQ123456C"] == Label.NATIONAL_ID
    assert got["47227820"] == Label.DRIVER_LICENCE and got["S-0042"] == Label.INTERNAL_ID
    assert got["062-000"] == Label.BANK_ACCOUNT and got["12345678"] == Label.BANK_ACCOUNT
    assert got["4111 1111 1111 1111"] == Label.PAYMENT_CARD
    assert got["2123 45670 1"] == Label.MEDICARE and got["123 456 782"] == Label.TFN
    assert "1990-01-02" not in got


def test_an_unquoted_comma_never_hides_the_identifier():
    # The split D shape: the name's comma shifts every later cell by one.
    text = "worker,dob,licence,checked_by\nTHORNBURY, Leonard,1995-12-15,47227820,EM\nROE, Jane,1990-01-02,25024436,VF\n"
    values = [value for _, value in found(text)]
    assert "47227820" in values and "25024436" in values
    assert not any(value.startswith("19") for value in values)


@pytest.mark.parametrize("header", [
    "incident id", "po_number", "asset_id", "#", "case_ref", "order no", "invoice number", "ticket id",
    "licence expiry", "licence class", "card type", "account name", "account balance", "passport issue date",
    "row", "line no",
])
def test_organisation_ids_counters_and_properties_are_not_identifier_columns(header):
    text = f"name,{header}\nMara Lindahl,A-1042\n"
    assert all(value != "A-1042" for _, value in found(text))


def test_a_bare_id_counts_only_beside_a_name_or_another_identifier():
    assert found("product,id\nWidget,4471\n") == []
    assert found("name,id\nMara Lindahl,4471\n") == [(Label.INTERNAL_ID, "4471")]
    assert (Label.INTERNAL_ID, "4471") in found("passport,id\nE1234567,4471\n")


def test_markdown_tables_skip_the_alignment_row():
    text = "| Name | Staff ID | Start date |\n|---|---|---|\n| Mara | 4471 | 2027-03-01 |\n| Ravi | 4472 | 01/03/2027 |\n"
    assert found(text) == [(Label.INTERNAL_ID, "4471"), (Label.INTERNAL_ID, "4472")]


def test_cells_without_digits_are_left_to_the_other_detectors():
    assert found("name,member_no,union_member\nMara,pending,Yes\n") == []


def test_a_table_ends_at_a_short_row_or_a_blank_line():
    text = "name,staff id\nMara,4471\n\nThe staff id 9999 is not in the table.\n"
    assert found(text) == [(Label.INTERNAL_ID, "4471")]


def test_the_switch_turns_the_pass_off():
    text = "name,licence\nMara Lindahl,47227820\n"
    try:
        configure_detection_fixes("none")
        assert not any(text[f.start:f.end] == "47227820" for f in propagate(text, ()))
        configure_detection_fixes(None)
        assert any(text[f.start:f.end] == "47227820" for f in propagate(text, ()))
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# The organisation's formats never cancel a header-named identifier cell; a bare-number format needs its key words.
def _detect(text, institution):
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets, detect_batch

    from router_fakes import Finders, Tracer

    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,), institution=institution)
    result = detect_batch([text], profile, DetectionTargets(tracer=Tracer(), llm_client=Finders()), thorough=True)[0]
    return {text[f.start:f.end] for f in result.findings}


WORK_ORDERS = {"institution": "Synthetic Housing Co-operative", "campuses": [],
               "reference_number_formats": {"work_order": r"\d{8}"}}


def test_a_bare_number_format_never_cancels_a_licence_cell():
    text = 'worker,dob,licence\n"ROE, Jane",1990-01-02,47227820\n'
    assert "47227820" in _detect(text, WORK_ORDERS)


def test_a_bare_number_format_needs_its_key_words_before_the_number():
    from themis_shield.detectors.allowlist import compile_profile, terms
    from themis_shield.config import DeploymentProfile

    allow = compile_profile(DeploymentProfile("s", "1", (), (), institution=WORK_ORDERS))
    with_words = "Work order 12345678 is closed."
    without = "Her number is 12345678."
    assert [(a, b) for a, b, _ in terms(with_words, allow)] == [(11, 19)]
    assert terms(without, allow) == ()


def test_without_the_fix_the_old_behaviour_returns():
    from themis_shield.detectors.allowlist import compile_profile, terms
    from themis_shield.config import DeploymentProfile

    try:
        configure_detection_fixes("none")
        allow = compile_profile(DeploymentProfile("s", "1", (), (), institution=WORK_ORDERS))
        assert terms("Her number is 12345678.", allow) != ()
    finally:
        configure_detection_fixes(None)


def test_a_nameless_bare_format_allow_lists_nothing():
    from themis_shield.detectors.profile_adapter import key_words

    assert key_words("number") is None and key_words("ref_no") is None
    assert key_words("purchase_order_number") is not None
