"""Thin event adapters. Only prompt marking routing remains client-local."""

from __future__ import annotations

from themis_shield.config import UseCasePolicy
from themis_shield.detectors.markings import detect_markings
from themis_shield.routing import Route, route_request

from .client import HookClient
from .protocol import LOCAL_MESSAGE, bounded, prompt_block, session_id, text


_PROMPT_POLICY = UseCasePolicy("managed-hooks-synthetic", (), synthetic_only=True)


def _event(payload):
    if type(payload) is not dict:
        raise ValueError("HOOK_OBJECT_REQUIRED")
    bounded(payload)
    return payload


def _session(payload):
    return session_id(payload.get("session_id"))


def user_prompt_submit(payload, *, client=None):
    event = _event(payload)
    prompt = text(event.get("prompt"))
    # This preflight intentionally runs before token access and HTTP.
    routing = route_request(detect_markings(prompt), _PROMPT_POLICY)
    if routing.route == Route.LOCAL_ONLY:
        return prompt_block(LOCAL_MESSAGE)
    return (client or HookClient()).call("UserPromptSubmit", {
        "session_id": _session(event), "prompt": prompt,
    })


def pre_tool_use(payload, *, client=None):
    event = _event(payload)
    tool = text(event.get("tool_name"))
    if type(event.get("tool_input")) is not dict:
        raise ValueError("TOOL_INPUT_REQUIRED")
    return (client or HookClient()).call("PreToolUse", {
        "session_id": _session(event),
        "tool_name": tool,
        "tool_input": event["tool_input"],
    })


def post_tool_use(payload, *, client=None):
    event = _event(payload)
    tool = text(event.get("tool_name"))
    if "tool_response" not in event:
        raise ValueError("TOOL_RESPONSE_REQUIRED")
    return (client or HookClient()).call("PostToolUse", {
        "session_id": _session(event),
        "tool_name": tool,
        "tool_response": event["tool_response"],
    })


def message_display(payload, *, client=None):
    event = _event(payload)
    return (client or HookClient()).call("MessageDisplay", {
        "session_id": _session(event), "text": text(event.get("text")),
    })


def session_start(payload, *, client=None):
    event = _event(payload)
    return (client or HookClient()).call("SessionStart", {
        "session_id": _session(event),
    })


def stop(payload, *, client=None):
    event = _event(payload)
    return (client or HookClient()).call("Stop", {
        "session_id": _session(event),
    })
