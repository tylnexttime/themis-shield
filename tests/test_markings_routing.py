"""Exercise real S-M and routing contracts using synthetic strings and fakes.

No xfail marks, model processes, network calls or real personal information.
Interior prose fixtures deliberately sit beyond the first/last-three-line
heuristic. That heuristic is more conservative than ordinary body detection.
"""

from dataclasses import asdict, fields
import json

import pytest

from themis_shield.clients import (
    CLAUDE_CODE,
    KILO_CODE,
    CredentialPath,
    validate_credential_path,
)
from themis_shield.config import DeploymentProfile, LocalTargetName, UseCasePolicy
from themis_shield.detectors.markings import (
    MarkingClass,
    MarkingResult,
    PositionKind,
    detect_markings,
    summarise_markings,
)
from themis_shield.inference import TargetUnavailable
from themis_shield.inference import (
    InferenceCapabilities,
    InferenceRequest,
    InferenceResponse,
)
from themis_shield.inference.dgx_spark import DGXSparkTarget
from themis_shield.inference.laptop_llamacpp import MODEL_PROFILES
from themis_shield.routing import (
    ROUTING_TABLE,
    Route,
    RouteReasonCode,
    route_request,
    select_route,
)
from themis_shield.server import (
    CHAT_COMPLETIONS_ROUTE,
    MESSAGES_ROUTE,
    RESPONSES_ROUTE,
    LocalGateServer,
)
from themis_shield.types import ContentLeaf

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


def _body(text: str) -> str:
    return "\n".join(
        [f"ordinary opening line {index}" for index in range(4)]
        + [text]
        + [f"ordinary closing line {index}" for index in range(4)]
    )


def _policy(**changes) -> UseCasePolicy:
    return UseCasePolicy("synthetic", (), **changes)


def _assert_sensitive_choice(decision) -> None:
    assert decision.decision == "CHOOSE_LOCAL_OR_CLOUD_CONFIRMED"
    assert decision.route == Route.CLOUD
    assert decision.audit_fields.cloud_offered
    assert decision.mandatory_confirm
    assert decision.mandatory_ask
    assert decision.local_alternative_offered
    assert not decision.audit_fields.no_override
    assert decision.reason_code == RouteReasonCode.ROUTE_SENSITIVE_LOCAL_OR_CONFIRM


@pytest.mark.parametrize("banner", ["PROTECTED", "protected", "Protected"])
@pytest.mark.parametrize("at_end", [False, True])
def test_header_footer_banners(banner, at_end) -> None:
    ordinary = _body("ordinary interior text")
    text = ordinary + "\n   " + banner if at_end else "   " + banner + "\n" + ordinary
    result = detect_markings(text)
    assert result.classification == MarkingClass.RESTRICTED
    assert result.findings[0].position_kind == PositionKind.HEADER_FOOTER
    assert route_request(result, _policy()).route == Route.LOCAL_ONLY


@pytest.mark.parametrize(
    "text, classification",
    [
        ("Subject: Synthetic [SEC=PROTECTED]", MarkingClass.RESTRICTED),
        ("Subject: Synthetic [SEC=OFFICIAL:Sensitive]", MarkingClass.SENSITIVE),
        ("Subject: Synthetic SEC=protected", MarkingClass.RESTRICTED),
        ("Subject: Synthetic [sec=official : sensitive]", MarkingClass.SENSITIVE),
    ],
)
def test_epms(text, classification) -> None:
    result = detect_markings(text)
    assert result.classification == classification
    assert result.findings[0].position_kind == PositionKind.EPMS


@pytest.mark.parametrize(
    "marker, classification",
    [
        ("(P)", MarkingClass.RESTRICTED),
        ("(p)", MarkingClass.RESTRICTED),
        ("(S)", MarkingClass.SECRET),
        ("(s)", MarkingClass.SECRET),
        ("(TS)", MarkingClass.TOP_SECRET),
        ("(O:S)", MarkingClass.SENSITIVE),
        ("(PROTECTED)", MarkingClass.RESTRICTED),
        ("(SECRET)", MarkingClass.SECRET),
        ("(TOP SECRET)", MarkingClass.TOP_SECRET),
        ("(OFFICIAL: Sensitive)", MarkingClass.SENSITIVE),
    ],
)
def test_paragraph_markings(marker, classification) -> None:
    text = _body("ordinary text\n\n" + marker + " Synthetic content")
    result = detect_markings(text)
    assert result.classification == classification
    assert result.findings[0].position_kind == PositionKind.PARAGRAPH
    finding = result.findings[0]
    assert text[finding.start:finding.end] == marker


