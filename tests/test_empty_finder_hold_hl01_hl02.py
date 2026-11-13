"""Hold-lift review (HL-01, HL-02; 13/11/2026): under the DEFAULT switches every one of these documents is held for the
Operator (ASK, empty-finder disagreement), as in candidate 0.5 (``62b5865``).

``empty-finder-substitute`` was a default for a few hours on development replays alone. An independent adversarial
review then ran the real PII-Tracer over invented documents with both finders silent: a JSON ticket that says a caller
is staying at a refuge, an SMS call with an HIV result, a YAML record, and one sentence about "the night cook" in the
middle of a depot procedure were ASK in candidate 0.5 and RELEASE at that head, statement in clear. The steward's
decision (13/11/2026 17:50 UTC): a leak is worse than friction, so the switch is opt-in, off by default.

No model is needed here. Each case carries what the real tracer returned for that text in the review (its findings
and its document sensitivity score, copied from the reviewer's output ``repro1.out``); a fake tracer returns exactly
that, both finders return nothing, and everything after the tracer is the real code, with the shipped rule packs
(``core`` only, as in the review; the default-hold test also runs with the regional packs on). Invented text only.

The second half records, honestly, what the opt-in switch still does with the same documents: it releases them.
That is why it is opt-in. The table is a record of a known hazard, not a target; a statement-level check that reads
the text is what the switch would need before anyone reconsiders it (DEV-DEBT DD-37).
"""

from __future__ import annotations

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.rules import set_rule_packs
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, OPT_IN_DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.policy import configure_policy
from themis_shield.types import Decision, Label, ReasonCode

SWITCH = "empty-finder-substitute"
HOLD = ReasonCode.DISAGREEMENT_EMPTY_LLM
PHONE = "0491 570 156"

# The reviewer's fillers and sentences (repro1.py), unchanged.
PROC = "\n".join(
    f"{i}. Pallets for bay {i} are wrapped before 14:00 and logged in the stores system; forklifts are charged "
    f"overnight and the dock door is closed after the last truck." for i in range(1, 45)) + "\n"
CFG = "\n".join(
    f'  "job_{i}": {{"cron": "0 {i % 24} * * *", "retries": 3, "queue": "sync-{i}", "timeout_s": {30 + i}}},'
    for i in range(120)) + "\n"
S1 = "Rosters desk: 0491 570 156. The night cook is back in rehab and wears an ankle monitor, keep it quiet.\n"
S2 = ("Rosters desk: 0491 570 156. Heads up: the only woman on the night crew is pregnant and has an AVO against her "
      "ex.\n")


