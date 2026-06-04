from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .config import AppConfig
from .models import AgentSettings


def with_model_context_settings(
    config: AppConfig,
    llm: Any,
    settings: dict[str, Any],
) -> dict[str, Any]:
    patched = dict(settings or {})
    endpoint = str(patched.get("endpoint") or config.lmstudio_endpoint)
    model = str(patched.get("model") or config.lmstudio_model)
    fallback = safe_int(patched.get("context_window"), config.default_context_window)
    context = llm.model_context_window(endpoint, model, fallback)
    patched["context_window"] = int(context["context_window"])
    patched["max_tokens"] = min(
        safe_int(patched.get("max_tokens"), config.default_max_tokens),
        int(context["context_window"]),
    )
    return patched


def apply_model_context_to_agent_settings(config: AppConfig, llm: Any, settings: AgentSettings) -> None:
    context_settings = with_model_context_settings(config, llm, asdict(settings))
    settings.context_window = int(context_settings["context_window"])
    settings.max_tokens = int(context_settings["max_tokens"])


def safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)