@pytest.mark.parametrize(
    "text",
    [
        "A student(s) can read this.",
        "A student(p) can read this.",
        "(S) lowercase prose",
        "(P) lowercase prose",
        "(S)No whitespace",
        "(P)No whitespace",
        "ordinary text\n(S) Synthetic continuation",
    ],
)
def test_invalid_abbreviations(text) -> None:
    assert detect_markings(_body(text)).classification == MarkingClass.NONE


@pytest.mark.parametrize("marker", ["(P)", "(S)"])
def test_abbreviation_followed_by_digit(marker) -> None:
    result = detect_markings("\n\n" + marker + " 123 synthetic items")
    assert result.findings[0].position_kind == PositionKind.PARAGRAPH


@pytest.mark.parametrize(
    "text",
    ["protected species", "a secret recipe", "top secret plans", "Protected species"],
)
def test_interior_prose_negatives(text) -> None:
    result = detect_markings(_body(text))
    assert result.classification == MarkingClass.NONE
    assert route_request(result, _policy()).route == Route.CLOUD


@pytest.mark.parametrize(
    "text, position",
    [
        ('The note says "this file is marked PROTECTED".', PositionKind.BODY_QUOTED),
        ("This file is marked PROTECTED.", PositionKind.BODY_UPPER),
        ("The note says ‘marked SECRET’.", PositionKind.BODY_QUOTED),
    ],
)
def test_body_uppercase_counts(text, position) -> None:
    result = detect_markings(_body(text))
    assert result.findings[0].position_kind == position
    assert route_request(result, _policy()).route == Route.LOCAL_ONLY


@pytest.mark.parametrize(
    "text",
    [
        "PROTECTED//AUSTEO",
        "P R O T E C T E D",
        "p r o t e c t e d",
        "PROTECTED: CABINET",
        "OFFICIAL : SENSITIVE",
        "OFFICIAL:Sensitive",
    ],
)
def test_variants(text) -> None:
    result = detect_markings(text)
    assert result.classification >= MarkingClass.SENSITIVE
    decision = route_request(result, _policy())
    if result.classification >= MarkingClass.RESTRICTED:
        assert decision.route == Route.LOCAL_ONLY
        assert not decision.audit_fields.cloud_offered
    else:
        _assert_sensitive_choice(decision)


@pytest.mark.parametrize(
    "text, caveat",
    [
        ("AUSTEO", "AUSTEO"),
        ("AGAO", "AGAO"),
        ("REL TO AUS, NZL", "REL"),
        ("REL AUS/USA", "REL"),
        ("CABINET", "CABINET"),
        ("EXCLUSIVE FOR", "EXCLUSIVE_FOR"),
        ("protected//austeo", "AUSTEO"),
    ],
)
def test_caveats(text, caveat) -> None:
    result = detect_markings(text)
    assert caveat in result.caveats
    assert result.classification >= MarkingClass.RESTRICTED
    decision = route_request(result, _policy())
    assert decision.route == Route.LOCAL_ONLY
    assert decision.audit_fields.no_override
    assert decision.local_alternative_offered
    assert not decision.audit_fields.cloud_offered
    assert decision.reason_code == RouteReasonCode.ROUTE_LOCAL_MARKING


def test_rel_requires_country_code_syntax() -> None:
    assert not detect_markings(_body("REL")).caveats
    assert not detect_markings(_body("the relative release notes")).caveats


def test_uppercase_rel_to_errs_towards_local() -> None:
    # A false positive costs local speed; a miss could offer caveated text to cloud.
    decision = route_request(detect_markings(_body("REL TO COUNTRY")), _policy())
    assert decision.route == Route.LOCAL_ONLY


def test_official_only_is_cloud() -> None:
    decision = route_request(detect_markings("OFFICIAL"), _policy())
    assert decision.route == Route.CLOUD
    assert not decision.mandatory_ask


