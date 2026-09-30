from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Configuración centralizada; las claves se leen desde el entorno o .env."""

    gemini_api_key: str = ""
    runpod_api_key: str = ""
    gemini_vision_model: str = "gemini-3.5-flash-lite"
    image_edit_endpoint: str = "google-nano-banana-2-edit"
    video_endpoint: str = "zp5wbt7171lxfg"
    image_edit_resolution: str = "1k"
    image_edit_output_format: str = "png"
    image_edit_aspect_ratio: str = "auto"
    image_edit_timeout_seconds: int = 900
    video_timeout_seconds: int = 2400
    max_upload_mb: int = 20
    max_image_pixels: int = 40_000_000
    provider_image_max_edge: int = 2048
    provider_image_jpeg_quality: int = 88
    gemini_inline_request_budget_mb: int = 12
    outputs_dir: Path = PROJECT_ROOT / "outputs"
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'outputs' / 'h3_api.db').as_posix()}"
    redis_url: str = "redis://localhost:6379/0"
    celery_enabled: bool = False
    storage_backend: str = "local"
    storage_dir: Path = PROJECT_ROOT / "outputs"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    async_jobs_enabled: bool = True
    enhance_references_enabled: bool = True
    auto_master_selection_enabled: bool = False
    video_qc_enabled: bool = True
    image_qc_min_confidence: float = 0.75
    video_qc_min_confidence: float = 0.70
    image_max_attempts: int = 2
    video_max_attempts: int = 2
    video_duration_seconds: float = 5.0
    pipeline_version: str = "1.2.0"
    pipeline_mode: str = "real"

    @field_validator("max_upload_mb")
    @classmethod
    def validate_upload_limit(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("MAX_UPLOAD_MB debe estar entre 1 y 100")
        return value

    @field_validator("max_image_pixels")
    @classmethod
    def validate_pixel_limit(cls, value: int) -> int:
        if not 1_000_000 <= value <= 100_000_000:
            raise ValueError("MAX_IMAGE_PIXELS debe estar entre 1 y 100 millones")
        return value

    @field_validator("provider_image_max_edge")
    @classmethod
    def validate_provider_image_edge(cls, value: int) -> int:
        if not 256 <= value <= 4096:
            raise ValueError("PROVIDER_IMAGE_MAX_EDGE debe estar entre 256 y 4096")
        return value

    @field_validator("provider_image_jpeg_quality")
    @classmethod
    def validate_provider_image_quality(cls, value: int) -> int:
        if not 1 <= value <= 95:
            raise ValueError("PROVIDER_IMAGE_JPEG_QUALITY debe estar entre 1 y 95")
        return value

    @field_validator("gemini_inline_request_budget_mb")
    @classmethod
    def validate_gemini_inline_budget(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("GEMINI_INLINE_REQUEST_BUDGET_MB debe estar entre 1 y 16")
        return value

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def validate_runtime_credentials(self) -> None:
        missing = []
        if not self.gemini_api_key.strip():
            missing.append("GEMINI_API_KEY")
        if not self.runpod_api_key.strip():
            missing.append("RUNPOD_API_KEY")
        if missing:
            raise RuntimeError("Faltan credenciales: " + ", ".join(missing))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

