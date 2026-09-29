from __future__ import annotations

import hashlib
from pathlib import Path


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def job_fingerprint(*, image_digest: str, reference_digests: list[str], title_plate: str, description_plate: str, prompt: str) -> str:
    raw = "\n".join([image_digest, *reference_digests, title_plate, description_plate, prompt])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
