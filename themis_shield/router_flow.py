"""Orchestration using authoritative register commands and immutable packages."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
from threading import Lock
from .timezone import business_timezone

from .catalogues import tripwire
from .catalogues.gate import GateDecision, sweep_findings_text
from .chat_boundary import ChatBoundary
from .cloud.openai_compat import CloudFailure, Outbound, canonical, strict_json, verify_json
from .detectors.markings import MarkingClass, detect_markings, marking_policy, summarise_markings
from .placeholders import SENSITIVE_LABELS
from .register.client import JobStatus as S, SensitivePath
from .register.models import JobIntake, canonical_json
from .routing import Route, route_request
from .summary import outbound_user_text, problem_summary
from .types import Decision, Label


def refresh(router, job_id):
    from .app_services import job_view
    row = router._register_call("get_details", job_id)
    job = router._register_call("get_job", job_id)
    with router._state_lock:
        router._jobs[job_id] = job_view(row)
    return job


def register_call(router, method, *args, **kwargs):
    from .router import GateFailure
    try:
        return getattr(router.register, method)(*args, **kwargs)
    except Exception:
        raise GateFailure("REGISTER_UNAVAILABLE") from None


def transition(router, job, status):
    commands = {
        S.AUTO_ALLOWED_LOCAL: "auto_allow_local", S.COMPLETED: "complete",
        S.REFUSED: "gate_refuse", S.HELD_OFFLINE: "hold_offline",
    }
    if status not in commands:
        raise ValueError("unsupported gate transition")
    router._register_call(commands[status], job.job_id, now=router.clock())
    return refresh(router, job.job_id)


def log(
    router, request, conversation_id, fingerprint, *, local, marking,
    confirmation_required, document_count,
):
    from .router import GateFailure, JOB_ID
    key = conversation_id, fingerprint
    # Cleanup and intake/activity publication share this lock.
    with router._state_lock:
        existing = router._retry.get(key)
        if existing is None and len(router._jobs) >= 1000:
            raise GateFailure("ROUTER_RECORD_CAPACITY")
        if existing:
            router._active_jobs.add(existing)
            router._job_activity[existing] = router.clock()
            return refresh(router, existing)
        boundary = ChatBoundary(request, router.allowed_tools, router.cloud_model)
        candidate = JobIntake(
            "", router.clock(), S.LOGGED,
            Route.LOCAL_ONLY if local else Route.CLOUD,
            marking_class=marking,
            destination_id=None if local else router.cloud.settings.destination_id,
            model=(
                str(request.get("model", "themis/auto")).replace("/", ":")
                if local else router.cloud.settings.model
            ),
            deployment_type="local-demo" if local else router.cloud.settings.deployment_type,
            source_document_count=document_count,
            source_word_count=sum(len(surface.text.split()) for surface in boundary.surfaces),
            sensitive_path=(
                SensitivePath.LOCAL if local and marking_policy().needs_confirm(marking) else None
            ),
            requester_identity=router.identity, client="KILO_CODE", integration_mode="handover",
            synthetic=True, use_case_record_ref="SYNTHETIC-ENDORSEMENT",
            local_authorisation_ref=(
                "SYNTHETIC-LOCAL-AUTHORITY" if router.local_authorised else None
            ),
            problem_summary_deid=(
                problem_summary("", marking=marking) if local else problem_summary("")
            ),
            confirmation_required=confirmation_required,
        )
        job = router._register_call("log_job", candidate, now=router.clock())
        if not JOB_ID.fullmatch(job.job_id) or job.status != S.LOGGED:
            raise GateFailure("REGISTER_INTAKE_CONTRACT")
        router._retry[key] = job.job_id
        router._active_jobs.add(job.job_id)
        router._job_activity[job.job_id] = router.clock()
    if not local:
        router._register_call("request_stage1", job.job_id, now=router.clock())
        if router.pilot_fixture_enabled and router.pilot_approve:
            router.pilot_approve(job)
    return refresh(router, job.job_id)


def fail_job(router, job, code):
    if job is None:
        return
    if code in {"DETECTOR_CANARY_FAILED", "DETECTOR_CANARY_PENDING"}:
        # The audit trail says why: the detector canary, not the document, stopped this job.
        try:
            router._register_call("record_canary_block", job.job_id, code, now=router.clock())
        except Exception:
            pass
    try:
        transition(router, job, S.HELD_OFFLINE if code.startswith("REGISTER_") else S.REFUSED)
    except Exception:
        pass


def local_due(value):
    if not value:
        return "not available"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return "not available"
        return parsed.astimezone(business_timezone()).strftime(
            "%d/%m/%Y, %H:%M local time"
        )
    except (ValueError, TypeError):
        return "not available"


def held(router, job, reason, timings):
    row = router._register_call("get_details", job.job_id)
    return router._response(
        job, "held", reason,
        f"Held for operator approval {job.job_id}, due {local_due(row.get('due_utc'))}. "
        "Re-send when both stages are approved.", timings=timings,
    )


def handle(router, request, headers=None, *, source_document_count=1):
    from .router import Conversation, GateFailure, RouterResponse, completion
    headers = {key.lower(): value for key, value in (headers or {}).items()}
    job = stream = None
    cleanup = lambda: None
    timings = {}
    try:
        if (
            type(request) is not dict or type(source_document_count) is not int
            or not 1 <= source_document_count <= 100
            or len(canonical(request)) > 2 * 1024 * 1024
        ):
            raise GateFailure("INVALID_REQUEST", 400)
        router.purge_idle()
        identifier = router._conversation_id(request, headers)
        boundary = ChatBoundary(request, router.allowed_tools, router.cloud_model)
        with router._state_lock:
            if identifier not in router._conversations:
                if len(router._conversations) >= 256:
                    raise GateFailure("CONVERSATION_CAPACITY")
                router._conversations[identifier] = Conversation(last_used=router.clock())
            state = router._conversations[identifier]
        if not state.lock.acquire(timeout=30):
            raise GateFailure("CONVERSATION_BUSY")
        released = False
        release_lock = Lock()

        def release():
            nonlocal released
            with release_lock:
                if not released:
                    released = True
                    state.last_used = router.clock()
                    state.lock.release()

        cleanup = release
        marking = summarise_markings(tuple(
            detect_markings(surface.text) for surface in boundary.surfaces
        ))
        marking = replace(marking, classification=max(marking.classification, state.marking_class))
        routing = route_request(marking, router.use_case)
        state.marking_class = routing.audit_fields.marking_class
        cloud_confirm = headers.get("x-themis-confirm-cloud", "").lower() == "yes"
        local = (
            routing.route == Route.LOCAL_ONLY
            or boundary.model.startswith("themis/local:")
            or routing.local_alternative_offered and not cloud_confirm
        )
        fingerprint = hashlib.sha256(canonical(request)).digest() + bytes([cloud_confirm])
        job = log(
            router, request, identifier, fingerprint, local=local,
            marking=state.marking_class, confirmation_required=routing.mandatory_confirm,
            document_count=source_document_count,
        )
        if job.status == S.COMPLETED:
            return router._response(
                job, "held", "JOB_ALREADY_COMPLETED",
                "This job is completed. Start new work as a new request.",
            )
        if job.status in {S.REFUSED, S.WITHDRAWN, S.EXPIRED}:
            return router._response(job, "blocked", "APPROVAL_NOT_ACTIVE", "This job is closed.")
        stopped = tripwire.pending_response(router, job)
        if stopped is not None:
            return stopped
        if local:
            if not router.local_authorised:
                return router._response(
                    job, "held", "LOCAL_AUTHORITY_REQUIRED",
                    "Held: authorised local processing is required.",
                )
            job = transition(router, job, S.AUTO_ALLOWED_LOCAL)
            role = (
                boundary.model.removeprefix("themis/local:")
                if boundary.model.startswith("themis/local:") else "answer"
            )
            if getattr(router, "local_target", "laptop") != "laptop":
                raise GateFailure("SPARK_NOT_CONFIGURED")
            with router.runtime.acquire(role) as target:
                output = target.chat(boundary.local_messages(), stream=False, max_tokens=1024)
            if (
                type(output) is not dict or len(output.get("choices", [])) != 1
                or output["choices"][0].get("finish_reason") != "stop"
                or not isinstance(output["choices"][0].get("message", {}).get("content"), str)
            ):
                raise GateFailure("LOCAL_OUTPUT_INCOMPLETE")
            job = transition(router, job, S.COMPLETED)
            return router._response(
                job, "local", routing.reason_code.value,
                "[LOCAL-ONLY — processed locally]\n" + output["choices"][0]["message"]["content"],
            )
        if (
            not router.use_case.approved_for_cloud
            or router.use_case.requires_in_region_inference
            or router.cloud.settings.destination_id not in router.use_case.allowed_destination_ids
        ):
            raise GateFailure("DESTINATION_NOT_AUTHORISED", 200)
        original_messages = deepcopy(boundary.body["messages"])
        messages, matched = router._canonical_messages(identifier, original_messages)
        working = deepcopy(request)
        working["messages"] = messages
        boundary = ChatBoundary(working, router.allowed_tools, router.cloud_model)
        raw, mapping, sent, originals, policy, timings, counts = router._protect(
            boundary, identifier, matched,
        )
        digest = hashlib.sha256(raw).hexdigest()
        row = router._register_call("get_details", job.job_id)
        if row["package"] == "ready" and row["manifest_hash"] != digest:
            raise GateFailure("PACKAGE_CHANGED", 200)
        if row["package"] == "building":
            metadata = JobIntake(**{
                name: getattr(job, name)
                for name in JobIntake.__dataclass_fields__ if hasattr(job, name)
            })
            metadata = replace(
                metadata, pii_counts_json=canonical_json(counts),
                sensitive_categories_present=any(
                    Label(label) in SENSITIVE_LABELS for label in counts
                ),
                manifest_hash=digest,
                problem_summary_deid=problem_summary(
                    outbound_user_text(strict_json(raw)), originals=originals,
                ),
                confirmation_required=routing.mandatory_confirm or policy.decision == Decision.ASK,
            )
            runtime_modes = {
                item["model_id"]: item["mode"]
                for item in router.runtime.status().get("timings", [])
                if item.get("mode") in {"full", "mmap"}
            }
            router._register_call(
                "set_package", metadata,
                timings={
                    "tiers_ms": {str(key): value for key, value in timings.items()},
                    "runtime_modes": runtime_modes,
                }, now=router.clock(),
            )
            with router._state_lock:
                router._payloads[job.job_id] = raw
        job = refresh(router, job.job_id)
        if isinstance(policy, GateDecision):
            # v0.3 decision record: model output and catalogue override kept apart, with the register version.
            router._register_call(
                "record_gate_decision", job.job_id, digest, policy.record(), now=router.clock(),
            )
        row = router._register_call("get_details", job.job_id)
        sweep = getattr(policy, "sweep", None)
        if sweep is not None and sweep.needs_ask and not row["confirmation_required"]:
            # The package was assessed before the sweep asked. Not yet approved: it now needs a person (ASK).
            # Already approved: it cannot be released as it stands (fail closed), under the sweep's own reason.
            if job.status not in {S.LOGGED, S.AWAITING_OPERATOR, S.ESCALATED_REVIEW}:
                raise GateFailure(sweep.reason, 200)
            router._register_call("require_confirmation", job.job_id, digest, now=router.clock())
            job = refresh(router, job.job_id)
            row = router._register_call("get_details", job.job_id)
        confirmed = cloud_confirm if routing.local_alternative_offered else headers.get("x-themis-confirm") == job.job_id
        if policy.decision == Decision.ASK:
            confirmed = confirmed and headers.get("x-themis-confirm") == job.job_id
        if row["confirmation_required"] and not confirmed:
            shown = sweep_findings_text(policy)
            return router._response(
                job, "held", (sweep.reason if sweep is not None and sweep.needs_ask else None)
                or "REQUESTER_CONFIRM_REQUIRED",
                f"Held for confirmation of the clean package {job.job_id}.{shown}", timings=timings,
            )
        if row["confirmation_required"] and router._register_call("get_approval", job.job_id) is None:
            router._register_call(
                "confirm_payload", job.job_id, requester_identity=router.identity,
                revision=row["approval_revision"], manifest_hash=digest, now=router.clock(),
            )
        if router.pilot_fixture_enabled and router.pilot_approve:
            router.pilot_approve(job)
        router._register_call("finalize_stages", job.job_id, now=router.clock())
        job = refresh(router, job.job_id)
        if router._register_call("get_approval", job.job_id) is None:
            return held(router, job, "OPERATOR_APPROVAL_REQUIRED", timings)
        verify_json(raw, originals)
        if getattr(router, "tripwire", None) is not None:
            stopped = tripwire.run(router, job, raw, digest, originals, state.marking_class, timings)
            if stopped is not None:
                return stopped
        outbound = Outbound(job.job_id, state.marking_class, raw, digest, True, originals)
        with router.register.release_guard(job.job_id, digest, now=router.clock()):
            stream = router.cloud.open(outbound)
        clean_messages = strict_json(raw)["messages"]
        # The placeholder legend (v0.4) is the gate's own message, prepended; history pairs the client's messages with
        # what was sent for each of them, so it is left out.
        prepended = len(clean_messages) - len(original_messages)
        if prepended not in (0, 1):
            raise GateFailure("OUTBOUND_MESSAGES_MISMATCH", 200)
        chunks = router._restore_chunks(
            stream, job, identifier, original_messages,
            clean_messages[prepended:], mapping, sent, boundary.schemas,
        )
        if request.get("stream") is True:
            owned_stream, owned_cleanup = stream, cleanup

            def stream_cleanup():
                try:
                    owned_stream.close()
                finally:
                    owned_cleanup()

            response = RouterResponse(
                job.job_id, "cloud-tokenised", "APPROVED_TOKENISED", 200,
                timings, chunks=chunks, _cleanup=stream_cleanup,
            )
            stream = None
            cleanup = lambda: None
            return response
        text, calls, finish, usage, report = [], [], "stop", None, {}
        for chunk in chunks:
            report, usage = chunk.get("themis", report), chunk.get("usage", usage)
            for choice in chunk["choices"]:
                text.append(choice["delta"].get("content", ""))
                calls.extend(choice["delta"].get("tool_calls", []))
                finish = choice["finish_reason"] or finish
        body = completion(
            job.job_id, request.get("model", "themis/auto"), "".join(text) or None,
            tool_calls=calls, finish=finish, usage=usage,
        )
        body["themis"] = report
        return RouterResponse(job.job_id, "cloud-tokenised", "APPROVED_TOKENISED", 200, timings, body)
    except (GateFailure, CloudFailure) as failure:
        fail_job(router, job, failure.code)
        return router._response(
            job, "blocked", failure.code, "Processing held or refused: " + failure.code,
            status=getattr(failure, "status", 503), timings=timings,
        )
    except ValueError:
        fail_job(router, job, "UNSUPPORTED_OR_UNSAFE_CONTENT")
        return router._response(
            job, "blocked", "UNSUPPORTED_OR_UNSAFE_CONTENT",
            "Request blocked: unsupported or unsafe content.", status=400, timings=timings,
        )
    except Exception:
        fail_job(router, job, "REQUIRED_STAGE_FAILED")
        return router._response(
            job, "blocked", "REQUIRED_STAGE_FAILED",
            "Request blocked: required stage failed.", status=503, timings=timings,
        )
    finally:
        if job is not None:
            with router._state_lock:
                router._active_jobs.discard(job.job_id)
                router._job_activity[job.job_id] = router.clock()
        try:
            if stream is not None:
                stream.close()
        finally:
            cleanup()
