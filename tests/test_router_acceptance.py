"""Router acceptance tests on synthetic data. No real model, register or cloud."""

from dataclasses import replace
import asyncio
import json

import httpx
import pytest

from themis_shield.cloud import ApiKeyCredential, CloudClient, CloudFailure, CloudSettings, Outbound
from themis_shield.cloud.openai_compat import canonical
from themis_shield.detectors.markings import MarkingClass
from themis_shield.router import Router
from themis_shield.web.app import create_app

from router_fakes import NOW, approved_response, harness, request

# Existing marking cases use the au-pspf example profile.
pytestmark = pytest.mark.usefixtures("au_pspf_markings")


@pytest.mark.parametrize("marker", ["PROTECTED", "SECRET", "TOP SECRET", "PROTECTED//AUSTEO"])
def test_protected_never_calls_cloud(tmp_path, marker):
    h = harness(tmp_path)
    try:
        response = h.router.handle(request(marker + "\nSynthetic arithmetic."))
        assert response.route == "local"
        assert "LOCAL-ONLY" in response.body["choices"][0]["message"]["content"]
        assert h.runtime.calls == ["answer"]
        assert h.fake.requests == []
        assert h.register.jobs[response.job_id].status.value == "Completed"
        assert h.finders.roles == []
    finally:
        h.cloud.close()


def test_names_tokenised_and_restored(tmp_path):
    h = harness(tmp_path)
    try:
        response = approved_response(h, request())
        assert response.route == "cloud-tokenised"
        assert len(h.fake.requests) == 1
        assert "Mara Lindahl" not in json.dumps(h.fake.requests)
        assert "Mara Lindahl" in response.body["choices"][0]["message"]["content"]
        assert h.finders.roles == [
            "gemma4-e4b", "gemma4-26b-a4b",
            "gemma4-e4b", "gemma4-26b-a4b",
        ]
        assert set(response.timings_ms) == {1, 2, 3}
    finally:
        h.cloud.close()


def test_held_approved_resend_then_sent(tmp_path):
    h = harness(tmp_path)
    try:
        body = request("What is a synthetic retention schedule?")
        first = h.router.handle(body)
        second = h.router.handle(body)
        assert first.job_id == second.job_id
        assert first.route == second.route == "held"
        assert "Held for operator approval JOB-" in first.body["choices"][0]["message"]["content"]
        assert h.fake.requests == []
        h.register.approve(first.job_id)
        sent = h.router.handle(body)
        assert sent.route == "cloud-tokenised"
        assert len(h.fake.requests) == 1
        assert h.router.handle(body).reason == "JOB_ALREADY_COMPLETED"
        assert len(h.fake.requests) == 1
    finally:
        h.cloud.close()


@pytest.mark.parametrize("stage", ["register", "tracer", "finders", "vault"])
def test_gate_down_fails_closed(tmp_path, stage):
    h = harness(tmp_path)
    try:
        if stage == "register":
            h.register.available = False
        elif stage == "tracer":
            h.tracer.fail = True
        elif stage == "finders":
            h.finders.fail = True
        else:
            h.router.vault.close()
        response = h.router.handle(request())
        assert response.status == 503
        assert response.route == "blocked"
        assert h.fake.requests == []
        assert "SYNTHETIC_PRIVATE_ERROR" not in json.dumps(response.body)
    finally:
        h.cloud.close()


def test_official_sensitive_cloud_confirmation_required(tmp_path):
    h = harness(tmp_path)
    try:
        body = request("OFFICIAL: Sensitive\nSynthetic timetable.")
        assert h.router.handle(body).route == "local"
        cloud_headers = {"X-Themis-Confirm-Cloud": "yes"}
        held = h.router.handle(body, cloud_headers)
        assert held.route == "held"
        assert h.fake.requests == []
        h.register.approve(held.job_id)
        response = h.router.handle(body, cloud_headers)
        assert response.route == "cloud-tokenised"
        assert len(h.fake.requests) == 1
    finally:
        h.cloud.close()


