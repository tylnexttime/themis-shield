"""v0.3 lane 1: deterministic friction fixes, each a named detection switch, tested both ways.

Synthetic text only; no torch, model or server. Every name, address, number and domain is invented here (reserved
``.example`` domains, published checksum test vectors) and none is taken from the evaluation sets.
"""

from __future__ import annotations

import random

import pytest

from themis_shield.detectors import DetectionTargets, detect
from themis_shield.detectors.allowlist import apply_allowlist, compile_profile
from themis_shield.detectors.contracts import StageReport, StageResult, Status, finding
from themis_shield.detectors.propagate import (
    COMMON_ROLES, TITLE_WORDS, common_role, extra_spans, table_quasi_spans,
)
from themis_shield.detectors.rules import (
    PLACEHOLDER, RulesDetector, acn_valid, inbound_number, luhn_valid, number_context, organisation_value,
    payment_card_valid, placeholder_value, role_mailbox, validate_identifier,
)
from themis_shield.detectors.settings import (
    DEFAULT_DETECTION_FIXES, DETECTION_FIXES, configure_detection_fixes, configure_detection_version,
    detection_fixes, fix_on, validate_detection_fixes,
)
from themis_shield.integration import configure_versions
from themis_shield.policy import surviving_tracer_labels
from themis_shield.types import Decision, Label, ReasonCode


@pytest.fixture(autouse=True)
def _no_fixes():
    """Each test starts with every v0.3 fix off and switches on the one it tests, whatever the shipped default."""
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


def thorough(tracer, client=None):
    return DetectionTargets(tracer=tracer, llm_client=client or EmptyLLM(), thorough=True)


def tracer_on(text, value, label, score=None):
    start = text.index(value)
    return finding(start, start + len(value), label, "pii-tracer", 1, score)


def rules(text, packs=("core", "au", "intl")):
    return [(text[f.start:f.end], f.labels) for f in RulesDetector(packs=packs).detect(text).findings]


def secrets(text):
    return [value for value, labels in rules(text, ("core",)) if Label.SECRET in labels]


# ----------------------------------------------------------------------------------------------------------------------
# Switches
# ----------------------------------------------------------------------------------------------------------------------
def test_fix_selection_and_controls():
    assert configure_detection_fixes(None) == DEFAULT_DETECTION_FIXES == detection_fixes()
    assert configure_detection_fixes("all") == DETECTION_FIXES
    assert all(fix_on(name) for name in DETECTION_FIXES)
    configure_detection_version("v0.1")  # the v0.1 control switches every fix off
    assert not any(fix_on(name) for name in DETECTION_FIXES)
    configure_detection_version(None)
    assert configure_detection_fixes("none") == ()
    assert configure_detection_fixes(["title-tokens", "secret-placeholders"]) == (
        "secret-placeholders", "title-tokens",
    )
    assert validate_detection_fixes("checksum-context, contact-brackets") == ("checksum-context", "contact-brackets")
    for bad in ("all,none", "secret", ["unknown"], 3, [1]):
        with pytest.raises(ValueError):
            validate_detection_fixes(bad)
    with pytest.raises(ValueError):
        fix_on("no-such-fix")


def test_fixes_come_from_the_detection_table():
    active = configure_versions({"detection": {"fixes": ["table-role-words"]}})
    assert active["detection_fixes"] == ("table-role-words",)
    assert configure_versions({"detection": {"fixes": "all"}})["detection_fixes"] == DETECTION_FIXES
    assert configure_versions({})["detection_fixes"] == DEFAULT_DETECTION_FIXES
    with pytest.raises(ValueError):
        configure_versions({"detection": {"fixes": ["typo"]}})


