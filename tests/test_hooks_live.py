"""Managed-hooks mode tests against a real loopback uvicorn server.

No local models or external cloud traffic. The HTTP server owns the existing
router_fakes router, runtime, vault, detector clients and real SQLite register.

"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
import time

import httpx
import pytest
import uvicorn

from themis_shield.cloud.openai_compat import canonical
from themis_shield.hooks.__main__ import main
from themis_shield.hooks.client import HookClient, budget
from themis_shield.hooks.protocol import tool_output
from themis_shield.hooks.service import HookPolicy
from themis_shield.web.app import create_app

from router_fakes import harness

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "SYNTHETIC_LOCAL_GATE_TOKEN_1234567890"
SESSION = "synthetic-m6a"


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    token_file = tmp_path / "local" / "themis-shield" / "gate-token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(TOKEN, encoding="ascii")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    h = harness(tmp_path)
    h.router.hooks_policy = HookPolicy(
        cloud_authorised=True, roots=(workspace,),
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_app(h.router, token=TOKEN, port=port)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, workers=1,
        access_log=False, log_level="critical",
    ))
    thread = Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(5)
        h.cloud.close()
        pytest.fail("Synthetic live server did not start")
    h.url = f"http://127.0.0.1:{port}"
    h.app = app
    h.workspace = workspace
    h.local = tmp_path / "local"
    try:
        yield h
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        h.cloud.close()
        h.router.vault.close()
        assert not thread.is_alive()


def cli(h, event, **payload):
    output, error = io.StringIO(), io.StringIO()
    code = main(
        [event],
        stdin=io.StringIO(json.dumps({
            "session_id": SESSION, "hook_event_name": event, **payload,
        })),
        stdout=output, stderr=error, base_url=h.url,
    )
    return code, json.loads(output.getvalue()) if output.getvalue() else {}, error.getvalue()


def active(h):
    code, held, _ = cli(h, "UserPromptSubmit", prompt="What is a retention schedule?")
    assert code == 0 and held["decision"] == "block"
    state = h.app.state.hooks.sessions[SESSION]
    identifier = next(reversed(state.jobs.values()))
    h.register.approve(identifier)
    code, allowed, _ = cli(h, "UserPromptSubmit", prompt="What is a retention schedule?")
    assert code == 0 and allowed == {}
    return identifier


def read_response(path, content):
    return {
        "type": "text",
        "file": {
            "filePath": str(path), "content": content,
            "numLines": 1, "startLine": 1, "totalLines": 1,
        },
    }


def issue_name(h):
    active(h)
    path = h.workspace / "clean.txt"
    code, response, _ = cli(
        h, "PostToolUse", tool_name="Read",
        tool_response=read_response(path, "Mara Lindahl"),
    )
    assert code == 0
    clean = response["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "Mara Lindahl" not in clean and "⟦P:" in clean
    return clean


def test_protected_prompt_is_blocked_before_http_or_token(live, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Token/HTTP must not be used for local marking")

    monkeypatch.setattr("themis_shield.hooks.client.read_token", forbidden)
    code, response, diagnostics = cli(live, "UserPromptSubmit", prompt="PROTECTED")
    assert code == 0
    assert response["decision"] == "block"
    assert response["suppressOriginalPrompt"] is True
    assert "local route" in response["reason"]
    assert diagnostics == ""
    assert live.finders.roles == []
    assert live.fake.requests == []


def test_prompt_with_name_writes_only_clean_draft(live):
    code, response, diagnostics = cli(
        live, "UserPromptSubmit", prompt="Mara Lindahl asks a synthetic question.",
        transcript_path="DO-NOT-OPEN-synthetic-transcript.jsonl",
    )
    assert code == 0 and diagnostics == ""
    assert response["decision"] == "block"
    assert response["suppressOriginalPrompt"] is True
    assert "Mara Lindahl" not in json.dumps(response)
    assert str(live.local / "themis-shield" / "hooks" / SESSION) in response["reason"]
    files = list((live.local / "themis-shield" / "hooks" / SESSION).glob("prompt-*.txt"))
    assert len(files) == 1
    clean = files[0].read_text(encoding="utf-8")
    assert "Mara Lindahl" not in clean and "⟦P:" in clean
    assert live.fake.requests == []
    # The server, not the CLI, owns the mapping under the specified key.
    mapping = live.router.vault.mapping("claude:" + SESSION)
    assert "Mara Lindahl" in mapping.values()

    code, held, _ = cli(live, "UserPromptSubmit", prompt=clean)
    assert code == 0 and held["decision"] == "block"
    state = live.app.state.hooks.sessions[SESSION]
    identifier = next(reversed(state.jobs.values()))
    live.register.approve(identifier)
    code, allowed, _ = cli(live, "UserPromptSubmit", prompt=clean)
    assert code == 0 and allowed == {}
    assert live.fake.requests == []


def test_clean_prompt_two_stage_hold_then_no_stdout(live):
    active(live)
    assert live.fake.requests == []
    row = live.register.list_jobs()[-1]
    assert row["client"] == "CLAUDE_CODE" and row["integration_mode"] == "managed-hooks"
    assert row["destination_id"] == "claude-direct-synthetic"
    assert row["stage1"] == row["stage2"] == "approved"


def test_real_cli_process_clean_prompt(live):
    active(live)
    script = (
        "from themis_shield.hooks.__main__ import main; "
        "import sys; "
        "raise SystemExit(main(['UserPromptSubmit'], base_url=sys.argv[1]))"
    )
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script, live.url],
        input=json.dumps({
            "session_id": SESSION, "prompt": "What is a retention schedule?",
        }),
        text=True, encoding="utf-8", capture_output=True,
        cwd=ROOT, env=environment, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert live.fake.requests == []


def test_read_protected_file_denied_before_execution(live):
    active(live)
    path = live.workspace / "protected.txt"
    path.write_text("PROTECTED\nSynthetic timetable.", encoding="utf-8")
    code, response, _ = cli(
        live, "PreToolUse", tool_name="Read", tool_input={"file_path": str(path)},
    )
    assert code == 0
    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "local route" in output["permissionDecisionReason"]


@pytest.mark.parametrize("tool", [
    "WebFetch", "WebSearch", "mcp__unknown__lookup", "Bash", "UnknownTool",
])
def test_network_shell_and_unknown_tools_denied(live, tool):
    code, response, _ = cli(
        live, "PreToolUse", tool_name=tool, tool_input={},
    )
    # Unknown session is a transport failure, so register it first.
    if code:
        assert cli(live, "SessionStart")[0] == 0
        code, response, _ = cli(
            live, "PreToolUse", tool_name=tool, tool_input={},
        )
    assert code == 0
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert live.fake.requests == []


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_candidate_tools_without_output_profile_are_denied(live, tool):
    assert cli(live, "SessionStart")[0] == 0
    code, response, _ = cli(
        live, "PreToolUse", tool_name=tool, tool_input={},
    )
    assert code == 0
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "output profile" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_tool_output_deidentified_in_its_own_shape(live):
    token = issue_name(live)
    assert token.startswith("⟦P:")
    assert live.fake.requests == []


def test_tool_input_restored_strictly(live):
    token = issue_name(live)
    path = live.workspace / "clean.txt"
    path.write_text("Harmless synthetic text.", encoding="utf-8")
    code, response, _ = cli(
        live, "PreToolUse", tool_name="Grep",
        tool_input={
            "pattern": token, "path": str(live.workspace),
            "output_mode": "content",
        },
    )
    assert code == 0
    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["pattern"] == "Mara Lindahl"
    assert output["updatedInput"]["path"] == str(live.workspace)


def test_display_restored_and_cross_session_not_restored(live):
    token = issue_name(live)
    code, response, _ = cli(live, "MessageDisplay", text="Hello " + token)
    assert code == 0 and response == {"displayContent": "Hello Mara Lindahl"}
    with httpx.Client(trust_env=False) as client:
        headers = {"Authorization": "Bearer " + TOKEN}
        assert client.post(
            live.url + "/api/hooks/session-start",
            json={"session_id": "synthetic-other"}, headers=headers,
        ).status_code == 200
        other = client.post(
            live.url + "/api/hooks/display",
            json={"session_id": "synthetic-other", "text": token},
            headers=headers,
        )
    assert other.status_code == 200
    assert other.json() == {"displayContent": token}


@pytest.mark.parametrize("tool,value", [
    ("Read", {"content": "Mara Lindahl"}),
    ("Grep", {"content": "Mara Lindahl"}),
    ("Glob", {"filenames": ["Mara Lindahl"]}),
    ("Bash", {"stdout": "Mara Lindahl"}),
])
def test_mismatched_output_schema_is_not_returned_as_replacement(live, tool, value):
    active(live)
    code, response, diagnostics = cli(
        live, "PostToolUse", tool_name=tool, tool_response=value,
    )
    assert code != 0 and response == {}
    assert "Mara Lindahl" not in diagnostics


@pytest.mark.parametrize("tool,value", [
    ("Glob", {
        "filenames": ["Mara Lindahl.txt"],
        "numFiles": 1, "durationMs": 1, "truncated": False,
    }),
    ("Grep", {
        "mode": "content", "filenames": ["synthetic.txt"], "numFiles": 1,
        "content": "Mara Lindahl", "numMatches": 1, "numLines": 1,
    }),
    ("Bash", {
        "stdout": "Mara Lindahl", "stderr": "", "interrupted": False, "isImage": False,
    }),
])
def test_each_defensive_output_profile_has_a_shape_test(live, tool, value):
    active(live)
    code, response, _ = cli(
        live, "PostToolUse", tool_name=tool, tool_response=value,
    )
    assert code == 0
    updated = response["hookSpecificOutput"]["updatedToolOutput"]
    tool_output(tool, updated)
    assert "Mara Lindahl" not in json.dumps(updated)


def test_unexpected_tool_post_blocks_without_replacement(live):
    assert cli(live, "SessionStart")[0] == 0
    code, response, _ = cli(
        live, "PostToolUse", tool_name="WebFetch",
        tool_response={"text": "Mara Lindahl"},
    )
    assert code == 0 and response["decision"] == "block"
    assert "Mara Lindahl" not in json.dumps(response)
    assert "hookSpecificOutput" not in response


def test_stop_completes_active_job_and_is_idempotent(live):
    identifier = active(live)
    assert cli(live, "Stop")[0] == 0
    assert live.register.get_job(identifier).status.value == "Completed"
    assert cli(live, "Stop")[0] == 0


def test_gate_down_nonzero():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    output = io.StringIO()
    code = main(
        ["UserPromptSubmit"],
        stdin=io.StringIO(json.dumps({
            "session_id": SESSION, "prompt": "Harmless synthetic question.",
        })),
        stdout=output, stderr=io.StringIO(),
        base_url=f"http://127.0.0.1:{port}",
    )
    assert code != 0 and output.getvalue() == ""


def test_live_timeout_nonzero(live, monkeypatch):
    original = live.app.state.hooks.prompt

    def slow(*args):
        time.sleep(0.2)
        return original(*args)

    monkeypatch.setattr(live.app.state.hooks, "prompt", slow)
    output, error = io.StringIO(), io.StringIO()
    client = HookClient(live.url, wait_seconds=0.05)
    began = time.monotonic()
    code = main(
        ["UserPromptSubmit"],
        stdin=io.StringIO(json.dumps({
            "session_id": SESSION, "prompt": "Synthetic harmless text.",
        })),
        stdout=output, stderr=error, client=client,
    )
    assert code != 0 and output.getvalue() == ""
    assert time.monotonic() - began < 0.5
    assert "Synthetic harmless text" not in error.getvalue()
    # Let the harmless late preparation finish before fixture shutdown.
    time.sleep(0.3)
    assert live.fake.requests == []


@pytest.mark.parametrize("reply", [
    b"not-json",
    b"[]",
    b'{"displayContent":"wrong-event"}',
    b'{"decision":"allow"}',
    b'{"decision":"block"}',
    b'{"decision":"block","reason":"x","suppressOriginalPrompt":false}',
    b'{"decision":"block","reason":"x","reason":"y","suppressOriginalPrompt":true}',
])
def test_malformed_server_reply_nonzero(live, reply):
    client = HookClient(live.url, exchange=lambda *args: reply)
    output, error = io.StringIO(), io.StringIO()
    code = main(
        ["UserPromptSubmit"],
        stdin=io.StringIO(json.dumps({"session_id": SESSION, "prompt": "Synthetic"})),
        stdout=output, stderr=error, client=client,
    )
    assert code != 0 and output.getvalue() == ""
    assert "Traceback" not in error.getvalue()


def test_real_non200_and_cookie_only_denied(live):
    with httpx.Client(trust_env=False) as client:
        denied = client.post(
            live.url + "/api/hooks/display",
            json={"session_id": SESSION, "text": "Synthetic"},
        )
        assert denied.status_code == 401
    bad_token = live.local / "themis-shield" / "gate-token"
    bad_token.write_text("DIFFERENT_SYNTHETIC_GATE_TOKEN_123456789", encoding="ascii")
    code, response, _ = cli(live, "UserPromptSubmit", prompt="Synthetic clean question.")
    assert code != 0 and response == {}


def test_transcript_field_is_discarded_at_client_boundary(live, monkeypatch):
    seen = []

    def exchange(url, raw, headers, seconds):
        seen.append(json.loads(raw))
        return canonical({})

    client = HookClient(live.url, exchange=exchange)
    output = io.StringIO()
    assert main(
        ["UserPromptSubmit"],
        stdin=io.StringIO(json.dumps({
            "session_id": SESSION, "prompt": "Synthetic clean question.",
            "transcript_path": {"credential": "DO-NOT-OPEN"},
        })),
        stdout=output, stderr=io.StringIO(), client=client,
    ) == 0
    assert seen == [{"session_id": SESSION, "prompt": "Synthetic clean question."}]
    assert output.getvalue() == ""


def test_budgets_and_managed_matchers():
    assert budget("UserPromptSubmit") == 25
    assert budget("MessageDisplay") == 8
    settings = json.loads(
        (ROOT / "examples" / "claude-code-managed-settings.example.json").read_text(encoding="utf-8"),
    )
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "*"
    assert settings["hooks"]["PostToolUse"][0]["matcher"] == "*"
    for event, groups in settings["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                if event not in {"SessionStart", "Stop"}:
                    assert hook["onFailure"] == "block"
                else:
                    assert "onFailure" not in hook
    assert settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 30
    assert settings["hooks"]["MessageDisplay"][0]["hooks"][0]["timeout"] == 10
