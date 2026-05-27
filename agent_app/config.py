from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_path(name: str, default: str) -> Path:
    raw = env_str(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    static_dir: Path
    prompt_dir: Path
    visual_check_dir: Path
    log_dir: Path
    log_db_file: Path
    lmstudio_raw_log_file: Path
    preferences_file: Path
    server_host: str
    server_port: int
    lmstudio_endpoint: str
    lmstudio_model: str
    agent_name: str
    docker_container_name: str
    docker_image: str
    docker_workdir: str
    docker_command_timeout_seconds: int
    default_temperature: float
    default_top_p: float
    default_repetition_penalty: float
    default_max_tokens: int
    default_context_window: int
    default_max_steps: int
    default_enable_thinking: bool
    default_stream: bool
    lmstudio_raw_log_enabled: bool
    llm_timeout_seconds: int
    llm_stop_sequences: list[str]
    browser_executable: str
    status_timeout_seconds: int
    tool_max_output_chars: int
    tool_max_error_chars: int
    tool_observation_max_chars: int
    context_prune_ratio: float
    write_file_summary_min_chars: int
    write_file_summary_max_tokens: int
    context_summary_max_tokens: int
    event_history_limit: int

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_env_file(ROOT_DIR / ".env")
        default_max_tokens = env_int("DEFAULT_MAX_TOKENS", 32000)
        default_context_window = env_int("DEFAULT_CONTEXT_WINDOW", 100000)
        log_dir = env_path("LOG_DIR", "logs")
        log_db_name = env_str("LOG_DB_FILE", "thursday_logs.sqlite3")
        log_db_file = Path(log_db_name)
        if not log_db_file.is_absolute():
            log_db_file = log_dir / log_db_file
        raw_log_name = env_str("LMSTUDIO_RAW_LOG_FILE", "lmstudio_raw.jsonl")
        raw_log_file = Path(raw_log_name)
        if not raw_log_file.is_absolute():
            raw_log_file = log_dir / raw_log_file
        preferences_name = env_str("PREFERENCES_FILE", "dashboard_preferences.json")
        preferences_file = Path(preferences_name)
        if not preferences_file.is_absolute():
            preferences_file = log_dir / preferences_file
        return cls(
            root_dir=ROOT_DIR,
            static_dir=env_path("STATIC_DIR", "web"),
            prompt_dir=env_path("PROMPT_DIR", "prompts"),
            visual_check_dir=env_path("VISUAL_CHECK_DIR", "logs/visual_checks"),
            log_dir=log_dir,
            log_db_file=log_db_file.resolve(),
            lmstudio_raw_log_file=raw_log_file.resolve(),
            preferences_file=preferences_file.resolve(),
            server_host=env_str("SERVER_HOST", "127.0.0.1"),
            server_port=env_int("SERVER_PORT", 8787),
            lmstudio_endpoint=env_str("LMSTUDIO_ENDPOINT", "http://127.0.0.1:2134"),
            lmstudio_model=env_str("LMSTUDIO_MODEL", "qwen3.5-9b-mtp"),
            agent_name=env_str("AGENT_NAME", "thursday"),
            docker_container_name=env_str("DOCKER_CONTAINER_NAME", "Thursday"),
            docker_image=env_str("DOCKER_IMAGE", "ubuntu:24.04"),
            docker_workdir=env_str("DOCKER_WORKDIR", "/workspace"),
            docker_command_timeout_seconds=env_int("DOCKER_COMMAND_TIMEOUT_SECONDS", 120),
            default_temperature=env_float("DEFAULT_TEMPERATURE", 0.6),
            default_top_p=env_float("DEFAULT_TOP_P", 0.9),
            default_repetition_penalty=env_float("DEFAULT_REPETITION_PENALTY", 1.1),
            default_max_tokens=default_max_tokens,
            default_context_window=default_context_window,
            default_max_steps=env_int("DEFAULT_MAX_STEPS", 8),
            default_enable_thinking=env_bool("DEFAULT_ENABLE_THINKING", True),
            default_stream=env_bool("DEFAULT_STREAM", False),
            lmstudio_raw_log_enabled=env_bool("LMSTUDIO_RAW_LOG_ENABLED", True),
            llm_timeout_seconds=env_int("LLM_TIMEOUT_SECONDS", 180),
            llm_stop_sequences=env_list("LLM_STOP_SEQUENCES", ["<|im_end|>", "<|observation|>", "<|end|>"]),
            browser_executable=env_str("BROWSER_EXECUTABLE", ""),
            status_timeout_seconds=env_int("STATUS_TIMEOUT_SECONDS", 5),
            tool_max_output_chars=env_int("TOOL_MAX_OUTPUT_CHARS", default_context_window),
            tool_max_error_chars=env_int("TOOL_MAX_ERROR_CHARS", default_context_window),
            tool_observation_max_chars=env_int("TOOL_OBSERVATION_MAX_CHARS", default_context_window),
            context_prune_ratio=env_float("CONTEXT_PRUNE_RATIO", 0.9),
            write_file_summary_min_chars=env_int("WRITE_FILE_SUMMARY_MIN_CHARS", 500),
            write_file_summary_max_tokens=env_int("WRITE_FILE_SUMMARY_MAX_TOKENS", 300),
            context_summary_max_tokens=env_int("CONTEXT_SUMMARY_MAX_TOKENS", default_context_window),
            event_history_limit=env_int("EVENT_HISTORY_LIMIT", 200),
        )

    def public(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("root_dir", "static_dir", "prompt_dir", "visual_check_dir", "log_dir", "log_db_file", "lmstudio_raw_log_file", "preferences_file"):
            data[key] = str(data[key])
        data["workspace"] = {
            "type": "docker",
            "container_name": self.docker_container_name,
            "image": self.docker_image,
            "workdir": self.docker_workdir,
        }
        data["default_settings"] = {
            "endpoint": self.lmstudio_endpoint,
            "model": self.lmstudio_model,
            "temperature": self.default_temperature,
            "top_p": self.default_top_p,
            "repetition_penalty": self.default_repetition_penalty,
            "max_tokens": self.default_max_tokens,
            "context_window": self.default_context_window,
            "max_steps": self.default_max_steps,
            "enable_thinking": self.default_enable_thinking,
            "stream": self.default_stream,
        }
        data["lmstudio"] = {
            "raw_logging": {
                "enabled": self.lmstudio_raw_log_enabled,
                "storage": "sqlite",
                "db_file": str(self.log_db_file),
                "legacy_import_file": str(self.lmstudio_raw_log_file),
            },
        }
        return data


CONFIG = AppConfig.from_env()
