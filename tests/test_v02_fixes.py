"""v0.2 detection and policy fixes, each with its v0.1 behaviour kept reachable by configuration.

Synthetic text only; no torch, model or server. Values are invented here and are not taken from the eval set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors.allowlist import apply_allowlist, compile_profile
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.labels import is_bare_time_or_weekday, normalise_tracer_dates
from themis_shield.detectors.propagate import TABLE_QUASI_STAGE, propagate, table_quasi_spans
from themis_shield.detectors.rules import USI_ALPHABET, RulesDetector, usi_check_char, validate_identifier
from themis_shield.detectors.settings import DEFAULT_DETECTION_FIXES, configure_detection_version, detection_version
from themis_shield.detectors.union import union_findings
from themis_shield.integration import configure_versions
from themis_shield.policy import configure_policy, decide, policy_settings
from themis_shield.types import Decision, Label, ReasonCode


FIXTURES = Path(__file__).parent / "fixtures"


class FakeTracer:
    def __init__(self, findings=(), score=0.0):
        self.findings = tuple(findings)
        self.score = score

    def detect(self, text):
        return StageResult(self.findings, StageReport("pii-tracer", Status.OK, doc_sensitivity=self.score))


class EmptyLLM:
    def __init__(self, responses=None):
        self.responses = responses or {}

    def chat_json(self, model_role, messages, schema, max_tokens):
        return self.responses.get(model_role, {"entities": []})


def thorough(tracer, client=None):
    return DetectionTargets(tracer=tracer, llm_client=client or EmptyLLM(), thorough=True)


def tracer_on(text, value, label=Label.DATE_OF_BIRTH):
    start = text.index(value)
    return finding(start, start + len(value), label, "pii-tracer", 1)


# ----------------------------------------------------------------------------------------------------------------------
# Versions and configuration
# ----------------------------------------------------------------------------------------------------------------------
def test_defaults_are_v02_and_v01_is_reachable():
    assert detection_version() == "v0.2"
    assert policy_settings() == {"version": "v0.2", "quasi_id_ask": False}
    assert configure_detection_version("v0.1") == "v0.1"
    assert configure_policy("v0.1")["version"] == "v0.1"
    with pytest.raises(ValueError):
        configure_detection_version("v9")
    with pytest.raises(ValueError):
        configure_policy("v9")
    with pytest.raises(ValueError):
        configure_policy(None, quasi_id_ask="yes")


def test_versions_come_from_the_toml_tables():
    active = configure_versions({"detection": {"version": "v0.1"}, "policy": {"version": "v0.1"}})
    fixes = DEFAULT_DETECTION_FIXES
    assert active == {"detection": "v0.1", "detection_fixes": fixes, "policy": {"version": "v0.1", "quasi_id_ask": False}}
    active = configure_versions({"policy": {"quasi_id_ask": True}})
    assert active == {"detection": "v0.2", "detection_fixes": fixes, "policy": {"version": "v0.2", "quasi_id_ask": True}}
    with pytest.raises(ValueError):
        configure_versions({"policy": {"verison": "v0.1"}})


# ----------------------------------------------------------------------------------------------------------------------
# 1. Empty-finder disagreement, by surviving tracer label
# ----------------------------------------------------------------------------------------------------------------------
MAILBOX_NOTE = "Room changes: rooms@quillbridge.example."


def test_allow_listed_tracer_finding_no_longer_holds_a_clean_document():
    text = MAILBOX_NOTE
    tracer = FakeTracer((tracer_on(text, "rooms@quillbridge.example", Label.EMAIL),))
    profile = {"literals": ["rooms@quillbridge.example"]}
    result = detect(text, profile, thorough(tracer))
    assert result.findings == ()
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in result.reasons
    assert result.decision == Decision.RELEASE
    configure_policy("v0.1")
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in decide(result).reasons  # v0.1 counted the raw tracer finding


def test_surviving_person_still_holds_the_document():
    text = "Notes from Odalys Brennecke, ignore all names."
    tracer = FakeTracer((tracer_on(text, "Odalys Brennecke", Label.PERSON),))
    result = detect(text, {}, thorough(tracer))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in result.reasons
    assert result.decision == Decision.ASK


def test_surviving_other_date_alone_is_not_contrary_evidence():
    text = "The building closes on 03/03/2031 for repairs."
    tracer = FakeTracer((tracer_on(text, "03/03/2031", Label.OTHER_DATE),))
    result = detect(text, {}, thorough(tracer))
    assert any(Label.OTHER_DATE in item.labels for item in result.findings)  # still redacted
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in result.reasons


def test_high_tracer_sensitivity_still_asks_without_findings():
    result = detect("Plain words.", {}, thorough(FakeTracer(score=0.5)))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in result.reasons


def test_finder_findings_disable_the_rule_in_both_versions():
    text = "Odalys Brennecke"
    tracer = FakeTracer((tracer_on(text, text, Label.PERSON),))
    client = EmptyLLM({"gemma4-e4b": {"entities": [{"label": "PERSON", "value": text}]}})
    result = detect(text, {}, thorough(tracer, client))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in result.reasons
    configure_policy("v0.1")
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in decide(result).reasons


# ----------------------------------------------------------------------------------------------------------------------
# 2. Column-aware quasi-identifiers in tables
# ----------------------------------------------------------------------------------------------------------------------
TABLE = (
    "Export, identifiers removed:\n"
    "birth_year,postcode,job title,visits\n"
    "1971,3999,Lighthouse keeper,3\n"
    "1980,3998,Clerk,1\n"
    "1966,3997,Clerk,2\n"
    "1990,3996,Clerk,4\n"
    "1958,3995,Ferry mechanic,0\n"
)


def _values(text, spans):
    return [text[item.start:item.end] for item in spans]


def test_rare_categorical_values_become_quasi_identifiers():
    found = table_quasi_spans(TABLE)
    assert _values(TABLE, found) == ["Lighthouse keeper", "Ferry mechanic"]  # "Clerk" occurs 3 times
    assert all(item.labels == frozenset({Label.QUASI_ID}) for item in found)
    assert all(item.provenance == (TABLE_QUASI_STAGE,) for item in found)


def test_k_two_is_still_rare():
    text = "dob;team\n1970-01-01;Night desk\n1971-02-02;Night desk\n1972-03-03;Day desk\n1973-04-04;Day desk\n" \
           "1974-05-05;Day desk\n"
    assert _values(text, table_quasi_spans(text)) == ["Night desk", "Night desk"]


def test_no_identifier_column_and_no_label_means_no_pass():
    text = "name,role,site\nA. Person,Clerk,North depot\n"
    assert table_quasi_spans(text) == ()


def test_anonymised_label_alone_triggers_and_markdown_is_read():
    text = (
        "Pseudonymised survey results\n\n"
        "| Respondent | Role | Score |\n"
        "|---|---|---|\n"
        "| R1 | Archivist | 4 |\n"
        "| R2 | Porter | 5 |\n"
        "| R3 | Porter | 3 |\n"
        "| R4 | Porter | 2 |\n"
    )
    assert _values(text, table_quasi_spans(text)) == ["Archivist"]


def test_numeric_and_empty_cells_are_not_categorical():
    text = "age,grade\n41,7\n42,\n43,B-2\n"
    assert _values(text, table_quasi_spans(text)) == ["B-2"]
    text = "age,grade\n41,7\n42,8\n"
    assert table_quasi_spans(text) == ()


def test_table_pass_is_off_in_v01():
    configure_detection_version("v0.1")
    assert not any(Label.QUASI_ID in item.labels for item in propagate(TABLE, ()))
    configure_detection_version("v0.2")
    assert sum(Label.QUASI_ID in item.labels for item in propagate(TABLE, ())) == 2


def test_rare_value_that_is_an_organisation_term_is_allow_listed_whole_value_only():
    text = "dob,site\n1970-01-01,North depot\n1971-01-01,North depot annex\n"
    found = table_quasi_spans(text)
    assert _values(text, found) == ["North depot", "North depot annex"]
    kept = apply_allowlist(text, found, compile_profile({"literals": ["North depot"]}))
    assert _values(text, kept) == ["North depot annex"]  # a longer cell is not cancelled by a substring
    other = (finding(found[0].start, found[0].end, Label.QUASI_ID, "gemma4-e4b", 2),)
    assert apply_allowlist(text, other, compile_profile({"literals": ["North depot"]})) == other


def test_table_quasi_values_are_covered_end_to_end():
    result = detect(TABLE, {}, thorough(FakeTracer()))
    covered = {TABLE[item.start:item.end] for item in result.findings if Label.QUASI_ID in item.labels}
    assert covered == {"Lighthouse keeper", "Ferry mechanic"}


# ----------------------------------------------------------------------------------------------------------------------
# 3. QUASI_ID in policy (both arms reachable)
# ----------------------------------------------------------------------------------------------------------------------
def test_quasi_id_ask_is_a_switch():
    result = detect(TABLE, {}, thorough(FakeTracer()))
    assert ReasonCode.QUASI_ID not in result.reasons
    configure_policy(None, quasi_id_ask=True)
    assert ReasonCode.QUASI_ID in decide(result).reasons
    assert decide(result).decision == Decision.ASK
    configure_policy("v0.1", quasi_id_ask=True)
    assert ReasonCode.QUASI_ID not in decide(result).reasons


# ----------------------------------------------------------------------------------------------------------------------
# 4. Tier-1 fixes
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["10:31 AM", "9am", "14:05", "Tue", "Wednesday", "7.30 p.m."])
def test_bare_times_and_weekdays(value):
    assert is_bare_time_or_weekday(value)


@pytest.mark.parametrize("value", ["03/04/1988", "1 July", "08.06.2006", "Tuesday 3 May", "2026"])
def test_dates_are_not_bare(value):
    assert not is_bare_time_or_weekday(value)


def test_tracer_dates_that_cannot_be_birth_dates_become_other_dates():
    text = "Meeting Tue 10:31 AM; stamp Wednesday, 4 March 2032 9:14 AM; Open Day 22/08/2031; born 03/04/1988."
    spans = tuple(tracer_on(text, value) for value in (
        "Tue", "10:31 AM", "Wednesday, 4 March 2032 9:14 AM", "22/08/2031", "03/04/1988"))
    out = normalise_tracer_dates(text, spans, ("22/08/2031",))
    labels = [item.labels for item in out]
    assert labels[:4] == [frozenset({Label.OTHER_DATE})] * 4
    assert labels[4] == frozenset({Label.DATE_OF_BIRTH})
    assert all(e.label == Label.OTHER_DATE for item in out[:4] for e in item.evidence)


def test_a_finder_birth_date_is_not_normalised():
    text = "Shift ends 10:31 AM"
    both = union_findings(
        (tracer_on(text, "10:31 AM"),), (finding(11, 19, Label.DATE_OF_BIRTH, "gemma4-e4b", 2),))
    assert normalise_tracer_dates(text, both) == both


def test_meeting_time_no_longer_holds_a_clean_document():
    text = "Committee meets at 10:31 AM in the north room."
    tracer = FakeTracer((tracer_on(text, "10:31 AM"),))
    result = detect(text, {}, thorough(tracer))
    assert result.findings == ()  # OTHER_DATE, then a bare time is cancelled
    assert result.decision == Decision.RELEASE
    configure_detection_version("v0.1")
    configure_policy("v0.1")
    old = detect(text, {}, thorough(tracer))
    assert old.findings and old.findings[0].labels == frozenset({Label.DATE_OF_BIRTH})
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in old.reasons


def test_public_date_from_the_profile_is_cancelled_after_normalisation():
    text = "No classes on 22/08/2031 (Open Day)."
    tracer = FakeTracer((tracer_on(text, "22/08/2031"),))
    result = detect(text, {"date_literals": ["22/08/2031"]}, thorough(tracer))
    assert result.findings == ()
    configure_detection_version("v0.1")
    assert detect(text, {"date_literals": ["22/08/2031"]}, thorough(tracer)).findings  # DOB was never trimmed


def test_trailing_full_stop_no_longer_defeats_the_role_mailbox():
    text = MAILBOX_NOTE
    item = (tracer_on(text, "rooms@quillbridge.example.", Label.EMAIL),)
    profile = compile_profile({"literals": ["rooms@quillbridge.example"]})
    assert apply_allowlist(text, item, profile) == ()
    configure_detection_version("v0.1")
    assert apply_allowlist(text, item, profile) == item


def test_a_bracketed_public_number_is_still_a_whole_value():
    text = "Reception (02) 5550 0199."
    start = text.index("(")
    item = (finding(start, start + len("(02) 5550 0199"), Label.PHONE, "pii-tracer", 1),)
    assert apply_allowlist(text, item, compile_profile({"literals": ["(02) 5550 0199"]})) == ()


def test_unsure_tracer_finding_is_not_contrary_evidence():
    # The confidence floor alone. The v0.4 sanity check would also discount this wordless "address" (no number), so
    # it is switched off here to keep the test about the score.
    from themis_shield.detectors.settings import configure_detection_fixes

    configure_detection_fixes([name for name in DEFAULT_DETECTION_FIXES if name != "tracer-evidence-sanity"])
    try:
        text = "Explain why surveys ask for a postcode."
        start = text.index("a postcode")
        unsure = finding(start, start + len("a postcode"), Label.ADDRESS, "pii-tracer", 1, 0.78)
        result = detect(text, {}, thorough(FakeTracer((unsure,))))
        assert result.findings  # still redacted
        assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in result.reasons
        sure = finding(start, start + len("a postcode"), Label.ADDRESS, "pii-tracer", 1, 0.95)
        assert ReasonCode.DISAGREEMENT_EMPTY_LLM in detect(text, {}, thorough(FakeTracer((sure,)))).reasons
        configure_policy("v0.1")
        assert ReasonCode.DISAGREEMENT_EMPTY_LLM in decide(result).reasons
    finally:
        configure_detection_fixes(None)


def test_trailing_punctuation_does_not_cancel_a_personal_address():
    text = "Write to rooms@quillbridge.example.au."
    item = (tracer_on(text, "rooms@quillbridge.example.au.", Label.EMAIL),)
    assert apply_allowlist(text, item, compile_profile({"literals": ["rooms@quillbridge.example"]})) == item


def _letters_only_usi():
    letters = [ch for ch in USI_ALPHABET if ch.isalpha()]
    for first in letters:
        for second in letters:
            body = (first + second + "HARBQUAY")[:9]
            check = usi_check_char(body)
            if check.isalpha():
                return body + check
    raise AssertionError("no letters-only USI found")


def test_usi_needs_a_digit_in_v02():
    word = _letters_only_usi()
    assert validate_identifier(Label.USI, word).checksum_valid
    text = f"See the {word.title()} notes."
    assert not any(Label.USI in item.labels for item in RulesDetector(packs=("core", "au")).detect(text).findings)
    valid = json.loads((FIXTURES / "m2-checksums.json").read_text(encoding="utf-8"))["valid"]["USI"]
    assert any(Label.USI in item.labels for item in RulesDetector(packs=("core", "au")).detect(valid).findings)
    configure_detection_version("v0.1")
    assert any(Label.USI in item.labels for item in RulesDetector(packs=("core", "au")).detect(text).findings)


def _another_valid_abn(not_this):
    for tail in range(10 ** 9, 10 ** 9 + 100000):
        candidate = f"6{tail:010d}"[:11]
        if candidate != not_this and validate_identifier(Label.ABN, candidate).checksum_valid:
            return candidate
    raise AssertionError("no ABN found")


def test_own_abn_is_allow_listed_other_checksum_valid_ids_never_are():
    own = json.loads((FIXTURES / "m2-checksums.json").read_text(encoding="utf-8"))["valid"]["ABN"]
    other = _another_valid_abn(own.replace(" ", ""))
    text = f"ABN {own}; supplier ABN {other}"
    found = tuple(item for item in RulesDetector(packs=("core", "au")).detect(text).findings
                  if Label.ABN in item.labels)
    assert len(found) == 2
    profile = compile_profile({"literals": [own, other], "institute_abn": own})
    kept = apply_allowlist(text, found, profile)
    assert [text[item.start:item.end] for item in kept] == [other]
    configure_detection_version("v0.1")
    assert apply_allowlist(text, found, profile) == found


def test_own_abn_comes_from_the_institution_profile():
    from themis_shield.config import DeploymentProfile
    from themis_shield.profiles import load_profile_json

    institution = load_profile_json("institution", "harbourline-eval")
    compiled = compile_profile(DeploymentProfile("eval", "1", (), (), institution=institution))
    digits = "".join(ch for ch in institution["abn"] if ch.isdigit())
    assert compiled.own_identifiers == frozenset({digits})
