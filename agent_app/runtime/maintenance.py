from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..logging import clear_log_tables
from .config import AppConfig


def clear_runtime_logs(config: AppConfig) -> dict[str, Any]:
    result = clear_log_tables(config.log_db_file)
    artifacts = {
        "visual_checks": clear_directory_contents(config.visual_check_dir),
        "server_log": truncate_file(config.log_dir / "server.log"),
        "server_error_log": truncate_file(config.log_dir / "server.err.log"),
        "legacy_raw_log": truncate_file(config.lmstudio_raw_log_file),
    }
    return {**result, "artifacts": artifacts}


def clear_directory_contents(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    removed = 0
    errors: list[str] = []
    for child in path.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except Exception as exc:
            errors.append(f"{child}: {exc}")
    return {"path": str(path), "removed": removed, "errors": errors}


def truncate_file(path: Path) -> dict[str, Any]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return {"path": str(path), "ok": True}
    except Exception as exc:
        return {"path": str(path), "ok": False, "error": str(exc)}
