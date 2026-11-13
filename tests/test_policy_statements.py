"""v0.5 fix "policy-statements": a sensitive "statement" that is a general rule about nobody is not personal data.

Split G's finders (and the union-role pass) held three clean documents for sentences such as "staff with a disability
can request reasonable adjustments" (taxonomy section 3: sensitive statements are labelled only when they are about a
person; section 4: ``policy_statement``). The pass drops a sensitive finding whose clause is plainly a general rule, by
fixed wording only. Synthetic only: every sentence below is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time

import pytest

from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, fix_on, validate_detection_fixes,
)
from themis_shield.types import Decision, Label, ReasonCode

ROOT = Path(__file__).resolve().parents[1]
SWITCH = "policy-statements"

# The three shapes of split G, in invented words.
YAML_COMMENT = (
    "service: roster-sync\nowner: platform team (no named maintainers)\nretries: 3\n"
    "# Accessibility note: employees with a vision impairment may request a larger font\n"
    "# in every console generated from this file.\n"
)
YAML_RULE = "employees with a vision impairment may request a larger font"
NOTICE = "Applicants with a hearing loss can ask for a captioned interview; requests go to the Rostering Office."
LOG_LINE = (
    "[14:02:11] INFO  Housekeeping: reminder sent to all depot terminals.\n"
    f'[14:02:15] INFO  Notice text: "{NOTICE}" Notice ref NT-5520.\n'
    "[14:02:19] INFO  Heartbeat OK.\n"
)
FAQ = (
    "Q: Can casual staff join a union?\nA: Union matters are governed by the enterprise agreement. The employer does "
    "not ask whether any employee is or is not a union member, and union activity is treated as private.\n"
)


class Tracer:
    def detect(self, text):
        from themis_shield.detectors.contracts import StageReport, StageResult, Status

        return StageResult((), StageReport("pii-tracer", Status.OK))


class Finder:
    """A local-model stand-in that returns the given (label, value) entities that are in the document."""

    def __init__(self, entities=()):
        self.entities = tuple(entities)

    def chat_json(self, model_role, messages, schema, max_tokens):
        content = "\n".join(message["content"] for message in messages)
        return {"entities": [{"label": label, "value": value} for label, value in self.entities if value in content]}


def gate(text, entities=()):
    """The decision result of the whole detection entry point with the given finder answers."""
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets, detect_batch

    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,))
    targets = DetectionTargets(tracer=Tracer(), llm_client=Finder(entities))
    return detect_batch([text], profile, targets, thorough=True)[0]


def reasons(text, entities=()):
    return list(gate(text, entities).policy_result.reasons)


def without_switch():
    configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != SWITCH])


SHAPES = [
    pytest.param(YAML_COMMENT, [("DISABILITY", YAML_RULE)], id="comment-in-a-configuration-file"),
    pytest.param(LOG_LINE, [("DISABILITY", NOTICE)], id="notice-quoted-in-a-log-line-two-clauses"),
    pytest.param(FAQ, [], id="faq-union-role-pass-no-finder"),
]


# ----------------------------------------------------------------------------------------------------------------
def test_the_switch_is_a_default_fix_after_quasi_cells():
    assert DETECTION_FIXES[DETECTION_FIXES.index("quasi-cells") + 1] == SWITCH
    assert SWITCH in DEFAULT_DETECTION_FIXES and fix_on(SWITCH)
    assert validate_detection_fixes(SWITCH) == (SWITCH,)


# Regression: before the switch each of these clean documents was held for ASK:sensitive.
@pytest.mark.parametrize("text, entities", SHAPES)
def test_a_general_rule_no_longer_holds_a_clean_document(text, entities):
    result = gate(text, entities)
    assert result.policy_result.decision == Decision.RELEASE
    assert result.policy_result.reasons == ()
    assert not any(f.labels & {Label.DISABILITY, Label.UNION, Label.HEALTH} for f in result.findings)


@pytest.mark.parametrize("text, entities", SHAPES)
def test_without_the_switch_the_old_behaviour_is_restored(text, entities):
    try:
        without_switch()
        assert reasons(text, entities) == [ReasonCode.SENSITIVE]
        configure_detection_fixes(None)
        assert reasons(text, entities) == []
    finally:
        configure_detection_fixes(None)


@pytest.mark.parametrize("text, entities", SHAPES[:2])
def test_with_no_fixes_a_finders_statement_holds_as_in_v0_4(text, entities):
    try:
        configure_detection_fixes("none")
        assert reasons(text, entities) == [ReasonCode.SENSITIVE]
    finally:
        configure_detection_fixes(None)


# ----------------------------------------------------------------------------------------------------------------
# The clause test, on its own.
@pytest.mark.parametrize("clause", [
    "staff with a disability can request reasonable adjustments",
    "Employees may apply for study leave",
    "Any employee is entitled to ask for a quieter desk",
    "Anyone who is pregnant may ask for a risk assessment",
    "All staff are entitled to a copy of the policy",
    "Applicants are not required to disclose a health condition",
    "customers experiencing family violence can request a secure contact channel",
    "union activity is treated as private",
    "Union matters are governed by the enterprise agreement",
    "The employer does not ask whether any employee is or is not a union member",
    "The organisation does not ask whether applicants have a criminal record",
])
def test_a_general_rule_is_a_policy_clause(clause):
    from themis_shield.detectors.policy_statements import policy_clause

    assert policy_clause(clause)


@pytest.mark.parametrize("clause", [
    # A person: a name, a pronoun, a role held by one.
    "Staff member Dana can request adjustments because of her disability",
    "he has asked whether staff with a disability can request adjustments",
    "as a union member she may apply for the dues rebate",
    "Dana asked whether staff with a disability can request adjustments",
    "Dana's staff with a disability can request adjustments",
    "I think staff with a disability can request adjustments",
    "You may request adjustments if you have a disability",
    "A staff member at the Dock 7 depot with a disability can request adjustments",
    "The only analyst on the overnight roster with a disability can request adjustments",
    "One employee with a disability may request adjustments",
    "The sole contractor with a disability may request adjustments",
    # No generic subject, or no modal or generic verb.
    "Staff with a disability requested adjustments last week",
    "Staff with a disability",
    "can request reasonable adjustments",
    "The employee has a disability and can request adjustments",
    "Our employee Dana is entitled to adjustments",
    "Staff meeting on Friday in May",
    # A statement, not a rule.
    "Mara is a union member",
    "Employees at the Dock 7 depot, Mara among them, use wheelchairs",
])
def test_a_clause_about_a_person_is_not_a_policy_clause(clause):
    from themis_shield.detectors.policy_statements import policy_clause

    assert not policy_clause(clause)


def test_a_span_is_judged_with_the_clauses_it_touches():
    from themis_shield.detectors.policy_statements import policy_statement

    def judged(text, value=None):
        start = text.index(value) if value else 0
        return policy_statement(text, start, start + len(value) if value else len(text))

    both = "Staff with a disability can request adjustments; requests go to the Rostering Office."
    assert judged(both)  # the span runs on into a second clause that says nothing sensitive
    assert judged(both, "Staff with a disability")  # a span inside the rule clause
    # A span that ends with the full stop (and the closing quote) does not reach into the next sentence.
    quoted = 'Notice: "' + both + '" Policy ref Staff with a disability can request adjustments; ' + both
    assert judged(quoted, both)
    assert not judged("Staff with a disability can request adjustments; the night analyst has epilepsy.")
    assert not judged("Staff with a disability can request adjustments; Dana's request was approved.")
    assert not judged("The night analyst has epilepsy; staff with a disability can request adjustments.")
    assert not judged("Staff with a disability can request adjustments. They can also ask for a quiet room.")
    assert not judged("8.2 Tobias Varga, who is the shop steward at Corella, said staff may attend.")
    # Far into a long document the same sentence reads the same; a sentence that runs on for thousands of characters is
    # never read as a rule (and the scan stays local to the span).
    far = "Filler sentence. " * 500 + "Staff with a disability can request adjustments."
    assert judged(far, "Staff with a disability can request adjustments")
    run_on = "plain words " * 400 + "staff with a disability can request adjustments " + "plain words " * 400
    assert not judged(run_on, "staff with a disability can request adjustments")
    # A person finding in the clause keeps it, whatever the wording.
    text = "Staff with a disability can request adjustments from Dana Lund in the People team."
    names = ((text.index("Dana"), text.index("Dana") + 9),)
    from themis_shield.detectors.policy_statements import policy_statement

    assert policy_statement(text, 0, len(text)) and not policy_statement(text, 0, len(text), names)


# ----------------------------------------------------------------------------------------------------------------
# A statement about a person is held, with the switch on.
@pytest.mark.parametrize("text, value", [
    ("Staff member Dana can request adjustments because of her disability.", "can request adjustments because of her"),
    ("Ticket 77: he has asked whether staff with a disability can request adjustments.",
     "he has asked whether staff with a disability can request adjustments"),
    ("As a union member she may apply for the dues rebate.", "As a union member she may apply for the dues rebate"),
    ("Dana asked whether staff with a disability can request adjustments.",
     "asked whether staff with a disability can request adjustments"),
    ("A staff member at the Dock 7 depot with a disability can request adjustments.",
     "A staff member at the Dock 7 depot with a disability can request adjustments"),
    ("Staff with a disability can request adjustments; the night analyst has epilepsy.",
     "Staff with a disability can request adjustments; the night analyst has epilepsy."),
])
def test_a_statement_about_a_person_is_still_held(text, value):
    assert reasons(text, [("DISABILITY", value)]) == [ReasonCode.SENSITIVE]


def test_a_union_statement_about_a_person_is_still_held_by_the_union_role_pass():
    assert reasons("Mara is not a union member, and staff may ask her about it.") == [ReasonCode.SENSITIVE]
    assert reasons("The employer does not ask whether Mara is a union member.") == [ReasonCode.SENSITIVE]


# ----------------------------------------------------------------------------------------------------------------
# Only the finding goes: nothing else in the document changes.
def test_only_the_sensitive_finding_on_the_rule_is_dropped():
    from themis_shield.detectors.contracts import finding
    from themis_shield.detectors.policy_statements import drop_policy_statements
    from themis_shield.detectors.union import union_findings

    text = ("Staff with a disability can request adjustments by emailing access@example.org. "
            "Dana Lund has epilepsy. Staff with a disability may apply for a parking permit.")
    rule = (0, text.index(" by emailing"))
    email = (text.index("access@"), text.index("access@") + len("access@example.org"))
    person_statement = (text.index("Dana"), text.index(". Staff with a disability may"))
    second = text.index("Staff with a disability may")
    findings = [
        finding(*rule, Label.DISABILITY, "gemma4-e4b", 2),
        finding(*email, Label.EMAIL, "rules", 1),
        finding(*person_statement, Label.HEALTH, "gemma4-e4b", 2),
        finding(second, len(text) - 1, Label.DISABILITY, "gemma4-26b-a4b", 3),
    ]
    kept = drop_policy_statements(text, tuple(findings))
    assert [(f.start, f.end) for f in kept] == [email, person_statement]
    # Same interval as an e-mail: the finding carries another label, so it stays.
    mixed = union_findings([finding(*rule, Label.DISABILITY, "gemma4-e4b", 2), finding(*rule, Label.EMAIL, "rules", 1)])
    assert drop_policy_statements(text, mixed) == mixed


def test_a_finding_across_sentences_and_a_sensitive_column_cell_are_never_dropped():
    from themis_shield.detectors.contracts import finding
    from themis_shield.detectors.policy_statements import drop_policy_statements, policy_statement
    from themis_shield.detectors.propagate import propagate

    text = "Staff with a disability can request adjustments. They can also ask for a quiet room."
    across = (finding(0, len(text), Label.DISABILITY, "gemma4-26b-a4b", 3),)
    assert drop_policy_statements(text, across) == across
    table = "name,union status\nMara,Employees may join\n"
    cells = [f for f in propagate(table, ()) if Label.UNION in f.labels]
    assert [table[f.start:f.end] for f in cells] == ["Employees may join"]
    assert policy_statement(table, cells[0].start, cells[0].end)  # the wording alone would pass ...
    assert drop_policy_statements(table, tuple(cells)) == tuple(cells)  # ... the column makes it a statement


# ----------------------------------------------------------------------------------------------------------------
# Guard (in the spirit of DD-16): the rule was written from three documents, so apply the clause test to every gold
# sensitive span of every evaluation set (split G: the adjudicated gold) and to the whole sentence around it, which is
# what a finder may answer with. It must never read a labelled statement about a person as a general rule.
_CASES = ROOT / "eval" / "cases"
_GUARD_FILES = sorted(
    p for p in _CASES.glob("*.jsonl") if not p.name.endswith(".adjudicated.jsonl") and not p.name.startswith("test-g-")
) + sorted(_CASES.glob("test-g-*.adjudicated.jsonl"))
_SENSITIVE_GOLD = {label.value for label in (
    Label.HEALTH, Label.DISABILITY, Label.INDIGENOUS, Label.SEXUALITY, Label.RELIGION, Label.CRIMINAL,
    Label.FAMILY_VIOLENCE, Label.ETHNICITY, Label.UNION)}
_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)]*(?=\s)|\n")


def test_no_gold_sensitive_span_of_the_evaluation_sets_is_read_as_a_general_rule():
    from themis_shield.detectors.policy_statements import policy_statement

    started = time.perf_counter()
    tested, fired, files = 0, [], set()
    for path in _GUARD_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            text = case["text"]
            for gold in case["spans"]:
                if gold["label"] not in _SENSITIVE_GOLD:
                    continue
                tested += 1
                files.add(path.name)
                ends = [m.end() for m in _SENTENCE_END.finditer(text, 0, gold["start"])]
                after = _SENTENCE_END.search(text, gold["end"])
                sentence = (ends[-1] if ends else 0, after.start() if after else len(text))
                for start, end in ((gold["start"], gold["end"]), sentence):
                    if policy_statement(text, start, end):
                        fired.append((case["id"], gold["label"], text[start:end][:120]))
    assert fired == []
    assert len(files) == len(_GUARD_FILES) == 9  # dev, test, C, D-q, D-c, E, F and the adjudicated G halves
    assert tested >= 3200  # 3248 gold sensitive spans when written
    assert time.perf_counter() - started < 30

# ----------------------------------------------------------------------------------------------------------------
# FAB-01 (independent review of candidate 0.5): a general rule in the same sentence must never excuse a clause whose
# own subject is a named person, a pronoun or a definite singular role. 0.4.0 held every one of these (ASK:sensitive);
# the first cut of this switch released them. The development sets passed with that bug present, so these cases are
# the guard. Invented sentences; the finder answers are stubbed.
FAB_01_MUST_HOLD = [
    # The gate's own rules make the statement (no finder entity).
    ("The cook has no criminal record and staff are expected to stop the gossip.", []),
    ("Our storeman is not a member of any union and staff are expected to respect that.", []),
    ("The new groundskeeper is not on any medication and employees are encouraged to welcome the change.", []),
    ("Our storeman is on parole and staff are expected to report any contact.", []),
    ("Amira Haddad, the payroll officer, is not a member of any union and staff are expected to respect that.",
     [("PERSON", "Amira Haddad")]),
    # A finder statement.
    ("The receptionist has epilepsy and staff are expected to keep the corridor lights dimmed.",
     [("HEALTH", "has epilepsy")]),
    ("Priya Nand, our receptionist, has epilepsy and staff are expected to keep the corridor lights dimmed.",
     [("PERSON", "Priya Nand"), ("HEALTH", "has epilepsy")]),
    ("The finance manager was diagnosed with bipolar disorder last month and employees are expected to be supportive.",
     [("HEALTH", "was diagnosed with bipolar disorder")]),
    ("Both crew members are treated for hepatitis C at the clinic.", [("HEALTH", "are treated for hepatitis C")]),
    ("The site foreman's union activity is considered a problem by management.",
     [("UNION", "union activity is considered a problem")]),
    ("The two apprentices are members of the union and members are entitled to paid meeting time.",
     [("UNION", "are members of the union")]),
    ("Our three volunteers are covered by a family violence protection order.",
     [("FAMILY_VIOLENCE", "are covered by a family violence protection order")]),
    ("The night cleaner is HIV positive and staff must not share this.", [("HEALTH", "is HIV positive")]),
    ("Priya has epilepsy and staff are expected to keep the lights dimmed.", [("HEALTH", "has epilepsy")]),
    ("Mum has cancer and carers are encouraged to visit on Tuesdays.", [("HEALTH", "has cancer")]),
    ("Staff who know the cook has epilepsy are expected to keep it private.", [("HEALTH", "has epilepsy")]),
    ("Sam told us they have epilepsy, and staff with epilepsy may request a quiet room.",
     [("HEALTH", "staff with epilepsy may request a quiet room")]),
]


@pytest.mark.parametrize("text, entities", FAB_01_MUST_HOLD)
def test_fab_01_a_rule_beside_a_specific_subject_does_not_release_it(text, entities):
    from themis_shield.detectors.policy_statements import policy_statement

    result = gate(text, entities)
    assert result.policy_result.decision == Decision.ASK
    assert ReasonCode.SENSITIVE in result.policy_result.reasons
    for label, value in entities:
        if label != "PERSON":
            start = text.index(value)
            assert not policy_statement(text, start, start + len(value))
    assert not policy_statement(text, 0, len(text))
