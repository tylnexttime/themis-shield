"""v0.4 friction fix tracer-evidence-sanity: PII-Tracer values that cannot be what their label says. Synthetic only."""

from __future__ import annotations

from dataclasses import replace

import pytest

from themis_shield.detectors.contracts import FindingEvidence, finding
from themis_shield.detectors.tracer_sanity import implausible, mark_implausible_tracer_values
from themis_shield.policy import contrary_tracer_findings
from themis_shield.types import Label

TEXT = ("Notice: the depot opens on 10 May 2024 at Kestrel Reservoir. Born 3 March 1990 at 12 Harbour Way. "
        "Amount 186.50, see registry. Chair\nname,dob\nMara,1990-03-03\n")


def tracer(value, label):
    start = TEXT.index(value)
    return finding(start, start + len(value), label, "pii-tracer", 1)


@pytest.mark.parametrize("value, label, expected", [
    ("10 May 2024", Label.DATE_OF_BIRTH, True),
    ("3 March 1990", Label.DATE_OF_BIRTH, False),   # birth context in the same sentence
    ("1990-03-03", Label.DATE_OF_BIRTH, False),     # a table column headed "dob"
    ("Kestrel Reservoir", Label.ADDRESS, True),
    ("12 Harbour Way", Label.ADDRESS, False),
    ("186.50", Label.STUDENT_ID, True),
    ("registry", Label.URL_PERSONAL, True),
    ("Chair", Label.PERSON, True),
    ("Mara", Label.PERSON, False),
])
def test_implausible_tracer_values(value, label, expected):
    assert implausible(TEXT, tracer(value, label)) is expected


def test_flagged_values_stay_substituted_but_stop_holding():
    dates = tracer("10 May 2024", Label.DATE_OF_BIRTH)
    marked = mark_implausible_tracer_values(TEXT, (dates,))
    assert marked[0].implausible_tracer_value and marked[0].span == dates.span

    class Evidence:
        findings = marked

    assert contrary_tracer_findings(Evidence) == []


def test_a_value_a_finder_also_found_is_never_flagged():
    start = TEXT.index("10 May 2024")
    both = finding(start, start + 11, Label.DATE_OF_BIRTH, "pii-tracer", 1)
    both = replace(both, evidence=both.evidence + (FindingEvidence(Label.DATE_OF_BIRTH, "gemma4-e4b", 2),))
    assert not mark_implausible_tracer_values(TEXT, (both,))[0].implausible_tracer_value


def test_a_person_in_the_document_keeps_dates_and_addresses_holding():
    # A person the finders both missed, beside a date and a street: the double miss the hold is for.
    text = "Notice: Mara Quinlan, 10 May 2024, Kestrel Reservoir."
    start = text.index("Mara Quinlan")
    person = finding(start, start + len("Mara Quinlan"), Label.PERSON, "pii-tracer", 1)
    date_start, place_start = text.index("10 May 2024"), text.index("Kestrel Reservoir")
    date = finding(date_start, date_start + 11, Label.DATE_OF_BIRTH, "pii-tracer", 1)
    place = finding(place_start, place_start + len("Kestrel Reservoir"), Label.ADDRESS, "pii-tracer", 1)
    marked = mark_implausible_tracer_values(text, (person, date, place))
    assert not any(item.implausible_tracer_value for item in marked)
    # Without the person both are flagged; a lone role word does not count as a person.
    assert all(item.implausible_tracer_value for item in mark_implausible_tracer_values(text, (date, place)))
    chair_text = "Chair: 10 May 2024, Kestrel Reservoir."
    chair = finding(0, 5, Label.PERSON, "pii-tracer", 1)
    d = finding(7, 18, Label.DATE_OF_BIRTH, "pii-tracer", 1)
    assert all(item.implausible_tracer_value for item in mark_implausible_tracer_values(chair_text, (chair, d)))


def test_amounts_are_flagged_even_beside_a_person():
    text = "Mara Quinlan paid 186.50 on account."
    person = finding(0, 12, Label.PERSON, "gemma4-e4b", 2)
    start = text.index("186.50")
    amount = finding(start, start + 6, Label.STUDENT_ID, "pii-tracer", 1)
    assert mark_implausible_tracer_values(text, (person, amount))[1].implausible_tracer_value