# ----------------------------------------------------------------------------------------------------------------------
# 1. Secret-manager references and template placeholders
# ----------------------------------------------------------------------------------------------------------------------
PLACEHOLDER_LINES = [
    "DB_PASSWORD=${VAULT:billing-db-password}",
    "API_TOKEN=<set in vault>",
    "api_key: ${{ secrets.DEPLOY_KEY }}",
    'secret: "{{ db_secret }}"',
    "password: {{- .Values.db.password -}}",
    "token = '${DB_TOKEN}'",
    "password=%(db_password)s",
    "pwd=$(DB_PASS)",
    "password: <REDACTED>",
    "password = [REDACTED]",
    "password=********",
    "access_key=%SERVICE_ACCESS_KEY%",
    "api_key=$SERVICE_API_KEY",
    "secret=vault://kv/app/db#password",
    "password=arn:cloud:secretsmanager:region-1:000000000000:secret:app-db",
    "password=@Platform.KeyVault(SecretUri=https://kv.example/secrets/db)",
    "token=projects/demo/secrets/db-token/versions/latest",
    "password is <set in vault>",
    "password: <your-password-here>",
]


@pytest.mark.parametrize("line", PLACEHOLDER_LINES)
def test_placeholders_are_not_secret_values(line):
    configure_detection_fixes(["secret-placeholders"])
    assert secrets(line) == []


def test_placeholders_were_secrets_before_the_fix():
    assert secrets("DB_PASSWORD=${VAULT:billing-db-password}") == ["${VAULT:billing-db-password"]
    assert secrets("API_TOKEN=<set in vault>") == ["<set"]


@pytest.mark.parametrize("line, value", [
    ("DB_PASSWORD=${VAULT:x}Zq8kLm2Pw", "${VAULT:x}Zq8kLm2Pw"),          # a value glued to the placeholder
    ('token: "${VAULT:x} Zq8kLm2Pw"', "${VAULT:x} Zq8kLm2Pw"),             # quoted: the whole quote is the value
    ('secret: "{{ db }}Zq8kLm2Pw"', "{{ db }}Zq8kLm2Pw"),
    ("password=Reef$4471Q", "Reef$4471Q"),                                # "$" and "%" inside real passwords
    ("password=Dune%2093z", "Dune%2093z"),
    ("password=<Kelp4471Q>", "<Kelp4471Q>"),                              # angle brackets around a real value
    ("password=hunter2abc", "hunter2abc"),
    ("password=$lowercase_word", "$lowercase_word"),                      # not an upper-case variable reference
])
def test_real_values_next_to_placeholder_syntax_are_still_secrets(line, value):
    configure_detection_fixes(["secret-placeholders"])
    assert value in secrets(line)


def test_high_entropy_string_inside_a_placeholder_is_still_caught():
    configure_detection_fixes(["secret-placeholders"])
    hexed = "9f2c4e6a8b0d1f3e5a7c9e1b3d5f7a9c2e4f6a8b"
    text = "DB_PASSWORD=${VAULT:" + hexed + "}"
    assert hexed in secrets(text)  # the hex rule still runs over the whole text


def test_placeholder_value_is_whole_value_only():
    assert placeholder_value("x=${A_B} tail", 2)
    assert not placeholder_value("x=${A_B}tail", 2)
    assert placeholder_value('x="${A_B}"', 3, 9)
    assert not placeholder_value('x="${A_B}z"', 3, 10)
    assert PLACEHOLDER.fullmatch("<set in vault>")
    assert not placeholder_value("<Ab1Cd>", 0)


def test_placeholder_config_file_is_no_longer_blocked():
    text = (
        "Copy .env.example to .env and fill the values from the vault.\n"
        "APP_DB_PASSWORD=${VAULT:app-db-password}\n"
        "APP_API_TOKEN=<set in vault>\n"
        "APP_LOG_LEVEL=info\n"
    )
    assert ReasonCode.SECRET in detect(text, {}, thorough(FakeTracer())).reasons
    configure_detection_fixes(["secret-placeholders"])
    result = detect(text, {}, thorough(FakeTracer()))
    assert ReasonCode.SECRET not in result.reasons
    assert result.decision == Decision.RELEASE
    leaked = text.replace("${VAULT:app-db-password}", "Tide$5501xkelp")
    assert ReasonCode.SECRET in detect(leaked, {}, thorough(FakeTracer())).reasons