def _in_procedure(chars):
    return PROC[:chars // 2] + S1 + PROC[chars // 2:chars]


# id -> (text, what the real tracer found [(value, label)], its sensitivity score, the statement nobody protected)
CASES = {
    # HL-01: structured text scores near zero whatever its fields say.
    "A1-json-ticket": (
        '{"ticket": 4471902, "contact": "0491 570 156", "summary": "wife of the depot manager; staying at a refuge, '
        'do not disclose location to him", "status": "open"}',
        [("4471902", Label.STUDENT_ID), (PHONE, Label.PHONE)], 0.0002, "staying at a refuge"),
    "A2-json-name-in-path": (
        '{"callback": "0491 570 156", "file": "/hr/cases/okafor_dana_rehab_2026.pdf", "priority": 2}',
        [(PHONE, Label.PHONE)], 0.0002, "okafor_dana_rehab"),
    "A3-json-tags": (
        '[{"id": 1, "phone": "0491 570 156", "tags": ["hiv+", "on parole"], "ok": true}]',
        [(PHONE, Label.PHONE)], 0.0004, '"hiv+", "on parole"'),
    "A4-code": (
        'send_sms("0491 570 156", "Your HIV test result is positive. Please call the clinic.")',
        [(PHONE, Label.PHONE)], 0.0095, "Your HIV test result is positive"),
    "A5-yaml": (
        "ticket: 4471902\ncontact: 0491 570 156\nsummary: on methadone program, court date pending\nstatus: open\n",
        [("4471902", Label.STUDENT_ID), (PHONE, Label.PHONE)], 0.0024, "on methadone program, court date pending"),
    # HL-05: government identifiers and a surname arrive as the tracer's account number.
    "A6-government-ids": (
        '{"medicare": "2123 45670 1", "passport": "PA1234567", "phone": "0491 570 156", "note": "on methadone '
        'program"}',
        [("2123 45670 1", Label.STUDENT_ID), ("PA1234567", Label.STUDENT_ID), (PHONE, Label.PHONE)], 0.0003,
        "on methadone program"),
    "A7-spaced-name": (
        '{"callback": "0491 570 156", "note": "D a n a  O k a f o r relapsed, back in rehab"}',
        [(PHONE, Label.PHONE)], 0.0034, "D a n a  O k a f o r relapsed, back in rehab"),
    "A8-surname-as-identifier": (
        '{"callback": "0491 570 156", "note": "OKAFOR relapsed, back in rehab", "priority": 2}',
        [(PHONE, Label.PHONE), ("OKAFOR", Label.STUDENT_ID)], 0.0012, "relapsed, back in rehab"),
    # HL-06: the tracer's span starts on the opening quote and stops before the remark.
    "A9-partial-phone-span": (
        '{"contact": "+61 (0)491 570 156 ext 22 (ask for the deaf lady)", "priority": 2}',
        [('"+61 (0)491 570 156 ext 22', Label.PHONE)], 0.0012, "ask for the deaf lady"),
    # HL-02: one personal sentence diluted in ordinary text (the sentence alone scores 0.0575 and 0.0431).
    "B1-prose-in-1500-chars-of-procedure": (
        _in_procedure(1500), [(PHONE, Label.PHONE)], 0.0028, "The night cook is back in rehab and wears an ankle monitor"),
    "B1-prose-in-3000-chars-of-procedure": (
        _in_procedure(3000), [(PHONE, Label.PHONE)], 0.0039, "The night cook is back in rehab and wears an ankle monitor"),
    "B2-prose-in-3000-chars-of-config": (
        CFG[:1500] + S2 + CFG[1500:3000], [(PHONE, Label.PHONE)], 0.0006,
        "the only woman on the night crew is pregnant and has an AVO against her ex"),
}
# The reviewer's controls: held with and without the switch (the tracer called "Rosters" a name in the short document;
# the sentences alone score above the switch's limit).
HELD_EITHER_WAY = {
    "B1-prose-in-600-chars-of-procedure": (
        _in_procedure(600), [("Rosters", Label.PERSON), (PHONE, Label.PHONE)], 0.0059,
        "The night cook is back in rehab and wears an ankle monitor"),
    "B0-the-sentence-alone": (S1, [(PHONE, Label.PHONE)], 0.0575, "The night cook is back in rehab"),
    "B0-the-second-sentence-alone": (S2, [(PHONE, Label.PHONE)], 0.0431, "the only woman on the night crew is pregnant"),
}
ALL = {**CASES, **HELD_EITHER_WAY}


class RecordedTracer:
    """Returns what the real PII-Tracer returned for this text in the review: no model."""

    def __init__(self, text, found, sensitivity):
        self.findings = tuple(
            finding(text.index(value), text.index(value) + len(value), label, "pii-tracer", 1) for value, label in found)
        self.sensitivity = sensitivity

    def detect(self, text):
        persons = sum(item.primary_label == Label.PERSON for item in self.findings)
        return StageResult(self.findings, StageReport("pii-tracer", Status.OK, len(self.findings), persons,
                                                      self.sensitivity))


class SilentFinders:
    """Both LLM finders return nothing: the precondition of the empty-finder hold."""

    def chat_json(self, model_role, messages, schema, max_tokens):
        return {"entities": []}


def gate(case_id):
    text, found, sensitivity, _ = ALL[case_id]
    targets = DetectionTargets(tracer=RecordedTracer(text, found, sensitivity), llm_client=SilentFinders(),
                               thorough=True)
    return detect(text, {}, targets)


def unprotected(case_id, result):
    """The statement is in the text the gate would send: no finding touches it."""
    text, statement = ALL[case_id][0], ALL[case_id][3]
    start = text.index(statement)
    return not any(item.start < start + len(statement) and start < item.end for item in result.findings)


@pytest.fixture(autouse=True)
def _defaults(_rule_packs):
    # The suite's shared fixture turns the Australian rule pack on; the review ran the shipped default (core only).
    set_rule_packs(None)
    configure_detection_fixes(None)
    configure_policy(None)
    yield
    configure_detection_fixes(None)
    configure_policy(None)


def test_the_switch_is_opt_in():
    assert SWITCH in OPT_IN_DETECTION_FIXES and SWITCH not in DEFAULT_DETECTION_FIXES and not fix_on(SWITCH)


@pytest.mark.parametrize("case_id", ALL)
def test_the_fake_tracer_reproduces_the_reviews_findings(case_id):
    # The findings the review printed for this text (after rules, propagation and the allow-list) are the ones this
    # test's pipeline ends with: nothing else found the statement, and the recorded tracer is not doing extra work.
    text, found, sensitivity, _ = ALL[case_id]
    result = gate(case_id)
    assert sorted((text[item.start:item.end], sorted(item.labels)) for item in result.findings) == sorted(
        (value, [label]) for value, label in found)
    assert result.doc_sensitivity == sensitivity
    assert unprotected(case_id, result)


@pytest.mark.parametrize("packs", [None, ("core", "au"), ("core", "au", "intl")], ids=["shipped", "au", "au-intl"])
@pytest.mark.parametrize("case_id", ALL)
def test_default_switches_hold_it_for_the_operator_as_in_candidate_0_5(case_id, packs):
    set_rule_packs(packs)
    result = gate(case_id)
    assert result.decision == Decision.ASK
    assert result.reasons == (HOLD,)
    assert unprotected(case_id, result)  # held with the statement still unprotected: the hold is what protects it


# ----------------------------------------------------------------------------------------------------------------------
# What the opt-in switch still does (a record of the known hazard, not a target): with the switch on, the documents
# below leave the gate with the statement in clear. Structured text and a diluted sentence score under the switch's
# sensitivity limit, and with the finders silent nothing else reads statements.
# ----------------------------------------------------------------------------------------------------------------------
RELEASED_WITH_THE_SWITCH_ON = set(CASES)


@pytest.mark.parametrize("case_id", ALL)
def test_what_the_opt_in_switch_releases(case_id):
    configure_detection_fixes([*DEFAULT_DETECTION_FIXES, SWITCH])
    result = gate(case_id)
    if case_id in RELEASED_WITH_THE_SWITCH_ON:
        assert result.decision == Decision.RELEASE and result.reasons == ()
        assert unprotected(case_id, result)  # the sensitive statement leaves in clear: the reason it is opt-in
    else:
        assert result.decision == Decision.ASK and result.reasons == (HOLD,)


def test_the_hazard_is_recorded_for_every_shape_the_review_named():
    # Structured text (JSON, YAML, code) and a sentence diluted in a long document are both on record as released
    # with the switch on, and the review's controls as held.
    assert {case_id[:2] for case_id in RELEASED_WITH_THE_SWITCH_ON} == {"A1", "A2", "A3", "A4", "A5", "A6", "A7",
                                                                        "A8", "A9", "B1", "B2"}
    assert not RELEASED_WITH_THE_SWITCH_ON & set(HELD_EITHER_WAY)
