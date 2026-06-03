from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ..utils import parse_arguments
from .api import parse_input_envelope
from .registry import ToolRegistry


@dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    args: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ToolExecution:
    call: ParsedToolCall
    result: dict[str, Any]


class ToolCallDispatcher:
    """Parses raw model tool calls and routes them to the registered tool."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def dispatch(self, raw_call: dict[str, Any], enabled_tools: list[str] | None = None) -> ToolExecution:
        call = self.parse(raw_call)
        return self.dispatch_parsed(call, enabled_tools)

    def dispatch_parsed(self, call: ParsedToolCall, enabled_tools: list[str] | None = None) -> ToolExecution:
        result = self.registry.invoke(call.name, call.args, enabled_tools)
        return ToolExecution(call=call, result=result)

    def parse(self, raw_call: dict[str, Any]) -> ParsedToolCall:
        function = raw_call.get("function", {}) if isinstance(raw_call.get("function"), dict) else {}
        name = str(function.get("name") or raw_call.get("name") or "")
        raw_args = function.get("arguments", "{}") or raw_call.get("arguments", "{}")
        parsed_args = parse_arguments(raw_args)
        input_args, _meta = parse_input_envelope(parsed_args)
        return ParsedToolCall(
            id=str(raw_call.get("id") or f"call_{uuid.uuid4().hex[:10]}"),
            name=name,
            args=input_args,
            raw=raw_call,
        )
