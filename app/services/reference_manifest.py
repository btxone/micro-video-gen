from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.http_client import write_json
from app.schemas import MAX_REFERENCE_IMAGES
from app.services.input_validation import inspect_image, normalize_provider_image


@dataclass
class ReferenceImage:
    """One input image and all provider-facing derivatives, with stable identity/order."""

    picture_number: int
    role: str
    original_path: Path
    provider_path: Path
    reference_index: int | None = None
    enhanced_path: Path | None = None
    description: str | None = None
    original_sha256: str = ""
    provider_sha256: str = ""
    enhanced_sha256: str | None = None
    provider_bytes: int = 0
    original_bytes: int = 0
    original_dimensions: list[int] = field(default_factory=list)
    provider_dimensions: list[int] = field(default_factory=list)
    enhanced_bytes: int | None = None
    integrity: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["original_path"] = Path(value["original_path"]).name
        value["provider_path"] = (
            f"provider_inputs/{Path(value['provider_path']).name}"
        )
        value["enhanced_path"] = (
            Path(value["enhanced_path"]).name if value["enhanced_path"] is not None else None
        )
        return value


@dataclass
class ReferenceManifest:
    job_id: str
    pipeline_version: str
    images: list[ReferenceImage] = field(default_factory=list)

    @property
    def primary(self) -> ReferenceImage:
        return self.images[0]

    @property
    def references(self) -> list[ReferenceImage]:
        return self.images[1:]

    def validate(self, *, require_enhanced: bool = False) -> None:
        if not 1 <= len(self.images) <= MAX_REFERENCE_IMAGES + 1:
            raise ValueError("El manifest debe contener la imagen principal y hasta cuatro referencias")
        if [image.picture_number for image in self.images] != list(range(1, len(self.images) + 1)):
            raise ValueError("El orden de Picture 1..5 en el manifest no es contiguo")
        if self.primary.role != "primary" or self.primary.reference_index is not None:
            raise ValueError("Picture 1 debe ser la imagen principal")
        if [image.reference_index for image in self.references] != list(
            range(1, len(self.images))
        ):
            raise ValueError("Las referencias deben conservar sus índices de carga del 1 al 4")
        if require_enhanced and any(image.enhanced_path is None for image in self.images):
            raise ValueError("Falta una imagen publicitaria aprobada en el manifest")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "manifest_version": 1,
            "job_id": self.job_id,
            "pipeline_version": self.pipeline_version,
            "picture_count": len(self.images),
            "reference_count": len(self.references),
            "images": [image.as_dict() for image in self.images],
        }

    def save(self, path: Path) -> None:
        write_json(path, self.to_dict())


def create_reference_manifest(
    *,
    primary_path: Path,
    reference_paths: list[Path],
    run_dir: Path,
    settings: Settings,
    job_id: str,
) -> ReferenceManifest:
    if len(reference_paths) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"Se permiten como máximo {MAX_REFERENCE_IMAGES} imágenes de referencia")
    provider_dir = run_dir / "provider_inputs"
    sources = [primary_path, *reference_paths]
    images: list[ReferenceImage] = []
    for picture_number, source in enumerate(sources, start=1):
        input_mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(source.suffix.lower())
        if input_mime is None:
            raise ValueError(f"Formato no permitido para Picture {picture_number}")
        original_info = inspect_image(
            source,
            input_mime,
            max_pixels=settings.max_image_pixels,
        )
        provider_path = provider_dir / f"picture_{picture_number}.jpg"
        provider_info = normalize_provider_image(
            source,
            provider_path,
            max_edge=settings.provider_image_max_edge,
            jpeg_quality=settings.provider_image_jpeg_quality,
            max_pixels=settings.max_image_pixels,
        )
        is_primary = picture_number == 1
        images.append(
            ReferenceImage(
                picture_number=picture_number,
                role="primary" if is_primary else "supplementary_reference",
                original_path=source,
                provider_path=provider_path,
                reference_index=None if is_primary else picture_number - 1,
                original_sha256=str(original_info["sha256"]),
                provider_sha256=str(provider_info["sha256"]),
                provider_bytes=provider_path.stat().st_size,
                original_bytes=source.stat().st_size,
                original_dimensions=[int(original_info["width"]), int(original_info["height"])],
                provider_dimensions=[int(provider_info["width"]), int(provider_info["height"])],
            )
        )
    manifest = ReferenceManifest(
        job_id=job_id,
        pipeline_version=settings.pipeline_version,
        images=images,
    )
    manifest.validate()
    manifest.save(run_dir / "reference_manifest.json")
    return manifest
