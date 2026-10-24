"""Strict JSON parsing and the supported structured-output schema subset.

No repair, markdown stripping, substring extraction or model fallback.
Unsupported JSON Schema keywords are rejected before inference.
"""

from __future__ import annotations

import json
import math
from typing import Any

from . import RuntimeFailure

KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "minLength", "maxLength", "minItems", "maxItems",
    "minimum", "maximum", "description", "title",
}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def strict_loads(text: str | bytes) -> Any:
    try:
        return json.loads(
            text, object_pairs_hook=_pairs, parse_constant=_constant,
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise RuntimeFailure("local JSON output malformed") from None


def _check_schema(schema: Any, depth: int = 0) -> None:
    if (
        depth > 32 or not isinstance(schema, dict)
        or set(schema) - KEYS or schema.get("type") not in TYPES
    ):
        raise ValueError("unsupported local JSON schema")
    typ = schema["type"]
    if "properties" in schema:
        if typ != "object" or not isinstance(schema["properties"], dict):
            raise ValueError("invalid schema properties")
        for key, child in schema["properties"].items():
            if not isinstance(key, str):
                raise ValueError("invalid schema property")
            _check_schema(child, depth + 1)
    if "required" in schema:
        required = schema["required"]
        if (
            typ != "object" or not isinstance(required, list)
            or not all(isinstance(key, str) for key in required)
            or len(set(required)) != len(required)
            or not set(required) <= set(schema.get("properties", {}))
        ):
            raise ValueError("invalid schema required")
    if "additionalProperties" in schema:
        if typ != "object" or type(schema["additionalProperties"]) is not bool:
            raise ValueError("unsupported additionalProperties schema")
    if typ == "array":
        if "items" not in schema:
            raise ValueError("array schema needs items")
        _check_schema(schema["items"], depth + 1)
    elif "items" in schema:
        raise ValueError("invalid schema items")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            type(schema[key]) is not int or schema[key] < 0
        ):
            raise ValueError("invalid schema length")
    for key in ("minimum", "maximum"):
        if key in schema and (
            type(schema[key]) not in (int, float)
            or not math.isfinite(schema[key])
        ):
            raise ValueError("invalid schema numeric bound")
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not schema["enum"]
    ):
        raise ValueError("invalid schema enum")


def normalise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Accept a bare object schema or {name, strict, schema} wrapper."""
    if "schema" in schema:
        if set(schema) - {"name", "strict", "schema"}:
            raise ValueError("unsupported JSON schema wrapper")
        if schema.get("strict", True) is not True:
            raise ValueError("local JSON schema must be strict")
        wrapper = dict(schema)
        wrapper.setdefault("name", "local_output")
        wrapper["strict"] = True
    else:
        wrapper = {"name": "local_output", "strict": True, "schema": schema}
    if (
        not isinstance(wrapper["name"], str)
        or not wrapper["name"]
        or len(wrapper["name"]) > 64
    ):
        raise ValueError("invalid schema name")
    _check_schema(wrapper["schema"])
    if wrapper["schema"]["type"] != "object":
        raise ValueError("chat_json requires an object schema")
    return wrapper


def validate_output(value: Any, schema: dict[str, Any], depth: int = 0) -> None:
    if depth > 32:
        raise RuntimeFailure("local JSON output exceeds nesting bound")
    typ = schema["type"]
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }[typ]
    if not valid:
        raise RuntimeFailure("local JSON output violates schema")
    if typ == "number" and not math.isfinite(value):
        raise RuntimeFailure("local JSON output violates schema")
    if "enum" in schema and value not in schema["enum"]:
        raise RuntimeFailure("local JSON output violates schema")
    if "const" in schema and value != schema["const"]:
        raise RuntimeFailure("local JSON output violates schema")
    if typ == "object":
        props = schema.get("properties", {})
        if (
            not set(schema.get("required", [])) <= set(value)
            or (
                schema.get("additionalProperties", True) is False
                and set(value) - set(props)
            )
        ):
            raise RuntimeFailure("local JSON output violates schema")
        for key, child in props.items():
            if key in value:
                validate_output(value[key], child, depth + 1)
    elif typ == "array":
        for item in value:
            validate_output(item, schema["items"], depth + 1)
    if typ in ("string", "array"):
        low, high = (
            ("minLength", "maxLength") if typ == "string"
            else ("minItems", "maxItems")
        )
        if (
            len(value) < schema.get(low, 0)
            or len(value) > schema.get(high, math.inf)
        ):
            raise RuntimeFailure("local JSON output violates schema")
    if typ in ("number", "integer"):
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise RuntimeFailure("local JSON output violates schema")
