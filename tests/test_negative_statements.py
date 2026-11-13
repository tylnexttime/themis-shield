"""v0.5 fix "negative-statements": a statement that a person does not have a sensitive attribute is still a statement.

Split F's finders found the positive statements and missed most negative ones ("No disclosable court outcomes", "is
not on any medication", "denies any thoughts of self-harm"). Synthetic only: every sentence below is invented.
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
SWITCH = "negative-statements"
STAGE = "negative-statements"
SENSITIVE = (Label.HEALTH, Label.DISABILITY, Label.ETHNICITY, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
             Label.FAMILY_VIOLENCE, Label.UNION)


def statements(text):
    """(label, text) of every finding the negative-statement pass made, through the propagation entry point."""
    return [
        (f.primary_label, text[f.start:f.end]) for f in propagate(text, ())
        if any(item.stage == STAGE for item in f.evidence)
    ]


def sensitive(text):
    """(label, text) of every sensitive finding, whatever made it (the regression test does not name the pass)."""
    return [
        (label, text[f.start:f.end]) for f in propagate(text, ()) for label in f.labels if label in SENSITIVE
    ]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_propagate_detection_view():
    assert DETECTION_FIXES[DETECTION_FIXES.index("propagate-detection-view") + 1] == SWITCH
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)


# Regression (split F's shapes, invented words): before the switch none of these was a sensitive finding.
@pytest.mark.parametrize("text, label, value", [
    ("C4. Contractor: National Police Check result\n  Answer: No disclosable court outcomes\n",
     Label.CRIMINAL, "No disclosable court outcomes"),
    ("Q7. Have you been convicted of an offence?\nReply (labourer): No matters declared. Form attached.\n",
     Label.CRIMINAL, "No matters declared"),
    ("Lena told us the police clearance from her time in Canada was needed. The certificate shows no matters.",
     Label.CRIMINAL, "The certificate shows no matters"),
    ("Pre-start checklist done. Tomas is not on any medication that affects driving.",
     Label.HEALTH, "Tomas is not on any medication that affects driving"),
    ("Ex-smoker. No regular medications. Lives alone.", Label.HEALTH, "No regular medications"),
    ("She denies any thoughts of self-harm and has a safety plan.", Label.HEALTH,
     "She denies any thoughts of self-harm and has a safety plan"),
    ("When asked privately, he confirmed that he is not experiencing family violence; the leave was miscoded.",
     Label.FAMILY_VIOLENCE, "he confirmed that he is not experiencing family violence"),
    ("Deductions: none. I am not a member of any union at the moment, so no fee applies.",
     Label.UNION, "I am not a member of any union at the moment"),
    ("Is the client of Aboriginal or Torres Strait Islander origin? No\n", Label.ETHNICITY, "No"),
])
def test_negative_statements_are_sensitive_findings(text, label, value):
    assert (label, value) in sensitive(text)
    assert (label, value) in statements(text)


@pytest.mark.parametrize("text, label, value", [
    ("Rhys has never been charged with any offence.", Label.CRIMINAL, "Rhys has never been charged with any offence"),
    ("Background: she has no criminal record.", Label.CRIMINAL, "she has no criminal record"),
    ("The overseas check came back with no disclosable court outcomes.", Label.CRIMINAL,
     "The overseas check came back with no disclosable court outcomes"),
    ("Has the worker any criminal convictions? None\n", Label.CRIMINAL, "None"),
    ("Do you have a disability? Prefer not to say\n", Label.DISABILITY, "Prefer not to say"),
])
def test_more_negative_shapes(text, label, value):
    assert (label, value) in statements(text)


@pytest.mark.parametrize("text", [
    # Policy and templates: no person is said to lack anything.
    "Do you have a disability? Yes / No\n",
    "Is the applicant of Aboriginal or Torres Strait Islander origin? Yes or No\n",
    "No medication may be stored in the staff fridge.",
    "A conviction does not automatically exclude you from the role.",
    "Union delegates may attend the meeting. Membership of a union is voluntary.",
    # Matters and outcomes with no criminal context are not criminal.
    "Board minutes: no matters declared under conflicts of interest.",
    "The building check shows no matters requiring action.",
    # A company's record is not a person's.
    "In sentencing, the magistrate said the company had no prior convictions.",
    "The company pleaded guilty to two charges under the safety act. No conviction was recorded.",
    # Denying something that is not a health matter.
    "The contractor denies any wrongdoing.",
    # A question without an answer, and an answer to a question that names no sensitive category.
    "Is the patient of Aboriginal or Torres Strait Islander origin?\n",
    "Is parking available on site? No\n",
])
def test_look_alikes_are_not_statements(text):
    assert statements(text) == []


# DD-26 (split G's GT-008, a clean FAQ; invented words): a general rule about a class of people that mentions a
# negative ("a person with no criminal record is treated the same as ...") is policy, not a statement about anyone.
@pytest.mark.parametrize("text", [
    "A person with no convictions is treated the same as any other volunteer once the check is returned.",
    "Any applicant with no criminal history may skip the second interview.",
    "Anyone who has no criminal record pays the standard fee.",
    "Employees with no criminal record are not required to repeat the check.",
    "Staff with no police record can start on the same day.",
    "People who have no convictions need not attend the briefing.",
    "3.2 What happens if a person has no criminal record? Nothing changes.",
    "Does any applicant with no criminal record need a second check?",
])
def test_a_general_rule_about_a_class_of_people_is_not_a_statement(text):
    assert statements(text) == []


# The same words said of someone (a name, a pronoun, "I", an appositive) keep matching.
@pytest.mark.parametrize("text, value", [
    ("He has no criminal record, so the sign-off can proceed.", "He has no criminal record"),
    ("Theo has never been charged with anything.", "Theo has never been charged with anything"),
    ("I have no convictions.", "I have no convictions"),
    ("Mira is a person with no criminal record.", "Mira is a person with no criminal record"),
    ("Priya Nand, a person with no criminal record, applied on Friday.", "a person with no criminal record"),
    ("She said that any person with no criminal record would pass, and that she has no criminal record.",
     "and that she has no criminal record"),
    ("The applicant, who has no criminal record, was told to wait.", "who has no criminal record"),
])
def test_a_negative_about_a_named_or_pronoun_subject_still_matches(text, value):
    assert (Label.CRIMINAL, value) in statements(text)


def test_a_list_under_a_generic_heading_and_a_form_answer_still_match():
    text = "Staff with no criminal record: Dana Smith, Ravi Patel.\n"
    assert statements(text) and all(label is Label.CRIMINAL for label, _ in statements(text))
    text = ("Applicants with no criminal record are treated as any other applicant.\n"
            "Has the worker any criminal convictions? None\n")
    assert statements(text) == [(Label.CRIMINAL, "None")]
    text = "A person with no convictions is treated as any other volunteer. Rhys has no criminal record."
    assert statements(text) == [(Label.CRIMINAL, "Rhys has no criminal record")]


def test_matters_need_a_criminal_word_nearby_and_the_statement_is_the_clause():
    tail = "06:10 HR-2231 note: the renewed check shows no new matters, and access can go ahead."
    assert statements(tail) == []
    text = "Her old police record was declared at hiring. " + tail
    assert statements(text) == [(Label.CRIMINAL, "the renewed check shows no new matters")]


def test_a_quoted_sentence_ends_at_its_closing_quote():
    text = 'Asked about her past, she said: "I have no criminal record." The fee was refunded under PO-1182.'
    assert statements(text) == [(Label.CRIMINAL, "I have no criminal record")]


def test_the_switch_turns_the_pass_off():
    text = "Answer: No disclosable court outcomes\nOn review he is not on any medication that affects driving.\n"
    expected = [(Label.CRIMINAL, "No disclosable court outcomes"),
                (Label.HEALTH, "On review he is not on any medication that affects driving")]
    try:
        configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])
        assert statements(text) == [] and sensitive(text) == []
        configure_detection_fixes("none")
        assert statements(text) == []
        configure_detection_fixes(None)
        assert sorted(statements(text)) == sorted(expected)
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the pass was written from split F, so run it over every evaluation case. A statement
# it makes must never overlap a hard negative, never land in a document without personal information (that would hold
# a clean document), and each must cover a gold sensitive span of its own category.
# Split G (published 12/11/2026 with eval/RESULTS-v0.6.md) came after this switch shipped in 0.4.0 and broke the guard
# (DEV-DEBT DD-26): of its statements, three covered no gold span of their category. Two were gold omissions, now
# corrected in the adjudicated files (``eval/cases/test-g-*.adjudicated.jsonl``; the sealed ones stay as they were), so
# split G is read from those; the third was a general rule about "a person with no criminal record" in a clean FAQ,
# which the switch no longer matches. The guard is strict on every set.
_CASE_FILES = sorted(p for p in (ROOT / "eval" / "cases").glob("*.jsonl") if not p.name.endswith(".adjudicated.jsonl"))
_GUARD_SETS = [
    pytest.param([p for p in _CASE_FILES if not p.name.startswith("test-g-")], (40, 50), id="sets-before-split-g"),
    pytest.param(sorted((ROOT / "eval" / "cases").glob("test-g-*.adjudicated.jsonl")), (12, 14), id="split-g"),
]


@pytest.mark.parametrize(("paths", "floor"), _GUARD_SETS)
def test_no_negative_statement_of_the_evaluation_sets_overlaps_a_hard_negative(paths, floor):
    from themis_shield.detectors.statements import negative_statement_spans

    started = time.perf_counter()
    made, covered, hits, clean, wrong = 0, 0, [], [], []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            for item in negative_statement_spans(text):
                a, b = item.start, item.end
                made += 1
                hits += [(case["id"], text[a:b], n["kind"]) for n in case["hard_negatives"]
                         if n["start"] < b and a < n["end"]]
                if not case.get("case_level", {}).get("contains_pi", True):
                    clean.append((case["id"], text[a:b]))
                gold = [s for s in case["spans"] if s["start"] < b and a < s["end"]]
                covered += len(gold)
                if not any(s["label"] == item.primary_label.value for s in gold):
                    wrong.append((case["id"], text[a:b]))
    assert hits == [] and clean == [] and wrong == []
    assert made >= floor[0] and covered >= floor[1]  # 42 statements covering 56 gold spans when written
    assert time.perf_counter() - started < 30


# ----------------------------------------------------------------------------------------------------------------
# FAB-02 (independent review of candidate 0.5): the DD-26 exemption read any bare plural at the start of a sentence as
# a general rule, so a case note's "Parents have no criminal record." (two identifiable people) was released. A bare
# plural is specific unless an explicit generic marker is present: a quantifier ("any", "every", "all") or a
# "with/who/that" class followed by a modal rule ("may", "can", "are not required", "need not"). 0.4.0 held every one
# of these; the development sets passed with the bug present, so these cases are the guard. Invented sentences.
FAB_02_MUST_HOLD = [
    "Home visit completed. Parents have no criminal record. Child settled at school.",
    "Parents have no criminal record.",
    "Parents have no criminal convictions and no child protection history.",
    "Carers have never been charged.",
    "Tenants have no criminal history.",
    "Clients have never been convicted.",
    "Students have no criminal record",
    "Both crew members have no criminal record.",
    "Parents with no criminal record were interviewed on Tuesday.",
    "Parents have no criminal record and may collect the child.",
]


@pytest.mark.parametrize("text", FAB_02_MUST_HOLD)
def test_fab_02_a_bare_plural_subject_is_not_a_general_rule(text):
    from themis_shield.detectors.statements import negative_statement_spans

    assert any(Label.CRIMINAL in f.labels for f in negative_statement_spans(text))
    assert (Label.CRIMINAL in {label for label, _ in sensitive(text)})


@pytest.mark.parametrize("text", FAB_02_MUST_HOLD[:2] + FAB_02_MUST_HOLD[3:4])
def test_fab_02_the_whole_gate_holds_it_as_in_v0_4(text):
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets, detect_batch
    from themis_shield.types import Decision, ReasonCode
    from test_policy_statements import Finder, Tracer

    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,))
    result = detect_batch([text], profile, DetectionTargets(tracer=Tracer(), llm_client=Finder()), thorough=True)[0]
    assert result.policy_result.decision == Decision.ASK
    assert ReasonCode.SENSITIVE in result.policy_result.reasons
