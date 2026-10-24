"""Rule packs: core by default, regional identifier packs are opt-in."""

from __future__ import annotations

import pytest

from themis_shield.detectors.rules import (
    DEFAULT_RULE_PACKS, RulesDetector, active_rule_packs, iban_valid, set_rule_packs,
)
from themis_shield.integration import configure_rule_packs
from themis_shield.types import Label

TFN = "731 842 658"          # synthetic, checksum-valid
IBAN = "GB82 WEST 1234 5698 7654 32"   # the published IBAN example
UK_DRAMA_NUMBER = "+44 20 7946 0958"   # reserved for drama use


def _labels(text, packs=None):
    return {label for item in RulesDetector(packs=packs).detect(text).findings for label in item.labels}


def test_default_is_core_only(monkeypatch):
    monkeypatch.delenv("THEMIS_RULE_PACKS", raising=False)
    assert set_rule_packs(None) == DEFAULT_RULE_PACKS == ("core",)
    found = _labels(f"TFN {TFN}, mail a.person@example.com, call 0491 570 156")
    assert Label.EMAIL in found
    assert Label.TFN not in found and Label.PHONE not in found


def test_au_pack_adds_checksummed_identifiers_and_phones():
    found = _labels(f"TFN {TFN}, call 0491 570 156", packs=("au",))
    assert {Label.TFN, Label.PHONE} <= found
    assert Label.TFN not in _labels("TFN 731 842 659", packs=("au",))  # bad checksum


def test_intl_pack_adds_iban_and_e164():
    assert iban_valid(IBAN) and not iban_valid("GB83 WEST 1234 5698 7654 32")
    found = _labels(f"Pay {IBAN} or ring {UK_DRAMA_NUMBER}", packs=("intl",))
    assert {Label.BANK_ACCOUNT, Label.PHONE} <= found
    assert Label.BANK_ACCOUNT not in _labels(f"Pay {IBAN}")


def test_rule_packs_from_configuration_and_environment(monkeypatch):
    assert configure_rule_packs({"detection": {"rule_packs": ["au", "intl"]}}) == ("core", "au", "intl")
    assert configure_rule_packs({}) == ("core",)
    with pytest.raises(ValueError):
        configure_rule_packs({"detection": {"rule_packs": ["xx"]}})
    monkeypatch.setenv("THEMIS_RULE_PACKS", "au")
    set_rule_packs(None)
    assert active_rule_packs() == ("core", "au")
