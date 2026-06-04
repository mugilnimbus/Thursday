from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]


def __getattr__(name: str):
    if name == "AgentOrchestrator":
        from .orchestrator import AgentOrchestrator

        return AgentOrchestrator
    raise AttributeError(name)
