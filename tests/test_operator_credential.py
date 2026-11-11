"""SEC-04 / R-02: Operator approval needs an Operator credential distinct from the API client credential.

The client credential (the /v1 bearer gate-token, or a browser session opened with the client launcher code) is
refused on every Operator endpoint; the Operator credential (operator-token, or a browser session opened with a
one-use Operator launcher code) approves but is never accepted as a client credential (/v1, hooks, writes).
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from themis_shield import startup
from themis_shield.web.__main__ import main as serve, provision_operator_token, provision_token
from themis_shield.web.app import create_app, default_operator_token_path, default_token_path, read_gate_token

from router_fakes import harness, request

CLIENT = "SYNTHETIC_LOCAL_GATE_TOKEN_1234567890"
OPERATOR = "SYNTHETIC_LOCAL_OPERATOR_TOKEN_0987654321"
ORIGIN = "http://127.0.0.1:8742"


def _gate(tmp_path):
    h = harness(tmp_path)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"
    return h


def _client(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)), base_url=ORIGIN,
        headers={"Authorization": "Bearer " + token} if token else {},
    )


def test_client_token_cannot_approve_its_own_held_job(tmp_path):
    # The review reproduction (held, approve 1, approve 2, sent) with the client token: refused, nothing changes.
    h = _gate(tmp_path)

    async def exercise():
        async with _client(create_app(h.router, token=CLIENT), CLIENT) as client:
            body = request("Mara Lindahl asks a synthetic question.")
            first = await client.post("/v1/chat/completions", json=body)
            job = first.headers["x-themis-job"]
            assert first.headers["x-themis-reason"] == "OPERATOR_APPROVAL_REQUIRED"
            before = h.register.get_details(job)
            for stage in (1, 2):
                refused = await client.post(
                    f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
                )
                assert refused.status_code == 403
                assert refused.json()["error"]["code"] == "OPERATOR_CREDENTIAL_REQUIRED"
            for action, reason in (("refuse", "REFUSED"), ("escalate", "OPERATOR_DOUBT")):
                refused = await client.post(f"/api/operator/{job}/{action}", json={"reason": reason})
                assert refused.status_code == 403
            for method, path in (("GET", "/api/operator/queue"), ("POST", "/api/operator/launcher")):
                assert (await client.request(method, path)).status_code == 403
            after = h.register.get_details(job)
            assert (after["status"], after["stage1"], after["stage2"], after["approval_revision"]) == (
                before["status"], before["stage1"], before["stage2"], before["approval_revision"],
            )
            second = await client.post("/v1/chat/completions", json=body)
            assert second.headers["x-themis-route"] != "cloud-tokenised"
            assert h.fake.requests == []

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_operator_token_approves_and_is_refused_as_a_client_credential(tmp_path):
    h = _gate(tmp_path)
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR)

    async def exercise():
        async with _client(app, CLIENT) as client, _client(app, OPERATOR) as operator:
            body = request("Mara Lindahl asks a synthetic question.")
            # The Operator token is not a client token: /v1, the hooks API and the app writes refuse it.
            assert (await operator.post("/v1/chat/completions", json=body)).status_code == 401
            assert (await operator.get("/v1/models")).status_code == 401
            assert (await operator.post("/api/hooks/session-start", json={"session_id": "s1"})).status_code == 401
            assert (await operator.post("/api/preview", json={
                "text": "[user]\nSynthetic.", "attachments": [], "intent": "cloud",
            })).status_code == 401
            assert h.fake.requests == [] and h.register.list_jobs() == ()
            # It reads the app views, so the browser UI loads for the Operator role.
            assert (await operator.get("/api/settings")).status_code == 200
            job = (await client.post("/v1/chat/completions", json=body)).headers["x-themis-job"]
            queue = await operator.get("/api/operator/queue")
            assert queue.status_code == 200 and [row["job_id"] for row in queue.json()["jobs"]] == [job]
            for stage in (1, 2):
                approved = await operator.post(
                    f"/api/operator/{job}/approve", json={"stage": stage, "reason": "ROUTINE_APPROVED"},
                )
                assert approved.status_code == 200, approved.text
                assert h.fake.requests == []
            sent = await client.post("/v1/chat/completions", json=body)
            assert sent.headers["x-themis-route"] == "cloud-tokenised" and len(h.fake.requests) == 1

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_operator_browser_session_needs_its_own_one_use_launcher_code(tmp_path):
    h = _gate(tmp_path)
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR, launcher_code="synthetic-client-code")
    held = h.router.handle(request())

    async def exercise():
        async with _client(app) as browser, _client(app, OPERATOR) as operator:
            assert (await browser.get("/?t=synthetic-client-code")).status_code == 303
            # The client browser session is a client credential: it reads and chats, but cannot decide.
            assert (await browser.get("/api/settings")).status_code == 200
            refused = await browser.post(f"/api/operator/{held.job_id}/approve", json={
                "stage": 1, "reason": "ROUTINE_APPROVED"})
            assert refused.status_code == 403
            assert h.register.get_details(held.job_id)["stage1"] == "requested"
            minted = await operator.post("/api/operator/launcher")
            assert minted.status_code == 200 and minted.json()["path"].startswith("/?t=")
            opened = await browser.get(minted.json()["path"])
            assert opened.status_code == 303
            cookie = opened.headers["set-cookie"].lower()
            assert cookie.startswith("themis_operator=") and "httponly" in cookie and "samesite=strict" in cookie
            assert (await browser.get(minted.json()["path"])).status_code == 401  # one use
            approved = await browser.post(f"/api/operator/{held.job_id}/approve", json={
                "stage": 1, "reason": "ROUTINE_APPROVED"})
            assert approved.status_code == 200, approved.text
            # An Operator-only browser session reads the views but is not a client session.
            async with _client(app) as only:
                only.cookies.set("themis_operator", browser.cookies.get("themis_operator"))
                assert (await only.get("/api/settings")).status_code == 200
                assert (await only.get("/api/operator/queue")).status_code == 200
                assert (await only.post("/v1/chat/completions", json=request())).status_code == 401

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_without_an_operator_credential_nobody_acts_as_the_operator(tmp_path):
    h = _gate(tmp_path)
    with pytest.raises(ValueError):
        create_app(h.router, token=CLIENT, operator_token=CLIENT)
    held = h.router.handle(request())

    async def exercise():
        async with _client(create_app(h.router, token=CLIENT)) as anonymous:
            answer = await anonymous.post(f"/api/operator/{held.job_id}/approve", json={
                "stage": 1, "reason": "ROUTINE_APPROVED"})
            assert answer.status_code == 401
            assert answer.json()["error"]["code"] == "OPERATOR_CREDENTIAL_REQUIRED"

    try:
        asyncio.run(exercise())
        assert h.register.get_details(held.job_id)["stage1"] == "requested"
    finally:
        h.cloud.close()


def test_operator_token_file_is_created_like_the_gate_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    serve(["--provision-token"])
    assert "Operator token" in capsys.readouterr().out
    gate_path, operator_path = default_token_path(), default_operator_token_path()
    assert operator_path.parent == gate_path.parent and operator_path.name == "operator-token"
    client, operator = read_gate_token(gate_path), read_gate_token(operator_path)
    assert client != operator
    assert os.stat(operator_path).st_mode == os.stat(gate_path).st_mode
    # Created once: later starts read the same credential.
    assert provision_operator_token(client) == operator and provision_token() == client
    # The same format checks as gate-token: a malformed token, or the client token, is refused at start.
    operator_path.write_text("short", encoding="ascii")
    with pytest.raises(startup.StartupRefused) as refused:
        provision_operator_token(client)
    assert refused.value.code == "OPERATOR_TOKEN_UNAVAILABLE"
    operator_path.write_text(client, encoding="ascii")
    with pytest.raises(startup.StartupRefused) as refused:
        provision_operator_token(client)
    assert refused.value.code == "OPERATOR_TOKEN_UNAVAILABLE" and client not in refused.value.message

def test_open_operator_mints_a_code_with_the_operator_token_only(tmp_path, monkeypatch, capsys):
    import webbrowser
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    serve(["--provision-token"])
    operator = read_gate_token(default_operator_token_path())
    calls, opened = [], []

    def post(url, *, headers, **options):
        calls.append((url, headers["Authorization"]))
        return httpx.Response(200, json={"code": "c" * 43, "path": "/?t=" + "c" * 43})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    serve(["--open-operator"])
    assert calls == [("http://127.0.0.1:8742/api/operator/launcher", "Bearer " + operator)]
    assert opened == ["http://127.0.0.1:8742/?t=" + "c" * 43]
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(403, json={}))
    with pytest.raises(SystemExit) as caught:
        serve(["--open-operator"])
    assert caught.value.code == 2 and "[OPERATOR_LAUNCH_FAILED]" in capsys.readouterr().err