"""Second independent review of candidate 0.5 (RC-01, RC-02): under the DEFAULT switches every one of these sentences is
held for the Operator (ASK, sensitive), as in 0.4.0.

Two rounds of review showed that judging "general rule or statement about a specific person" with rules is not safe:
each narrowing left another sentence shape that released a person's sensitive information. The steward's decision
(13/11/2026): a leak is worse than friction, so ``policy-statements`` and the general-rule exemption of
``negative-statements`` (``negative-statements-general-exemption``) are opt-in, off by default. These sentences are
the reviewer's reproductions (``fr2.py``), invented words; the finder answers are stubbed where a label is shown.

The second half records, honestly, what the two opt-in switches still do with the same sentences: they release them.
That is why they are opt-in. Nothing here asks them to be fixed; a model-based check with abstain-to-hold is the
planned replacement.
"""

from __future__ import annotations

import pytest

from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, OPT_IN_DETECTION_FIXES, configure_detection_fixes, fix_on,
)
from themis_shield.types import Decision, ReasonCode

OPT_IN = ("policy-statements", "negative-statements-general-exemption")

# RC-01 (High): a rule clause followed by a statement about one identifiable person. (text, stubbed finder entities)
RC_01 = [
    ("Staff are expected to remember that the cook has epilepsy.", [("HEALTH", "has epilepsy")]),
    ("Employees must not mention that the night cleaner is HIV positive.", [("HEALTH", "is HIV positive")]),
    ("Staff should be aware that our receptionist is pregnant.", [("HEALTH", "is pregnant")]),
    ("All staff are required to know that the new chef has a severe nut allergy.",
     [("HEALTH", "has a severe nut allergy")]),
    ("Carers must remember that Mum has dementia.", [("HEALTH", "has dementia")]),
    ("Team members should know the storeman is a union delegate.", []),  # the gate's own union-role rule
    ("Staff are expected to remember that the cook has no criminal record.", []),  # the gate's own negative rule
    ("Employees must accept that the storeman is not a member of any union.", []),  # the gate's own negative rule
    ("Volunteers must not tell anyone that Mr Okafor has dementia.", [("HEALTH", "has dementia")]),  # no PERSON found
    ("Volunteers must not tell anyone that Mr Okafor has dementia.", [("PERSON", "Okafor"), ("HEALTH", "has dementia")]),
]
# RC-02 (Medium): a plural with "with/who/that" and a modal, about one named child's parents or one address. None of
# these depends on policy-statements; the gate's own negative-statement rule makes the statement.
RC_02 = [
    ("Parents with no criminal record can be considered for kinship care of Jayden.", []),
    ("Parents with no criminal record can be considered for kinship care of Jayden.", [("PERSON", "Jayden")]),
    ("Tenants that have no criminal history can stay at 14 Wattle Street.", []),
    ("Carers who have never been charged may keep the placement.", []),
    ("Patients with no criminal record may be moved to the open ward.", []),
]
CASES = [pytest.param(text, entities, id=f"rc01-{n}") for n, (text, entities) in enumerate(RC_01, 1)] + [
    pytest.param(text, entities, id=f"rc02-{n}") for n, (text, entities) in enumerate(RC_02, 1)]


def gate(text, entities):
    from themis_shield.config import DeploymentProfile, UseCasePolicy
    from themis_shield.detectors import DetectionTargets, detect_batch
    from test_policy_statements import Finder, Tracer

    policy = UseCasePolicy("s", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    profile = DeploymentProfile("s", "1", (), (policy,))
    return detect_batch([text], profile, DetectionTargets(tracer=Tracer(), llm_client=Finder(entities)),
                        thorough=True)[0]


@pytest.fixture(autouse=True)
def _defaults():
    configure_detection_fixes(None)
    yield
    configure_detection_fixes(None)


def test_both_switches_are_opt_in():
    for name in OPT_IN:
        assert name in OPT_IN_DETECTION_FIXES and name not in DEFAULT_DETECTION_FIXES and not fix_on(name)


@pytest.mark.parametrize("text, entities", CASES)
def test_default_switches_hold_it_for_the_operator_as_in_0_4_0(text, entities):
    result = gate(text, entities)
    assert result.policy_result.decision == Decision.ASK
    assert ReasonCode.SENSITIVE in result.policy_result.reasons


# What the opt-in switches do with the same sentences: the known shapes they release, per configuration. These tables
# are the honest record, not a target. If a change to either switch makes one of these hold, update the table.
# policy-statements also drops the gate's own negative statements when it reads their clause as a rule, so it alone
# releases three of RC-02's five; the exemption alone releases all five and none of RC-01's.
_RC01_RELEASED = {f"rc01-{n}": "release" for n in range(1, 10)} | {"rc01-10": "held"}  # a found name keeps it
_RC01_HELD = {f"rc01-{n}": "held" for n in range(1, 11)}
_RC02_RELEASED = {f"rc02-{n}": "release" for n in range(1, 6)}
KNOWN_WITH_OPT_IN = {
    "both": _RC01_RELEASED | _RC02_RELEASED,
    "policy-statements": _RC01_RELEASED | {
        "rc02-1": "release", "rc02-2": "held", "rc02-3": "held", "rc02-4": "release", "rc02-5": "release"},
    "negative-statements-general-exemption": _RC01_HELD | _RC02_RELEASED,
}


@pytest.mark.parametrize("configuration", list(KNOWN_WITH_OPT_IN))
def test_with_the_opt_in_switches_on_the_known_shapes_are_documented(configuration):
    observed = {}
    try:
        configure_detection_fixes([*DEFAULT_DETECTION_FIXES, *(OPT_IN if configuration == "both" else (configuration,))])
        for case in CASES:
            text, entities = case.values
            decision = gate(text, entities).policy_result.decision
            observed[case.id] = "held" if decision == Decision.ASK else "release"
    finally:
        configure_detection_fixes(None)
    assert observed == KNOWN_WITH_OPT_IN[configuration]
