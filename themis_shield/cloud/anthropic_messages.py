"""The native Anthropic Messages leg, beside the OpenAI-compatible one (``openai_compat``).

Selected by ``api = "anthropic-messages"`` in the cloud settings. The router still builds one OpenAI-shaped package
per job, and approvals stay bound to its hash. ``CloudClient`` turns that approved package into a Messages request at
send time (a pure function of it: the system and developer messages become the top-level ``system`` text,
``max_completion_tokens`` becomes ``max_tokens``), runs the exact-value check on the translated bytes, and turns the
reply back into the Chat Completions chunks that ``Router._restore_chunks`` already reads, so restoration, URL
withholding and the reports are shared with the other leg. Tool use is out of scope and refused. Thinking blocks, which
a newer model sends by default, are dropped from the reply (their tokens still count in ``output_tokens``).

Nothing here reads the environment or a credential: the key is read by ``ApiKeyCredential`` and only ever placed in
the ``x-api-key`` header. Provider bodies and error text are never copied into an exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from .openai_compat import CloudFailure, canonical, strict_json


ANTHROPIC_VERSION = "2023-06-01"
_REQUEST_FIELDS = {
    "model", "messages", "max_completion_tokens", "max_tokens", "stream", "stream_options",
    "temperature", "top_p", "stop",
}
_FINISH = {"end_turn": "stop", "stop_sequence": "stop"}
# Content blocks the model may open although the request asked for none (newer models think by default): their
# deltas are read and dropped. Only ``text`` blocks are ever delivered, captured or restored.
_HIDDEN = {"thinking": {"thinking_delta", "signature_delta"}, "redacted_thinking": set()}
_INPUT_COUNTS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def request_headers(secret: str) -> dict[str, str]:
    return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    body: bytes
    stream: bool


def _blocks(content):
    """Content as the package carries it: a string, or a list of ``{"type": "text", "text": ...}`` blocks."""
    if isinstance(content, str):
        return content
    if (
        not isinstance(content, list) or not content
        or any(
            type(block) is not dict or set(block) != {"type", "text"}
            or block["type"] != "text" or not isinstance(block["text"], str)
            for block in content
        )
    ):
        raise CloudFailure("UNSUPPORTED_REQUEST")
    return content


def _number(body, name, high):
    value = body[name]
    if type(value) not in (int, float) or not 0 <= value <= high:
        raise CloudFailure("UNSUPPORTED_REQUEST")
    return value


def messages_request(payload: bytes) -> MessagesRequest:
    """The Messages request for one approved package (``ChatBoundary.final_body`` or the trip-wire's body).

    Anything that has no Messages equivalent here is refused with a fixed code, never dropped: tools and tool
    results with ``UNSUPPORTED_TOOL``, any other field or shape with ``UNSUPPORTED_REQUEST``.
    """
    body = strict_json(payload)
    if type(body) is not dict:
        raise CloudFailure("UNSUPPORTED_REQUEST")
    if "tools" in body or "tool_choice" in body:
        raise CloudFailure("UNSUPPORTED_TOOL")
    if set(body) - _REQUEST_FIELDS or not isinstance(body.get("model"), str) or type(body.get("messages")) is not list:
        raise CloudFailure("UNSUPPORTED_REQUEST")
    system, turns = [], []
    for message in body["messages"]:
        if type(message) is not dict:
            raise CloudFailure("UNSUPPORTED_REQUEST")
        if message.get("role") == "tool" or "tool_calls" in message or "tool_call_id" in message:
            raise CloudFailure("UNSUPPORTED_TOOL")
        if set(message) != {"role", "content"} or message["role"] not in {"system", "developer", "user", "assistant"}:
            raise CloudFailure("UNSUPPORTED_REQUEST")
        content = _blocks(message["content"])
        if message["role"] in {"system", "developer"}:
            system.append(content if isinstance(content, str) else "\n".join(block["text"] for block in content))
        else:
            turns.append({"role": message["role"], "content": content})
    if not turns or ("max_completion_tokens" in body) == ("max_tokens" in body):
        raise CloudFailure("UNSUPPORTED_REQUEST")
    budget = body.get("max_completion_tokens", body.get("max_tokens"))
    stream = body.get("stream", True)
    if type(budget) is not int or not 1 <= budget <= 128000 or type(stream) is not bool:
        raise CloudFailure("UNSUPPORTED_REQUEST")
    native = {"model": body["model"], "max_tokens": budget, "messages": turns, "stream": stream}
    if system:
        native["system"] = "\n\n".join(system)
    if "temperature" in body:
        native["temperature"] = _number(body, "temperature", 1)  # Messages accepts 0 to 1; Chat Completions 0 to 2
    if "top_p" in body:
        native["top_p"] = _number(body, "top_p", 1)
    if body.get("stop") is not None:
        stop = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
        if not stop or any(not isinstance(item, str) for item in stop):
            raise CloudFailure("UNSUPPORTED_REQUEST")
        native["stop_sequences"] = stop
    return MessagesRequest(canonical(native), stream)


class MessagesReply:
    """One Messages reply, as the Chat Completions chunks the router reads.

    ``event`` takes the data of one server-sent event, ``message`` the whole body of a non-streaming reply. Both
    are strict: an unknown event or block type, an out-of-order event or a malformed field raises, and the caller
    turns that into ``CLOUD_PROTOCOL``. Only text is understood (no tools are requested); thinking blocks, which a
    model may send unasked, are read and dropped here, never delivered, captured or restored.
    """

    def __init__(self, streaming: bool):
        self.streaming = streaming
        self.started = self.finished = False
        self.open = None  # index of the content block being streamed
        self.kind = None  # its type
        self.blocks = 0
        self.usage: dict[str, int] = {}

    @staticmethod
    def _chunk(delta, finish=None, **extras):
        return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extras}

    def _count(self, usage):
        if usage is None:
            return
        if not isinstance(usage, dict):
            raise ValueError
        for name in (*_INPUT_COUNTS, "output_tokens"):
            number = usage.get(name)
            if number is None:
                continue
            if type(number) is not int or number < 0:
                raise ValueError
            self.usage[name] = number  # a later report is cumulative: it replaces the earlier one

    def _final(self, finish):
        """The closing chunk: the finish reason, and the usage mapped to the Chat Completions names."""
        return self._chunk({}, self._finish(finish), **({"usage": self._usage()} if self.usage else {}))

    def _usage(self):
        prompt = sum(self.usage.get(name, 0) for name in _INPUT_COUNTS)
        completion = self.usage.get("output_tokens", 0)
        usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
        if "cache_read_input_tokens" in self.usage or "cache_creation_input_tokens" in self.usage:
            usage["prompt_tokens_details"] = {
                "cached_tokens": self.usage.get("cache_read_input_tokens", 0),
                "cache_creation_tokens": self.usage.get("cache_creation_input_tokens", 0),
            }
        return usage

    @staticmethod
    def _finish(reason):
        if reason == "refusal":
            raise CloudFailure("CLOUD_REFUSED")
        if reason not in _FINISH:  # max_tokens, pause_turn, tool_use, null: the answer is not complete
            raise CloudFailure("CLOUD_INCOMPLETE")
        return _FINISH[reason]

    def event(self, text: str):
        """The chunk for one event (or None), and whether the stream is complete (``message_stop``)."""
        value = strict_json(text)
        if type(value) is not dict:
            raise ValueError
        kind = value.get("type")
        if kind == "ping":
            return None, False
        if kind == "error":
            raise CloudFailure("CLOUD_PROTOCOL")
        if kind == "message_start":
            message = value.get("message")
            if (
                self.started or type(message) is not dict or message.get("role") != "assistant"
                or message.get("content") != []
            ):
                raise ValueError
            self.started = True
            self._count(message.get("usage"))
            return self._chunk({"role": "assistant"}), False
        if not self.started:
            raise ValueError
        if kind == "message_stop":
            # Complete only after message_delta has given the stop reason (the other leg's [DONE] rule).
            if not self.finished:
                raise CloudFailure("CLOUD_INCOMPLETE")
            return None, True
        if self.finished:
            raise ValueError
        if kind == "content_block_start":
            block = value.get("content_block")
            if self.open is not None or value.get("index") != self.blocks or type(block) is not dict:
                raise ValueError
            shape = block.get("type")
            if shape == "text":
                if not isinstance(block.get("text"), str):
                    raise ValueError
            elif shape not in _HIDDEN:
                raise ValueError
            self.open, self.kind, self.blocks = self.blocks, shape, self.blocks + 1
            return (self._chunk({"content": block["text"]}) if shape == "text" and block["text"] else None), False
        if kind == "content_block_delta":
            delta = value.get("delta")
            if self.open is None or value.get("index") != self.open or type(delta) is not dict:
                raise ValueError
            if self.kind != "text":
                if delta.get("type") not in _HIDDEN[self.kind]:
                    raise ValueError
                return None, False
            if delta.get("type") != "text_delta" or not isinstance(delta.get("text"), str):
                raise ValueError
            return (self._chunk({"content": delta["text"]}) if delta["text"] else None), False
        if kind == "content_block_stop":
            if self.open is None or value.get("index") != self.open:
                raise ValueError
            self.open = self.kind = None
            return None, False
        if kind == "message_delta":
            delta = value.get("delta")
            if self.open is not None or type(delta) is not dict:
                raise ValueError
            self._count(value.get("usage"))
            closing = self._final(delta.get("stop_reason"))
            self.finished = True
            return closing, False
        raise ValueError  # an event type this gate does not know

    def message(self, raw: bytes) -> list[dict]:
        """The chunks for a whole non-streaming reply (``content[].text``, ``stop_reason``, ``usage``)."""
        value = strict_json(raw)
        if type(value) is not dict or value.get("type") != "message" or value.get("role") != "assistant":
            raise ValueError
        content = value.get("content")
        if type(content) is not list or any(
            type(block) is not dict or not (
                block.get("type") in _HIDDEN or block.get("type") == "text" and isinstance(block.get("text"), str)
            )
            for block in content
        ):
            raise ValueError
        content = [block for block in content if block["type"] == "text"]
        self._count(value.get("usage"))
        chunks = [self._chunk({"role": "assistant"})]
        chunks += [self._chunk({"content": block["text"]}) for block in content if block["text"]]
        chunks.append(self._final(value.get("stop_reason")))
        return chunks
