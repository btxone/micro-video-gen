from app.config import Settings
from app.storage.base import ArtifactStorage
from app.storage.local import LocalArtifactStorage
from app.storage.s3 import S3ArtifactStorage


def build_storage(settings: Settings) -> ArtifactStorage:
    if settings.storage_backend.lower() == "s3":
        return S3ArtifactStorage(settings)
    return LocalArtifactStorage(settings)
