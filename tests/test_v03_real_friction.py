"""v0.3 pre-freeze: secret-rule context classes and whitespace-tolerant finder matching, each switch both ways.

Found on a first run over real documents; every example here is INVENTED (reserved .example domains, made-up codes,
a made-up machine-readable zone with a computed check digit). No real value appears in this file.
"""

from __future__ import annotations

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.llm_finder import spans_for
from themis_shield.detectors.rules import RulesDetector
from themis_shield.detectors.secret_context import (
    _mrz_check, credential_shaped, mrz_line, secret_parameter, secret_relabel, url_component, words_only_path,
)
from themis_shield.detectors.settings import SECRET_CONTEXT_FIXES, configure_detection_fixes
from themis_shield.types import Decision, Label, ReasonCode


@pytest.fixture(autouse=True)
def _no_fixes():
    configure_detection_fixes("none")
    yield


class FakeTracer:
    def __init__(self, findings=()):
        self.findings = tuple(findings)

    def detect(self, text):
        return StageResult(self.findings, StageReport("pii-tracer", Status.OK))


class EmptyLLM:
    def __init__(self, responses=None):
        self.responses = responses or {}

    def chat_json(self, model_role, messages, schema, max_tokens):
        return self.responses.get(model_role, {"entities": []})


def run(text, tracer_values=(), label=Label.SECRET, client=None):
    found = []
    for value in tracer_values:
        start = text.index(value)
        found.append(finding(start, start + len(value), label, "pii-tracer", 1))
    targets = DetectionTargets(tracer=FakeTracer(found), llm_client=client or EmptyLLM(), thorough=True)
    return detect(text, {}, targets)


def labels_of(result, text, value):
    return [f.labels for f in result.findings if text[f.start:f.end] == value]


ALL_SECRET = list(SECRET_CONTEXT_FIXES) + ["secret-prose-values"]

# ----------------------------------------------------------------------------------------------------------------------
# URL components
# ----------------------------------------------------------------------------------------------------------------------
TRACKING = "Rate us: https://mail.example/feedback/?p=bT1hYjEyY2QzNGVmNTZnaDc4aWo5MGtsMTJtbjM0b3A1NnF"
TOKEN_URL = "Resume: https://forms.example/s?id=2521312&submission_token=Zq8kLm2Pw9Xr4Tq7Vb3Nm5Lk8Jh2Gf6D"


def test_tracking_query_value_is_an_identifier_not_a_secret():
    held = run(TRACKING)
    assert ReasonCode.SECRET in held.reasons
    configure_detection_fixes(["secret-url-components"])
    result = run(TRACKING)
    assert ReasonCode.SECRET not in result.reasons
    value = TRACKING.split("?p=")[1]
    assert any(Label.INTERNAL_ID in labels for labels in labels_of(result, TRACKING, value))  # still substituted


def test_credential_named_parameter_stays_a_secret():
    configure_detection_fixes(ALL_SECRET)
    result = run(TOKEN_URL)
    assert ReasonCode.SECRET in result.reasons and result.decision == Decision.BLOCK


@pytest.mark.parametrize("name, query, secret", [
    ("access_token", "", True), ("X-Sig", "", True), ("api_key", "", True), ("sessionid", "", True),
    ("code", "?code=1&state=2", True), ("code", "?code=1", False), ("p", "", False), ("utm_source", "", False),
    ("token_type", "", False),
])
def test_secret_parameter_names(name, query, secret):
    assert secret_parameter(name, query) is secret


def test_url_parts():
    text = "a https://cdn.example/assets/fonts/Sherpa-Regular/Font?v=Ab12Cd34Ef#frag b"
    path = text.index("assets")
    assert url_component(text, path, path + 6) == "path"
    query = text.index("Ab12")
    assert url_component(text, query, query + 4) == "query"
    assert url_component("no url here", 0, 2) is None
    assert words_only_path("net/promos/htmlemails/fonts/Sherpa") and words_only_path("365/commerce/cancel")
    assert not words_only_path("reset/aZ9kQ2xL7mN4pR8sT1vW")


def test_word_only_url_paths_are_not_emitted():
    text = "src: url('https://cdn.example/net/promos/htmlemails/fonts/SherpaRegular/latin.woff')"
    assert any(Label.SECRET in labels for _, labels in [
        (text[f.start:f.end], f.labels) for f in RulesDetector(packs=("core",)).detect(text).findings
    ])
    configure_detection_fixes(["secret-url-components"])
    assert not any(Label.SECRET in f.labels for f in RulesDetector(packs=("core",)).detect(text).findings)


