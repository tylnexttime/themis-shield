"""v0.5 fix "indirect-statements", and split G's additions to "negative-statements".

Split G's finders missed sensitive statements that do not name their category, in the taxonomy's own examples ("did
two years inside", "is on a community corrections order", "leaves early on Fridays for Shabbat", a person's union
delegate role), and three more negative shapes ("has never been the subject of any police check", "does not have any
other court orders", "has no family violence indicators on file", a "No" after a form line ending with ":").
Synthetic only: every sentence below is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time

import pytest

from themis_shield.detectors.propagate import propagate
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Label

ROOT = Path(__file__).resolve().parents[1]
SWITCH = "indirect-statements"
SENSITIVE = (Label.HEALTH, Label.DISABILITY, Label.ETHNICITY, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
             Label.FAMILY_VIOLENCE, Label.UNION)


def statements(text, stage="indirect-statements"):
    """(label, text) of every finding the named pass made, through the propagation entry point."""
    return [
        (f.primary_label, text[f.start:f.end]) for f in propagate(text, ())
        if any(item.stage == stage for item in f.evidence)
    ]


def sensitive(text):
    """(label, text) of every sensitive finding, whatever made it (the regression test does not name the pass)."""
    return [
        (label, text[f.start:f.end]) for f in propagate(text, ()) for label in f.labels if label in SENSITIVE
    ]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_policy_statements():
    assert DETECTION_FIXES[DETECTION_FIXES.index("policy-statements") + 1] == SWITCH
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)


# Regression (split G's shapes, invented words): before this change none of these was a sensitive finding.
@pytest.mark.parametrize("text, label, value", [
    # indirect-statements
    ("Item 4: a Yard Hand said he did eighteen months inside years ago; noted only.",
     Label.CRIMINAL, "did eighteen months inside years ago"),
    ('He calls those years "doing time inside." He now runs the tool store.', Label.CRIMINAL, "doing time inside"),
    ("CR-2207,LT-11873,completed a community corrections order in 2024; follow-up booked\n",
     Label.CRIMINAL, "completed a community corrections order in 2024"),
    ("Odile told the panel that she is on a community corrections order until March.",
     Label.CRIMINAL, "is on a community corrections order until March"),
    ("Kofi asked us to note that he is out on parole and reports on Mondays.", Label.CRIMINAL, "is out on parole"),
    ("Team, Avram has asked me to share that he leaves early on Fridays for Shabbat and thanks you all.",
     Label.RELIGION, "leaves early on Fridays for Shabbat"),
    ("Catering note: Leah told us she keeps kosher, so no order for her.", Label.RELIGION, "keeps kosher"),
    ("Your colleague Ivo, a Rigger at North Quay, told you that he carries the delegate's card for the yard crews.",
     Label.UNION, "carries the delegate's card for the yard crews"),
    ("CR-3310,,Gardener,Elm Depot,,CC0100,,,Mows the parks; I am the delegate contact for field staff on Sundays\n",
     Label.UNION, "I am the delegate contact for field staff on Sundays"),
    # negative-statements (split G's additions)
    ("For the record, I have never been the subject of any police check.", Label.CRIMINAL,
     "I have never been the subject of any police check"),
    ("The interim intervention order was extended. Tane said he does not have any other court orders in place.",
     Label.FAMILY_VIOLENCE, "Tane said he does not have any other court orders in place"),
    ("Two files flagged. File 3 has no family violence indicators on file, so standard letters apply.",
     Label.FAMILY_VIOLENCE, "File 3 has no family violence indicators on file"),
    ("5.2 I do not identify as Aboriginal or Torres Strait Islander: No\n", Label.ETHNICITY, "No"),
])
def test_split_g_statements_are_sensitive_findings(text, label, value):
    assert (label, value) in sensitive(text)


@pytest.mark.parametrize("text, label, value", [
    ("Ama told me she served two years inside before she trained as a welder.", Label.CRIMINAL,
     "served two years inside before she trained as a welder"),
    ("I did time inside when I was young.", Label.CRIMINAL, "did time inside when I was young"),
    ("Roza finished her community corrections order last spring.", Label.CRIMINAL,
     "finished her community corrections order last spring"),
    ("Pita was granted bail on Tuesday.", Label.CRIMINAL, "was granted bail on Tuesday"),
    ("He sees his parole officer every Monday.", Label.CRIMINAL, "sees his parole officer every Monday"),
    ("Noor said she only eats halal.", Label.RELIGION, "eats halal"),
    ("Please order his halal meal for the Thursday course.", Label.RELIGION, "his halal meal for the Thursday course"),
    ("He observes the Sabbath.", Label.RELIGION, "observes the Sabbath"),
    ("Mere is the delegate contact for the night shift.", Label.UNION, "is the delegate contact for the night shift"),
])
def test_more_indirect_shapes(text, label, value):
    assert (label, value) in statements(text)


@pytest.mark.parametrize("text", [
    # Policy: no person is said to be anything.
    "Staff on a community corrections order may apply for flexible rostering.",
    "If an applicant is on parole, they must tell the screening officer.",
    "Any worker who is on bail can ask for a roster change.",
    "The canteen offers halal options on weekdays.",
    "The office closes early on Fridays for Shabbat.",
    "Delegates carry a card issued by the branch.",
    "Prisoners who did time inside are offered a mentor.",
    # Sentences about an organisation, a building or a meal.
    "The council finished its review of the community corrections order program.",
    "The contractor spent two years inside the tunnel project.",
    "The chicken is halal and the kitchen keeps it separate.",
    "The kitchen keeps kosher on Fridays.",
    # A name with no pronoun: "keeps kosher" and "observes the Sabbath" could be a kitchen's; left to the finders.
    "Dov observes the Sabbath.",
    "Our firm is the delegate contact for the regional board.",
    # Indoors, not prison; a conference delegate; a band.
    "He did two years inside the head office before moving to the depot.",
    "She spent time inside because of the rain.",
    "He was a delegate at the conference and carries a lanyard.",
    "He has tickets for Black Sabbath.",
    # Fiction is not a person (taxonomy section 4, fiction_statement).
    "Short story: The Ferry\n\nThe narrator meets a man. He did two years inside, or so he said.\n",
    "CHAPTER NINE\n\nThe porter said the patient is on parole and must report on Christmas Day.\n",
])
def test_policy_organisations_and_look_alikes_are_not_statements(text):
    assert statements(text) == []


@pytest.mark.parametrize("text", [
    "Police check required: Yes\n",
    "Aboriginal or Torres Strait Islander liaison officer on site: Yes\n",
    "Do you identify as Aboriginal or Torres Strait Islander: Yes / No\n",
    "The applicant must have a national police check: Yes\n",
    "The company does not have any court orders against it.",
    "The tenancy form says the agency has no family violence policy on file.",
])
def test_negative_look_alikes_are_not_statements(text):
    assert statements(text, "negative-statements") == []


def test_court_orders_are_criminal_without_family_violence_context():
    text = "Background check done. He confirmed he does not have any court orders in place."
    assert statements(text, "negative-statements") == [
        (Label.CRIMINAL, "He confirmed he does not have any court orders in place")]


def test_the_switch_turns_the_pass_off():
    text = "Odile told the panel that she is on a community corrections order until March.\n"
    expected = [(Label.CRIMINAL, "is on a community corrections order until March")]
    try:
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])
        assert statements(text) == [] and sensitive(text) == []
        configure_detection_fixes("none")
        assert statements(text) == []
        configure_detection_fixes(None)
        assert statements(text) == expected
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the vocabulary was written from split G's misses, so run every span it makes over
# every evaluation set (split G's adjudicated files): it must never overlap a hard negative, never land in a document
# without personal information, and each must cover a gold span of its own category. For negative-statements only the
# spans split G's additions make are counted here (the rest of that pass has its own guard).
# One known gold omission: GR-097 quotes "did eighteen months inside in my twenties" and restates it two sentences
# later; only the restatement is gold CRIMINAL, though the taxonomy labels every indirect statement (section 3).
_GOLD_OMISSIONS = {("GR-097", 862)}
_NEVER = re.compile(r"(?!)")
_CASE_FILES = [p for p in sorted((ROOT / "eval" / "cases").glob("*.jsonl"))
               if p.name.endswith(".adjudicated.jsonl") or not p.name.startswith("test-g-")]
# Spans per set when written: dev 5, C 3, D-c 24, D-q 10, E 13, F 18, G-r 12, G-t 15 (4 of them negative), test 5.
_FLOORS = {"dev": 5, "test-c": 3, "test-d-c": 24, "test-d-q": 10, "test-e-k": 13, "test-f-o": 18,
           "test-g-r.adjudicated": 12, "test-g-t.adjudicated": 15, "test": 5}


def _new_spans(text, monkeypatch):
    from themis_shield.detectors import statements as st

    items = [(f.start, f.end, f.primary_label) for f in st.indirect_statement_spans(text)]
    after = {(f.start, f.end, f.primary_label) for f in st.negative_statement_spans(text)}
    with monkeypatch.context() as patch:
        patch.setattr(st, "_NEGATIVE_RECORDS", ())
        patch.setattr(st, "_COURT_ORDERS", _NEVER)
        patch.setattr(st, "_ANSWERED_FORM_LINE", _NEVER)
        before = {(f.start, f.end, f.primary_label) for f in st.negative_statement_spans(text)}
    return items + sorted(after - before)


@pytest.mark.parametrize("path", _CASE_FILES, ids=lambda p: p.name.removesuffix(".jsonl"))
def test_no_new_statement_of_the_evaluation_sets_overlaps_a_hard_negative(path, monkeypatch):
    started = time.perf_counter()
    made, hits, clean, wrong = 0, [], [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        text = case["text"]
        for a, b, label in _new_spans(text, monkeypatch):
            made += 1
            hits += [(case["id"], text[a:b], n["kind"]) for n in case["hard_negatives"] if n["start"] < b and a < n["end"]]
            if not case.get("case_level", {}).get("contains_pi", True):
                clean.append((case["id"], text[a:b]))
            gold = [s for s in case["spans"] if s["start"] < b and a < s["end"]]
            if not any(s["label"] == label.value for s in gold) and (case["id"], a) not in _GOLD_OMISSIONS:
                wrong.append((case["id"], text[a:b]))
    assert hits == [] and clean == [] and wrong == []
    assert made >= _FLOORS[path.name.removesuffix(".jsonl")]
    assert time.perf_counter() - started < 30