# ----------------------------------------------------------------------------------------------------------------------
# 2. Role mailboxes and inbound numbers: substituted, but not evidence for the empty-finder hold
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    "info@quarrybend.example", "support@quarrybend.example.", "no-reply@quarrybend.example",
    "do-not-reply@quarrybend.example", "noreply@quarrybend.example", "hr.payroll@quarrybend.example",
    "enquiries+web@quarrybend.example", "Bookings@quarrybend.example", "customer_service@quarrybend.example",
])
def test_role_mailboxes(value):
    assert role_mailbox(value)


@pytest.mark.parametrize("value", [
    "odalys.brennecke@quarrybend.example", "info.odalys@quarrybend.example", "us@quarrybend.example",
    "no@quarrybend.example", "mayor@quarrybend.example", "ward7@quarrybend.example", "not-an-email",
])
def test_not_role_mailboxes(value):
    assert not role_mailbox(value)


@pytest.mark.parametrize("value, inbound", [
    ("1300 555 017", True), ("1800-555-017", True), ("13 55 01", True), ("135501", True), ("(1300) 555 017", True),
    ("0491 570 006", False), ("(08) 5550 0199", False), ("1900 555 017", False), ("1300 555 01", False),
    ("Ext 1300 555 017", False),
])
def test_inbound_numbers(value, inbound):
    assert inbound_number(value) is inbound


ORG_NOTICE = (
    "Lost property notice. Ring 1300 555 017 or email lostproperty@quarrybend.example or "
    "support@quarrybend.example. Items are kept for 30 days."
)


def test_organisation_contacts_no_longer_hold_a_clean_document():
    tracer = FakeTracer((
        tracer_on(ORG_NOTICE, "1300 555 017", Label.PHONE),
        tracer_on(ORG_NOTICE, "support@quarrybend.example.", Label.EMAIL),
    ))
    held = detect(ORG_NOTICE, {}, thorough(tracer))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in held.reasons
    configure_detection_fixes(["organisation-contacts"])
    result = detect(ORG_NOTICE, {}, thorough(tracer))
    assert result.decision == Decision.RELEASE
    # Still substituted: the findings stay, flagged.
    values = {ORG_NOTICE[f.start:f.end] for f in result.findings if f.organisation_value}
    assert {"1300 555 017", "support@quarrybend.example."} <= values
    assert surviving_tracer_labels(result) == frozenset()


def test_a_personal_contact_beside_role_contacts_still_holds():
    text = ORG_NOTICE + " Finder: odalys.brennecke@quarrybend.example"
    tracer = FakeTracer((
        tracer_on(text, "1300 555 017", Label.PHONE),
        tracer_on(text, "odalys.brennecke@quarrybend.example", Label.EMAIL),
    ))
    configure_detection_fixes(["organisation-contacts"])
    result = detect(text, {}, thorough(tracer))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in result.reasons


def test_organisation_contacts_never_cancel_a_finder_finding():
    text = "Write to info@quarrybend.example about the refund."
    tracer = FakeTracer((tracer_on(text, "info@quarrybend.example", Label.EMAIL),))
    client = EmptyLLM({"gemma4-e4b": {"entities": [{"label": "EMAIL", "value": "info@quarrybend.example"}]}})
    configure_detection_fixes(["organisation-contacts"])
    result = detect(text, {}, thorough(tracer, client))
    found = [f for f in result.findings if text[f.start:f.end] == "info@quarrybend.example"]
    assert found and Label.EMAIL in found[0].labels
    assert {e.stage for e in found[0].evidence} >= {"gemma4-e4b", "pii-tracer"}


def test_geographic_numbers_and_personal_labels_are_not_organisation_values():
    text = "Call (08) 5550 0199 or 1300 555 017."
    a = text.index("(08)")
    assert not organisation_value(text, a, a + 14, {Label.PHONE}, contacts=True, companies=True)
    b = text.index("1300")
    assert organisation_value(text, b, b + 12, {Label.PHONE}, contacts=True, companies=False)
    assert not organisation_value(text, b, b + 12, {Label.PERSON}, contacts=True, companies=False)
    assert not organisation_value(text, b, b + 12, {Label.PHONE}, contacts=False, companies=True)


