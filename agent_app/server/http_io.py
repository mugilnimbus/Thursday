from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler
from typing import Any


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def read_multipart_image(handler: BaseHTTPRequestHandler) -> tuple[bytes, str, str]:
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=default).parsebytes(header + raw)
    if not message.is_multipart():
        raise ValueError("Expected multipart image upload.")
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "image":
            continue
        filename = part.get_filename() or "image"
        mime_type = part.get_content_type()
        data = part.get_payload(decode=True) or b""
        return data, filename, mime_type
    raise ValueError("Missing multipart image field named 'image'.")