@pytest.mark.parametrize(
    "text",
    ["OFFICIAL:Sensitive", "OFFICIAL : SENSITIVE", "(O:S) Synthetic content"],
)
def test_sensitive_offers_local_and_cloud_with_mandatory_confirm(text) -> None:
    _assert_sensitive_choice(route_request(detect_markings(text), _policy()))


def test_sensitive_routing_needs_no_classification_authorisation_switch() -> None:
    assert "sensitive_cloud_authorised" not in {item.name for item in fields(UseCasePolicy)}
    with pytest.raises(TypeError):
        _policy(sensitive_cloud_authorised=True)


@pytest.mark.parametrize(
    "text, marker",
    [
        ("For Official Use Only", "FOR_OFFICIAL_USE_ONLY"),
        ("Sensitive: Personal", "SENSITIVE_PERSONAL"),
        ("Sensitive: Legal", "SENSITIVE_LEGAL"),
        ("Sensitive: Cabinet", "SENSITIVE_CABINET"),
        ("Sensitive", "SENSITIVE"),
    ],
)
def test_legacy_dlm_policy(text, marker) -> None:
    result = detect_markings(text)
    assert marker in result.legacy
    decision = route_request(result, _policy())
    _assert_sensitive_choice(decision)
    assert decision.audit_fields.marking_class == MarkingClass.SENSITIVE


@pytest.mark.parametrize("local_only", [False, True])
@pytest.mark.parametrize("imm", [(), ("PERSONAL_PRIVACY",)])
def test_sensitive_never_offers_cloud_without_confirm_and_always_offers_local(
    local_only, imm,
) -> None:
    decision = route_request(
        MarkingResult(MarkingClass.SENSITIVE, handling=imm),
        _policy(local_only=local_only),
    )
    assert decision.local_alternative_offered
    if decision.audit_fields.cloud_offered:
        assert decision.mandatory_confirm
        _assert_sensitive_choice(decision)
    else:
        assert decision.route == Route.LOCAL_ONLY
        assert decision.audit_fields.no_override


def test_legacy_signal_without_classification_still_offers_both() -> None:
    decision = route_request(
        MarkingResult(legacy=("FOR_OFFICIAL_USE_ONLY",)),
        _policy(),
    )
    _assert_sensitive_choice(decision)


@pytest.mark.parametrize(
    "text, marker",
    [
        ("Personal privacy", "PERSONAL_PRIVACY"),
        ("Legal privilege", "LEGAL_PRIVILEGE"),
        ("Legislative secrecy", "LEGISLATIVE_SECRECY"),
    ],
)
def test_imm_forces_ask(text, marker) -> None:
    result = detect_markings(_body(text))
    assert marker in result.handling
    assert result.classification == MarkingClass.NONE
    decision = route_request(result, _policy())
    assert decision.route == Route.CLOUD
    assert decision.mandatory_ask
    assert decision.mandatory_confirm
    assert decision.reason_code == RouteReasonCode.ROUTE_CLOUD_HANDLING_ASK


@pytest.mark.parametrize("marker", ["PROTECTED", "SECRET", "TOP SECRET"])
def test_marked_without_pii_stays_local(marker) -> None:
    result = detect_markings(marker + "\nSynthetic arithmetic: 2 + 2")
    decision = route_request(result, _policy())
    assert decision.route == Route.LOCAL_ONLY
    assert not decision.audit_fields.cloud_offered


@pytest.mark.parametrize(
    "classification",
    [MarkingClass.RESTRICTED, MarkingClass.SECRET, MarkingClass.TOP_SECRET],
)
@pytest.mark.parametrize("local_only", [False, True])
def test_protected_plus_never_offers_cloud_even_with_lower_signals(
    classification, local_only,
) -> None:
    decision = route_request(
        MarkingResult(
            classification,
            handling=("PERSONAL_PRIVACY",),
            legacy=("SENSITIVE_PERSONAL",),
        ),
        _policy(local_only=local_only),
    )
    assert decision.route == Route.LOCAL_ONLY
    assert decision.local_alternative_offered
    assert decision.audit_fields.no_override
    assert not decision.audit_fields.cloud_offered
    assert decision.reason_code == RouteReasonCode.ROUTE_LOCAL_MARKING


def test_secret_outranks_official() -> None:
    result = detect_markings("OFFICIAL\nSynthetic contents\nSECRET")
    assert result.classification == MarkingClass.SECRET


