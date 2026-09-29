import base64
import pathlib

import pytest

from app.clients.runpod import RunpodClient


def test_runpod_extracts_result_url_candidate() -> None:
    candidate = RunpodClient._image_candidate(
        {"cost": 0.0875, "result": "https://image.runpod.ai/result.png"}
    )
    assert candidate == "https://image.runpod.ai/result.png"


def test_runpod_extracts_nested_base64_candidate() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"test"
    encoded = base64.b64encode(png).decode("ascii")
    candidate = RunpodClient._image_candidate({"images": [{"image_base64": encoded}]})
    assert candidate == encoded


def test_runpod_rejects_more_than_five_total_images() -> None:
    client = object.__new__(RunpodClient)
    with pytest.raises(ValueError):
        client.generate_video(
            image_paths=[pathlib.Path(f"image_{index}.png") for index in range(6)],
            h3_prompt="prompt",
            run_dir=pathlib.Path("."),
        )

