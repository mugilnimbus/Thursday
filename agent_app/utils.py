from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(value: str) -> int:
    # LM Studio raw logs for tool-heavy JSON conversations trend close to
    # two characters per token. Use the conservative estimate so context
    # pruning starts before the model is already out of headroom.
    return max(1, len(value) // 2)


def clamp_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def safe_join(base: Path, requested: str) -> Path:
    candidate = (base / requested).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValueError("Path escapes the agent workspace")
    return candidate


def parse_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if raw_args is None:
        return {}
    try:
        return json.loads(raw_args)
    except json.JSONDecodeError:
        return {}