@pytest.mark.parametrize("separator", ["\f", "\nPage 2 of 2\n"])
def test_each_page_has_edge_positions(separator) -> None:
    text = _body("ordinary text") + separator + "classification: Protected\n" + _body("x")
    result = detect_markings(text)
    assert result.classification == MarkingClass.RESTRICTED
    assert result.findings[0].position_kind == PositionKind.FIRST_LAST_LINES


def test_short_prose_is_conservative_at_document_edge() -> None:
    result = detect_markings("protected species")
    assert result.classification == MarkingClass.RESTRICTED
    assert result.findings[0].position_kind == PositionKind.FIRST_LAST_LINES


def test_offsets_are_original_half_open_character_offsets() -> None:
    text = _body('é 🙂 "marked P R O T E C T E D"')
    finding = detect_markings(text).findings[0]
    assert text[finding.start:finding.end] == "P R O T E C T E D"
    assert finding.start == text.index("P R O T E C T E D")


def test_no_original_text_in_findings_or_route_audit() -> None:
    original = _body('SYNTHETIC_ONLY_SENTINEL "marked PROTECTED//AUSTEO"')
    result = detect_markings(original)
    decision = route_request(result, _policy())
    assert "SYNTHETIC_ONLY_SENTINEL" not in repr(asdict(result))
    assert "SYNTHETIC_ONLY_SENTINEL" not in repr(asdict(decision.audit_fields))
    assert original not in repr(asdict(decision.audit_fields))
    assert set(asdict(decision.audit_fields)) == {
        "route", "marking_class", "reason_code",
        "mandatory_ask", "cloud_offered", "no_override",
        "local_alternative_offered",
    }


def test_protected_user_message_is_fixed() -> None:
    decision = route_request(detect_markings("PROTECTED"), _policy())
    assert decision.user_message == (
        "This stays on your computer because it is marked PROTECTED. "
        "It will be processed by the local model; this is slower."
    )


@pytest.mark.parametrize("value", [Route.HYBRID, "HYBRID"])
def test_hybrid_not_selectable(value) -> None:
    with pytest.raises(ValueError, match="P3"):
        select_route(value)


def test_policy_can_strengthen_cloud_route() -> None:
    decision = route_request(detect_markings("OFFICIAL"), _policy(local_only=True))
    assert decision.route == Route.LOCAL_ONLY
    assert decision.reason_code == RouteReasonCode.ROUTE_LOCAL_POLICY


def test_routing_table_and_rules_are_frozen() -> None:
    with pytest.raises(TypeError):
        ROUTING_TABLE[MarkingClass.RESTRICTED] = ROUTING_TABLE[MarkingClass.NONE]
    with pytest.raises(AttributeError):
        ROUTING_TABLE[MarkingClass.RESTRICTED].cloud_offered = True
    assert ROUTING_TABLE[MarkingClass.SENSITIVE].mandatory_confirm
    assert ROUTING_TABLE[MarkingClass.SENSITIVE].local_alternative_offered


def test_multi_surface_summary_retains_strongest_signals() -> None:
    result = summarise_markings((
        detect_markings("OFFICIAL"),
        detect_markings("SECRET"),
        detect_markings("Personal privacy"),
    ))
    assert result.classification == MarkingClass.SECRET
    assert result.handling == ("PERSONAL_PRIVACY",)
    assert result.findings == ()
    assert route_request(result, _policy()).route == Route.LOCAL_ONLY


@pytest.mark.parametrize("authorised", [False, True])
def test_kilo_never_uses_claude_subscription(authorised) -> None:
    with pytest.raises(PermissionError):
        validate_credential_path(
            KILO_CODE,
            CredentialPath.CLAUDE_SUBSCRIPTION_OAUTH,
            claude_subscription_passthrough=authorised,
        )


def test_claude_subscription_disabled_by_default() -> None:
    with pytest.raises(PermissionError):
        validate_credential_path(
            CLAUDE_CODE, CredentialPath.CLAUDE_SUBSCRIPTION_OAUTH
        )
    assert not DeploymentProfile("synthetic", "1", (), ()).claude_subscription_passthrough


