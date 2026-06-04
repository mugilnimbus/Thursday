from __future__ import annotations

from http.server import ThreadingHTTPServer

from ..runtime.config import AppConfig
from ..runtime import AppState
from .handler import make_handler


def create_server(config: AppConfig, state: AppState) -> ThreadingHTTPServer:
    handler = make_handler(config, state)
    return ThreadingHTTPServer((config.server_host, config.server_port), handler)
