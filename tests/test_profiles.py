"""Profile-driven markings, policy configuration and external deployment profiles."""

from __future__ import annotations

import json

import pytest

from themis_shield.config import DeploymentProfile
from themis_shield.detectors.allowlist import compile_profile
from themis_shield.detectors.markings import (
    MarkingClass, MarkingPolicy, active_marking_profile, detect_markings,
    load_marking_profile, marking_policy, profile_from_dict,
)
from themis_shield.detectors.profile_adapter import adapt
from themis_shield.integration import (
    cloud_settings, configure_markings, institution_profile, runtime_settings,
)
from themis_shield.profiles import available_profiles, load_profile_json, profile_path
from themis_shield.routing import Route, RouteReasonCode, route_request
from themis_shield.config import UseCasePolicy


def _policy() -> UseCasePolicy:
    return UseCasePolicy("synthetic", ("cloud",), approved_for_cloud=True)


def _body(text: str) -> str:
    return "\n".join(
        [f"ordinary opening line {index}" for index in range(4)]
        + [text]
        + [f"ordinary closing line {index}" for index in range(4)]
    )


# --- generic profile (the default) -----------------------------------------


def test_generic_is_the_default_profile():
    assert active_marking_profile().name == "generic"
    assert marking_policy() == MarkingPolicy(MarkingClass.SENSITIVE, MarkingClass.RESTRICTED)


@pytest.mark.parametrize("banner,tier,route", [
    ("CONFIDENTIAL", MarkingClass.SENSITIVE, Route.CLOUD),
    ("STRICTLY CONFIDENTIAL", MarkingClass.RESTRICTED, Route.LOCAL_ONLY),
    ("DO NOT SHARE", MarkingClass.RESTRICTED, Route.LOCAL_ONLY),
    ("Restricted", MarkingClass.RESTRICTED, Route.LOCAL_ONLY),
    ("TOP SECRET", MarkingClass.TOP_SECRET, Route.LOCAL_ONLY),
    ("INTERNAL USE ONLY", MarkingClass.INTERNAL, Route.CLOUD),
])
def test_generic_banners(banner, tier, route):
    result = detect_markings(f"{banner}\nSynthetic content.\n{banner}")
    assert result.classification == tier
    decision = route_request(result, _policy())
    assert decision.route == route
    if tier == MarkingClass.SENSITIVE:
        assert decision.mandatory_confirm and decision.local_alternative_offered


def test_generic_body_case_rule_and_foreign_labels():
    assert detect_markings(_body("the confidential nature of the work")).classification == MarkingClass.NONE
    assert detect_markings(_body("This file is CONFIDENTIAL.")).classification == MarkingClass.SENSITIVE
    # Another scheme's labels mean nothing to the generic profile.
    assert detect_markings("PROTECTED\nSynthetic.").classification == MarkingClass.NONE


def test_generic_caveat_and_handling_markers():
    caveat = detect_markings(_body("Board pack, EYES ONLY."))
    assert caveat.caveats == ("EYES_ONLY",)
    assert caveat.classification == MarkingClass.RESTRICTED
    assert route_request(caveat, _policy()).route == Route.LOCAL_ONLY
    handling = detect_markings(_body("This advice is legally privileged."))
    assert handling.handling == ("LEGAL_PRIVILEGE",)
    decision = route_request(handling, _policy())
    assert decision.reason_code == RouteReasonCode.ROUTE_CLOUD_HANDLING_ASK
    assert decision.mandatory_confirm


def test_local_only_message_uses_the_profile_label():
    decision = route_request(detect_markings("DO NOT SHARE"), _policy())
    assert "marked RESTRICTED" in decision.user_message


# --- policy is configuration -------------------------------------------------


def test_policy_override_moves_the_local_only_threshold():
    profile = configure_markings({"markings": {"profile": "generic", "local_only_at": "SECRET"}})
    assert profile.policy.local_only_at == MarkingClass.SECRET
    assert not marking_policy().is_local_only(MarkingClass.RESTRICTED)
    assert marking_policy().needs_confirm(MarkingClass.RESTRICTED)
    decision = route_request(detect_markings("STRICTLY CONFIDENTIAL"), _policy())
    assert decision.route == Route.CLOUD and decision.mandatory_confirm
    assert route_request(detect_markings("SECRET"), _policy()).route == Route.LOCAL_ONLY


@pytest.mark.parametrize("bad", [
    {"confirm_at": "RESTRICTED", "local_only_at": "SENSITIVE"},
    {"confirm_at": "NONE"},
    {"local_only_at": "BANANA"},
])
def test_invalid_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        configure_markings({"markings": {"profile": "generic", **bad}})


def test_unknown_markings_settings_are_rejected():
    with pytest.raises(ValueError):
        configure_markings({"markings": {"profile": "generic", "allow_cloud": True}})


def test_profile_documents_are_validated():
    with pytest.raises(ValueError):
        profile_from_dict({"name": "x", "rules": [{"category": "CLASSIFICATION", "value": "NOPE", "phrase": "X"}]})
    with pytest.raises(ValueError):
        profile_from_dict({"name": "x", "rules": [{"category": "CAVEAT", "value": "A", "phrase": "A", "regex": "A"}]})
    with pytest.raises(ValueError):
        profile_from_dict({"name": "x", "rules": []})
    with pytest.raises(ValueError):
        profile_from_dict({"name": "x", "surprise": 1, "rules": [
            {"category": "CLASSIFICATION", "value": "SECRET", "phrase": "SECRET"},
        ]})


