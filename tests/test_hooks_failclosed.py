"""Real synthetic CLI, marking and managed-settings tests.

No Claude credential files or transcripts are read. PII/register protection is
not implemented; the strict xfail records that missing behaviour, not safety.
"""

import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from themis_shield.hooks import claude_code_hooks as hooks
from themis_shield.hooks.__main__ import MAXIMUM_INPUT_CHARACTERS, main

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "examples" / "claude-code-managed-settings.example.json"


def _cli(event, raw, *, script=None):
    command = (
        [sys.executable, "-m", "themis_shield.hooks", event]
        if script is None else [sys.executable, "-c", script]
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        command, input=raw, text=True, encoding="utf-8",
        capture_output=True, cwd=ROOT, env=environment, timeout=10,
    )


@pytest.mark.parametrize(
    "raw", ["{", "[]", '{"prompt":NaN}', '{"prompt":"a","prompt":"b"}']
)
def test_cli_exits_nonzero_on_malformed_json(raw):
    result = _cli("UserPromptSubmit", raw)
    assert result.returncode != 0
    assert result.stdout == ""


def test_cli_exits_nonzero_on_unknown_event():
    result = _cli("UnknownEvent", '{"prompt":"synthetic"}')
    assert result.returncode != 0
    assert result.stdout == ""


def test_cli_exits_nonzero_on_internal_error_without_content_diagnostics():
    script = """
import runpy
import sys
from unittest.mock import patch
import themis_shield.hooks.claude_code_hooks as hooks

sys.argv = ["themis_shield.hooks", "UserPromptSubmit"]
with patch.object(hooks, "detect_markings",
                  side_effect=RuntimeError("SYNTHETIC_SECRET_SENTINEL")):
    runpy.run_module("themis_shield.hooks", run_name="__main__")
"""
    result = _cli("UserPromptSubmit", '{"prompt":"synthetic"}', script=script)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "SYNTHETIC_SECRET_SENTINEL" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "event", ["PreToolUse", "PostToolUse", "MessageDisplay", "SessionStart", "Stop"]
)
def test_unimplemented_events_fail_instead_of_succeeding(event):
    result = _cli(event, "{}")
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("prompt", ["PROTECTED", "SECRET", "TOP SECRET", "AUSTEO"])
def test_user_prompt_submit_blocks_local_markings(prompt):
    response = hooks.user_prompt_submit({"prompt": prompt})
    assert response["decision"] == "block"
    assert response["suppressOriginalPrompt"] is True
    assert "Kilo Code" in response["reason"]
    assert "local route" in response["reason"]


def test_protected_cli_returns_block_json():
    result = _cli("UserPromptSubmit", '{"prompt":"PROTECTED"}')
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "block"


def test_cli_rejects_oversized_input():
    output = io.StringIO()
    assert main(
        ["UserPromptSubmit"],
        stdin=io.StringIO(" " * (MAXIMUM_INPUT_CHARACTERS + 1)),
        stdout=output, stderr=io.StringIO(),
    ) != 0
    assert output.getvalue() == ""


def test_managed_settings_fail_closed_on_supported_events():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    assert settings["allowManagedHooksOnly"] is True
    assert set(settings["hooks"]) == {
        "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "MessageDisplay", "SessionStart", "Stop",
    }
    for event, groups in settings["hooks"].items():
        commands = [hook for group in groups for hook in group["hooks"]]
        assert commands
        for hook in commands:
            assert hook["type"] == "command"
            assert hook["command"] == f"python -m themis_shield.hooks {event}"
            if event in {"SessionStart", "Stop"}:
                assert "onFailure" not in hook
            else:
                assert hook["onFailure"] == "block"
    assert ">= 2.1.295" in settings["_comment"]


def test_managed_settings_has_no_proxy_and_telemetry_is_off():
    raw = SETTINGS.read_text(encoding="utf-8")
    assert "ANTHROPIC_BASE_URL" not in raw
    settings = json.loads(raw)
    assert settings["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"
    assert settings["env"]["OTEL_SDK_DISABLED"] == "true"
    assert settings["env"]["DISABLE_TELEMETRY"] == "1"


# Retired: four skeleton-era tests of the stub hook handlers. The handlers are now live clients
# of the gate; tests/test_hooks_live.py covers the same properties against a running gate (PROTECTED blocked before
# any HTTP or token use, plain prompts, PII prompts blocked with a clean draft, transcript field discarded).
