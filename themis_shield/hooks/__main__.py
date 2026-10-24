"""Bounded, value-free Claude Code command-hook boundary.

Allow means no stdout. Every failure returns non-zero, including lifecycle and
display failures; Claude's documented event semantics determine their effect.
"""

from __future__ import annotations

import sys
from typing import TextIO

from themis_shield.cloud.openai_compat import canonical, strict_json
from . import claude_code_hooks
from .client import HookClient


MAXIMUM_INPUT_CHARACTERS = 1024 * 1024
EVENT_FUNCTIONS = {
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "MessageDisplay": "message_display",
    "SessionStart": "session_start",
    "Stop": "stop",
}


USAGE = (
    "usage: themis hooks <event>   (reads the Claude Code hook payload on stdin)\n"
    "events: " + ", ".join(EVENT_FUNCTIONS) + "\n"
    "Needs a running `themis serve` and a provisioned gate token; see "
    "examples/claude-code-managed-settings.example.json.\n"
)


def main(
    argv: list[str] | None = None, *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    base_url: str | None = None,
    client: HookClient | None = None,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    incoming = sys.stdin if stdin is None else stdin
    outgoing = sys.stdout if stdout is None else stdout
    diagnostic = sys.stderr if stderr is None else stderr
    if not arguments or arguments[0] in ("-h", "--help"):
        # A person at a terminal, not Claude Code (which always names the event).
        stream = outgoing if arguments else diagnostic
        stream.write(USAGE)
        stream.flush()
        return 0 if arguments else 2
    if len(arguments) != 1 or arguments[0] not in EVENT_FUNCTIONS:
        # Fail closed, but say which event names exist (never echo the argument).
        diagnostic.write("themis: unknown hook event\n" + USAGE)
        diagnostic.flush()
        return 1
    try:
        if len(arguments) != 1 or arguments[0] not in EVENT_FUNCTIONS:
            raise ValueError("UNKNOWN_EVENT")
        raw = incoming.read(MAXIMUM_INPUT_CHARACTERS + 1)
        if len(raw) > MAXIMUM_INPUT_CHARACTERS:
            raise ValueError("HOOK_INPUT_BOUND")
        payload = strict_json(raw)
        if type(payload) is not dict:
            raise ValueError("HOOK_OBJECT_REQUIRED")
        event = arguments[0]
        if payload.get("hook_event_name", event) != event:
            raise ValueError("EVENT_MISMATCH")
        transport = client or (
            HookClient(base_url) if base_url is not None else HookClient()
        )
        handler = getattr(claude_code_hooks, EVENT_FUNCTIONS[event])
        response = handler(payload, client=transport)
        if type(response) is not dict:
            raise ValueError("INVALID_HANDLER_REPLY")
        # Validate serialization before writing any part of a response.
        encoded = canonical(response).decode("utf-8")
        if response:
            outgoing.write(encoded + "\n")
            outgoing.flush()
        return 0
    except Exception:
        try:
            diagnostic.write("themis: hook failed; no payload diagnostics emitted\n")
            diagnostic.flush()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
