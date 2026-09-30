from __future__ import annotations

from pathlib import Path
from typing import Any


def validate_mp4(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    valid = len(data) >= 12 and data[4:8] == b"ftyp"
    if not valid:
        raise ValueError("El resultado no es un MP4 válido")
    return {"valid": True, "bytes": len(data), "container": "mp4"}


def video_qc_passed(result: dict[str, Any], *, min_confidence: float) -> bool:
    required_checks = (
        "valid_video",
        "camera_orbit_detected",
        "full_360_orbit_completed",
        "camera_only_motion",
        "plate_stationary",
        "food_preserved",
        "background_consistent",
        "no_visible_morphing",
        "start_end_view_aligned",
        "advertising_quality",
    )
    confidence = result.get("confidence")
    return (
        all(result.get(field) is True for field in required_checks)
        and isinstance(confidence, (int, float))
        and float(confidence) >= min_confidence
    )