# ----------------------------------------------------------------------------------------------------------------------
# 3. Context on checksum-valid numbers
# ----------------------------------------------------------------------------------------------------------------------
TFN_VALID = "123 456 782"  # the widely published TFN check example


@pytest.mark.parametrize("prefix", [
    "Invoice ", "Order no. ", "Ref: ", "Reference ", "PO ", "Ticket #", "Case ", "Account no ", "ACN ",
    "Build ", "Serial ", "Receipt no: ", "A.C.N. ", "Order number = ",
])
def test_negative_context_makes_a_generic_identifier(prefix):
    text = prefix + TFN_VALID + " was processed."
    assert (TFN_VALID, frozenset({Label.TFN})) in rules(text)
    configure_detection_fixes(["checksum-context"])
    found = rules(text)
    assert (TFN_VALID, frozenset({Label.INTERNAL_ID})) in found  # still substituted, never dropped
    assert all(Label.TFN not in labels for _, labels in found)


@pytest.mark.parametrize("text", [
    "TFN " + TFN_VALID,
    "Tax file number: " + TFN_VALID,
    "TFN noted. Order no. " + TFN_VALID,          # positive context on the line keeps the TFN
    TFN_VALID + " (TFN), quoted on the order form",
    "Number " + TFN_VALID,                        # no context: unchanged
    "Keep this (in case you need it again): " + TFN_VALID,   # "case" here labels nothing
    "The reference you asked for is " + TFN_VALID,           # a reference word that does not label the number
])
def test_positive_or_no_context_keeps_the_tfn(text):
    configure_detection_fixes(["checksum-context"])
    assert (TFN_VALID, frozenset({Label.TFN})) in rules(text)


def test_reference_word_must_label_the_number():
    text = "Ticket\n" + TFN_VALID  # context is read on the number's own line only
    assert number_context(Label.TFN, text, text.index(TFN_VALID), len(text)) is None
    text = "Order no. " + TFN_VALID
    assert number_context(Label.TFN, text, text.index(TFN_VALID), len(text)) == "negative"
    text = "Order of service, then " + TFN_VALID
    assert number_context(Label.TFN, text, text.index(TFN_VALID), len(text)) is None
    abn = "51 824 753 556"
    text = "The ABN for the invoice is " + abn
    assert number_context(Label.ABN, text, text.index(abn), len(text)) == "positive"


def test_medicare_and_abn_context():
    configure_detection_fixes(["checksum-context"])
    medicare = "2123 45670 1"   # published Medicare check example
    abn = "51 824 753 556"      # published ABN check example
    assert (medicare, frozenset({Label.INTERNAL_ID})) in rules("Invoice " + medicare)
    assert (medicare, frozenset({Label.MEDICARE})) in rules("Medicare card " + medicare)
    assert (abn, frozenset({Label.INTERNAL_ID})) in rules("Order " + abn)
    assert (abn, frozenset({Label.ABN})) in rules("ABN " + abn)
    assert (abn, frozenset({Label.ABN})) in rules("Supplier " + abn)


def test_context_never_drops_a_checksum_number():
    configure_detection_fixes(["checksum-context"])
    text = "Ref " + TFN_VALID
    with_fix = {value for value, _ in rules(text)}
    configure_detection_fixes("none")
    assert with_fix == {value for value, _ in rules(text)}


# ----------------------------------------------------------------------------------------------------------------------
# 4. Rule-pack completeness: payment cards, ACN, and a cross-test of the AU validators
# ----------------------------------------------------------------------------------------------------------------------
PUBLISHED_TEST_CARDS = [
    "4111 1111 1111 1111", "4012 8888 8888 1881", "5555 5555 5555 4444", "2223 0031 2200 3222",
    "3782 822463 10005", "3714 496353 98431", "6011 1111 1111 1117", "3530 1113 3330 0000",
    "3056 9309 0259 04", "3852 0000 0232 37", "6200 0000 0000 0005", "4111-1111-1111-1111", "4111111111111111",
]


@pytest.mark.parametrize("card", PUBLISHED_TEST_CARDS)
def test_published_test_cards_are_valid(card):
    assert payment_card_valid(card)


