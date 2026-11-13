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


# DD-26 (split G's GT-027, invented words): a "who" clause that was cut at 50 characters runs to its end, which is the
# main verb ("has requested") or a punctuation mark, never into it.
@pytest.mark.parametrize("text, value", [
    ("The panel noted that the only apprentice in this intake's welding cohort who grew up speaking Greek at home "
     "has requested a bilingual assessor for her exams.",
     "the only apprentice in this intake's welding cohort who grew up speaking Greek at home"),
    ("We were told the only trainee at the Carrow Street kitchen who learned to cook in a refugee camp in Kenya "
     "will take leave.", "the only trainee at the Carrow Street kitchen who learned to cook in a refugee camp in Kenya"),
    ("Note: the sole technician on the northern line who moved here from Perth last winter with her two children "
     "will not be rostered on Sunday.",
     "the sole technician on the northern line who moved here from Perth last winter with her two children"),
])
def test_a_who_clause_runs_to_its_end(text, value):
    assert roles(text) == [value]


def test_a_who_clause_in_a_cell_runs_to_the_end_of_the_cell():
    cell = "the only forklift driver at the Bunderra Logistics Park who speaks four languages and mentors new starters"
    text = "row\tsite\trole\n7\tBunderra\t" + cell + "\t7\n"
    assert roles(text) == [cell]


@pytest.mark.parametrize("text, value", [
    # Prose punctuation after the cut is no proof that the clause ended before the main verb, so it is cut as before.
    ("The only female forklift driver who started in February in the Copperleaf back office signed up with the union.",
     "The only female forklift driver who started in February in the Copperleaf back office"),
    ("The one trainee who commutes from Marrowgate by ferry left after a long dispute at home.",
     "The one trainee who commutes from Marrowgate by ferry left after a"),
])
def test_a_who_clause_followed_by_a_main_verb_the_list_does_not_know_is_cut_as_before(text, value):
    assert roles(text) == [value]


def test_a_who_clause_with_no_end_in_sight_is_cut_as_before():
    text = ("the only apprentice in this intake's welding cohort who grew up speaking Greek at home requested a "
            "bilingual assessor for her practical exams and a quiet room for the written papers afterwards")
    assert roles(text) == ["the only apprentice in this intake's welding cohort who grew up speaking"]


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
# Split G (published 12/11/2026 with eval/RESULTS-v0.6.md) came after this switch shipped in 0.4.0 and broke the guard
# (DEV-DEBT DD-26): 1 of its 20 spans covered no gold QUASI_ID span, a gold omission (GT-027, "the only apprentice ...
# who grew up speaking Mandarin at home") now corrected in the adjudicated files (``eval/cases/test-g-*.adjudicated.jsonl``;
# the sealed ones stay as they were), so split G is read from those. The guard is strict on every set.
_CASE_FILES = sorted(p for p in (ROOT / "eval" / "cases").glob("*.jsonl") if not p.name.endswith(".adjudicated.jsonl"))
_GUARD_SETS = [
    pytest.param([p for p in _CASE_FILES if not p.name.startswith("test-g-")], (120, 120), id="sets-before-split-g"),
    pytest.param(sorted((ROOT / "eval" / "cases").glob("test-g-*.adjudicated.jsonl")), (18, 18), id="split-g"),
]


@pytest.mark.parametrize(("paths", "floor"), _GUARD_SETS)
def test_no_unique_role_of_the_evaluation_sets_overlaps_a_hard_negative(paths, floor):
    from themis_shield.detectors.propagate import unique_role_spans

    started = time.perf_counter()
    made, covered, hits, wrong = 0, 0, [], []
    for path in paths:
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
    assert made >= floor[0] and covered >= floor[1]  # 124 spans, each on a gold QUASI_ID, when written
    assert time.perf_counter() - started < 30
