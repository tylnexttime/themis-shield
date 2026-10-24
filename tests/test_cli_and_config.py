"""The `themis` command surface, the [hooks] config section and the shipped example configs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from themis_shield import __version__
from themis_shield.cli import main as themis
from themis_shield.detectors import llm_finder
from themis_shield.hooks.__main__ import EVENT_FUNCTIONS, main as hooks_main
from themis_shield.hooks.protocol import DEFAULT_TOOLS
from themis_shield.hooks.service import HookPolicy
from themis_shield.integration import (
    build_router, configure_rule_packs, hooks_policy, institution_profile, load_config,
)
from themis_shield.register.cli import parser as register_parser
from themis_shield.register.models import REVIEW_BOARD_OUTCOMES

ROOT = Path(__file__).resolve().parents[1]
REHEARSAL = ROOT / "config" / "rehearsal.example.toml"
MINIMAL = ROOT / "config" / "themis.example.toml"


@pytest.fixture(autouse=True)
def _reset_finder():
    """build_router configures the process-wide finder prompt; restore the default afterwards."""
    yield
    llm_finder.configure_finder(context=llm_finder.DEFAULT_FINDER_CONTEXT, prompt_version=None, do_not_flag=None)


# --- CLI -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version(flag, capsys):
    assert themis([flag]) == 0
    assert capsys.readouterr().out.strip() == f"themis-shield {__version__}"


def test_bare_themis_prints_help_and_usage_error(capsys):
    assert themis([]) == 2
    out = capsys.readouterr().out
    assert "themis serve" in out and "themis --version" in out


@pytest.mark.parametrize("command, prog", [
    ("register", "usage: themis register"),
    ("runtime", "usage: themis runtime"),
    ("serve", "usage: themis serve"),
    ("profiles", "usage: themis profiles"),
])
def test_subcommand_help_names_the_themis_command(command, prog, capsys):
    with pytest.raises(SystemExit) as caught:
        themis([command, "--help"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.startswith(prog)


def test_hooks_without_event_prints_usage_and_events():
    out, err = io.StringIO(), io.StringIO()
    assert hooks_main([], stdin=io.StringIO(""), stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "usage: themis hooks <event>" in err.getvalue()
    for event in EVENT_FUNCTIONS:
        assert event in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert hooks_main(["--help"], stdin=io.StringIO(""), stdout=out, stderr=err) == 0
    assert "events: " in out.getvalue()


def test_hooks_unknown_event_fails_closed_without_echoing_it():
    out, err = io.StringIO(), io.StringIO()
    assert hooks_main(["NotAnEvent-SYNTHETIC"], stdin=io.StringIO("{}"), stdout=out, stderr=err) == 1
    assert out.getvalue() == ""
    assert "unknown hook event" in err.getvalue() and "NotAnEvent-SYNTHETIC" not in err.getvalue()


def test_register_board_outcomes_match_the_model():
    action = next(
        option for sub in register_parser()._subparsers._group_actions[0].choices.values()
        for option in sub._actions if "--board-outcome" in option.option_strings
    )
    assert set(action.choices) == REVIEW_BOARD_OUTCOMES


# --- [hooks] ---------------------------------------------------------------------------------------------------------

def test_hooks_policy_defaults_closed():
    for config in ({}, {"hooks": {}}, load_config(MINIMAL), load_config(REHEARSAL)):
        policy = hooks_policy(config)
        assert policy == HookPolicy()
        assert policy.cloud_authorised is False and policy.roots == () and policy.drafts_dir is None


def test_hooks_policy_from_config(tmp_path):
    policy = hooks_policy({"hooks": {
        "cloud_authorised": True, "roots": [str(tmp_path)], "allowed_tools": ["Read", "Grep"],
        "drafts_dir": str(tmp_path / "drafts"), "destination_id": "claude-direct-rehearsal",
    }})
    assert policy.cloud_authorised is True
    assert policy.roots == (tmp_path,)
    assert policy.allowed_tools == frozenset({"Read", "Grep"})
    assert policy.drafts_dir == tmp_path / "drafts"
    assert policy.destination_id == "claude-direct-rehearsal"
    assert hooks_policy({"hooks": {"allowed_tools": sorted(DEFAULT_TOOLS)}}).allowed_tools == DEFAULT_TOOLS


@pytest.mark.parametrize("section", [
    {"cloud_authorised": "true"},
    {"cloud_authorised": 1},
    {"roots": "C:/work"},
    {"roots": [""]},
    {"roots": ["relative/path"]},
    {"roots": ["/definitely/not/an/existing/synthetic/root"]},
    {"allowed_tools": ["Bash"]},
    {"allowed_tools": "Read"},
    {"drafts_dir": "relative"},
    {"drafts_dir": 3},
    {"destination_id": "has spaces"},
    {"model": 7},
    {"unexpected": True},
])
def test_hooks_policy_refuses_invalid_values(section):
    with pytest.raises(ValueError):
        hooks_policy({"hooks": section})


def _rehearsal_copy(tmp_path, hooks=""):
    text = REHEARSAL.read_text(encoding="utf-8")
    replacements = {
        'protector = "auto"': 'protector = "null"',
        "insecure_test_only = false": "insecure_test_only = true",
        '# path = "~/.local/share/themis-shield/register.db"':
            f'path = "{(tmp_path / "register.db").as_posix()}"',
        '# path = "~/.local/share/themis-shield/vault.db"':
            f'path = "{(tmp_path / "vault.db").as_posix()}"',
    }
    for old, new in replacements.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    if hooks:
        old = "cloud_authorised = false\nroots = []"
        assert text.count(old) == 1
        text = text.replace(old, hooks)
    path = tmp_path / "rehearsal.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _build(path):
    router = build_router(path)
    try:
        return router, router.hooks_policy
    finally:
        router.cloud.close()
        router.vault.close()
        router.runtime.close()


def test_configured_hook_policy_reaches_the_router(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, closed = _build(_rehearsal_copy(tmp_path))
    assert closed == HookPolicy()
    path = _rehearsal_copy(
        tmp_path, f'cloud_authorised = true\nroots = ["{workspace.as_posix()}"]',
    )
    router, policy = _build(path)
    assert policy.cloud_authorised is True and policy.roots == (workspace,)
    assert router.use_case.approved_for_cloud is True


def test_invalid_hooks_section_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    with pytest.raises(ValueError):
        build_router(_rehearsal_copy(tmp_path, 'cloud_authorised = "yes"\nroots = []'))


# --- shipped example configs -----------------------------------------------------------------------------------------

def test_rehearsal_example_is_the_evaluated_pipeline_with_a_second_party():
    config = load_config(REHEARSAL)
    gate = config["gate"]
    assert gate["synthetic_only"] is True
    assert gate["synthetic_cloud_authorised"] is True and gate["local_authorised"] is True
    assert gate["operator_console_enabled"] is True and gate["pilot_fixture_enabled"] is False
    assert gate["operator_identity"] != gate["requester_identity"]
    assert config["markings"]["profile"] == "generic"
    assert configure_rule_packs(config) == ("core", "au", "intl")
    assert config["detection"]["finder_context"] == "a training and research institute"
    assert institution_profile(config) is not None
    assert config["detection"]["institution_profile"] == "harbourline-eval"


def test_minimal_example_stays_closed_and_points_at_the_rehearsal():
    text = MINIMAL.read_text(encoding="utf-8")
    assert "config/rehearsal.example.toml" in text.split("[gate]")[0]
    gate = load_config(MINIMAL)["gate"]
    for flag in ("synthetic_cloud_authorised", "local_authorised", "pilot_fixture_enabled",
                 "operator_console_enabled"):
        assert gate[flag] is False, flag


def test_rehearsal_example_builds_a_router(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    router, _ = _build(_rehearsal_copy(tmp_path))
    assert router.operator_console_enabled is True
    assert router.operator_identity == "synthetic-operator-01"
    assert router.local_authorised is True
