"""v0.4 item 4: sensitive statements from column headers and from a person's union role. Synthetic only."""

from __future__ import annotations

import pytest

from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.settings import configure_detection_fixes
from themis_shield.detectors.statements import table_statement_spans, union_role_spans
from themis_shield.placeholders import TokenAllocator
from themis_shield.substitute import substitute
from themis_shield.types import Label
from themis_shield.vault import InMemoryVault


def cells(text):
    return [(f.primary_label, text[f.start:f.end]) for f in table_statement_spans(text)]


def test_cells_under_a_sensitive_header_are_statements_even_when_negative():
    text = ("name,role,police_check,union,medical_condition,religion\n"
            "Mara,Cook,Clear,non-member,asthma,-\n"
            "Ravi,Carer,No disclosable outcomes,member (dues via payroll),nil,Catholic\n")
    got = cells(text)
    assert (Label.CRIMINAL, "Clear") in got and (Label.CRIMINAL, "No disclosable outcomes") in got
    assert (Label.UNION, "non-member") in got and (Label.UNION, "member (dues via payroll)") in got
    assert (Label.HEALTH, "asthma") in got and (Label.HEALTH, "nil") in got
    assert (Label.RELIGION, "Catholic") in got
    assert all(value != "-" for _, value in got)  # an empty-looking cell says nothing
    assert all(value not in ("Mara", "Cook") for _, value in got)


@pytest.mark.parametrize("header", ["police check date", "union fees paid", "health and safety", "faith-based school"])
def test_headers_must_name_the_category_itself(header):
    assert cells(f"name,{header}\nMara,Yes\n") == []


@pytest.mark.parametrize("sentence", [
    "Mirabel Wetherall was elected delegate by the depot crew.",
    "fyi I am the shop steward on B shift, just so you know",
    "As you said, you collect the dues envelope from the crew every payday.",
    "Diego is the union delegate for the depot.",
    "Sam stood on the picket line outside the gate.",
    "She joined the union after the restructure.",
    "He lodged a grievance through the union.",
    "Ana is not a union member.",
    "Lee recruited six colleagues into the union.",
    "Jo is a delegate for the transport workers.",
])
def test_a_persons_union_role_is_a_statement(sentence):
    (item,) = union_role_spans(sentence)
    assert item.primary_label == Label.UNION and item.statement_span is not None
    assert sentence[item.start:item.end].rstrip(".") == sentence.strip().rstrip(".")


@pytest.mark.parametrize("sentence", [
    "Union delegates may attend consultation meetings during work hours.",
    "Union members and non-members are covered by the same agreement.",
    "Ironbark Credit Union Ltd sponsors the barbecue.",
    "Priya is the data steward for the finance domain.",
    "Tom is a delegate to the national conference.",
    "Yes. Delegates can attend training with paid release.",
    "Members can raise issues through the union.",
    "The student union runs the bookshop.",
    "The European Union published new guidance.",
])
def test_policy_and_look_alikes_are_not(sentence):
    assert union_role_spans(sentence) == ()


def test_only_the_sentence_with_the_role_is_marked():
    text = "Kim works nights. She is the shop steward on B shift. The roster changes in May."
    (item,) = union_role_spans(text)
    assert text[item.start:item.end] == "She is the shop steward on B shift."


def test_statements_substitute_whole():
    text = "name,police_check\nMara,Clear\n"
    found = propagate(text, ())
    result = substitute(text, found, TokenAllocator("synthetic", InMemoryVault()))
    assert "Clear" not in result.tokenised_text and "⟦S:" in result.tokenised_text


def test_the_switches_turn_the_passes_off():
    text = "name,police_check\nMara,Clear\nSam stood on the picket line.\n"
    try:
        configure_detection_fixes("none")
        assert not any(Label.CRIMINAL in f.labels or Label.UNION in f.labels for f in propagate(text, ()))
    finally:
        configure_detection_fixes(None)
    labels = {label for f in propagate(text, ()) for label in f.labels}
    assert {Label.CRIMINAL, Label.UNION} <= labels
