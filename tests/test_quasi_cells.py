"""v0.5 fix "quasi-cells": a QUASI_ID finding that covers part of one table cell is widened to the whole cell.

On split F the finders marked half of two role descriptions in tables ("Longest-serving founding member" of
"Longest-serving founding member, in the same corner flat since ...") and the rest was released beside them.
Synthetic only: every table below is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

from themis_shield.detectors.contracts import finding
from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Label

ROOT = Path(__file__).resolve().parents[1]
SWITCH = "quasi-cells"
STAGE = "quasi-cells"

MARKDOWN = (
    "Tenancy review (names removed)\n\n"
    "| Row | Description | Tenancy start | Notes |\n"
    "|---|---|---|---|\n"
    "| 1 | Member, studio | 3 May 2021 | Rent at co-op rate |\n"
    "| 2 | Longest-standing tenant, in the same top-floor flat since the block was built | 1 June 1990 | None |\n"
)
CSV = (
    "record,staff_no,role,work_unit,dob\n"
    "R1,KX-1001,Field Operator,Moss Depot,withheld\n"
    "R2,KX-1002,Second-year carpentry apprentice, youngest on the canal crew,Moss Depot,withheld\n"
)


def given(text, value, label=Label.QUASI_ID):
    start = text.index(value)
    return (finding(start, start + len(value), label, "llm", 1),)


def quasi(text, findings):
    return sorted(text[f.start:f.end] for f in propagate(text, findings) if Label.QUASI_ID in f.labels)


def widened(text, findings):
    return [text[f.start:f.end] for f in propagate(text, findings)
            if Label.QUASI_ID in f.labels and any(item.stage == STAGE for item in f.evidence)]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_unique_roles():
    assert DETECTION_FIXES[DETECTION_FIXES.index("unique-roles") + 1] == SWITCH
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)


# Regression (split F's two released role descriptions, invented words): before the switch only the part the finder
# marked was a QUASI_ID and the rest of the cell was released.
@pytest.mark.parametrize("text, part, cell", [
    (MARKDOWN, "Longest-standing tenant",
     "Longest-standing tenant, in the same top-floor flat since the block was built"),
    (CSV, "youngest on the canal crew", "Second-year carpentry apprentice, youngest on the canal crew"),
])
def test_a_partial_quasi_identifier_covers_its_whole_cell(text, part, cell):
    assert cell in quasi(text, given(text, part))
    assert widened(text, given(text, part)) == [cell]


def test_a_quoted_cell_is_widened_inside_its_quotes():
    text = 'row,role,site\n1,"Night porter, the last one hired before the freeze",Kell Bay\n'
    assert widened(text, given(text, "the last one hired")) == ["Night porter, the last one hired before the freeze"]


def test_tab_separated_rows():
    text = "row\trole\tsite\n1\tLongest-serving porter, 38 years\tKell Bay\n"
    assert widened(text, given(text, "38 years")) == ["Longest-serving porter, 38 years"]


@pytest.mark.parametrize("text, part", [
    (MARKDOWN, "Longest-standing tenant, in the same top-floor flat since the block was built"),  # already whole
    (MARKDOWN, "the block was built | 1 June 1990"),                                              # across cells
    ("We met the longest-standing tenant, in the same flat since 1990, at the AGM.", "longest-standing tenant"),
])
def test_whole_cells_crossing_findings_and_prose_are_left_alone(text, part):
    assert widened(text, given(text, part)) == []


def test_only_quasi_identifiers_are_widened():
    assert widened(MARKDOWN, given(MARKDOWN, "Longest-standing tenant", Label.PERSON)) == []


def test_a_pii_tracer_quasi_identifier_alone_is_not_widened():
    # PII-Tracer calls single sensitive words quasi-identifiers; widening them took case numbers and sites with them.
    text = "row,note\n1,Kit is on her second round of chemo. CASE-26-0007.\n"
    start = text.index("chemo")
    tracer = (finding(start, start + 5, Label.QUASI_ID, "pii-tracer", 1),)
    assert widened(text, tracer) == []
    assert widened(text, given(text, "chemo")) == ["Kit is on her second round of chemo. CASE-26-0007."]


def test_a_long_cell_is_left_alone():
    text = "row|notes|site\n1|" + "Longest-standing tenant " + "and more words " * 12 + "|Kell Bay\n"
    assert widened(text, given(text, "Longest-standing tenant")) == []


def test_unquoted_commas_that_do_not_explain_the_extra_cells_keep_cells_apart():
    # One ", " piece but two extra cells: the columns are not known, so only the cell itself is widened.
    text = "row,role,site\n1,Porter, night,Kell Bay,2021\n"
    assert widened(text, given(text, "nigh")) == ["night"]


def test_the_switch_turns_the_pass_off():
    part = "Longest-standing tenant"
    try:
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])
        assert widened(MARKDOWN, given(MARKDOWN, part)) == [] and quasi(MARKDOWN, given(MARKDOWN, part)) == [part]
        configure_detection_fixes("none")
        assert widened(MARKDOWN, given(MARKDOWN, part)) == []
        configure_detection_fixes(None)
        assert len(widened(MARKDOWN, given(MARKDOWN, part))) == 1
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the pass widens findings, so give it the first half of every gold QUASI_ID span of
# every evaluation case (a finder's partial answer) and check that no widened cell overlaps a hard negative and that
# the widened cells cover the gold spans they were cut from.
# Split G (published 12/11/2026 with eval/RESULTS-v0.6.md) came after this switch shipped in 0.4.0 and breaks the
# guard: 1 of its 54 widened cells on split G overlaps a hard negative (a role title) (DEV-DEBT DD-26). The guard stays strict on the sets it was written from; on split G it is a strict
# expected failure, so it fails loudly once the switch is fixed.
_CASE_FILES = sorted((ROOT / "eval" / "cases").glob("*.jsonl"))
_GUARD_SETS = [
    pytest.param([p for p in _CASE_FILES if not p.name.startswith("test-g-")], (300, 300), id="sets-before-split-g"),
    pytest.param([p for p in _CASE_FILES if p.name.startswith("test-g-")], (0, 0), id="split-g",
                 marks=pytest.mark.xfail(strict=True, reason="DD-26: 1 of its 54 widened cells on split G overlaps a hard negative (a role title)")),
]


@pytest.mark.parametrize(("paths", "floor"), _GUARD_SETS)
def test_no_widened_cell_of_the_evaluation_sets_overlaps_a_hard_negative(paths, floor):
    from themis_shield.detectors.propagate import quasi_cell_spans

    started = time.perf_counter()
    made, completed, hits = 0, 0, []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            golds = [s for s in case["spans"] if s["label"] == "QUASI_ID" and len(s["value"]) >= 8]
            halves = tuple(finding(s["start"], s["start"] + len(s["value"]) // 2, Label.QUASI_ID, "llm", 1)
                           for s in golds)
            for item in quasi_cell_spans(text, halves):
                a, b = item.start, item.end
                made += 1
                hits += [(case["id"], text[a:b], n["kind"]) for n in case["hard_negatives"]
                         if n["start"] < b and a < n["end"]]
                completed += sum(1 for s in golds if a <= s["start"] and s["end"] <= b)
    assert hits == []
    assert made >= floor[0] and completed >= floor[1]  # 329 cells, each completing a gold span, when written
    assert time.perf_counter() - started < 30