def tool_request(**changes):
    return request(
        tools=[{
            "type": "function",
            "function": {
                "name": "write_local", "description": "Prepare synthetic local output.",
                "parameters": {
                    "type": "object", "additionalProperties": False,
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            },
        }],
        **changes,
    )


def test_tool_argument_round_trip(tmp_path):
    h = harness(tmp_path, tool=True)
    try:
        response = approved_response(h, tool_request())
        call = response.body["choices"][0]["message"]["tool_calls"][0]
        assert json.loads(call["function"]["arguments"]) == {"name": "Mara Lindahl"}
        assert "Mara Lindahl" not in json.dumps(h.fake.requests)
        assert response.body["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        h.cloud.close()


def test_streaming_split_tokens(tmp_path):
    h = harness(tmp_path)
    try:
        body = request(stream=True)
        response = approved_response(h, body)
        wire = "".join(response.sse())
        chunks = [
            json.loads(line[6:]) for line in wire.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        text = "".join(
            choice["delta"].get("content", "")
            for chunk in chunks for choice in chunk.get("choices", [])
        )
        assert text == "Hello Mara Lindahl."
        assert "⟦P:" not in text
        assert "data: [DONE]" in wire
        assert chunks[-1]["themis"]["unknown_tokens"] == 0
        # Explicit response cleanup releases conversation ownership.
        assert h.router.handle(request("PROTECTED\nNew local turn.")).route == "local"
    finally:
        h.cloud.close()


def test_incomplete_sse_is_not_success(tmp_path):
    h = harness(tmp_path, malformed=True)
    try:
        response = approved_response(h, request(stream=True))
        wire = "".join(response.sse())
        assert "CLOUD_INCOMPLETE" in wire
        assert "data: [DONE]" not in wire
        assert h.register.jobs[response.job_id].status.value != "Completed"
    finally:
        h.cloud.close()


def test_payload_capture_written_tokenised(tmp_path):
    h = harness(tmp_path)
    try:
        response = approved_response(h, request())
        path = tmp_path / "captures" / f"{response.job_id}.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        outbound = next(record for record in records if record["kind"] == "outbound")
        assert outbound["verify_no_originals"] is True
        assert json.loads(outbound["exact_json"]) == h.fake.requests[0]
        assert "Mara Lindahl" not in path.read_text(encoding="utf-8")
        assert "SYNTHETIC_BEARER_NOT_REAL" not in path.read_text(encoding="utf-8")
        assert any(record["kind"] == "answer-tokenised" for record in records)
    finally:
        h.cloud.close()


def test_api_key_is_read_from_the_named_environment_variable():
    environ = {"THEMIS_TEST_KEY": "sk-synthetic-not-real-0001"}
    credential = ApiKeyCredential("THEMIS_TEST_KEY", environ=environ)
    assert credential.secret() == "sk-synthetic-not-real-0001"
    environ["THEMIS_TEST_KEY"] = "sk-synthetic-not-real-0002"
    assert credential.secret() == "sk-synthetic-not-real-0002"  # read at send time
    for bad in ("", "short", "has space inside-key", "tab\tinside-key"):
        environ["THEMIS_TEST_KEY"] = bad
        with pytest.raises(CloudFailure) as caught:
            credential.secret()
        assert caught.value.code == "CREDENTIAL_UNAVAILABLE"
        assert bad not in str(caught.value) or bad == ""
    with pytest.raises(CloudFailure):
        ApiKeyCredential("THEMIS_ABSENT_KEY", environ={}).secret()


def test_cloud_settings_url_and_auth_styles(tmp_path):
    openai = CloudSettings(base_url="https://api.example.invalid/v1", model="m-1")
    assert openai.url == "https://api.example.invalid/v1/chat/completions"
    azure = CloudSettings(
        base_url="https://resource.example.invalid/openai/deployments/m-1",
        model="m-1", auth_style="api-key", api_version="2024-10-21",
    )
    assert azure.url.endswith("/openai/deployments/m-1/chat/completions?api-version=2024-10-21")
    local = CloudSettings(base_url="http://127.0.0.1:8080/v1", model="m-1")
    assert local.url.startswith("http://127.0.0.1:8080/")
    for bad in (
        {"base_url": "http://api.example.invalid/v1", "model": "m"},
        {"base_url": "https://user:pw@api.example.invalid", "model": "m"},
        {"base_url": "https://api.example.invalid/v1?x=1", "model": "m"},
        {"base_url": "https://api.example.invalid/v1", "model": "m", "auth_style": "cookie"},
        {"base_url": "https://api.example.invalid/v1", "model": "m", "api_key_env": "bad name"},
    ):
        with pytest.raises(ValueError):
            CloudSettings(**bad)
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(401)

    for style, header in (("bearer", "authorization"), ("api-key", "api-key")):
        client = CloudClient(
            CloudSettings(
                base_url="https://api.example.invalid/v1", model="m-1", auth_style=style,
                api_key_env="K", capture_dir=tmp_path / style,
            ),
            credential=ApiKeyCredential("K", environ={"K": "sk-synthetic-not-real"}),
            transport=httpx.MockTransport(handler),
        )
        try:
            raw = canonical({"model": "m-1", "messages": [{"role": "user", "content": "Synthetic"}]})
            import hashlib
            with pytest.raises(CloudFailure):
                client.open(Outbound(
                    "JOB-2026-0001", MarkingClass.NONE, raw,
                    hashlib.sha256(raw).hexdigest(), True, (),
                ))
        finally:
            client.close()
        assert header in seen[-1]
        assert "sk-synthetic-not-real" not in (tmp_path / style / "JOB-2026-0001.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("marking,verified", [(MarkingClass.RESTRICTED, True), (MarkingClass.INTERNAL, False)])
def test_cloud_client_third_lock(tmp_path, marking, verified):
    h = harness(tmp_path)
    try:
        raw = canonical({"messages": [{"role": "user", "content": "Synthetic"}]})
        import hashlib
        with pytest.raises(CloudFailure):
            h.cloud.open(Outbound(
                "JOB-2026-0001", marking, raw,
                hashlib.sha256(raw).hexdigest(), verified, (),
            ))
        assert h.fake.requests == []
    finally:
        h.cloud.close()


def test_local_bearer_required_and_fail_closed_http(tmp_path):
    h = harness(tmp_path)

    async def exercise():
        app = create_app(h.router, token="synthetic-local-bearer")
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8742",
        ) as client:
            denied = await client.post("/v1/chat/completions", json=request())
            assert denied.status_code == 401
            headers = {"Authorization": "Bearer synthetic-local-bearer"}
            h.register.available = False
            closed = await client.post("/v1/chat/completions", json=request(), headers=headers)
            assert closed.status_code == 503
            assert closed.headers["X-Themis-Route"] == "blocked"
            assert h.fake.requests == []

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_changed_clean_input_does_not_use_old_approval(tmp_path):
    h = harness(tmp_path)
    try:
        body = request("Synthetic clean question A.")
        held = h.router.handle(body, {"X-Themis-Conversation": "synthetic"})
        h.register.approve(held.job_id)
        changed = h.router.handle(request("Synthetic clean question B."), {"X-Themis-Conversation": "synthetic"})
        assert changed.route == "held"
        assert changed.job_id != held.job_id
        assert h.fake.requests == []
    finally:
        h.cloud.close()
