"""Register, markings, detection, substitution, approval and cloud orchestration.

"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import re
from threading import Lock, RLock
from typing import Callable, Iterator

from .catalogues.gate import decide_with_catalogue, final_sweep
from .chat_boundary import ChatBoundary, assign
from .cloud.openai_compat import (
    CloudFailure, CloudClient, canonical, strict_json, verify_json,
)
from .config import DeploymentProfile, UseCasePolicy
from .detectors import DetectionTargets, detect, detect_batch
from .detectors.contracts import Status
from .detectors.engine import FINAL_STAGES, LLM_STAGES, TIER1_STAGES
from .detectors.markings import MarkingClass
from .history import CanonicalHistory, ClientTurn
from .inference.json_output import validate_output
from .placeholders import SENSITIVE_LABELS, TokenAllocator, parse_strict_token
from .policy import decide
from .register.client import JobRegisterClient, JobRecord, JobStatus
from .restore import StreamingRestorer, ToolCallAccumulator, restore_text
from .substitute import SpanFinding, substitute
from .types import Decision, Label, Span


JOB_ID = re.compile(r"JOB-[0-9]{4}-[0-9]{4}")
EXPECTED_STAGES = TIER1_STAGES + FINAL_STAGES + LLM_STAGES
CATEGORY_LABEL = {
    "P": Label.PERSON, "C": Label.EMAIL, "A": Label.ADDRESS,
    "I": Label.STUDENT_ID, "D": Label.OTHER_DATE,
    "S": Label.HEALTH, "Q": Label.QUASI_ID,
}


class GateFailure(RuntimeError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def utc_now():
    return datetime.now(timezone.utc)


def completion(job_id, model, text, *, tool_calls=None, finish="stop", usage=None):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    result = {
        "id": "chatcmpl-" + job_id, "object": "chat.completion",
        "created": int(utc_now().timestamp()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage is not None:
        result["usage"] = usage
    return result


@dataclass
class RouterResponse:
    job_id: str
    route: str
    reason: str
    status: int = 200
    timings_ms: dict[int, float] = field(default_factory=dict)
    body: dict | None = field(default=None, repr=False)
    chunks: Iterator[dict] | None = field(default=None, repr=False)
    _cleanup: Callable[[], None] = field(default=lambda: None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def headers(self):
        return {
            "X-Themis-Route": self.route, "X-Themis-Job": self.job_id,
            "X-Themis-Reason": self.reason,
            **{f"X-Themis-Tier-{tier}-Ms": f"{duration:.3f}"
               for tier, duration in sorted(self.timings_ms.items())},
            "Cache-Control": "no-store",
        }

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                if self.chunks is not None:
                    self.chunks.close()
            finally:
                self._cleanup()

    def sse(self):
        try:
            if self.chunks is None:
                message = self.body["choices"][0]["message"]
                common = {
                    "id": "chatcmpl-" + self.job_id,
                    "object": "chat.completion.chunk",
                    "created": int(utc_now().timestamp()), "model": self.body["model"],
                }
                for delta, finish in (
                    (message, None),
                    ({}, self.body["choices"][0]["finish_reason"]),
                ):
                    yield "data: " + canonical({
                        **common, "choices": [{
                            "index": 0, "delta": delta, "finish_reason": finish,
                        }],
                    }).decode() + "\n\n"
            else:
                for chunk in self.chunks:
                    yield "data: " + canonical(chunk).decode() + "\n\n"
            yield "data: [DONE]\n\n"
        except GateFailure as failure:
            yield "data: " + canonical({"error": {
                "message": failure.code, "type": "themis_error", "code": failure.code,
            }}).decode() + "\n\n"
            yield ": themis " + canonical({
                "route": "blocked", "reason": failure.code,
            }).decode() + "\n\n"
        finally:
            self.close()


@dataclass
class Conversation:
    lock: Lock = field(default_factory=Lock, repr=False)
    last_used: datetime = field(default_factory=utc_now)
    marking_class: MarkingClass = MarkingClass.NONE


class Router:
    def __init__(
        self, *, register: JobRegisterClient, vault, runtime,
        cloud: CloudClient, profile: DeploymentProfile,
        use_case: UseCasePolicy, targets: DetectionTargets,
        requester_identity: str, allowed_tools=frozenset(),
        local_authorised=False, pilot_fixture_enabled=False,
        pilot_approve=None, holidays=frozenset(), clock=utc_now,
        detector=detect, batch_detector=None,
    ):
        if not requester_identity:
            raise ValueError("trusted-requester-identity-required")
        self.register, self.vault, self.runtime, self.cloud = register, vault, runtime, cloud
        self.profile, self.use_case, self.targets = profile, use_case, targets
        self.identity, self.allowed_tools = requester_identity, allowed_tools
        self.local_authorised = local_authorised
        self.pilot_fixture_enabled, self.pilot_approve = pilot_fixture_enabled, pilot_approve
        self.holidays, self.clock, self.detector = holidays, clock, detector
        if batch_detector is not None:
            self.batch_detector = batch_detector
        elif detector is detect:
            self.batch_detector = detect_batch
        else:
            # Preserve existing trusted detector injection. A custom production
            # detector should supply its matching batch implementation.
            def injected_batch(texts, profile, targets, *, thorough):
                effective = replace(targets, thorough=targets.thorough or thorough)
                return [detector(text, profile, effective) for text in texts]
            self.batch_detector = injected_batch
        self.history = CanonicalHistory()
        self._state_lock = RLock()
        self._conversations = {}
        self._retry, self._jobs, self._payloads = {}, {}, {}
        self._active_jobs = set()
        self._job_activity = {}
        self._detection_status = "not-yet-tested"
        # v0.3 detector canary (themis_shield.canary); None means no canary gating. Set from [canary] by integration.
        self.canary = None

    @property
    def cloud_model(self):
        """The configured cloud model name, if the cloud client exposes one."""
        return getattr(getattr(self.cloud, "settings", None), "model", None)

    @property
    def cloud_model_id(self):
        return "themis/cloud:" + (self.cloud_model or "unconfigured")

    def _register_call(self, method, *args, **kwargs):
        from .router_flow import register_call
        return register_call(self, method, *args, **kwargs)

    def _transition(self, job, status):
        from .router_flow import transition
        return transition(self, job, status)

    def _conversation_id(self, request, headers):
        supplied = headers.get("x-themis-conversation")
        if supplied is not None:
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", supplied):
                raise GateFailure("INVALID_CONVERSATION", 400)
            seed = supplied
        else:
            first = next((
                message.get("content") for message in request.get("messages", [])
                if isinstance(message, dict) and message.get("role") == "user"
            ), "")
            seed = canonical(first).decode()
        return hashlib.sha256((self.identity + "\0" + seed).encode()).hexdigest()

    def purge_idle(self):
        now = self.clock()
        with self._state_lock:
            for identifier, state in tuple(self._conversations.items()):
                if now - state.last_used < timedelta(hours=4) or not state.lock.acquire(False):
                    continue
                try:
                    self.vault.wipe(identifier)
                    self.history.wipe(identifier)
                    del self._conversations[identifier]
                    for key in tuple(self._retry):
                        if key[0] == identifier:
                            del self._retry[key]
                finally:
                    state.lock.release()

    def cleanup_abandoned(self):
        """Legal, transactional withdrawal; never a direct status UPDATE."""
        with self._state_lock:
            result = self._register_call(
                "withdraw_abandoned", now=self.clock(),
                active_ids=frozenset(self._active_jobs),
                activity=dict(self._job_activity),
            )
            for identifier in result:
                self._job_activity.pop(identifier, None)
            return result

    def _log(self, request, conversation_id, fingerprint):
        # Retained compatibility seam; normal intake is router_flow.log.
        from .detectors.markings import detect_markings, summarise_markings
        from .router_flow import log
        boundary = ChatBoundary(request, self.allowed_tools, self.cloud_model)
        marking = summarise_markings(tuple(
            detect_markings(surface.text) for surface in boundary.surfaces
        )).classification
        return log(
            self, request, conversation_id, fingerprint, local=True,
            marking=marking, confirmation_required=False, document_count=1,
        )

    def _response(self, job, route, reason, text, *, status=200, timings=None):
        return RouterResponse(
            job.job_id if job else "", route, reason, status, timings or {},
            completion(job.job_id if job else "unassigned", "themis/auto", text),
        )

    def _held(self, job, reason, timings):
        from .router_flow import held
        return held(self, job, reason, timings)

    def _scan(self, text):
        try:
            evidence = self.detector(
                text, self.profile, replace(self.targets, thorough=True, local_only=False),
            )
            if evidence.tiers_run != (1, 2, 3) or any(
                evidence.stage_status.get(stage) is None
                or evidence.stage_status[stage].status != Status.OK
                for stage in EXPECTED_STAGES
            ):
                raise ValueError("incomplete-required-detection")
            self._detection_status = "ok"
            return evidence
        except GateFailure:
            # Preserve admission/deadline codes rather than hiding them behind
            # DETECTION_UNAVAILABLE. All remain fail-closed.
            self._detection_status = "unavailable"
            raise
        except Exception:
            self._detection_status = "unavailable"
            raise GateFailure("DETECTION_UNAVAILABLE") from None

    def _canonical_messages(self, conversation_id, messages):
        turns = tuple(
            ClientTurn(f"t{index:04d}", message["role"], canonical(message).decode())
            for index, message in enumerate(messages)
        )
        resolution = self.history.reconcile(conversation_id, turns)
        matching = {turn.turn_id: turn for turn in resolution.matched_turns}
        working, matched_indices = [], set()
        for index, message in enumerate(messages):
            match = matching.get(f"t{index:04d}")
            if match is not None:
                working.append(strict_json(match.payload.body))
                matched_indices.add(index)
            else:
                working.append(deepcopy(message))
        return working, matched_indices

    def _protect(self, boundary, conversation_id, matched_indices):
        starts, position = [], 0
        for surface in boundary.surfaces:
            starts.append(position)
            position += len(surface.text) + 2
        scanned = "\n\n".join(surface.text for surface in boundary.surfaces)
        canary = self.canary
        if canary is not None:
            # A due tier-1 canary runs now; the finder canaries run inside their residencies during the scan.
            canary.before_cloud_work()
        evidence = self._scan(scanned)
        if canary is not None:
            # Cloud-bound work only (local-only work never reaches this method): a failing, or not yet run,
            # detector canary blocks.
            blocked = canary.block_reason()
            if blocked is not None:
                raise GateFailure(blocked, 200)
        # v0.3: exactly decide(evidence) unless the reviewed false-positive catalogue is enabled.
        result, evidence = decide_with_catalogue(self, scanned, evidence)
        timings = dict(evidence.timings_ms)
        if result.decision == Decision.BLOCK:
            raise GateFailure(result.reasons[0].value if result.reasons else "POLICY_BLOCK", 200)
        allocator = TokenAllocator(conversation_id, self.vault)
        previous = allocator.mapping()
        sent, counts = set(), Counter()
        for start, surface in zip(starts, boundary.surfaces):
            end = start + len(surface.text)
            findings = []
            for finding in evidence.findings:
                if finding.span.start < end and finding.span.end > start:
                    a, b = max(start, finding.span.start) - start, min(end, finding.span.end) - start
                    findings.append(replace(
                        finding, span=Span(a, b),
                        **({"statement_span": Span(0, len(surface.text))}
                           if finding.labels.intersection(SENSITIVE_LABELS) else {}),
                    ))
            matched = (
                len(surface.path) >= 2 and surface.path[0] == "messages"
                and surface.path[1] in matched_indices
            )
            if matched:
                if findings:
                    raise GateFailure("CANONICAL_HISTORY_NEEDS_REASSESSMENT", 200)
                sent.update(token for token in previous if token in surface.text)
                continue
            for token, original in previous.items():
                category = parse_strict_token(token).token_class.value
                for match in re.finditer(re.escape(original), surface.text):
                    findings.append(SpanFinding(
                        match.start(), match.end(), CATEGORY_LABEL[category],
                    ))
            if not surface.mutable:
                if findings:
                    raise GateFailure("PII_IN_PROTOCOL_FIELD", 200)
                continue
            protected = substitute(surface.text, findings, allocator)
            assign(boundary.body, surface.path, protected.tokenised_text)
            sent.update(protected.authorised_tokens)
            counts.update(protected.redaction_summary)
        mapping = allocator.mapping()
        sent = frozenset(sent)
        scoped = {token: mapping[token] for token in sent}
        raw = canonical(boundary.final_body(self.cloud.settings.max_tokens, self.cloud.settings.model))
        originals = tuple(mapping.values())
        verify_json(raw, originals)
        # v0.3: the known-miss final sweep on the exact outbound JSON (a no-op unless enabled).
        result = final_sweep(self, raw, result)
        return raw, scoped, sent, originals, result, timings, dict(counts)

    def handle(self, request, headers=None, *, source_document_count=1):
        from .router_flow import handle
        return handle(self, request, headers, source_document_count=source_document_count)

    def _restore_chunks(
        self, upstream, job, conversation_id, local_messages, clean_messages,
        mapping, sent, schemas,
    ):
        restorer = StreamingRestorer(mapping, sent_tokens=sent)
        accumulator = ToolCallAccumulator(mapping)
        calls, raw_arguments = {}, {}
        local_text, canonical_text = [], []
        finish, usage = None, None
        common = {
            "id": "chatcmpl-" + job.job_id, "object": "chat.completion.chunk",
            "created": int(self.clock().timestamp()), "model": self.cloud_model_id,
        }

        def chunk(delta, finished=None, **extras):
            return {**common, "choices": [{
                "index": 0, "delta": delta, "finish_reason": finished,
            }], **extras}

        try:
            yield chunk({"role": "assistant"})
            for value in upstream:
                if isinstance(value.get("usage"), dict):
                    usage = {
                        key: number for key, number in value["usage"].items()
                        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                        and type(number) is int and number >= 0
                    }
                for choice in value["choices"]:
                    delta = choice["delta"]
                    content = delta.get("content") or ""
                    if content:
                        canonical_text.append(content)
                        restored = restorer.feed(content)
                        local_text.append(restored)
                        if restored:
                            yield chunk({"content": restored})
                    for call in delta.get("tool_calls", []):
                        if type(call) is not dict or set(call) - {"index", "id", "type", "function"}:
                            raise GateFailure("TOOL_PROTOCOL", 200)
                        index = call.get("index")
                        if type(index) is not int or not 0 <= index < 32:
                            raise GateFailure("TOOL_PROTOCOL", 200)
                        saved = calls.setdefault(index, {"function": {}})
                        for key in ("id", "type"):
                            if key in call:
                                if key in saved:
                                    raise GateFailure("TOOL_PROTOCOL", 200)
                                saved[key] = call[key]
                        function = call.get("function", {})
                        if type(function) is not dict or set(function) - {"name", "arguments"}:
                            raise GateFailure("TOOL_PROTOCOL", 200)
                        if "name" in function:
                            if "name" in saved["function"]:
                                raise GateFailure("TOOL_PROTOCOL", 200)
                            saved["function"]["name"] = function["name"]
                        if "arguments" in function:
                            argument = function["arguments"]
                            accumulator.feed(index, argument)
                            raw_arguments[index] = raw_arguments.get(index, "") + argument
                    if choice["finish_reason"] is not None:
                        finish = choice["finish_reason"]
            tail = restorer.close()
            local_text.append(tail)
            if tail:
                yield chunk({"content": tail})
            if finish not in {"stop", "tool_calls"} or bool(calls) != (finish == "tool_calls"):
                raise GateFailure("CLOUD_INCOMPLETE")
            restored_calls, canonical_calls = [], []
            for index in sorted(calls):
                call = calls[index]
                name = call["function"].get("name")
                if (
                    call.get("type") != "function" or not isinstance(call.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"])
                    or name not in schemas
                ):
                    raise GateFailure("TOOL_NOT_ALLOWED", 200)
                parsed = strict_json(raw_arguments[index])
                validate_output(parsed, schemas[name])

                def strict_tokens(value):
                    if isinstance(value, str):
                        if restore_text(value, mapping).report.tolerant_count:
                            raise GateFailure("TOOL_TOKEN_NOT_STRICT", 200)
                    elif isinstance(value, list):
                        for item in value:
                            strict_tokens(item)
                    elif isinstance(value, dict):
                        for key, item in value.items():
                            strict_tokens(key)
                            strict_tokens(item)

                strict_tokens(parsed)
                arguments = accumulator.complete(index)
                validate_output(strict_json(arguments), schemas[name])
                canonical_calls.append({
                    "id": call["id"], "type": "function",
                    "function": {"name": name, "arguments": raw_arguments[index]},
                })
                restored_calls.append({
                    "id": call["id"], "type": "function",
                    "function": {"name": name, "arguments": arguments},
                })
            self.history.wipe(conversation_id)
            for index, (clean, local) in enumerate(zip(clean_messages, local_messages)):
                self.history.record(
                    conversation_id, f"t{index:04d}", clean["role"],
                    canonical(clean).decode(), local_text=canonical(local).decode(),
                )
            canonical_answer = {"role": "assistant", "content": "".join(canonical_text) or None}
            local_answer = {"role": "assistant", "content": "".join(local_text) or None}
            if canonical_calls:
                canonical_answer["tool_calls"] = canonical_calls
                local_answer["tool_calls"] = restored_calls
            self.history.record(
                conversation_id, f"t{len(clean_messages):04d}", "assistant",
                canonical(canonical_answer).decode(), local_text=canonical(local_answer).decode(),
            )
            self._transition(job, JobStatus.COMPLETED)
            if restored_calls:
                yield chunk({"tool_calls": [
                    {"index": index, **call} for index, call in enumerate(restored_calls)
                ]})
            yield chunk({}, finish, **({"usage": usage} if usage is not None else {}), themis={
                "unknown_tokens": restorer.report.unknown_count,
                "dropped_tokens": restorer.report.dropped_count,
                "restored_tokens": restorer.report.restored_count,
            })
        except GateFailure as failure:
            from .router_flow import fail_job
            fail_job(self, job, failure.code)
            raise
        except CloudFailure as failure:
            from .router_flow import fail_job
            fail_job(self, job, failure.code)
            raise GateFailure(failure.code) from None
        except Exception:
            from .router_flow import fail_job
            fail_job(self, job, "RESTORATION_OR_REQUIRED_STAGE_FAILED")
            raise GateFailure("RESTORATION_OR_REQUIRED_STAGE_FAILED") from None
        finally:
            upstream.close()

    def safe_jobs(self):
        from .app_services import job_view
        return [job_view(row) for row in self._register_call("list_jobs")]

    def safe_job(self, identifier):
        from .app_services import job_view
        try:
            return job_view(self.register.get_details(identifier))
        except KeyError:
            return None

    def cloud_view(self, identifier):
        from .app_services import cloud_view
        return cloud_view(self, identifier)

    def models(self):
        from .app_services import models
        return models(self)["local"]

    def preview(self, request):
        from .app_services import PreviewStore
        return PreviewStore().create(self, request)

    def health(self):
        register_ok = vault_ok = False
        try:
            register_ok = tuple(self._register_call("sync_journal", ())) == ()
        except GateFailure:
            pass
        try:
            self.vault.mapping("themis-health-probe")
            vault_ok = True
        except Exception:
            pass
        try:
            status = self.runtime.status()
            scheduler = {key: status.get(key) for key in (
                "status", "queue", "loaded_models", "loading",
                "resource_error", "inference_active",
            )}
        except Exception:
            scheduler = {"status": "unavailable"}
        canary = self.canary
        return {
            "canary": canary.status() if canary is not None else {"enabled": False},
            "gate": "ok" if register_ok and vault_ok else "unavailable",
            "register": "ok" if register_ok else "unavailable",
            "vault": "ok" if vault_ok else "unavailable",
            "detection": self._detection_status, "scheduler": scheduler,
            "synthetic_only": True, "pilot_fixture": self.pilot_fixture_enabled,
        }