def test_shipped_profiles_load():
    assert {"generic", "au-pspf"} <= set(available_profiles("markings"))
    pspf = load_marking_profile("au-pspf")
    assert detect_markings("PROTECTED", pspf).classification == MarkingClass.RESTRICTED
    assert pspf.label(MarkingClass.RESTRICTED) == "PROTECTED"
    assert "harbourline-demo" in available_profiles("institution")
    with pytest.raises(FileNotFoundError):
        profile_path("markings", "does-not-exist")
    with pytest.raises(ValueError):
        profile_path("markings", "../generic")


# --- external deployment profiles directory ----------------------------------


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_profiles_load_from_an_external_directory(tmp_path):
    deploy = tmp_path / "deployment-layer"
    _write(deploy / "markings" / "acme.json", {
        "name": "acme",
        "policy": {"confirm_at": "SENSITIVE", "local_only_at": "RESTRICTED"},
        "rules": [
            {"category": "CLASSIFICATION", "value": "RESTRICTED", "phrase": "ACME EYES"},
            {"category": "CLASSIFICATION", "value": "SENSITIVE", "phrase": "ACME INTERNAL"},
        ],
        "tier_labels": {"RESTRICTED": "ACME EYES"},
    })
    _write(deploy / "institution" / "acme.json", {
        "institution": "Acme Widget Academy",
        "email_domain": "acme.example",
        "role_mailboxes": ["help@acme.example"],
        "campuses": [{"town": "Gearton", "forms": ["Gearton campus"], "reception": "(02) 5550 0199"}],
    })
    _write(deploy / "cloud" / "acme.json", {
        "base_url": "https://llm.acme.example/v1", "model": "acme-large",
        "api_key_env": "ACME_LLM_KEY", "destination_id": "acme-cloud",
    })
    _write(deploy / "catalogue" / "acme.json", {
        "version": 1, "models": [{"id": "gemma4-e4b", "display_name": "Acme tuned E4B"}],
    })
    config = {
        "profiles": {"dir": str(deploy)},
        "markings": {"profile": "acme"},
        "detection": {"institution_profile": "acme"},
        "cloud": {"profile": "acme", "max_tokens": 4096},
        "runtime": {"catalogue": "acme"},
    }

    profile = configure_markings(config)
    assert profile.name == "acme"
    result = detect_markings("ACME EYES\nSynthetic.")
    assert result.classification == MarkingClass.RESTRICTED
    decision = route_request(result, _policy())
    assert decision.route == Route.LOCAL_ONLY and "ACME EYES" in decision.user_message

    facts = institution_profile(config)
    adapted = adapt(DeploymentProfile("acme", "1", (), (), institution=facts))
    assert "Gearton campus" in adapted["literals"]
    assert "Acme Widget Academy" in adapted["literals"]
    assert compile_profile(DeploymentProfile("acme", "1", (), (), institution=facts)).literal_patterns

    settings = cloud_settings(config)
    assert settings.url == "https://llm.acme.example/v1/chat/completions"
    assert settings.api_key_env == "ACME_LLM_KEY" and settings.max_tokens == 4096

    runtime = runtime_settings(config)
    assert runtime.catalogue_file == deploy / "catalogue" / "acme.json"
    from themis_shield.runtime.catalogue import Catalogue
    assert Catalogue.load(runtime.catalogue_file).get("gemma4-e4b").display_name == "Acme tuned E4B"


def test_external_profile_overrides_a_shipped_name(tmp_path):
    _write(tmp_path / "markings" / "generic.json", {
        "name": "generic",
        "rules": [{"category": "CLASSIFICATION", "value": "SECRET", "phrase": "HUSH"}],
    })
    profile = load_marking_profile("generic", tmp_path)
    assert detect_markings("HUSH", profile).classification == MarkingClass.SECRET
    assert detect_markings("CONFIDENTIAL", profile).classification == MarkingClass.NONE


def test_profiles_dir_from_environment(tmp_path, monkeypatch):
    _write(tmp_path / "institution" / "envco.json", {"institution": "Env Co"})
    monkeypatch.setenv("THEMIS_PROFILES_DIR", str(tmp_path))
    assert load_profile_json("institution", "envco") == {"institution": "Env Co"}


def test_cloud_configuration_never_holds_a_credential(tmp_path):
    with pytest.raises(ValueError, match="credentials"):
        cloud_settings({"cloud": {"base_url": "https://x.example/v1", "model": "m", "api_key": "sk-not-real"}})
    _write(tmp_path / "cloud" / "leaky.json", {"base_url": "https://x.example/v1", "model": "m", "token": "t"})
    with pytest.raises(ValueError, match="credentials"):
        cloud_settings({"profiles": {"dir": str(tmp_path)}, "cloud": {"profile": "leaky"}})


def test_example_configuration_is_valid():
    from pathlib import Path
    from themis_shield.integration import load_config
    from themis_shield.protectors import build_protector
    from themis_shield.dpapi import ProtectionError

    config = load_config(Path(__file__).resolve().parents[1] / "config" / "themis.example.toml")
    assert configure_markings(config).name == "generic"
    settings = cloud_settings(config)
    assert settings.api_key_env == "THEMIS_CLOUD_API_KEY"
    assert runtime_settings(config).catalogue_file is None
    assert institution_profile(config) is None
    with pytest.raises(ProtectionError):
        build_protector(config["vault"], platform="posix")
