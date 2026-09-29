from __future__ import annotations

import shutil
from pathlib import Path

from app.config import Settings


class LocalArtifactStorage:
    """Storage local determinista para desarrollo y QA; reemplazable por S3/R2."""

    def __init__(self, settings: Settings):
        self.root = settings.storage_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, source: Path, object_key: str) -> str:
        target = self.root / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return object_key

    def local_path(self, object_key: str) -> Path:
        return self.root / object_key

    def url(self, object_key: str, base_url: str = "") -> str:
        return f"{base_url.rstrip('/')}/artifacts/{object_key}" if base_url else f"/artifacts/{object_key}"
