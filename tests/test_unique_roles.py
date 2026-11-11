"""v0.5 fix "unique-roles": a role the text says only one person holds is a quasi-identifier.

On split F the finders missed "the only analyst on the overnight roster" and "the one contractor seconded from ..."
in prose and a "Sole night-shift pharmacist at ..." cell in a CSV. Synthetic only: every sentence below is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Label

ROOT = Path(__file__).resolve().parents[1]
SWITCH = "unique-roles"
STAGE = "unique-roles"


def roles(text):
    return [text[f.start:f.end] for f in propagate(text, ())
            if Label.QUASI_ID in f.labels and any(item.stage == STAGE for item in f.evidence)]


def quasi(text):
    """Every QUASI_ID finding, whatever made it (the regression test does not name the pass)."""
    return [text[f.start:f.end] for f in propagate(text, ()) if Label.QUASI_ID in f.labels]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_negative_statements():
    assert DETECTION_FIXES[DETECTION_FIXES.index("negative-statements") + 1] == SWITCH
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)


# Regression (split F's shapes, invented words): before the switch none of these was a QUASI_ID finding.
@pytest.mark.parametrize("text, value", [
    ("The access log shows that the only analyst on the weekend fraud-review roster opened the file nine times.",
     "the only analyst on the weekend fraud-review roster"),
    ("The sheet was built with the one contractor seconded from Darwin to the payments squad.",
     "the one contractor seconded from Darwin to the payments squad"),
    ("row,site,role,status\n3,Gannet Bay,\"Sole night-shift Hospital Pharmacist at Gannet Bay Hospital\",open\n",
     "Sole night-shift Hospital Pharmacist at Gannet Bay Hospital"),
])
def test_a_role_only_one_person_holds_is_a_quasi_identifier(text, value):
    assert any(value in found for found in quasi(text))
    assert any(value in found for found in roles(text))


@pytest.mark.parametrize("text, value", [
    ("Please ask the sole weekend Enrolled Nurse at Kestrel Flat, Mira, to swap.",
     "the sole weekend Enrolled Nurse at Kestrel Flat"),
    ("The log shows that the only analyst on the weekend fraud-review roster opened the file nine times.",
     "the only analyst on the weekend fraud-review roster opened the file"),  # at most 50 characters after "on"
    ("| 4 | Only female grader operator on the Tamsin road crew | 2019 |",
     "Only female grader operator on the Tamsin road crew"),
    ("First-year electrical apprentice, only apprentice on the Moss Vale roster",
     "only apprentice on the Moss Vale roster"),
    ("We heard that the only night-shift ward clerk at Pellow Ridge is leaving.",
     "the only night-shift ward clerk at Pellow Ridge"),
    ("She is the only accountant who speaks Tongan.", "the only accountant who speaks Tongan"),
])
def test_prose_and_cells(text, value):
    assert roles(text) == [value]


@pytest.mark.parametrize("text", [
    "Only one manager on duty may approve a refund.",       # a number, not a describing word
    "Reports are read only by the screening officer in People and Culture.",  # function words
    "One of the nurses at the clinic asked about parking.",  # one of many
    "Only staff on the night roster may use the side door.",  # generic role word
    "Each team has one supervisor at every site.",            # no "the one"
    "The only option on the table is to wait.",               # not a role
    "Sole traders at the market pay a lower fee.",            # not a role word
])
def test_look_alikes_are_not_unique_roles(text):
    assert roles(text) == []


def test_the_span_stops_at_a_cell_separator_or_an_auxiliary_verb():
    text = "the only welder at Dock 3\tDock 3 office\t2021\n"
    assert roles(text) == ["the only welder at Dock 3"]
    text = "the only welder at the Corran yard was moved to days."
    assert roles(text) == ["the only welder at the Corran yard"]


def test_the_switch_turns_the_pass_off():
    text = "Ask the only pharmacist at the Gannet Bay clinic to call me."
    try:
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])
        assert roles(text) == [] and quasi(text) == []
        configure_detection_fixes("none")
        assert roles(text) == []
        configure_detection_fixes(None)
        assert roles(text) == ["the only pharmacist at the Gannet Bay clinic to call me"]
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the pass was written from split F, so run it over every evaluation case. A span it
# makes must never overlap a hard negative (a site, a business address) and each must cover a gold QUASI_ID span.
def test_no_unique_role_of_the_evaluation_sets_overlaps_a_hard_negative():
    from themis_shield.detectors.propagate import unique_role_spans

    started = time.perf_counter()
    made, covered, hits, wrong = 0, 0, [], []
    for path in sorted((ROOT / "eval" / "cases").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            for item in unique_role_spans(text):
                a, b = item.start, item.end
                made += 1
                hits += [(case["id"], text[a:b], n["kind"]) for n in case["hard_negatives"]
                         if n["start"] < b and a < n["end"]]
                gold = [s for s in case["spans"] if s["start"] < b and a < s["end"]]
                covered += sum(1 for s in gold if s["label"] == "QUASI_ID")
                if not any(s["label"] == "QUASI_ID" for s in gold):
                    wrong.append((case["id"], text[a:b]))
    assert hits == [] and wrong == []
    assert made >= 120 and covered >= 120  # 124 spans, each on a gold QUASI_ID, when written
    assert time.perf_counter() - started < 30
