"""Bounded hook protocol and deliberately narrow built-in-tool profiles.


These profiles require actual-client regression before activation. Accepting a
Python object here is not proof that a particular Claude Code build accepts its
replacement schema.
"""

from __future__ import annotations

import re

from themis_shield.cloud.openai_compat import canonical


MAX_BYTES = 2 * 1024 * 1024
SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}")
RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}
LOCAL_MESSAGE = (
    "This stays local: continue in Kilo Code on the local route. "
    "No cloud override is available."
)
EVENT_PATHS = {
    "UserPromptSubmit": "prompt",
    "PreToolUse": "tool-input",
    "PostToolUse": "tool-output",
    "MessageDisplay": "display",
    "SessionStart": "session-start",
    "Stop": "stop",
}
# Candidate allow-list and executable output capability are separate.
DEFAULT_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep"})
EXECUTABLE_TOOLS = frozenset({"Read", "Glob", "Grep"})
OUTPUT_TOOLS = EXECUTABLE_TOOLS | {"Bash"}


def session_id(value):
    if (
        not isinstance(value, str)
        or not SESSION.fullmatch(value)
        or value.casefold() in RESERVED
    ):
        raise ValueError("INVALID_SESSION")
    return value


def bounded(value):
    if len(canonical(value)) > MAX_BYTES:
        raise ValueError("HOOK_BOUND")


def text(value):
    if not isinstance(value, str):
        raise ValueError("TEXT_REQUIRED")
    value.encode("utf-8")
    bounded(value)
    return value


def _integer(value, *, minimum=0):
    return type(value) is int and value >= minimum


def tool_output(tool, value):
    """Validate the entire supported output envelope, not just its text field."""
    bounded(value)
    if type(value) is not dict:
        raise ValueError("UNSUPPORTED_TOOL_OUTPUT")
    if tool == "Read":
        if set(value) != {"type", "file"} or value["type"] != "text":
            raise ValueError("UNSUPPORTED_READ_OUTPUT")
        file = value["file"]
        if type(file) is not dict or set(file) != {
            "filePath", "content", "numLines", "startLine", "totalLines",
        }:
            raise ValueError("UNSUPPORTED_READ_OUTPUT")
        text(file["filePath"])
        text(file["content"])
        if not all(
            _integer(file[key]) for key in ("numLines", "startLine", "totalLines")
        ):
            raise ValueError("UNSUPPORTED_READ_OUTPUT")
    elif tool == "Glob":
        if (
            set(value) != {"filenames", "numFiles", "durationMs", "truncated"}
            or type(value["filenames"]) is not list
            or not _integer(value["numFiles"])
            or type(value["durationMs"]) not in (int, float)
            or value["durationMs"] < 0
            or type(value["truncated"]) is not bool
            or value["numFiles"] != len(value["filenames"])
        ):
            raise ValueError("UNSUPPORTED_GLOB_OUTPUT")
        for name in value["filenames"]:
            text(name)
    elif tool == "Grep":
        allowed = {
            "mode", "filenames", "numFiles", "content", "numMatches", "numLines",
        }
        if (
            set(value) - allowed
            or not {"mode", "filenames", "numFiles"} <= set(value)
            or value["mode"] not in {"content", "files_with_matches", "count"}
            or type(value["filenames"]) is not list
            or not _integer(value["numFiles"])
            or value["numFiles"] != len(value["filenames"])
        ):
            raise ValueError("UNSUPPORTED_GREP_OUTPUT")
        for name in value["filenames"]:
            text(name)
        if "content" in value:
            text(value["content"])
        for key in ("numMatches", "numLines"):
            if key in value and not _integer(value[key]):
                raise ValueError("UNSUPPORTED_GREP_OUTPUT")
        if value["mode"] == "content" and "content" not in value:
            raise ValueError("UNSUPPORTED_GREP_OUTPUT")
    elif tool == "Bash":
        # Defensive post adapter only. Bash is never permitted at PreToolUse.
        if (
            set(value) != {"stdout", "stderr", "interrupted", "isImage"}
            or value["stderr"] != ""
            or value["interrupted"] is not False
            or value["isImage"] is not False
        ):
            raise ValueError("UNSUPPORTED_BASH_OUTPUT")
        text(value["stdout"])
    else:
        raise ValueError("UNSUPPORTED_TOOL_OUTPUT")
    return value


