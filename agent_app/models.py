from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import AppConfig, CONFIG
from .utils import utc_now


@dataclass
class AgentSettings:
    endpoint: str = CONFIG.lmstudio_endpoint
    model: str = CONFIG.lmstudio_model
    temperature: float = CONFIG.default_temperature
    top_p: float = CONFIG.default_top_p
    repetition_penalty: float = CONFIG.default_repetition_penalty
    max_tokens: int = CONFIG.default_max_tokens
    context_window: int = CONFIG.default_context_window
    max_steps: int = CONFIG.default_max_steps
    enable_thinking: bool = CONFIG.default_enable_thinking
    stream: bool = CONFIG.default_stream
    enabled_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: AppConfig) -> "AgentSettings":
        return cls(
            endpoint=config.lmstudio_endpoint,
            model=config.lmstudio_model,
            temperature=config.default_temperature,
            top_p=config.default_top_p,
            repetition_penalty=config.default_repetition_penalty,
            max_tokens=config.default_max_tokens,
            context_window=config.default_context_window,
            max_steps=config.default_max_steps,
            enable_thinking=config.default_enable_thinking,
            stream=config.default_stream,
        )


@dataclass
class Event:
    type: str
    message: str
    timestamp: str = field(default_factory=utc_now)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    id: str
    title: str
    settings: AgentSettings
    event_history_limit: int
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    status: str = "idle"
    messages: list[dict[str, Any]] = field(default_factory=list)
    visible_messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    summary: str = ""
    tool_counts: dict[str, int] = field(default_factory=dict)
    modified_files: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)
    current_goal: str = ""
    token_estimate: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add_event(self, event_type: str, message: str, **data: Any) -> None:
        self.events.append(Event(event_type, message, data=data))
        self.events = self.events[-self.event_history_limit :]
        self.updated_at = utc_now()

    def to_public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "title": self.title,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "status": self.status,
                "settings": asdict(self.settings),
                "visible_messages": self.visible_messages,
                "events": [asdict(event) for event in self.events],
                "summary": self.summary,
                "tool_counts": self.tool_counts,
                "modified_files": self.modified_files,
                "recent_errors": self.recent_errors[-8:],
                "current_goal": self.current_goal,
                "token_estimate": self.token_estimate,
                "context_usage": min(1.0, self.token_estimate / max(1, self.settings.context_window)),
            }

    def to_trace(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "title": self.title,
                "status": self.status,
                "updated_at": self.updated_at,
                "token_estimate": self.token_estimate,
                "context_window": self.settings.context_window,
                "messages": self.messages,
                "message_count": len(self.messages),
            }
