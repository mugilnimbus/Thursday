from __future__ import annotations

import base64
import binascii
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.config import AppConfig


ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class StoredImage:
    name: str
    mime_type: str
    size: int
    path: str
    url: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mime_type": self.mime_type,
            "size": self.size,
            "path": self.path,
            "url": self.url,
        }


class ImageStore:
    """Persists image bytes and returns path-only metadata for sessions."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.root = config.image_upload_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_uploaded_image(self, data: bytes, name: str, mime_type: str) -> StoredImage:
        mime_type = normalize_mime_type(mime_type, name)
        if mime_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError("Only JPEG, PNG, and WebP images are supported.")
        suffix = ALLOWED_IMAGE_TYPES[mime_type]
        safe_stem = safe_name(Path(name or "image").stem) or "image"
        path = (self.root / f"{uuid.uuid4().hex}_{safe_stem}{suffix}").resolve()
        if self.root not in path.parents:
            raise ValueError("Image path escaped upload directory.")
        path.write_bytes(data)
        return StoredImage(
            name=name or path.name,
            mime_type=mime_type,
            size=len(data),
            path=str(path),
            url=image_url(path),
        )

    def save_data_url_image(self, data_url: str, name: str = "image") -> StoredImage:
        header, separator, encoded = str(data_url or "").partition(",")
        if separator != "," or not header.startswith("data:image/"):
            raise ValueError("Expected an image data URL.")
        mime_type = header.removeprefix("data:").split(";", 1)[0]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image data URL could not be decoded.") from exc
        return self.save_uploaded_image(data, name, mime_type)

    def normalize_payload_images(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for image in images[:6]:
            if not isinstance(image, dict):
                continue
            path = str(image.get("path") or "")
            if path:
                resolved = Path(path).resolve()
                if not resolved.exists() or self.root not in resolved.parents:
                    continue
                normalized.append(
                    {
                        "name": str(image.get("name") or resolved.name or "image"),
                        "mime_type": normalize_mime_type(str(image.get("mime_type") or ""), resolved.name),
                        "size": int(image.get("size") or 0),
                        "path": str(resolved),
                        "url": str(image.get("url") or image_url(resolved)),
                    }
                )
                continue
            data_url = str(image.get("data_url") or "")
            if data_url.startswith("data:image/"):
                normalized.append(self.save_data_url_image(data_url, str(image.get("name") or "image")).public())
        return normalized


def image_url(path: str | Path) -> str:
    return f"/api/images?path={quote(str(Path(path).resolve()), safe='')}"


def normalize_mime_type(mime_type: str, name: str = "") -> str:
    lowered = str(mime_type or "").split(";", 1)[0].strip().lower()
    if lowered in ALLOWED_IMAGE_TYPES:
        return lowered
    guessed = mimetypes.guess_type(str(name))[0] or ""
    guessed = guessed.split(";", 1)[0].strip().lower()
    if guessed == "image/jpg":
        return "image/jpeg"
    return guessed if guessed in ALLOWED_IMAGE_TYPES else "image/png"


def safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return cleaned.strip("._")[:60]


def resolve_served_image(config: AppConfig, path_value: str) -> tuple[Path, str]:
    path = Path(path_value).resolve()
    allowed_roots = [config.image_upload_dir.resolve(), config.visual_check_dir.resolve()]
    if not any(root == path or root in path.parents for root in allowed_roots):
        raise PermissionError("Image is outside served image directories.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Image not found.")
    mime_type = normalize_mime_type("", path.name)
    return path, mime_type

