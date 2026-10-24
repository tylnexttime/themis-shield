"""Synthetic fixtures using the real controlled SQLite register.

No real model or external cloud connection. Runtime fakes expose the Scheduler
members consumed by the app; tracer/finder outputs retain the detector contract.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace

import httpx

from themis_shield.cloud import ApiKeyCredential, CloudClient, CloudSettings
from themis_shield.cloud.openai_compat import canonical, strict_json
from themis_shield.config import DeploymentProfile, UseCasePolicy
from themis_shield.detectors import DetectionTargets
from themis_shield.detectors.contracts import StageReport, StageResult, Status
from themis_shield.register.sqlite_store import SqliteRegister
from themis_shield.router import Router
from themis_shield.runtime.catalogue import Catalogue
from themis_shield.vault import InMemoryVault


NOW = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)


class MemoryRegister(SqliteRegister):
    def __init__(self, path):
        self.available = True
        super().__init__(path, requester_identity="synthetic-requester")

    def check(self):
        if not self.available:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")

    def _transaction(self):
        self.check()
        return super()._transaction()

    @property
    def jobs(self):
        return {
            row["job_id"]: self.get_job(row["job_id"])
            for row in self.list_jobs()
        }

    def get_approval(self, identifier):
        return self.current_approval(identifier, NOW)

    def approve(self, identifier):
        return super().approve(
            identifier, "operator", "ROUTINE_APPROVED", NOW,
            approver_identity="synthetic-operator",
        )


class Tracer:
    def __init__(self, fail=False):
        self.fail = fail

    def detect(self, text):
        if self.fail:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
        return StageResult((), StageReport("pii-tracer", Status.OK))


class Finders:
    def __init__(self, fail=False):
        self.fail = fail
        self.roles = []

    def chat_json(self, model_role, messages, schema, max_tokens):
        self.roles.append(model_role)
        if self.fail:
            raise TimeoutError("SYNTHETIC_PRIVATE_ERROR")
        # The document is in the last message (prompt v2 puts fixed instructions in a system message first).
        content = "\n".join(message["content"] for message in messages)
        return {"entities": [
            {"label": "PERSON", "value": name}
            for name in ("Mara Lindahl", "Ravi Example")
            if name in content
        ]}


class LocalRuntime:
    catalogue = Catalogue()

    def __init__(self):
        self.calls = []
        # Per-instance, just like Scheduler; settings must not leak across tests.
        self.role_models = {
            "finder-fast": "gemma4-e4b",
            "finder-thorough": "gemma4-26b-a4b",
            "answer": "gemma4-e4b",
        }

    @contextmanager
    def acquire(self, role, *, mode=None, cancel_event=None, timeout=None):
        if mode not in {None, "full", "mmap"}:
            raise ValueError("invalid synthetic load mode")
        self.catalogue.get(self.role_models.get(role, role))
        self.calls.append(role)
        yield self

    def chat(self, messages, **kwargs):
        return {"choices": [{
            "message": {"content": "Synthetic local answer."},
            "finish_reason": "stop",
        }]}

    def status(self):
        return {
            "status": "ready", "queue": 0, "loaded_models": [],
            "inference_active": False, "timings": [],
        }


class FakeCloud:
    def __init__(self, *, tool=False, malformed=False):
        self.requests = []
        self.tool, self.malformed = tool, malformed

    def __call__(self, request):
        body = strict_json(request.content)
        self.requests.append(body)
        tokens = re.findall(
            r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧", request.content.decode()
        )
        token = tokens[0] if tokens else "synthetic"
        events = []

        def add(delta, finish=None):
            events.append({"choices": [{
                "index": 0, "delta": delta, "finish_reason": finish,
            }]})

        if self.tool:
            arguments = canonical({"name": token}).decode()
            add({"tool_calls": [{
                "index": 0, "id": "call_synthetic", "type": "function",
                "function": {
                    "name": "write_local", "arguments": arguments[:7],
                },
            }]})
            add({"tool_calls": [{
                "index": 0, "function": {"arguments": arguments[7:]},
            }]})
            add({}, "tool_calls")
        else:
            add({"content": "Hello " + token[:4]})
            add({"content": token[4:] + "."})
            add({}, "stop")
        raw = b"".join(
            b"data: " + canonical(event) + b"\r\n\r\n"
            for event in events
        )
        if not self.malformed:
            raw += b"data: [DONE]\r\n\r\n"
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=raw
        )


def harness(tmp_path: Path, *, tool=False, malformed=False):
    register = MemoryRegister(tmp_path / "register.db")
    runtime, tracer, finders = LocalRuntime(), Tracer(), Finders()
    fake = FakeCloud(tool=tool, malformed=malformed)
    credential = ApiKeyCredential(
        "SYNTHETIC_CLOUD_KEY", environ={"SYNTHETIC_CLOUD_KEY": "SYNTHETIC_BEARER_NOT_REAL"},
    )
    cloud = CloudClient(
        CloudSettings(
            base_url="https://example.invalid/v1", model="example-cloud-model",
            api_key_env="SYNTHETIC_CLOUD_KEY", capture_dir=tmp_path / "captures",
        ),
        credential=credential, transport=httpx.MockTransport(fake),
    )
    policy = UseCasePolicy(
        "synthetic", ("cloud",),
        approved_for_cloud=True, requires_in_region_inference=False,
    )
    router = Router(
        register=register, runtime=runtime,
        vault=InMemoryVault(clock=lambda: NOW), cloud=cloud,
        profile=DeploymentProfile("synthetic", "1", (), (policy,)),
        use_case=policy,
        targets=DetectionTargets(tracer=tracer, llm_client=finders),
        requester_identity="synthetic-requester",
        allowed_tools=frozenset({"write_local"}),
        local_authorised=True, clock=lambda: NOW,
    )
    return SimpleNamespace(
        router=router, register=register, fake=fake, runtime=runtime,
        tracer=tracer, finders=finders, cloud=cloud,
    )


def request(text="Mara Lindahl asks a synthetic question.", **changes):
    return {
        "model": "themis/auto",
        "messages": [{"role": "user", "content": text}],
        **changes,
    }


def approved_response(h, body, headers=None):
    held = h.router.handle(body, headers)
    assert held.route == "held"
    h.register.approve(held.job_id)
    return h.router.handle(body, headers)
