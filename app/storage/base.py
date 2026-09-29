from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ArtifactStorage(Protocol):
    def put_file(self, source: Path, object_key: str) -> str: ...

    def local_path(self, object_key: str) -> Path: ...

    def url(self, object_key: str, base_url: str = "") -> str: ...
