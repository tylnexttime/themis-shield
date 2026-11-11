"""v0.4 fix "location-columns": a column headed as where a person lives is protected as ADDRESS, cell by cell.

Split F released the nine bare residence postcodes of an "anonymised" leavers table whole while the dates and roles
beside them were protected. Synthetic only: every table below is invented.
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
STAGE = "table-locations"


def _cells(text):
    """The cells the location pass protects: (start, end) of every ADDRESS finding that pass made."""
    return [
        (f.start, f.end) for f in propagate(text, ())
        if Label.ADDRESS in f.labels and any(item.stage == STAGE for item in f.evidence)
    ]


def found(text):
    return [text[a:b] for a, b in _cells(text)]


POSTCODES = ["2741", "2743", "2749", "2744", "2740", "2752", "2746", "2748", "2750"]
PLACES = ["Quillbarrow", "Tarnwick Flat", "Ostler Rise", "Pemberby", "Lorrimer Cove", "Haddock Bend",
          "Wystan Heights", "Nettlefold", "Brindlemere"]
ROLES = ["Staff Nurse", "Sole night-shift Pharmacist at the Ostler Rise ward", "Orderly", "Radiographer",
         "Longest-serving Porter, 38 years", "Cook", "Occupational Therapist", "Ward Clerk", "Dietitian"]
BUSINESS = [
    f"{10 + 3 * i} Lantern Road, {place} NSW {postcode}" for i, (place, postcode) in enumerate(zip(PLACES, POSTCODES))
]


def leavers(separator="\t"):
    header = ["row", "site", "role", "residence_postcode", "start_date", "exit_date", "ticket", "card_return_point"]
    rows = [
        [str(i + 1), PLACES[i], ROLES[i], POSTCODES[i], f"{3 + i:02d}/04/20{10 + i}", f"{12 + i:02d}/05/2026",
         f"HD-77{i:04d}", BUSINESS[i]]
        for i in range(9)
    ]
    return "".join(separator.join(row) + "\n" for row in [header, *rows])


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_joined_terms():
    assert DETECTION_FIXES[DETECTION_FIXES.index("joined-terms") + 1] == "location-columns"
    assert "location-columns" in DEFAULT_DETECTION_FIXES and fix_on("location-columns")


def test_every_postcode_of_the_leavers_table_is_protected_and_the_business_column_is_left_alone():
    text = leavers("\t")
    assert found(text) == POSTCODES
    business = [(text.index(value), text.index(value) + len(value)) for value in BUSINESS]
    assert not any(a < d and c < b for a, b in _cells(text) for c, d in business)


def test_the_cells_carry_the_address_label_and_nothing_wider_than_the_cell():
    text = leavers("\t")
    for f in propagate(text, ()):
        if any(item.stage == STAGE for item in f.evidence):
            assert f.primary_label == Label.ADDRESS and text[f.start:f.end] in POSTCODES


def test_a_csv_with_quoted_cells():
    text = leavers(",")
    # The role and business address hold commas: quote them as a spreadsheet export would.
    for value in [*ROLES, *BUSINESS]:
        text = text.replace(value, f'"{value}"')
    assert found(text) == POSTCODES


def test_a_csv_with_unquoted_commas_after_the_column_does_not_spill_into_the_next_column():
    # The comma in the free text moves every later cell, but a piece that follows the comma with a space belongs to
    # the cell before it, so the columns are known.
    text = "home suburb,site,reason\nQuillbarrow,Depot North,Moved, closer to family\nPemberby,Depot South,Retired\n"
    assert found(text) == ["Quillbarrow", "Pemberby"]


def test_an_unquoted_comma_before_the_column_never_hides_the_residence():
    text = "worker,dob,residence postcode,checked_by\nTHORNBURY, Leonard,1995-12-15,2741,EM\n"
    assert found(text) == ["2741"]
    # No space after the comma: which cell moved is unknown, so both candidates are protected.
    text = "worker,dob,residence postcode,checked_by\nTHORNBURY,Leonard,1995-12-15,2741,EM\n"
    assert "2741" in found(text)


def test_a_multi_comma_home_address_is_protected_whole():
    text = "name,home address,start\nMara,Unit 2,14 Wattle St,Redgum VIC,2024-03-01\n"
    assert found(text) == ["Unit 2", "14 Wattle St", "Redgum VIC"]


def test_markdown_tables_skip_the_alignment_row_and_the_outer_pipes():
    text = ("| Name | Home suburb | Site | Start date |\n|---|:---:|---|---|\n"
            "| Mara | Quillbarrow | Depot North | 2027-03-01 |\n| Ravi | Tarnwick Flat | Depot South | 01/03/2027 |\n")
    assert found(text) == ["Quillbarrow", "Tarnwick Flat"]


@pytest.mark.parametrize("header", [
    "residence_postcode", "home suburb", "postcode of residence", "home address", "residential town",
    "lives in", "place of residence", "Home-Postcode", "HomeAddress", "ResidencePostcode", "Residential Address",
    "home post code", "Postal code of residence", "residence", "Home City", "RESIDENCE SUBURB", "lives at",
])
def test_headers_that_name_where_a_person_lives_are_residence_columns(header):
    assert found(f"name,{header}\nMara Lindahl,Quillbarrow\nRavi Example,2741\n") == ["Quillbarrow", "2741"]


@pytest.mark.parametrize("header", [
    "site", "card_return_point", "office_postcode", "branch suburb", "postcode", "suburb", "Post Code", "address",
    "work suburb", "workplace postcode", "business address", "delivery address", "return address", "campus",
    "ward", "depot postcode", "home office postcode", "home delivery address", "country of residence",
    "residence status", "home phone", "home", "home email address", "town", "city", "name", "role",
])
def test_workplace_business_and_bare_location_headers_are_not_residence_columns(header):
    assert found(f"name,{header}\nMara Lindahl,Quillbarrow\nRavi Example,2741\n") == []


def test_prose_is_not_a_header():
    # Longer than any residence column header, and a header holding digits is a data row.
    assert found("The home address of the person who owns the account, as given, Quillbarrow\nx,y\n") == []
    assert found("home address 2,suburb\nQuillbarrow,2741\n") == []


def test_only_non_empty_cells_are_protected_and_a_table_ends_at_a_short_row_or_a_blank_line():
    text = "name,home suburb,site\nMara,,Depot North\nRavi,Quillbarrow,Depot South\nJoy,-,Depot East\n\nHome 9999,x,y\n"
    assert found(text) == ["Quillbarrow"]
    assert found("name,home suburb\nMara,Quillbarrow\nThe home suburb of Ravi is Pemberby.\n") == ["Quillbarrow"]


def test_two_residence_columns_are_both_protected():
    text = "name,home suburb,residence postcode\nMara,Quillbarrow,2741\n"
    assert found(text) == ["Quillbarrow", "2741"]


def test_the_switch_turns_the_pass_off():
    text = leavers("\t")
    try:
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != "location-columns"])
        assert found(text) == []
        configure_detection_fixes("none")
        assert found(text) == []
        configure_detection_fixes(None)
        assert found(text) == POSTCODES
    finally:
        configure_detection_fixes(None)


def test_the_whole_gate_protects_every_residence_postcode():
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets, detect_batch

    from router_fakes import Finders, Tracer

    text = leavers("\t")
    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,))

    def protected():
        targets = DetectionTargets(tracer=Tracer(), llm_client=Finders())
        result = detect_batch([text], profile, targets, thorough=True)[0]
        return {text[f.start:f.end] for f in result.findings}

    try:
        assert set(POSTCODES) <= protected()
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != "location-columns"])
        assert not set(POSTCODES) & protected()
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the pass was written from one table, so run it over every evaluation case. A cell it
# protects must never overlap a hard negative (a site, a business address: the things this pass must leave alone), and
# it must still cover the gold spans it exists for.
def test_no_protected_cell_of_the_evaluation_sets_overlaps_a_hard_negative():
    started = time.perf_counter()
    cells, covered, hits, fo_025 = 0, 0, [], 0
    for path in sorted((ROOT / "eval" / "cases").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            spans = _cells(text)
            cells += len(spans)
            hits += [
                (case["id"], text[a:b], negative["kind"]) for a, b in spans for negative in case["hard_negatives"]
                if negative["start"] < b and a < negative["end"]
            ]
            gold = [s for s in case["spans"] if any(a < s["end"] and s["start"] < b for a, b in spans)]
            covered += len(gold)
            if case["id"] == "FO-025":
                fo_025 = sum(1 for s in gold if s["label"] == "ADDRESS" and len(s["value"]) == 4)
    assert hits == []
    assert cells >= covered >= 300  # 310 gold spans in 351 cells on the sets that hold such a column
    assert fo_025 >= 9  # the nine residence postcodes of split F's FO-025
    assert time.perf_counter() - started < 30