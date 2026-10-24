"""Small, explicit Chat Completions profile; no opaque-content forwarding.


Mutable text leaves can be substituted. Protocol identifiers, schema property
names and fixed enum/const values are inspected but cannot be substituted:
a finding there blocks rather than silently changing a protocol contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re

from .cloud.openai_compat import canonical, strict_json
from .inference.json_output import normalise_schema


IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
MODEL = re.compile(r"themis/(?:auto|cloud:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}|local:[A-Za-z0-9_.:-]+)")
TOP_FIELDS = {
    "model", "messages", "stream", "tools", "tool_choice",
    "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop",
    "stream_options", "n",
}


@dataclass(frozen=True, slots=True)
class Surface:
    path: tuple
    text: str = field(repr=False)
    mutable: bool = True


def assign(root, path, value):
    parent = root
    for component in path[:-1]:
        parent = parent[component]
    parent[path[-1]] = value


class ChatBoundary:
    def __init__(self, request: dict, allowed_tools: frozenset[str], cloud_model: str | None = None):
        if (
            type(request) is not dict or set(request) - TOP_FIELDS
            or not isinstance(request.get("model", "themis/auto"), str)
            or not MODEL.fullmatch(request.get("model", "themis/auto"))
        ):
            raise ValueError("UNSUPPORTED_REQUEST")
        requested = request.get("model", "themis/auto")
        if (
            requested.startswith("themis/cloud:") and cloud_model is not None
            and requested != "themis/cloud:" + cloud_model
        ):
            # Only the one configured cloud model can be addressed.
            raise ValueError("UNSUPPORTED_REQUEST")
        if len(canonical(request)) > 2 * 1024 * 1024:
            raise ValueError("REQUEST_BOUND")
        self.model = request.get("model", "themis/auto")
        self.stream = request.get("stream", False)
        if type(self.stream) is not bool or request.get("n", 1) != 1:
            raise ValueError("UNSUPPORTED_REQUEST")
        if "stream_options" in request and request["stream_options"] != {"include_usage": True}:
            raise ValueError("UNSUPPORTED_STREAM_OPTIONS")
        self.body = deepcopy(request)
        self.surfaces: list[Surface] = []
        self.argument_paths = []
        self.schemas = {}
        self.allowed_tools = allowed_tools
        self._messages()
        self._tools()
        self._parameters()
        if len(self.surfaces) > 512 or sum(len(item.text) for item in self.surfaces) > 200000:
            raise ValueError("CONTENT_BOUND")

    def text(self, path, value, mutable=True):
        if not isinstance(value, str) or len(value) > 200000:
            raise ValueError("INVALID_TEXT")
        value.encode("utf-8")
        self.surfaces.append(Surface(tuple(path), value, mutable))

    def identifier(self, path, value):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ValueError("INVALID_PROTOCOL_IDENTIFIER")
        self.text(path, value, False)

    def json_content(self, path, value, depth=0):
        if depth > 24:
            raise ValueError("CONTENT_DEPTH")
        if isinstance(value, str):
            self.text(path, value)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.json_content((*path, index), item, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                # Keys are scanned; changing them would change the tool schema.
                self.text((*path, key), key, False)
                self.json_content((*path, key), item, depth + 1)

    def _messages(self):
        messages = self.body.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > 128:
            raise ValueError("INVALID_MESSAGES")
        for index, message in enumerate(messages):
            base = ("messages", index)
            if type(message) is not dict or set(message) - {
                "role", "content", "name", "tool_calls", "tool_call_id",
            }:
                raise ValueError("UNSUPPORTED_MESSAGE")
            role = message.get("role")
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                raise ValueError("UNSUPPORTED_ROLE")
            content = message.get("content")
            if isinstance(content, str):
                self.text((*base, "content"), content)
            elif isinstance(content, list):
                if not content:
                    raise ValueError("EMPTY_CONTENT")
                for block_index, block in enumerate(content):
                    if type(block) is not dict or set(block) != {"type", "text"} or block["type"] != "text":
                        raise ValueError("UNSUPPORTED_CONTENT")
                    self.text((*base, "content", block_index, "text"), block["text"])
            elif content is not None or role != "assistant" or not message.get("tool_calls"):
                raise ValueError("UNSUPPORTED_CONTENT")
            if "name" in message:
                self.identifier((*base, "name"), message["name"])
            if role == "tool":
                self.identifier((*base, "tool_call_id"), message.get("tool_call_id"))
            elif "tool_call_id" in message:
                raise ValueError("UNSUPPORTED_MESSAGE")
            if "tool_calls" in message:
                calls = message["tool_calls"]
                if role != "assistant" or not isinstance(calls, list) or not 1 <= len(calls) <= 32:
                    raise ValueError("INVALID_TOOL_CALLS")
                for call_index, call in enumerate(calls):
                    path = (*base, "tool_calls", call_index)
                    if type(call) is not dict or set(call) != {"id", "type", "function"} or call["type"] != "function":
                        raise ValueError("INVALID_TOOL_CALL")
                    self.identifier((*path, "id"), call["id"])
                    function = call["function"]
                    if type(function) is not dict or set(function) != {"name", "arguments"}:
                        raise ValueError("INVALID_TOOL_CALL")
                    self.identifier((*path, "function", "name"), function["name"])
                    if function["name"] not in self.allowed_tools:
                        raise ValueError("TOOL_NOT_ALLOWED")
                    arguments_path = (*path, "function", "arguments")
                    arguments = strict_json(function["arguments"])
                    if not isinstance(arguments, dict):
                        raise ValueError("TOOL_ARGUMENTS_NOT_OBJECT")
                    assign(self.body, arguments_path, arguments)
                    self.argument_paths.append(arguments_path)
                    self.json_content(arguments_path, arguments)

    def schema(self, path, value):
        for key, item in value.items():
            child = (*path, key)
            if key in {"description", "title"}:
                self.text(child, item)
            elif key == "properties":
                for name, definition in item.items():
                    self.text((*child, name), name, False)
                    self.schema((*child, name), definition)
            elif key == "items":
                self.schema(child, item)
            elif key == "enum":
                for index, choice in enumerate(item):
                    if isinstance(choice, str):
                        self.text((*child, index), choice, False)
            elif key == "const" and isinstance(item, str):
                self.text(child, item, False)

    def _tools(self):
        tools = self.body.get("tools", [])
        if not isinstance(tools, list) or len(tools) > 32:
            raise ValueError("INVALID_TOOLS")
        for index, tool in enumerate(tools):
            base = ("tools", index)
            if type(tool) is not dict or set(tool) != {"type", "function"} or tool["type"] != "function":
                raise ValueError("UNSUPPORTED_TOOL")
            function = tool["function"]
            if type(function) is not dict or set(function) - {"name", "description", "parameters", "strict"}:
                raise ValueError("UNSUPPORTED_TOOL")
            name = function.get("name")
            self.identifier((*base, "function", "name"), name)
            if name not in self.allowed_tools or name in self.schemas:
                raise ValueError("TOOL_NOT_ALLOWED")
            if "strict" in function and type(function["strict"]) is not bool:
                raise ValueError("INVALID_TOOL")
            parameters = function.get("parameters")
            normalise_schema(parameters)
            self.schemas[name] = deepcopy(parameters)
            self.schema((*base, "function", "parameters"), parameters)
            if "description" in function:
                self.text((*base, "function", "description"), function["description"])
        choice = self.body.get("tool_choice", "auto")
        if isinstance(choice, str):
            if choice not in {"auto", "none", "required"}:
                raise ValueError("INVALID_TOOL_CHOICE")
        elif (
            type(choice) is not dict or set(choice) != {"type", "function"}
            or choice["type"] != "function"
            or type(choice["function"]) is not dict
            or set(choice["function"]) != {"name"}
            or choice["function"]["name"] not in self.schemas
        ):
            raise ValueError("INVALID_TOOL_CHOICE")
        if not tools and "tool_choice" in self.body:
            raise ValueError("TOOL_CHOICE_WITHOUT_TOOLS")

    def _parameters(self):
        for name, low, high in (("temperature", 0, 2), ("top_p", 0, 1)):
            if name in self.body:
                value = self.body[name]
                if type(value) not in (int, float) or not low <= value <= high:
                    raise ValueError("INVALID_GENERATION_PARAMETER")
        for name in ("max_tokens", "max_completion_tokens"):
            if name in self.body and (
                type(self.body[name]) is not int or not 1 <= self.body[name] <= 128000
            ):
                raise ValueError("INVALID_TOKEN_BUDGET")
        if "max_tokens" in self.body and "max_completion_tokens" in self.body:
            raise ValueError("AMBIGUOUS_TOKEN_BUDGET")
        stop = self.body.get("stop")
        if stop is not None:
            if isinstance(stop, str):
                self.text(("stop",), stop)
            elif isinstance(stop, list) and 1 <= len(stop) <= 4:
                for index, item in enumerate(stop):
                    self.text(("stop", index), item)
            else:
                raise ValueError("INVALID_STOP")

    def final_body(self, maximum_tokens: int, model: str = "") -> dict:
        body = deepcopy(self.body)
        for path in self.argument_paths:
            parent = body
            for component in path[:-1]:
                parent = parent[component]
            parent[path[-1]] = canonical(parent[path[-1]]).decode("utf-8")
        budget = body.pop("max_tokens", body.get("max_completion_tokens", maximum_tokens))
        body["max_completion_tokens"] = min(budget, maximum_tokens)
        body["model"] = model
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        body.pop("n", None)
        return body

    def local_messages(self):
        if self.body.get("tools") or self.argument_paths or any(
            message["role"] == "tool" for message in self.body["messages"]
        ):
            raise ValueError("LOCAL_TOOLS_UNSUPPORTED")
        result = []
        for message in self.body["messages"]:
            content = message["content"]
            if isinstance(content, list):
                content = "\n".join(block["text"] for block in content)
            if "name" in message:
                raise ValueError("LOCAL_NAMED_MESSAGES_UNSUPPORTED")
            result.append({
                "role": "system" if message["role"] == "developer" else message["role"],
                "content": content,
            })
        return result
