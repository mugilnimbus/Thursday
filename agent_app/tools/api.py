from __future__ import annotations

import time
from typing import Any


TOOL_API_VERSION = "2026-06-02"


def input_envelope_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "input": input_schema or {"type": "object", "properties": {}},
            "meta": {
                "type": "object",
                "description": "Optional caller metadata. Usually omit this.",
                "properties": {},
                "additionalProperties": True,
            },
        },
        "required": ["input"],
        "additionalProperties": False,
    }


def output_envelope_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "tool": {"type": "string"},
            "output": {"type": "object"},
            "error": {
                "type": ["object", "null"],
                "properties": {
                    "message": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
            "meta": {"type": "object"},
        },
        "required": ["ok", "tool", "output", "error", "meta"],
    }


def parse_input_envelope(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(args.get("input"), dict):
        meta = args.get("meta") if isinstance(args.get("meta"), dict) else {}
        return args["input"], meta
    return args, {}


def tool_definition_envelope(definition: dict[str, Any]) -> dict[str, Any]:
    function = definition.get("function", {}) if isinstance(definition.get("function"), dict) else {}
    input_schema = function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object", "properties": {}}
    description = str(function.get("description") or "").strip()
    output_schema_text = (
        "Tool call format: pass arguments as {\"input\": {...}}. "
        "Tool result format is always {ok, tool, output, error, meta}. "
        "Read output for successful data and error.message for failures."
    )
    return {
        "type": "function",
        "function": {
            "name": function.get("name"),
            "description": f"{description}\n\n{output_schema_text}".strip(),
            "parameters": input_envelope_schema(input_schema),
        },
    }


def started_timer() -> float:
    return time.perf_counter()


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def success_response(
    tool_name: str,
    output: dict[str, Any],
    input_args: dict[str, Any],
    duration_ms: float,
    category: str,
    requires_container: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": tool_name,
        "output": output,
        "error": None,
        "meta": response_meta(input_args, duration_ms, category, requires_container),
    }


def error_response(
    tool_name: str,
    message: str,
    input_args: dict[str, Any] | None = None,
    duration_ms: float = 0,
    category: str = "tool",
    requires_container: bool = False,
    error_type: str = "ToolError",
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool_name,
        "output": output or {},
        "error": {"message": message, "type": error_type},
        "meta": response_meta(input_args or {}, duration_ms, category, requires_container),
    }


def response_meta(input_args: dict[str, Any], duration_ms: float, category: str, requires_container: bool) -> dict[str, Any]:
    return {
        "api_version": TOOL_API_VERSION,
        "input": input_args,
        "duration_ms": duration_ms,
        "category": category,
        "requires_container": requires_container,
    }


def normalize_legacy_result(
    tool_name: str,
    result: dict[str, Any],
    input_args: dict[str, Any],
    duration_ms: float,
    category: str,
    requires_container: bool,
) -> dict[str, Any]:
    if is_response_envelope(result):
        return result

    ok = bool(result.get("ok", True))
    output = {key: value for key, value in result.items() if key not in {"ok", "error"}}
    error_value = result.get("error")
    if ok:
        return success_response(tool_name, output, input_args, duration_ms, category, requires_container)
    return error_response(
        tool_name=tool_name,
        message=str(error_value or "Tool failed."),
        input_args=input_args,
        duration_ms=duration_ms,
        category=category,
        requires_container=requires_container,
        output=output,
    )


def is_response_envelope(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("tool"), str)
        and "output" in result
        and "error" in result
        and "meta" in result
    )
