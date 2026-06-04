from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app_state import AppState

__all__ = ["AppState"]


def __getattr__(name: str):
    if name == "AppState":
        from .app_state import AppState

        return AppState
    raise AttributeError(name)