def test_local_profiles_use_the_lab_settings() -> None:
    # The runtime gives E4B the same 16K context it was measured at.
    assert MODEL_PROFILES["gemma4-e4b"].context_tokens == 16384
    assert MODEL_PROFILES["gemma4-26b-a4b"].context_tokens == 16384
    assert MODEL_PROFILES["gemma4-26b-a4b"].cpu_moe_experts == 18


@pytest.mark.parametrize("operation", ["health", "chat"])
def test_spark_is_explicit_stub(operation) -> None:
    # M3: the Spark target reports itself unhealthy and refuses work (typed error) until configured and authorised.
    target = DGXSparkTarget()
    if operation == "health":
        assert target.health() is False
    else:
        with pytest.raises(TargetUnavailable, match="DGX Spark unavailable"):
            target.chat(InferenceRequest({"messages": []}))


class _FakeEngine:
    def __init__(self, text):
        self.text = text
        self.local_calls = 0

    def inspect_boundary(self, request, integration_mode, use_case):
        return (ContentLeaf(("synthetic",), self.text),)

    def prepare_local(self, request, integration_mode, capabilities):
        self.local_calls += 1
        return InferenceRequest({"messages": [{"role": "user", "content": self.text}]})

    def prepare(self, *args, **kwargs):
        raise AssertionError("PII/cloud preparation must not run")


class _FakeCredentials:
    def check_local_token(self, incoming):
        return None

    def cloud_headers(self, *args):
        raise AssertionError("cloud credentials must not be requested")

    def claude_headers(self, *args):
        raise AssertionError("subscription credentials must not be requested")

    def claude_api_headers(self, *args):
        raise AssertionError("cloud credentials must not be requested")


class _FakeUpstream:
    def send(self, *args):
        raise AssertionError("upstream must not be called")


class _FakeAudit:
    def __init__(self):
        self.rows = []

    def append_route(self, row):
        self.rows.append(row)


class _FakeTarget:
    name = "laptop"
    capabilities = InferenceCapabilities()

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def health(self):
        return True

    def chat(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("synthetic local failure")
        return InferenceResponse("Synthetic answer: four.")


@pytest.mark.parametrize(
    "api_route", [MESSAGES_ROUTE, RESPONSES_ROUTE, CHAT_COMPLETIONS_ROUTE]
)
def test_server_local_branch_answers_same_api_without_cloud(api_route) -> None:
    engine = _FakeEngine("PROTECTED\nSynthetic arithmetic: 2 + 2")
    target = _FakeTarget()
    audit = _FakeAudit()
    server = LocalGateServer(
        DeploymentProfile("synthetic", "1", (), (_policy(),)),
        engine, _FakeCredentials(), _FakeUpstream(), None, audit,
        {"laptop": target},
    )
    payload = server.handle_request(
        api_route, {}, "unused-cloud-destination", "synthetic", "synthetic", None
    )
    decoded = json.loads(payload.body)
    assert decoded["_themis"]["route"] == "LOCAL_ONLY"
    assert decoded["model"] == "local-laptop"
    assert "[LOCAL · laptop]" in payload.body.decode("utf-8")
    assert target.calls == 1
    assert engine.local_calls == 1
    assert audit.rows[0].fields.reason_code == RouteReasonCode.ROUTE_LOCAL_MARKING


def test_local_failure_has_no_cloud_fallback() -> None:
    server = LocalGateServer(
        DeploymentProfile("synthetic", "1", (), (_policy(),)),
        _FakeEngine("SECRET"), _FakeCredentials(), _FakeUpstream(),
        None, _FakeAudit(), {"laptop": _FakeTarget(fail=True)},
    )
    with pytest.raises(RuntimeError, match="synthetic local failure"):
        server.handle_request(
            MESSAGES_ROUTE, {}, "unused", "synthetic", "synthetic", None
        )


def test_spark_requires_boundary_approval() -> None:
    policy = _policy(local_inference_target=LocalTargetName.DGX_SPARK)
    server = LocalGateServer(
        DeploymentProfile("synthetic", "1", (), (policy,)),
        _FakeEngine("PROTECTED"), _FakeCredentials(), _FakeUpstream(),
        None, _FakeAudit(), {"dgx-spark": DGXSparkTarget()},
    )
    with pytest.raises(PermissionError, match="boundary"):
        server.handle_request(
            MESSAGES_ROUTE, {}, "unused", "synthetic", "synthetic", None
        )
