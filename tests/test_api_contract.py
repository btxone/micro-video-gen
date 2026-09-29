from app.main import app
from fastapi.testclient import TestClient


def test_generate_video_exposes_required_menu_inputs() -> None:
    openapi = app.openapi()
    request_schema = openapi["paths"]["/v1/generate-video"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    if "$ref" in request_schema:
        component_name = request_schema["$ref"].split("/")[-1]
        request_schema = openapi["components"]["schemas"][component_name]

    assert {"image", "title_plate", "description_plate"}.issubset(
        set(request_schema["required"])
    )
    assert "image_edit_prompt" not in request_schema["required"]
    assert "reference_images" not in request_schema["required"]
    reference_schema = request_schema["properties"]["reference_images"]
    schema_text = str(reference_schema)
    assert "array" in schema_text


def test_health_advertises_ref2va_reference_limit() -> None:
    response_schema = app.openapi()["components"]["schemas"]["HealthResponse"]
    assert "video_mode" in response_schema["required"]
    assert "max_reference_images" in response_schema["required"]


def test_api_rejects_five_optional_references_before_starting_pipeline() -> None:
    client = TestClient(app)
    files = [("image", ("primary.png", b"png", "image/png"))]
    files.extend(
        ("reference_images", (f"reference-{index}.png", b"png", "image/png"))
        for index in range(5)
    )
    response = client.post(
        "/v1/generate-video",
        files=files,
        data={
            "title_plate": "Gnocchi",
            "description_plate": "Gnocchi artesanales con salsa de la casa.",
        },
    )
    assert response.status_code == 422
    assert "máximo 4" in response.json()["detail"]
