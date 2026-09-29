from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


MAX_REFERENCE_IMAGES = 4


class DishMetadata(BaseModel):
    """Metadata obligatoria de la carta, tratada como contexto y no como instrucción."""

    title_plate: str = Field(min_length=2, max_length=120)
    description_plate: str = Field(min_length=10, max_length=1000)

    @field_validator("title_plate", "description_plate", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("debe ser texto")
        if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise ValueError("contiene caracteres de control no permitidos")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("no puede estar vacío")
        return normalized


class HealthResponse(BaseModel):
    status: str
    image_to_image_only: bool
    vision_model: str
    video_mode: str
    max_reference_images: int


class GenerateVideoResponse(BaseModel):
    job_id: str
    status: str
    title_plate: str
    description_plate: str
    menu_context_en: str
    menu_image_alignment: str
    menu_visual_conflicts: list[str]
    original_image_url: str
    enhanced_image_url: str
    reference_image_urls: list[str]
    reference_count: int
    video_mode: str
    video_url: str
    analysis: dict[str, Any]
    integrity_check: dict[str, Any]
    h3_prompt: str
    artifacts_dir: str


class StageResponse(BaseModel):
    stage: str
    status: str
    attempt: int
    provider_job_id: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ArtifactResponse(BaseModel):
    id: str
    kind: str
    url: str
    mime_type: str
    byte_size: int
    sha256: str
    approved: bool


class JobResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str
    title_plate: str
    description_plate: str
    pipeline_version: str
    reference_count: int
    error: str | None = None
    created_at: str
    updated_at: str
    stages: list[StageResponse] = Field(default_factory=list)
    artifacts: list[ArtifactResponse] = Field(default_factory=list)

