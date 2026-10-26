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

from catalogue_fakes import PhraseTracer, _replace_tracer
from router_fakes import NOW, approved_response, harness, request


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


def normalised(requests):
    return TOKEN.sub("⟦T⟧", json.dumps(requests, ensure_ascii=False, sort_keys=True))


def run(tmp_path, *, configured, text, tracer=None):
    h = harness(tmp_path)
    if tracer is not None:
        h.router.targets = _replace_tracer(h.router.targets, tracer)
    if configured:
        configure(h.router, example_config())
    try:
        held = h.router.handle(request(text))
        outcome = [held.route, held.reason, held.status]
        if held.reason == "REQUESTER_CONFIRM_REQUIRED":
            headers = {"X-Themis-Confirm": held.job_id}
            again = h.router.handle(request(text), headers)
            outcome += [again.route, again.reason]
        else:
            headers = None
        if held.route == "held":
            h.register.approve(held.job_id)
            sent = h.router.handle(request(text), headers)
            outcome += [sent.route, sent.reason, sent.body["choices"][0]["message"]["content"]]
        with sqlite3.connect(tmp_path / "register.db") as db:
            extra = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("gate_decisions", "tripwire_records", "tripwire_reviews")
            }
            audit = [row[0] for row in db.execute("SELECT event FROM audit ORDER BY audit_id")]
        boundary = ChatBoundary(request(text), frozenset(), h.router.cloud_model)
        decision = h.router._protect(boundary, "synthetic-probe", set())[4]
        return outcome, normalised(h.fake.requests), extra, audit, decision
    finally:
        h.cloud.close()


@pytest.mark.parametrize("text, tracer", [
    ("Mara Lindahl asks a synthetic question.", None),
    ("The Harbour Committee met to plan the synthetic roster.", PhraseTracer(("Harbour Committee",))),
    ("What is a synthetic retention schedule?", None),
])
def test_switched_off_the_gate_behaves_exactly_as_before(tmp_path, text, tracer):
    plain = run(tmp_path / "plain", configured=False, text=text, tracer=tracer)
    off = run(tmp_path / "off", configured=True, text=text, tracer=tracer)
    assert off[0] == plain[0]                      # same routes, reasons, statuses and restored answer
    assert off[1] == plain[1]                      # byte-identical cloud requests (placeholders aside)
    assert off[2] == plain[2] == {"gate_decisions": 0, "tripwire_records": 0, "tripwire_reviews": 0}
    assert off[3] == plain[3]                      # identical audit trail
    assert type(off[4]) is DecisionResult and not isinstance(off[4], GateDecision)
    assert off[4] == plain[4]
    assert "TRIPWIRE_SENT" not in off[3]