@pytest.mark.parametrize("value", [
    "4111 1111 1111 1112",       # Luhn fails
    "1111 1111 1111 1117",       # no issuer starts with 1
    "4111 1111 1111 111",        # Visa is 13, 16 or 19 digits
    "3782 822463 1000",          # Amex is 15 digits
    "0000 0000 0000 0000",       # one repeated digit
    "9999 9999 9999 9995",
])
def test_invalid_cards(value):
    assert not payment_card_valid(value)


def test_luhn():
    assert luhn_valid("79927398713")
    assert not luhn_valid("79927398710")
    assert not luhn_valid("7")


def test_card_rule_is_in_the_intl_pack_and_behind_its_switch():
    text = "Card on file: 4111 1111 1111 1111, expiry 12/30."
    card = ("4111 1111 1111 1111", frozenset({Label.PAYMENT_CARD}))
    assert card not in rules(text)
    configure_detection_fixes(["rule-completeness"])
    assert card in rules(text, ("core", "intl"))
    assert card not in rules(text, ("core", "au"))
    assert card not in rules("Order 4111 1111 1111 1112 shipped.", ("core", "intl"))
    assert card not in rules("Build 4111.1111.1111.1111", ("core", "intl"))


# Published ACN examples (the regulator's worked list of valid numbers), written out by hand.
ACN_KNOWN_VALID = [
    "000 000 019", "000 250 000", "000 500 005", "000 750 005", "001 000 004", "001 250 004", "001 500 009",
    "001 749 999", "001 999 999", "002 249 998", "002 499 998", "002 749 993", "002 999 993", "003 249 992",
    "003 499 992", "003 749 988", "003 999 988", "004 249 987", "004 499 987", "004 749 982", "004 999 982",
    "005 249 981", "005 499 981", "005 749 986", "005 999 977", "010 249 966", "010 499 966", "010 749 961",
]


@pytest.mark.parametrize("acn", ACN_KNOWN_VALID)
def test_acn_known_answers(acn):
    assert acn_valid(acn)


def test_acn_rejects_one_digit_changes():
    # A change is missed exactly when weight x difference is a multiple of 10 (weights 8, 6, 4, 2 with a difference
    # of 5; weight 5 with an even difference): a property of the ACN scheme itself. Weights 7, 3, 1 and the check
    # digit catch every change.
    for acn in ACN_KNOWN_VALID:
        digits = acn.replace(" ", "")
        for position in range(9):
            for replacement in "0123456789":
                if replacement != digits[position]:
                    changed = digits[:position] + replacement + digits[position + 1:]
                    weight = (8, 7, 6, 5, 4, 3, 2, 1, 1)[position]
                    blind = position < 8 and weight * (int(replacement) - int(digits[position])) % 10 == 0
                    assert acn_valid(changed) is blind


def test_acn_is_company_evidence_not_a_tfn():
    # A nine-digit number that passes both the ACN and the TFN check, written as an ACN.
    both = next(
        f"{n:09d}" for n in range(1000, 10 ** 9, 7919)
        if acn_valid(f"{n:09d}") and validate_identifier(Label.TFN, f"{n:09d}").checksum_valid
    )
    text = f"Quarrybend Holdings Pty Ltd ACN {both}"
    assert (both, frozenset({Label.TFN})) in rules(text)
    configure_detection_fixes(["rule-completeness"])
    assert (both, frozenset({Label.INTERNAL_ID})) in rules(text)
    start = text.index(both)
    assert organisation_value(text, start, start + 9, {Label.STUDENT_ID}, contacts=False, companies=True)
    # Without the ACN word, or failing the ACN check, it is not company evidence.
    plain = f"Quarrybend Holdings Pty Ltd {both}"
    assert not organisation_value(plain, plain.index(both), len(plain), {Label.STUDENT_ID},
                                  contacts=False, companies=True)
    bad = f"ACN {TFN_VALID}"
    assert not acn_valid(TFN_VALID)
    assert not organisation_value(bad, 4, len(bad), {Label.TFN}, contacts=False, companies=True)


