from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lmstudio_client import LMStudioChatClient

__all__ = ["LMStudioChatClient"]


def __getattr__(name: str):
    if name == "LMStudioChatClient":
        from .lmstudio_client import LMStudioChatClient

        return LMStudioChatClient
    raise AttributeError(name)
