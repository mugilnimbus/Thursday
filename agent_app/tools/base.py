from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .context import ToolContext


ToolRunner = Callable[["ToolContext", dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    definition: dict[str, Any]
    runner: ToolRunner
    requires_container: bool = False
    order: int = 1000