def test_acn_tracer_finding_does_not_hold_a_clean_document():
    acn = "004 085 616"
    assert acn_valid(acn)
    text = f"Supplier: Quarrybend Holdings Pty Ltd, ACN {acn}. Delivery on Mondays."
    tracer = FakeTracer((tracer_on(text, acn, Label.STUDENT_ID),))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM in detect(text, {}, thorough(tracer)).reasons
    configure_detection_fixes(["rule-completeness"])
    result = detect(text, {}, thorough(tracer))
    assert ReasonCode.DISAGREEMENT_EMPTY_LLM not in result.reasons
    assert any(text[f.start:f.end] == acn for f in result.findings)  # still substituted


# Cross-test: each AU check written a second, independent way (constructive check-digit generation from the public
# specifications) and compared with the shipped validators on the same synthetic vectors.
def _tfn_independent(digits):
    weights = [1, 4, 3, 7, 5, 8, 6, 9, 10]
    return len(digits) == 9 and sum(w * int(d) for w, d in zip(weights, digits)) % 11 == 0


def _medicare_independent(digits):
    if len(digits) not in (10, 11) or digits[0] not in "23456":
        return False
    check = sum(w * int(d) for w, d in zip([1, 3, 7, 9, 1, 3, 7, 9], digits[:8])) % 10
    return check == int(digits[8]) and digits[9] != "0"


def _abn_independent(digits):
    if len(digits) != 11 or digits[0] == "0":
        return False
    adjusted = [int(digits[0]) - 1] + [int(d) for d in digits[1:]]
    weights = [10] + list(range(1, 20, 2))
    return sum(w * d for w, d in zip(weights, adjusted)) % 89 == 0


def _make_valid(kind, rng):
    """Build a valid number constructively (choose the free digits, then solve for the check digit)."""
    while True:
        if kind == "tfn":
            body = [rng.randrange(10) for _ in range(8)]
            weights = [1, 4, 3, 7, 5, 8, 6, 9]
            # 10 * last = -sum (mod 11); 10 is its own inverse mod 11.
            last = (-sum(w * d for w, d in zip(weights, body)) * 10) % 11
            if last < 10:
                return "".join(map(str, body + [last]))
        elif kind == "medicare":
            body = [rng.randrange(2, 7)] + [rng.randrange(10) for _ in range(7)]
            check = sum(w * d for w, d in zip([1, 3, 7, 9, 1, 3, 7, 9], body)) % 10
            return "".join(map(str, body + [check, rng.randrange(1, 10)]))
        else:
            rest = [rng.randrange(10) for _ in range(9)]
            for prefix in range(10, 100):
                digits = [int(c) for c in str(prefix)] + rest
                if _abn_independent("".join(map(str, digits))):
                    return "".join(map(str, digits))


@pytest.mark.parametrize("kind, label, independent", [
    ("tfn", Label.TFN, _tfn_independent),
    ("medicare", Label.MEDICARE, _medicare_independent),
    ("abn", Label.ABN, _abn_independent),
])
def test_au_validators_agree_with_an_independent_implementation(kind, label, independent):
    rng = random.Random(20261027)
    for _ in range(300):
        valid = _make_valid(kind, rng)
        assert independent(valid)
        assert validate_identifier(label, valid).checksum_valid, (kind, valid)
        position = rng.randrange(len(valid))
        mutated = valid[:position] + str((int(valid[position]) + rng.randrange(1, 10)) % 10) + valid[position + 1:]
        assert validate_identifier(label, mutated).checksum_valid == independent(mutated), (kind, mutated)
    for _ in range(2000):
        random_digits = "".join(str(rng.randrange(10)) for _ in range({"tfn": 9, "medicare": 10, "abn": 11}[kind]))
        assert validate_identifier(label, random_digits).checksum_valid == independent(random_digits)


@pytest.mark.parametrize("label, value", [
    (Label.TFN, "123 456 782"), (Label.TFN, "876 543 210"),
    (Label.MEDICARE, "2123 45670 1"), (Label.MEDICARE, "2123 45670 11"),
    (Label.ABN, "51 824 753 556"), (Label.ABN, "53 004 085 616"),
])
def test_au_published_known_answers(label, value):
    assert validate_identifier(label, value).checksum_valid


