from __future__ import annotations

from pathlib import Path


def validate_mp4(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    valid = len(data) >= 12 and data[4:8] == b"ftyp"
    if not valid:
        raise ValueError("El resultado no es un MP4 válido")
    return {"valid": True, "bytes": len(data), "container": "mp4"}