def tool_input(tool, value):
    bounded(value)
    if type(value) is not dict:
        raise ValueError("TOOL_INPUT_REQUIRED")
    if tool == "Read":
        if (
            set(value) - {"file_path", "offset", "limit"}
            or "file_path" not in value
        ):
            raise ValueError("UNSUPPORTED_READ_INPUT")
        text(value["file_path"])
        for key in ("offset", "limit"):
            if key in value and not _integer(value[key], minimum=1):
                raise ValueError("UNSUPPORTED_READ_INPUT")
    elif tool == "Glob":
        if set(value) - {"pattern", "path"} or "pattern" not in value:
            raise ValueError("UNSUPPORTED_GLOB_INPUT")
        text(value["pattern"])
        if "path" in value:
            text(value["path"])
    elif tool == "Grep":
        allowed = {
            "pattern", "path", "glob", "output_mode",
            "-i", "-n", "-A", "-B", "-C", "head_limit", "offset", "multiline",
        }
        if set(value) - allowed or "pattern" not in value:
            raise ValueError("UNSUPPORTED_GREP_INPUT")
        for key in ("pattern", "path", "glob"):
            if key in value:
                text(value[key])
        if value.get("output_mode", "files_with_matches") not in {
            "content", "files_with_matches", "count",
        }:
            raise ValueError("UNSUPPORTED_GREP_INPUT")
        for key in ("-i", "-n", "multiline"):
            if key in value and type(value[key]) is not bool:
                raise ValueError("UNSUPPORTED_GREP_INPUT")
        for key in ("-A", "-B", "-C", "head_limit", "offset"):
            if key in value and not _integer(value[key]):
                raise ValueError("UNSUPPORTED_GREP_INPUT")
    else:
        raise ValueError("UNSUPPORTED_TOOL_INPUT")
    return value


def prompt_block(reason):
    return {
        "decision": "block", "reason": reason, "suppressOriginalPrompt": True,
    }


def tool_deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def response_for(event, value, *, tool=None):
    """Reject missing fields, unknown fields and wrong-event success replies."""
    bounded(value)
    if type(value) is not dict:
        raise ValueError("MALFORMED_HOOK_REPLY")
    if event == "UserPromptSubmit":
        if value == {}:
            return value
        if (
            set(value) != {"decision", "reason", "suppressOriginalPrompt"}
            or value["decision"] != "block"
            or value["suppressOriginalPrompt"] is not True
        ):
            raise ValueError("MALFORMED_PROMPT_REPLY")
        text(value["reason"])
    elif event == "PreToolUse":
        if set(value) != {"hookSpecificOutput"}:
            raise ValueError("MALFORMED_PRE_REPLY")
        output = value["hookSpecificOutput"]
        if type(output) is not dict or output.get("hookEventName") != event:
            raise ValueError("MALFORMED_PRE_REPLY")
        if output.get("permissionDecision") == "deny":
            if set(output) != {
                "hookEventName", "permissionDecision", "permissionDecisionReason",
            }:
                raise ValueError("MALFORMED_PRE_REPLY")
            text(output["permissionDecisionReason"])
        elif output.get("permissionDecision") == "allow":
            if set(output) != {
                "hookEventName", "permissionDecision", "updatedInput",
            }:
                raise ValueError("MALFORMED_PRE_REPLY")
            tool_input(tool, output["updatedInput"])
        else:
            raise ValueError("MALFORMED_PRE_REPLY")
    elif event == "PostToolUse":
        if set(value) == {"decision", "reason"}:
            if value["decision"] != "block":
                raise ValueError("MALFORMED_POST_REPLY")
            text(value["reason"])
        else:
            if set(value) != {"hookSpecificOutput"}:
                raise ValueError("MALFORMED_POST_REPLY")
            output = value["hookSpecificOutput"]
            if (
                type(output) is not dict
                or set(output) != {"hookEventName", "updatedToolOutput"}
                or output["hookEventName"] != event
            ):
                raise ValueError("MALFORMED_POST_REPLY")
            tool_output(tool, output["updatedToolOutput"])
    elif event == "MessageDisplay":
        if set(value) != {"displayContent"}:
            raise ValueError("MALFORMED_DISPLAY_REPLY")
        text(value["displayContent"])
    elif event in {"SessionStart", "Stop"}:
        if value != {}:
            raise ValueError("MALFORMED_LIFECYCLE_REPLY")
    else:
        raise ValueError("UNKNOWN_EVENT")
    return value
