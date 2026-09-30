from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
SUFFIX_MIME_TYPES = {suffix: mime for mime, suffix in MIME_SUFFIXES.items()}


def inspect_image(
    path: Path,
    declared_mime: str | None = None,
    *,
    max_pixels: int = 40_000_000,
) -> dict[str, object]:
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
    if width < 1 or height < 1 or width * height > max_pixels:
        raise ValueError(f"La imagen supera el límite de {max_pixels:,} píxeles")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"mime_type": detected, "width": width, "height": height, "sha256": digest}


def normalize_provider_image(
    source: Path,
    destination: Path,
    *,
    max_edge: int = 2048,
    jpeg_quality: int = 88,
    max_pixels: int = 40_000_000,
) -> dict[str, object]:
    """Create a bounded, orientation-correct provider copy without touching the upload."""
    if max_edge < 256 or not 1 <= jpeg_quality <= 95:
        raise ValueError("Parámetros de normalización de imagen inválidos")
    declared_mime = SUFFIX_MIME_TYPES.get(source.suffix.lower())
    if declared_mime is None:
        raise ValueError("La imagen debe ser JPG, PNG o WebP")
    inspect_image(source, declared_mime, max_pixels=max_pixels)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            # Saving a newly-created RGB image deliberately strips EXIF/GPS metadata.
            normalized = image.convert("RGB")
            normalized.save(
                destination,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
                subsampling=0,
            )
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("No se pudo normalizar la imagen para los proveedores") from error
    return inspect_image(destination, "image/jpeg", max_pixels=max_pixels)