# ----------------------------------------------------------------------------------------------------------------------
# 5. Table pass: common role words are not candidates; titles stay beside names
# ----------------------------------------------------------------------------------------------------------------------
ROLE_TABLE = (
    "Staff list (anonymised)\n"
    "dob,postcode,role\n"
    "1971-02-03,3999,Teacher\n"
    "1980-04-05,3998,Registered nurse\n"
    "1966-06-07,3997,Cleaners\n"
    "1990-08-09,3996,Night ferry dispatcher (only one)\n"
    "1958-10-11,3995,Branch manager\n"
    "1977-12-13,3994,Lighthouse keeper\n"
)


def test_common_roles_are_not_quasi_identifier_candidates():
    values = [ROLE_TABLE[f.start:f.end] for f in table_quasi_spans(ROLE_TABLE)]
    assert "Teacher" in values and "Registered nurse" in values
    configure_detection_fixes(["table-role-words"])
    values = [ROLE_TABLE[f.start:f.end] for f in table_quasi_spans(ROLE_TABLE)]
    assert values == ["Night ferry dispatcher (only one)", "Branch manager", "Lighthouse keeper"]


def test_common_role_matches_the_whole_cell_only():
    assert common_role("Teacher") and common_role("  registered   NURSE ") and common_role("Cleaners")
    for qualified in ("Head teacher", "Sole teacher", "Teacher (only one)", "Night security officer",
                      "Branch manager", "Head chef", "Lighthouse keeper"):
        assert not common_role(qualified)
    assert all(word == " ".join(word.split()) and word == word.casefold() for word in COMMON_ROLES)


TITLED = "Captain Odalys Brennecke signed the log. The captain also noted that Brennecke was late."


def test_leading_titles_are_not_propagated_as_names():
    parent = finding(0, len("Captain Odalys Brennecke"), Label.PERSON, "pii-tracer", 1)
    before = {TITLED[f.start:f.end] for f in extra_spans(TITLED, (parent,))}
    assert {"Captain", "captain"} <= before
    configure_detection_fixes(["title-tokens"])
    after = [TITLED[f.start:f.end] for f in extra_spans(TITLED, (parent,))]
    assert "captain" not in after and "Captain" not in after
    assert after.count("Brennecke") == 2 and "Odalys" in after


@pytest.mark.parametrize("text, name, kept", [
    ("Bishop, Odalys wrote. Bishop agreed.", "Bishop, Odalys", "Bishop"),      # surname-first list
    ("Odalys Bishop wrote. Bishop agreed.", "Odalys Bishop", "Bishop"),        # a surname that is a title word
    ("Sister Odalys wrote.", "Sister", None),                                  # a title alone is not a name split
])
def test_title_words_that_are_names_stay_names(text, name, kept):
    configure_detection_fixes(["title-tokens"])
    parent = finding(0, len(name), Label.PERSON, "pii-tracer", 1)
    values = [text[f.start:f.end] for f in extra_spans(text, (parent,))]
    if kept:
        assert values.count(kept) == 2


def test_title_vocabulary_is_lower_case():
    assert all(word == word.casefold() for word in TITLE_WORDS)


# ----------------------------------------------------------------------------------------------------------------------
# Extra: a contact span that lost its opening bracket still matches the public number
# ----------------------------------------------------------------------------------------------------------------------
def test_bracket_dropped_public_number_is_still_a_whole_value():
    text = "Reception (07) 5550 0199 for visitors."
    start = text.index("07)")
    item = (finding(start, start + len("07) 5550 0199"), Label.PHONE, "pii-tracer", 1),)
    profile = compile_profile({"literals": ["(07) 5550 0199"]})
    assert apply_allowlist(text, item, profile) == item
    configure_detection_fixes(["contact-brackets"])
    assert apply_allowlist(text, item, profile) == ()
    # Only dropped brackets: a span missing digits is not the whole value.
    inner = text.index("5550")
    partial = (finding(inner, inner + len("5550 0199"), Label.PHONE, "pii-tracer", 1),)
    assert apply_allowlist(text, partial, profile) == partial
