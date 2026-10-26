"""Shared pytest configuration.

Tests marked ``slow`` need real local models (PII-Tracer weights, llama-server)
and are skipped unless ``--run-slow`` is given.

Business-hours tests need a fixed non-UTC zone with daylight saving; the
fixture below pins Europe/Lisbon for every test so results do not depend on
the host or the shipped UTC default.
"""

from __future__ import annotations

import pytest

from themis_shield.detectors.markings import load_marking_profile, set_marking_profile
from themis_shield.detectors.rules import set_rule_packs
from themis_shield.detectors.settings import configure_detection_fixes, configure_detection_version
from themis_shield.policy import configure_policy
from themis_shield.timezone import set_business_timezone


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="run tests that need real local models",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="needs real local models; use --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _default_gate_versions():
    """Every test starts and ends on the shipped detection and policy versions (v0.2)."""
    configure_detection_version(None)
    configure_detection_fixes(None)
    configure_policy(None)
    yield
    configure_detection_version(None)
    configure_detection_fixes(None)
    configure_policy(None)


@pytest.fixture(autouse=True)
def _business_timezone():
    set_business_timezone("Europe/Lisbon")
    yield
    set_business_timezone(None)


@pytest.fixture(autouse=True)
def _default_markings(monkeypatch):
    """Every test starts on the shipped default (generic) marking profile."""
    monkeypatch.delenv("THEMIS_MARKINGS_PROFILE", raising=False)
    monkeypatch.delenv("THEMIS_PROFILES_DIR", raising=False)
    set_marking_profile(None)
    yield
    set_marking_profile(None)


@pytest.fixture
def au_pspf_markings(monkeypatch, _default_markings):
    """Route a test through the au-pspf example profile (also for subprocesses)."""
    monkeypatch.setenv("THEMIS_MARKINGS_PROFILE", "au-pspf")
    profile = set_marking_profile(load_marking_profile("au-pspf"))
    yield profile


@pytest.fixture(autouse=True)
def _rule_packs(monkeypatch):
    """Existing detector cases use Australian identifiers: enable the au pack.

    The shipped default is core-only; test_profiles covers that default.
    """
    monkeypatch.delenv("THEMIS_RULE_PACKS", raising=False)
    set_rule_packs(("core", "au"))
    yield
    set_rule_packs(None)
