from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class SQLiteLogHandler(logging.Handler):
    _lock = threading.Lock()

    def __init__(self, db_path: Path, source: str) -> None:
        super().__init__()
        self.db_path = db_path
        self.source = source
        self._ensure_schema()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            payload = {
                "pathname": record.pathname,
                "lineno": record.lineno,
                "funcName": record.funcName,
                "process": record.process,
                "thread": record.thread,
                "threadName": record.threadName,
            }
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                payload["exception"] = formatter.formatException(record.exc_info)
            if record.stack_info:
                payload["stack"] = record.stack_info

            created_at = datetime.fromtimestamp(record.created, timezone.utc).isoformat()
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    conn.execute(
                        """
                        INSERT INTO logs (
                            created_at, level, logger, source, message, module,
                            pathname, line_no, function_name, process_id, thread_id, extra_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            created_at,
                            record.levelname,
                            record.name,
                            self.source,
                            message,
                            record.module,
                            record.pathname,
                            record.lineno,
                            record.funcName,
                            record.process,
                            record.thread,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
        except Exception:
            self.handleError(record)

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        level TEXT NOT NULL,
                        logger TEXT NOT NULL,
                        source TEXT NOT NULL,
                        message TEXT NOT NULL,
                        module TEXT,
                        pathname TEXT,
                        line_no INTEGER,
                        function_name TEXT,
                        process_id INTEGER,
                        thread_id INTEGER,
                        extra_json TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source)")
