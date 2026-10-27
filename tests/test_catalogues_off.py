"""Proof that every v0.3 catalogue feature is off by default and that, off, the gate behaves as v0.2.1."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
import tomllib

import pytest

from themis_shield.catalogues.gate import GateDecision, configure, settings
from themis_shield.chat_boundary import ChatBoundary
from themis_shield.types import DecisionResult

from router_fakes import NOW, harness, request
from v021_golden import CORPUS, run_corpus


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧")


def example_config():
    with (ROOT / "config" / "themis.example.toml").open("rb") as stream:
        return tomllib.load(stream)


@pytest.mark.parametrize("config", [{}, "example", {"known_miss": {}, "false_positives": {}, "tripwire": {}}])
def test_every_feature_is_off_by_default(config):
    values = settings(example_config() if config == "example" else config)
    assert values["known_miss"]["enabled"] is False
    assert values["false_positives"]["enabled"] is False
    assert values["tripwire"]["enabled"] is False


def test_example_config_lists_every_key_switched_off():
    config = example_config()
    assert config["known_miss"]["enabled"] is False
    assert config["false_positives"]["enabled"] is False
    assert config["tripwire"]["enabled"] is False


@pytest.mark.parametrize("section, value", [
    ("known_miss", {"enabled": "yes"}), ("tripwire", {"enabled": 1}), ("false_positives", {"bogus": True}),
    ("known_miss", {"max_snapshot_age_days": 0}), ("tripwire", {"max_completion_tokens": 10}),
])
def test_bad_settings_refuse_to_start(section, value):
    with pytest.raises(ValueError):
        settings({section: value})


def test_configure_off_attaches_nothing_and_creates_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    router = SimpleNamespace(clock=lambda: NOW)
    configure(router, example_config())
    assert router.known_miss is None and router.fp_catalogue is None and router.tripwire is None
    assert list(tmp_path.iterdir()) == []


def test_configure_on_attaches_each_feature(tmp_path):
    from themis_shield.protectors import PassphraseProtector
    router = SimpleNamespace(clock=lambda: NOW)
    configure(router, {
        "known_miss": {"enabled": True, "path": str(tmp_path / "k.db")},
        "false_positives": {"enabled": True, "path": str(tmp_path / "f.db"), "scope": "synthetic-org"},
        "tripwire": {"enabled": True},
    }, protector_factory=lambda: PassphraseProtector("synthetic-passphrase-not-real-01"))
    assert router.known_miss is not None and router.fp_catalogue.scope == "synthetic-org"
    assert router.tripwire.max_completion_tokens == 2048
    assert (tmp_path / "k.db").is_file() and (tmp_path / "f.db").is_file()


GOLDEN = json.loads(
    (ROOT / "tests" / "fixtures" / "v021_router_golden.json").read_text(encoding="utf-8")
)


def test_golden_transcript_came_from_main():
    assert GOLDEN["generated_from"] == "main (v0.2.1)"
    assert set(GOLDEN["cases"]) == {case["name"] for case in CORPUS}


def _configured_off(router):
    configure(router, example_config())


def _enabled_but_empty(tmp_path):
    from themis_shield.protectors import PassphraseProtector

    def prepare(router):
        name = str(len(list(tmp_path.glob("k-*.db"))))
        configure(router, {
            "known_miss": {"enabled": True, "path": str(tmp_path / f"k-{name}.db")},
            "false_positives": {"enabled": True, "path": str(tmp_path / f"f-{name}.db")},
        }, protector_factory=lambda: PassphraseProtector("synthetic-passphrase-not-real-01"))
    return prepare


@pytest.mark.parametrize("setup", ["unconfigured", "configured-off", "enabled-but-empty"])
def test_off_or_empty_catalogues_reproduce_v021_exactly(tmp_path, setup):
    """The same fixed corpus, run on main's v0.2.1 code (the golden file) and on this code, gives an identical
    transcript: routes, reasons, statuses, response text, cloud requests, register rows and audit trail. Lane 1's
    friction fixes are set to "none" for the comparison (its documented v0.2.1 reproduction)."""
    stores = tmp_path / "stores"
    stores.mkdir()
    prepare = {
        "unconfigured": None, "configured-off": _configured_off,
        "enabled-but-empty": _enabled_but_empty(stores),
    }[setup]
    runs = tmp_path / "runs"
    runs.mkdir()
    assert run_corpus(prepare, runs) == GOLDEN["cases"]


def test_switched_off_the_decision_is_the_plain_v021_type(tmp_path):
    h = harness(tmp_path)
    try:
        configure(h.router, example_config())
        boundary = ChatBoundary(request("Mara Lindahl asks a synthetic question."), frozenset(), h.router.cloud_model)
        decision = h.router._protect(boundary, "synthetic-probe", set())[4]
        assert type(decision) is DecisionResult and not isinstance(decision, GateDecision)
        assert h.register.list_jobs() == ()
    finally:
        h.cloud.close()
