from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any


def request_json(
    url: str,
    *,
    api_key: str,
    provider: str,
    payload: dict[str, Any] | None = None,
    auth_header_name: str = "Authorization",
    timeout: int = 90,
    retries: int = 2,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    auth_value = f"Bearer {api_key}" if auth_header_name == "Authorization" else api_key
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            auth_header_name: auth_value,
            "Content-Type": "application/json",
            "User-Agent": "h3-image-to-video-fastapi/1.0",
        },
        method="GET" if payload is None else "POST",
    )

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(min(60, 3 * (2**attempt)))
                continue
            raise RuntimeError(f"{provider} respondió HTTP {error.code}: {detail[:2000]}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < retries:
                time.sleep(min(60, 3 * (2**attempt)))
                continue
            raise RuntimeError(f"No se pudo conectar con {provider}: {error}") from error

    raise RuntimeError(f"No se recibió respuesta de {provider}")


def write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def image_to_data_url(image_path: pathlib.Path) -> str:
    if not image_path.is_file():
        raise ValueError(f"No encuentro la imagen: {image_path}")
    mime_type = mimetypes.guess_type(image_path.name)[0]
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("La imagen debe ser JPG, PNG o WebP")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def decode_base64_image(value: str) -> bytes | None:
    if not isinstance(value, str) or not value.strip():
        return None
    encoded = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return data
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data
    return None


def download_image(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read()
    if not (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        raise RuntimeError("La respuesta descargada no parece una imagen PNG, JPG o WebP")
    return data