def test_high_entropy_path_segment_is_an_identifier():
    text = "Reset: https://id.example/reset/aZ9kQ2xL7mN4pR8sT1vW3yB6cD0eF5gH now"
    configure_detection_fixes(["secret-url-components"])
    result = run(text)
    assert ReasonCode.SECRET not in result.reasons
    assert any(Label.INTERNAL_ID in f.labels for f in result.findings)


# ----------------------------------------------------------------------------------------------------------------------
# Prose values
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, secret", [
    ("Call us and have your Phone Banking PIN handy.", False),
    ("Your new PIN 4821 is active.", True),
    ("Remember the password swordfish. It expires.", False),
    ("The password is Tide$771x for now.", True),
    ("password reset4u", True),
])
def test_prose_values_must_look_like_credentials(text, secret):
    configure_detection_fixes(["secret-prose-values"])
    found = any(Label.SECRET in f.labels for f in RulesDetector(packs=("core",)).detect(text).findings)
    assert found is secret


def test_prose_word_was_a_secret_before_the_fix():
    text = "Call us and have your Phone Banking PIN handy."
    assert any(Label.SECRET in f.labels for f in RulesDetector(packs=("core",)).detect(text).findings)
    assert not credential_shaped("PIN", "handy.") and credential_shaped("PIN", "4821")


# ----------------------------------------------------------------------------------------------------------------------
# Card authorisation and promo codes
# ----------------------------------------------------------------------------------------------------------------------
RECEIPT = "EFTPOS PURCHASE\r\nRRN 000111222333 (00)APPROVED\r\nAUTH 470193\r\nTOTAL $12.50"


def test_card_authorisation_code_is_not_a_secret():
    assert ReasonCode.SECRET in run(RECEIPT, ["470193"]).reasons
    configure_detection_fixes(["secret-auth-codes"])
    result = run(RECEIPT, ["470193"])
    assert ReasonCode.SECRET not in result.reasons
    assert labels_of(result, RECEIPT, "470193") == [frozenset({Label.INTERNAL_ID})]


@pytest.mark.parametrize("text, value, relabelled", [
    ("Use promo code QZRT2291K at checkout", "QZRT2291K", True),
    ("Approval code: 0A7Q3", "0A7Q3", True),
    ("Notes:\r\n470193", "470193", False),          # no context: still a secret
    ("AUTH Tide$771x", "Tide$771x", False),          # not a code shape
])
def test_auth_code_context(text, value, relabelled):
    start = text.index(value)
    got = secret_relabel(text, start, start + len(value), {"secret-auth-codes"})
    assert (got is not None) is relabelled


# ----------------------------------------------------------------------------------------------------------------------
# Document and transaction identifiers
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value, relabelled", [
    ("Sign Document ID: 1A2B3C4D-QWERTYU_ZX8CV7BN6M5L4K3J2H1G0FDSAPOIUY", "ZX8CV7BN6M5L4K3J2H1G0FDSAPOIUY", True),
    ("Envelope ID: 3f2504e0-4f89-11d3-9a0c-0305e82c3301", "3f2504e0-4f89-11d3-9a0c-0305e82c3301", True),
    ("Receipt number 9a1b2c3d4e5f60718293a4b5c6d7e8f9", "9a1b2c3d4e5f60718293a4b5c6d7e8f9", True),
    ("Transaction QW12ER34TY56UI78OP90", "QW12ER34TY56UI78OP90", True),
    ("Scan line 12000340000000000000000000000000001102", "12000340000000000000000000000000001102", True),
    ("Note: 9a1b2c3d4e5f60718293a4b5c6d7e8f9", "9a1b2c3d4e5f60718293a4b5c6d7e8f9", False),
    ("Transaction aZ9kQ2xL7mN4pR8sT1vW3yB6", "aZ9kQ2xL7mN4pR8sT1vW3yB6", False),  # mixed case: could be a key
])
def test_document_identifier_context(text, value, relabelled):
    start = text.index(value)
    got = secret_relabel(text, start, start + len(value), {"secret-document-ids"})
    assert (got == frozenset({Label.INTERNAL_ID})) is relabelled


def test_document_id_document_is_not_blocked():
    text = "Signed copy.\r\nSign Document ID: 1A2B3C4D-QWERTYU_ZX8CV7BN6M5L4K3J2H1G0FDSAPOIUY\r\n"
    assert ReasonCode.SECRET in run(text).reasons
    configure_detection_fixes(["secret-document-ids"])
    assert ReasonCode.SECRET not in run(text).reasons


