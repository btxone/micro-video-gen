from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError


ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def inspect_image(path: Path, declared_mime: str | None = None) -> dict[str, object]:
    if declared_mime not in ALLOWED_MIME_TYPES:
        raise ValueError("La imagen debe ser JPG, PNG o WebP")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            detected = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("El archivo no contiene una imagen válida") from error
    if detected != declared_mime:
        raise ValueError("El contenido de la imagen no coincide con su tipo declarado")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"mime_type": detected, "width": width, "height": height, "sha256": digest}
