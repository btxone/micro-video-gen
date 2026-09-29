from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    enhance_references_enabled: bool = False
    auto_master_selection_enabled: bool = False
    video_qc_enabled: bool = False
    pipeline_version: str = "1.0.0"
    pipeline_mode: str = "real"

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

