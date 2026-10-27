"""Contract-v1 loopback app with separate detection admission and deadlines."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hmac
import json
import os
from pathlib import Path
from themis_shield.paths import app_data_dir
import re
import secrets
from threading import Lock, Thread
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from themis_shield import app_services as services
from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.router import GateFailure
from themis_shield.runtime.calibration import CalibrationStore, calibrate
from themis_shield.runtime.resources import read_identity

from .detection import DetectionControl
from .reasons import catalogue


def default_token_path():
    return app_data_dir() / "gate-token"


def read_gate_token(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("LOCAL_TOKEN_UNAVAILABLE")
    token = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("LOCAL_TOKEN_INVALID")
    return token


def calibration_run(router, model_id, mode):
    settings = router.runtime_settings
    return calibrate(
        router.runtime, model_id, CalibrationStore(settings.storage_dir / "calibration.json"),
        read_identity(settings.gpu_index), mode=mode,
    )


def create_app(
    router, *, token, port=8742, launcher_code=None, clock=time.monotonic,
    detection_timeout_seconds=None, detection_lock_timeout_seconds=120,
):
    timeout = (
        float(os.environ.get("THEMIS_DETECTION_TIMEOUT_SECONDS", "600"))
        if detection_timeout_seconds is None else detection_timeout_seconds
    )
    session_lock, maintenance = Lock(), Lock()
    control = DetectionControl(
        maintenance, lock_timeout=detection_lock_timeout_seconds, detection_timeout=timeout,
    )
    raw_detector, raw_batch = router.detector, router.batch_detector
    router.detector, router.batch_detector = control.wrap(raw_detector), control.wrap(raw_batch)
    previews, handovers = services.PreviewStore(clock), services.HandoverStore()
    sessions, calibration_jobs = {}, {}
    code_digest = __import__("hashlib").sha256(launcher_code.encode()).digest() if launcher_code else None
    code_expiry = clock() + 120
    settings = {
        "local_target": "laptop", "default_local_model": "gemma4-e4b",
        "default_cloud_model": router.cloud_model_id,
    }

    async def cleanup_once():
        # Failure is not silently accepted as successful maintenance.
        try:
            await run_in_threadpool(router.cleanup_abandoned)
            app.state.cleanup_status = "ok"
        except Exception:
            app.state.cleanup_status = "unavailable"

    @asynccontextmanager
    async def lifespan(app):
        await cleanup_once()

        async def hourly():
            while True:
                await asyncio.sleep(3600)
                await cleanup_once()

        task = asyncio.create_task(hourly())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Restore injected detector callables after this app's lifetime.
            router.detector, router.batch_detector = raw_detector, raw_batch

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.detection_control = control
    app.state.cleanup_status = "not-yet-run"

    def error(code, status=503):
        return JSONResponse(
            {"error": {"message": code, "type": "themis_error", "code": code}},
            status_code=status, headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.client is None or request.client.host != "127.0.0.1":
            return error("LOOPBACK_REQUIRED", 403)
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return error("LOOPBACK_HOST_REQUIRED", 403)
        if request.headers.get("origin") not in {None, f"http://127.0.0.1:{port}"}:
            return error("ORIGIN_DENIED", 403)
        public = request.method == "GET" and not request.url.path.startswith(("/api", "/v1", "/health"))
        if not public:
            bearer = hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token)
            with session_lock:
                for key, expiry in tuple(sessions.items()):
                    if clock() >= expiry:
                        del sessions[key]
                authenticated = bearer or request.cookies.get("themis_session", "") in sessions
            if not authenticated:
                return error("LOCAL_BEARER_REQUIRED", 401)
        if request.headers.get("content-encoding"):
            return error("CONTENT_ENCODING_UNSUPPORTED", 400)
        try:
            response = await call_next(request)
        except Exception:
            response = error("FRONT_DOOR_FAILED")
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        })
        return response

    async def body(request, fields):
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("body bound")
        value = strict_json(bytes(raw))
        if type(value) is not dict or set(value) != fields:
            raise ValueError("unsupported body")
        return value

    async def operation(function, *args, **kwargs):
        # No maintenance lock is held through detection or service work.
        if maintenance.locked():
            return error("CALIBRATION_MAINTENANCE")
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except GateFailure as failure:
            return error(failure.code, failure.status)
        except KeyError:
            return error("NOT_FOUND_OR_EXPIRED", 404)
        except PermissionError:
            return error("OPERATION_REFUSED", 403)
        except ValueError:
            return error("INVALID_OR_UNSAFE_REQUEST", 400)
        except Exception:
            return error("REQUIRED_SERVICE_UNAVAILABLE")

    def settings_view():
        return {
            **settings, "local_target_status": (
                "not configured" if settings["local_target"] == "dgx-spark" else "configured"
            ),
            "pilot_fixture_enabled": bool(router.pilot_fixture_enabled),
            "operator_console_enabled": bool(getattr(router, "operator_console_enabled", False)),
        }

    @app.get("/")
    async def root(request: Request):
        nonlocal code_digest
        supplied = request.query_params.get("t")
        if supplied is not None:
            digest = __import__("hashlib").sha256(supplied.encode()).digest()
            with session_lock:
                if code_digest is None or clock() >= code_expiry or not hmac.compare_digest(code_digest, digest):
                    return error("LAUNCHER_CODE_INVALID", 401)
                code_digest = None
                cookie = secrets.token_urlsafe(32)
                sessions[cookie] = clock() + 8 * 3600
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                "themis_session", cookie, httponly=True, samesite="strict",
                secure=False, max_age=8 * 3600, path="/",
            )
            return response
        index = Path(__file__).parent / "static" / "index.html"
        if not index.is_file():
            return Response("App static files not installed.", media_type="text/plain", status_code=503)
        from fastapi.responses import FileResponse
        return FileResponse(index)

    @app.get("/api/reasons")
    async def reasons():
        return {"reasons": catalogue()}

    @app.get("/api/models")
    async def models():
        return await operation(services.models, router)

    @app.get("/api/runtime")
    async def runtime():
        try:
            value = await run_in_threadpool(router.runtime.status)
            return {key: value.get(key) for key in (
                "loaded_models", "loaded_modes", "resources", "queue",
                "loading", "inference_active", "status", "resource_error",
            )}
        except Exception:
            return error("RUNTIME_UNAVAILABLE")

    @app.get("/api/capacity")
    async def capacity():
        return await operation(services.capacity, router)

    @app.get("/api/canary")
    async def canary_status():
        # v0.3 detector canary: value-free state (healthy, degraded, pending), per detector and the latest runs.
        monitor = getattr(router, "canary", None)
        if monitor is None:
            return {"enabled": False, "cloud_blocked": False}
        try:
            value = await run_in_threadpool(monitor.status)
            value["recent"] = list(monitor.history)[-10:]
            return value
        except Exception:
            return error("REQUIRED_SERVICE_UNAVAILABLE")

    @app.post("/api/canary/run")
    async def canary_run():
        # Run every detector's canary now (also the way to clear a degraded state once the cause is fixed). It takes
        # the detection lock, so it never interleaves with a document's detection.
        monitor = getattr(router, "canary", None)
        if monitor is None:
            return error("OPERATION_REFUSED", 403)
        run = control.wrap(lambda: monitor.run("manual"))
        result = await operation(run)
        if isinstance(result, Response):
            return result
        return {**monitor.status(), "result": result.record()}

    @app.post("/api/preview")
    async def preview(request: Request):
        try:
            value = await body(request, {"text", "attachments", "intent"})
        except Exception:
            return error("INVALID_PREVIEW", 400)
        return await operation(previews.create, router, value)

    async def stream_response(response, *, app_metadata):
        iterator, sentinel = response.sse(), object()

        def step():
            return next(iterator, sentinel)

        async def events():
            latest = {}
            try:
                while True:
                    item = await run_in_threadpool(step)
                    if item is sentinel:
                        break
                    if app_metadata and item == "data: [DONE]\n\n":
                        yield "data: " + canonical({
                            "id": "chatcmpl-" + response.job_id, "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "themis": {
                                "route": response.route, "job_id": response.job_id, "reason": response.reason or "",
                                "tiers_ms": {str(key): value for key, value in response.timings_ms.items()},
                                "restored": latest.get("restored_tokens", 0),
                                "unknown_tokens": latest.get("unknown_tokens", 0),
                                "dropped_tokens": latest.get("dropped_tokens", 0),
                                "answer_source": "cloud" if response.route == "cloud-tokenised" else "local",
                            },
                        }).decode() + "\n\n"
                    elif item.startswith("data: {"):
                        value = strict_json(item[6:].strip())
                        latest = value.get("themis", latest)
                    yield item
            finally:
                try:
                    await run_in_threadpool(iterator.close)
                finally:
                    await run_in_threadpool(response.close)
        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={**response.headers, "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat")
    async def app_chat(request: Request):
        try:
            value = await body(request, {
                "preview_id", "messages", "model", "confirm_cloud", "conversation_id",
            })
            if type(value["confirm_cloud"]) is not bool:
                raise ValueError
            preview = previews.get(value["preview_id"])
            messages = deepcopy(value["messages"])
            if (
                not isinstance(messages, list) or not messages
                or any(not isinstance(message, dict) or not isinstance(message.get("content"), str) for message in messages)
                or messages[-1].get("role") != "user"
                or "\n\n".join(f"[{message.get('role')}]\n{message['content']}" for message in messages) != preview.text
            ):
                raise ValueError("preview does not match the conversation")
            messages[-1]["content"] += "".join(
                f"\n\nSynthetic document {index + 1}:\n{text}"
                for index, text in enumerate(preview.attachments)
            )
            model = (
                "themis/local:" + settings["default_local_model"]
                if preview.intent == "local" else value["model"]
            )
            headers = {"X-Themis-Conversation": value["conversation_id"]}
            if value["confirm_cloud"]:
                headers["X-Themis-Confirm-Cloud"] = "yes"
                if preview.safe.get("_job"):
                    headers["X-Themis-Confirm"] = preview.safe["_job"]
            outgoing = {"model": model, "messages": messages, "stream": True}
        except KeyError:
            return error("PREVIEW_EXPIRED", 410)
        except Exception:
            return error("INVALID_CHAT_OR_PREVIEW_BINDING", 400)
        if maintenance.locked():
            return error("CALIBRATION_MAINTENANCE")
        try:
            response = await run_in_threadpool(
                router.handle, outgoing, headers, source_document_count=1 + len(preview.attachments),
            )
            preview.safe["_job"] = response.job_id
            if response.status != 200:
                response.close()
                return error(response.reason, response.status)
            return await stream_response(response, app_metadata=True)
        except Exception:
            return error("CHAT_FAILED")

    @app.post("/v1/chat/completions")
    async def kilo_chat(request: Request):
        try:
            raw = bytearray()
            async for part in request.stream():
                raw.extend(part)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError
            value = strict_json(bytes(raw))
        except Exception:
            return error("INVALID_JSON", 400)
        if maintenance.locked():
            return error("CALIBRATION_MAINTENANCE")
        try:
            response = await run_in_threadpool(router.handle, value, dict(request.headers))
            if value.get("stream") is True and response.status == 200:
                return await stream_response(response, app_metadata=False)
            response.close()
            return JSONResponse(response.body, status_code=response.status, headers=response.headers)
        except Exception:
            return error("CHAT_FAILED")

    @app.get("/v1/models")
    async def kilo_models():
        value = services.models(router)
        identifiers = ["themis/auto"] + [
            "themis/local:" + item["id"] for item in value["local"]
        ] + [item["id"] for item in value["cloud"]]
        return {"object": "list", "data": [
            {"id": identifier, "object": "model", "owned_by": "themis"} for identifier in identifiers
        ]}

    @app.get("/health")
    async def health():
        result = await run_in_threadpool(router.health)
        result["abandoned_cleanup"] = app.state.cleanup_status
        return JSONResponse(result, status_code=200 if result["gate"] == "ok" else 503)

    @app.get("/api/jobs")
    async def jobs(request: Request):
        return await operation(lambda: {"jobs": [
            services.job_view(row) for row in router.register.list_jobs(request.query_params.get("status"))
        ]})

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return await operation(lambda: services.job_view(router.register.get_details(job_id)))

    @app.get("/api/jobs/{job_id}/cloud-view")
    async def job_cloud_view(job_id: str):
        return await operation(services.cloud_view, router, job_id)

    def operator_enabled():
        return bool(getattr(router, "operator_console_enabled", False))

    @app.get("/api/operator/queue")
    async def operator_queue():
        if not operator_enabled():
            return error("OPERATOR_CONSOLE_DISABLED", 404)
        return await operation(lambda: {
            "label": "SYNTHETIC REHEARSAL",
            "jobs": [{
                **services.job_view(row), "summary": row["problem_summary_deid"],
                "size": {"documents": row["source_document_count"], "words": row["source_word_count"]},
                "major_flags": json.loads(row["major_reason_codes_json"]),
            } for row in router.register.list_jobs()
                if row["route"] == "CLOUD"
                and row["status"] in {"Logged", "Awaiting-Operator", "Escalated-Review"}
                and (row["stage1"] == "requested" or row["stage2"] == "pending")],
        })

    async def operator_action(job_id, request, action):
        if not operator_enabled():
            return error("OPERATOR_CONSOLE_DISABLED", 404)
        try:
            value = await body(request, {"stage", "reason"} if action == "approve" else {"reason"})
        except Exception:
            return error("INVALID_OPERATOR_ACTION", 400)

        def act():
            row, actor, now = router.register.get_details(job_id), router.operator_identity, router.clock()
            if action == "approve":
                router.register.approve_stage(
                    job_id, value["stage"], "operator", value["reason"],
                    approver_identity=actor, expected_revision=row["approval_revision"],
                    manifest_hash=row["manifest_hash"] if value["stage"] == 2 else None, now=now,
                )
            else:
                getattr(router.register, action)(
                    job_id, "operator", value["reason"], approver_identity=actor, now=now,
                )
            return services.job_view(router.register.get_details(job_id))
        return await operation(act)

    @app.post("/api/operator/{job_id}/approve")
    async def approve(job_id: str, request: Request):
        return await operator_action(job_id, request, "approve")

    @app.post("/api/operator/{job_id}/refuse")
    async def refuse(job_id: str, request: Request):
        return await operator_action(job_id, request, "refuse")

    @app.post("/api/operator/{job_id}/escalate")
    async def escalate(job_id: str, request: Request):
        return await operator_action(job_id, request, "escalate")

    @app.post("/api/handover")
    async def handover(request: Request):
        try:
            value = await body(request, {"job_id"})
        except Exception:
            return error("INVALID_HANDOVER", 400)
        return await operation(handovers.build, router, value["job_id"])

    @app.get("/api/handover/{job_id}/peer-view")
    async def peer_view(job_id: str):
        return await operation(handovers.peer_view, job_id)

    @app.post("/api/handover/{job_id}/peer-check")
    async def peer_check(job_id: str, request: Request):
        try:
            value = await body(request, {"checked", "note"})
        except Exception:
            return error("INVALID_PEER_CHECK", 400)
        return await operation(handovers.peer_check, job_id, value["checked"], value["note"])

    @app.get("/api/handover/{job_id}/download")
    async def download(job_id: str):
        value = await operation(handovers.download, job_id)
        if isinstance(value, Response):
            return value
        return Response(
            value, media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="clean-package.zip"'},
        )

    @app.get("/api/settings")
    async def get_settings():
        return settings_view()

    @app.post("/api/settings")
    async def post_settings(request: Request):
        try:
            value = await body(request, set(settings))
        except Exception:
            return error("INVALID_SETTINGS", 400)

        def change():
            if (
                value["local_target"] not in {"laptop", "dgx-spark"}
                or not router.runtime.catalogue.contains(value["default_local_model"])
                or value["default_cloud_model"] != router.cloud_model_id
            ):
                raise ValueError
            if "answer" not in router.runtime.catalogue.get(value["default_local_model"]).roles:
                raise ValueError("default local model must have answer role")
            if not isinstance(router.runtime.role_models, dict):
                raise ValueError("invalid runtime role mapping")
            router.runtime.role_models["answer"] = value["default_local_model"]
            router.local_target = value["local_target"]
            settings.update(value)
            return settings_view()
        return await operation(change)

    @app.post("/api/calibrate")
    async def start_calibration(request: Request):
        try:
            value = await body(request, {"model_id", "mode"})
            if value["mode"] not in {"full", "mmap"}:
                raise ValueError
            router.runtime.catalogue.get(value["model_id"])
        except Exception:
            return error("INVALID_CALIBRATION", 400)
        if not control.begin_maintenance():
            return error("CALIBRATION_MAINTENANCE")
        if len(calibration_jobs) >= 256:
            control.end_maintenance()
            return error("CALIBRATION_JOB_CAPACITY")
        identifier = "cal-" + secrets.token_hex(12)
        calibration_jobs[identifier] = {"job": identifier, "status": "queued", "result": None}

        def worker():
            try:
                calibration_jobs[identifier]["status"] = "running"
                result = calibration_run(router, value["model_id"], value["mode"])
                calibration_jobs[identifier].update(status="completed", result={
                    key: result.get(key) for key in (
                        "model_id", "mode", "measured_utc", "prompt_tok_s", "gen_tok_s",
                        "prompt_tokens", "gen_tokens", "load_seconds",
                    )
                })
            except Exception:
                calibration_jobs[identifier].update(
                    status="failed", result={"reason_code": "CALIBRATION_FAILED"},
                )
            finally:
                control.end_maintenance()
        try:
            Thread(target=worker, name="themis-calibration", daemon=True).start()
        except Exception:
            calibration_jobs[identifier].update(
                status="failed", result={"reason_code": "CALIBRATION_FAILED"},
            )
            control.end_maintenance()
            return error("CALIBRATION_FAILED")
        return {"job": identifier}

    @app.get("/api/calibrate/{identifier}")
    async def calibration_status(identifier: str):
        return calibration_jobs.get(identifier) or error("CALIBRATION_NOT_FOUND", 404)

    from .hooks_api import mount_hooks
    mount_hooks(app, router, token=token, maintenance=maintenance)
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", check_dir=False), name="static")
    return app