# ----------------------------------------------------------------------------------------------------------------------
# Organisation codes and run-together words
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value, relabelled", [
    ("> Swift Code      :     QZBKAUEX XYZ", "QZBKAUEX XYZ", True),
    ("BIC: QZBKAU2S", "QZBKAU2S", True),
    ("Biller Code: 40912", "40912", True),
    ("RTP to bank\r\nQZBKAU2SXXXN20230812010713435280000", "QZBKAU2SXXXN20230812010713435280000", True),
    ("AmountDescriptionSupplierProduct", "AmountDescriptionSupplierProduct", True),
    ("Swift Code: aZ9kQ2xL7mN4", "aZ9kQ2xL7mN4", False),
    ("Code: 40912", "40912", False),
])
def test_organisation_codes(text, value, relabelled):
    start = text.index(value)
    got = secret_relabel(text, start, start + len(value), {"secret-org-codes"})
    assert (got is not None) is relabelled


def test_run_together_words_are_not_emitted():
    text = "Movements for the period\r\nAmountDescriptionSupplierProductTotals"
    assert any(Label.SECRET in f.labels for f in RulesDetector(packs=("core",)).detect(text).findings)
    configure_detection_fixes(["secret-org-codes"])
    assert not any(Label.SECRET in f.labels for f in RulesDetector(packs=("core",)).detect(text).findings)


# ----------------------------------------------------------------------------------------------------------------------
# Machine-readable zones
# ----------------------------------------------------------------------------------------------------------------------
def _td1(dob="850214", surname="ROE", given="JANE"):
    line1 = ("IDXYZ" + "QZ1234567" + "8").ljust(30, "<")
    line2 = (dob + str(_mrz_check(dob)) + "F" + "3001019" + "XYZ").ljust(29, "<") + "6"
    line3 = (surname + "<<" + given).ljust(30, "<")
    return line1, line2, line3


def test_mrz_lines_are_passport_and_name_material():
    rows = _td1()
    text = "Identity card\r\n" + "\r\n".join(rows) + "\r\nEnd"
    name = text.index(rows[2])
    data = text.index(rows[1])
    assert mrz_line(text, name, name + 30) == "name"
    assert mrz_line(text, data, data + 30) == "data"
    assert ReasonCode.SECRET in run(text, [rows[2], rows[1]]).reasons
    configure_detection_fixes(["secret-mrz"])
    result = run(text, [rows[2], rows[1]])
    assert ReasonCode.SECRET not in result.reasons
    assert labels_of(result, text, rows[2]) == [frozenset({Label.PERSON, Label.PASSPORT})]
    assert labels_of(result, text, rows[1]) == [frozenset({Label.PASSPORT})]


def test_mrz_needs_a_valid_check_digit():
    rows = list(_td1())
    wrong = str((_mrz_check("850214") + 1) % 10)
    rows[1] = rows[1][:6] + wrong + rows[1][7:]
    text = "\n".join(rows)
    start = text.index(rows[2])
    assert mrz_line(text, start, start + 30) is None


# ----------------------------------------------------------------------------------------------------------------------
# Finder values with other whitespace
# ----------------------------------------------------------------------------------------------------------------------
ADDRESS_TEXT = "Deliver to:\r\n80 Example\r\nDrive U12 Quarrybend NSW today."


def test_finder_value_matches_across_line_breaks_and_no_break_spaces():
    entity = [{"label": "ADDRESS", "value": "80 Example Drive U12 Quarrybend NSW"}]
    assert spans_for(ADDRESS_TEXT, entity) == ([], 1)  # fails closed before the fix
    configure_detection_fixes(["finder-whitespace"])
    spans, missing = spans_for(ADDRESS_TEXT, entity)
    assert missing == 1 and len(spans) == 1  # substituted, still counted as not verbatim
    configure_detection_fixes(["finder-whitespace", "finder-whitespace-trust"])
    spans, missing = spans_for(ADDRESS_TEXT, entity)
    assert missing == 0 and len(spans) == 1
    assert ADDRESS_TEXT[spans[0]["start"]:spans[0]["end"]] == "80 Example\r\nDrive U12 Quarrybend NSW"


