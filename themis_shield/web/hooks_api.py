"""Authenticated managed-hooks mode APIs with shared detection admission."""

from __future__ import annotations

import hmac
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from themis_shield.cloud.openai_compat import strict_json
from themis_shield.hooks.protocol import MAX_BYTES, bounded, response_for, session_id, text
from themis_shield.hooks.service import HookPolicy
from themis_shield.hooks.summary_service import SummarisingHookService
from themis_shield.router import GateFailure


def mount_hooks(app, router, *, token, maintenance):
    policy = getattr(router, "hooks_policy", HookPolicy())
    if not isinstance(policy, HookPolicy):
        raise ValueError("TRUSTED_HOOK_POLICY_REQUIRED")
    service = SummarisingHookService(router, policy)
    app.state.hooks = service
    execution = Lock()

    def error(status=503, code="HOOK_OPERATION_FAILED"):
        return JSONResponse(
            {"error": {"code": code}}, status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    async def invoke(request, event, fields, handler):
        if (
            request.client is None or request.client.host != "127.0.0.1"
            or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token)
        ):
            return error(401)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json" or request.query_params:
            return error(400)
        try:
            raw = bytearray()
            async for part in request.stream():
                raw.extend(part)
                if len(raw) > MAX_BYTES:
                    return error(400)
            value = strict_json(bytes(raw))
            if type(value) is not dict or set(value) != fields:
                return error(400)
            session_id(value["session_id"])
            bounded(value)
            for key in ("prompt", "text", "tool_name"):
                if key in value:
                    text(value[key])
        except Exception:
            return error(400)
        # Never hold maintenance while calling a detector: its shared guard
        # owns detection exclusion. Keep the shorter hook execution deadline.
        if maintenance.locked() or not execution.acquire(False):
            return error()
        try:
            result = await run_in_threadpool(handler, value)
            if event is not None:
                response_for(event, result, tool=value.get("tool_name"))
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except GateFailure as failure:
            return error(failure.status, failure.code)
        except Exception:
            return error()
        finally:
            execution.release()

    @app.post("/api/hooks/prompt")
    async def prompt(request: Request):
        return await invoke(request, "UserPromptSubmit", {"session_id", "prompt"},
                            lambda value: service.prompt(value["session_id"], value["prompt"]))

    @app.post("/api/hooks/tool-input")
    async def tool_input(request: Request):
        return await invoke(
            request, "PreToolUse", {"session_id", "tool_name", "tool_input"},
            lambda value: service.pre_tool(value["session_id"], value["tool_name"], value["tool_input"]),
        )

    @app.post("/api/hooks/tool-output")
    async def tool_output(request: Request):
        return await invoke(
            request, "PostToolUse", {"session_id", "tool_name", "tool_response"},
            lambda value: service.post_tool(value["session_id"], value["tool_name"], value["tool_response"]),
        )

    @app.post("/api/hooks/display")
    async def display(request: Request):
        return await invoke(request, "MessageDisplay", {"session_id", "text"},
                            lambda value: service.display(value["session_id"], value["text"]))

    @app.post("/api/hooks/session-start")
    async def start(request: Request):
        return await invoke(request, "SessionStart", {"session_id"},
                            lambda value: service.start(value["session_id"]))

    @app.post("/api/hooks/stop")
    async def stop(request: Request):
        return await invoke(request, "Stop", {"session_id"},
                            lambda value: service.stop(value["session_id"]))

    @app.post("/api/hooks/confirm")
    async def confirm(request: Request):
        return await invoke(
            request, None, {"session_id", "job_id", "manifest_hash"},
            lambda value: service.confirm(value["session_id"], value["job_id"], value["manifest_hash"]),
        )
