from __future__ import annotations

from pathlib import Path


def safe_join(base: Path, requested: str) -> Path:
    candidate = (base / requested).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValueError("Path escapes the agent workspace")
    return candidate
