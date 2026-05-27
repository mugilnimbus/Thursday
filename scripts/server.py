from __future__ import annotations

import shutil
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent_app.config import CONFIG
from agent_app.http_app import create_server
from agent_app.sqlite_logging import SQLiteLogHandler, import_raw_lmstudio_jsonl
from agent_app.state import AppState


def configure_logging() -> None:
    CONFIG.log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(message)s")
    app_handler = SQLiteLogHandler(CONFIG.log_db_file, "app")
    app_handler.setFormatter(formatter)
    http_handler = SQLiteLogHandler(CONFIG.log_db_file, "http")
    http_handler.setFormatter(formatter)
    server_handler = SQLiteLogHandler(CONFIG.log_db_file, "server")
    server_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(app_handler)

    http_logger = logging.getLogger("agent.http")
    http_logger.handlers.clear()
    http_logger.addHandler(http_handler)
    http_logger.propagate = False
    http_logger.setLevel(logging.INFO)

    server_logger = logging.getLogger("agent.server")
    server_logger.handlers.clear()
    server_logger.addHandler(server_handler)
    server_logger.propagate = False
    server_logger.setLevel(logging.INFO)


def main() -> None:
    configure_logging()
    logger = logging.getLogger("agent.server")
    if not shutil.which("docker"):
        logger.warning("Docker executable not found. Agent tools require Docker.")
    logger.info("Agent dashboard: http://%s:%s", CONFIG.server_host, CONFIG.server_port)
    logger.info("LM Studio endpoint: %s", CONFIG.lmstudio_endpoint)
    logger.info("Workspace: docker://%s%s", CONFIG.docker_container_name, CONFIG.docker_workdir)
    logger.info("SQLite logs: %s", CONFIG.log_db_file)
    imported_raw = import_raw_lmstudio_jsonl(CONFIG.log_db_file, CONFIG.lmstudio_raw_log_file)
    if imported_raw:
        logger.info("Imported %s legacy raw LM Studio records into SQLite.", imported_raw)
    print(f"Agent dashboard: http://{CONFIG.server_host}:{CONFIG.server_port}", flush=True)
    print(f"SQLite logs: {CONFIG.log_db_file}", flush=True)

    state = AppState(CONFIG)
    server = create_server(CONFIG, state)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down after KeyboardInterrupt.")
        print("\nShutting down.", flush=True)
    finally:
        state.shutdown()
        server.server_close()
        logger.info("Server closed.")


if __name__ == "__main__":
    main()
