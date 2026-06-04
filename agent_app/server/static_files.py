from __future__ import annotations

import mimetypes
from http.server import BaseHTTPRequestHandler

from ..runtime.config import AppConfig


def serve_static(handler: BaseHTTPRequestHandler, config: AppConfig, path: str) -> None:
    if path == "/":
        target = config.static_dir / "index.html"
    else:
        target = (config.static_dir / path.lstrip("/")).resolve()
        if config.static_dir not in target.parents and target != config.static_dir:
            handler.send_error(403)
            return
    if not target.exists() or not target.is_file():
        handler.send_error(404)
        return
    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