def test_tolerant_matching_keeps_token_count_and_order_and_fails_closed():
    configure_detection_fixes(["finder-whitespace"])
    assert spans_for("Example Big Drive", [{"label": "ADDRESS", "value": "Example Drive"}]) == ([], 1)
    assert spans_for("Drive Example", [{"label": "ADDRESS", "value": "Example Drive"}]) == ([], 1)
    spans, _ = spans_for("Example Drive and Example\nDrive", [{"label": "ADDRESS", "value": "Example Drive"}])
    assert [(s["start"], s["end"]) for s in spans] == [(0, 13)]  # exact matches first; tolerant only when none


def test_tolerant_matching_reads_the_nfkc_view():
    configure_detection_fixes(["finder-whitespace"])
    text = "Ref ＡＢＣ​ 12 noted"   # full-width letters and a zero-width space
    spans, missing = spans_for(text, [{"label": "INTERNAL_ID", "value": "ABC 12"}])
    assert missing == 1 and text[spans[0]["start"]:spans[0]["end"]] == "ＡＢＣ​ 12"


def test_tolerant_matches_reduce_unmatched_value_holds():
    client = EmptyLLM({"gemma4-e4b": {"entities": [
        {"label": "ADDRESS", "value": "80 Example Drive U12 Quarrybend NSW"},
        {"label": "PERSON", "value": "Odalys\nBrennecke"},
    ]}})
    text = ADDRESS_TEXT + " Attn: Odalys Brennecke"
    held = run(text, client=client)
    assert ReasonCode.UNMATCHED_LLM_VALUES in held.reasons
    configure_detection_fixes(["finder-whitespace"])
    covered = run(text, client=client)
    assert ReasonCode.UNMATCHED_LLM_VALUES in covered.reasons  # the hold stays; coverage improves
    assert any(text[f.start:f.end].startswith("80 Example") for f in covered.findings)
    assert not any(text[f.start:f.end].startswith("80 Example") for f in held.findings)
    configure_detection_fixes(["finder-whitespace", "finder-whitespace-trust"])
    assert ReasonCode.UNMATCHED_LLM_VALUES not in run(text, client=client).reasons


# ----------------------------------------------------------------------------------------------------------------------
# A street number alone on the line before an address
# ----------------------------------------------------------------------------------------------------------------------
from themis_shield.detectors.propagate import address_line_numbers  # noqa: E402

STATEMENT = "Statement\r\nMR J ROE\r\n48\r\nEXAMPLE PDE\r\nQUARRYBEND NSW 2999\r\nPage\r\n3\r\nTotal 12.00"


def _addr(text, value):
    start = text.index(value)
    return finding(start, start + len(value), Label.ADDRESS, "pii-tracer", 1)


def test_lone_street_number_joins_the_address():
    found = address_line_numbers(STATEMENT, (_addr(STATEMENT, "EXAMPLE PDE"),))
    assert [STATEMENT[f.start:f.end] for f in found] == ["48", "EXAMPLE PDE"]
    assert all(Label.ADDRESS in f.labels for f in found)


@pytest.mark.parametrize("line", ["Unit 4", "U12", "4/18", "Lot 7B", "12A"])
def test_unit_and_lot_forms(line):
    text = f"To:\n{line}\nEXAMPLE PDE"
    found = address_line_numbers(text, (_addr(text, "EXAMPLE PDE"),))
    assert line in [text[f.start:f.end] for f in found]


@pytest.mark.parametrize("text", [
    "48 people came\nEXAMPLE PDE",          # not a lone number
    "48\nDeliver to EXAMPLE PDE",           # the address does not start its line
    "Total 48\nEXAMPLE PDE",
])
def test_only_a_lone_number_before_a_line_starting_address(text):
    found = address_line_numbers(text, (_addr(text, "EXAMPLE PDE"),))
    assert len(found) == 1


def test_address_line_numbers_end_to_end_switch():
    held = run(STATEMENT, ["EXAMPLE PDE"], Label.ADDRESS)
    assert not any(STATEMENT[f.start:f.end] == "48" for f in held.findings)
    configure_detection_fixes(["address-line-numbers"])
    result = run(STATEMENT, ["EXAMPLE PDE"], Label.ADDRESS)
    assert any(STATEMENT[f.start:f.end] == "48" and Label.ADDRESS in f.labels for f in result.findings)


def test_default_has_every_fix_but_the_ones_still_off():
    from themis_shield.detectors.settings import DETECTION_FIXES

    selected = configure_detection_fixes(None)
    # Off on purpose: tolerant trust released genuine misses on the real slice. Adding to this set is a deliberate
    # decision; so is removing from it (the v0.4 readers left it on 30/10/2026 after a live measurement).
    off = {"finder-whitespace-trust"}
    assert set(selected) == set(DETECTION_FIXES) - off
