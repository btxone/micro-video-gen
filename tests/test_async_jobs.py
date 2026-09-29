from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.main import app


def image_bytes(color: tuple[int, int, int], fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (16, 16), color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_v2_job_runs_end_to_end_without_runpod(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_mode", "mock")
    monkeypatch.setattr(settings, "celery_enabled", False)
    client = TestClient(app)
    files = [("image", ("primary.png", image_bytes((180, 90, 30)), "image/png"))]
    files.extend(
        ("reference_images", (f"ref-{index}.jpg", image_bytes((index, 40, 90), "JPEG"), "image/jpeg"))
        for index in range(1, 5)
    )
    response = client.post(
        "/v2/jobs",
        files=files,
        data={
            "title_plate": "Gnocchi de prueba",
            "description_plate": "Gnocchi artesanal con salsa de manteca y salvia.",
        },
        headers={"Idempotency-Key": "qa-mock-5-images"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.json()["reference_count"] == 4

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get(f"/v2/jobs/{job_id}").json()
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert status["status"] == "completed", status
    assert status["current_stage"] == "publish"
    kinds = {artifact["kind"] for artifact in status["artifacts"]}
    assert {"original_image", "reference_image", "enhanced_image", "video", "h3_prompt"}.issubset(kinds)
    assert "fully_preserved" in open(
        get_settings().outputs_dir / job_id / "h3_prompt.txt", encoding="utf-8"
    ).read()


def test_v2_idempotency_returns_same_job(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_mode", "mock")
    client = TestClient(app)
    files = [("image", ("primary.png", image_bytes((10, 20, 30)), "image/png"))]
    payload = {
        "title_plate": "Plato idempotente",
        "description_plate": "Descripción suficientemente larga para la prueba.",
    }
    first = client.post("/v2/jobs", files=files, data=payload, headers={"Idempotency-Key": "qa-idempotent"})
    second = client.post(
        "/v2/jobs",
        files=[("image", ("different.png", image_bytes((255, 0, 0)), "image/png"))],
        data=payload,
        headers={"Idempotency-Key": "qa-idempotent"},
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_v2_rejects_corrupt_image(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pipeline_mode", "mock")
    client = TestClient(app)
    response = client.post(
        "/v2/jobs",
        files=[("image", ("primary.png", b"not-an-image", "image/png"))],
        data={
            "title_plate": "Plato válido",
            "description_plate": "Descripción suficientemente larga para la prueba.",
        },
    )
    assert response.status_code == 415
