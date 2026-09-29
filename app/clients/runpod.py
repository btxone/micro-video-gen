from __future__ import annotations

import base64
import binascii
import pathlib
import time
from typing import Any

from PIL import Image

from app.config import Settings
from app.http_client import (
    decode_base64_image,
    download_image,
    image_to_data_url,
    request_json,
    write_json,
)


RUNPOD_API_ROOT = "https://api.runpod.ai/v2"


class RunpodClient:
    """Cliente para Nano Banana 2 Edit y MiniMax H3 Serverless."""

    def __init__(self, settings: Settings):
        self.api_key = settings.runpod_api_key
        self.image_edit_endpoint = settings.image_edit_endpoint
        self.video_endpoint = settings.video_endpoint
        self.image_edit_resolution = settings.image_edit_resolution
        self.image_edit_output_format = settings.image_edit_output_format
        self.image_edit_aspect_ratio = settings.image_edit_aspect_ratio
        self.image_edit_timeout_seconds = settings.image_edit_timeout_seconds
        self.video_timeout_seconds = settings.video_timeout_seconds

    def _request(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: int = 90,
    ) -> dict[str, Any]:
        return request_json(
            url,
            api_key=self.api_key,
            provider="Runpod",
            payload=payload,
            timeout=timeout,
        )

    def _input_aspect_ratio(self, image_path: pathlib.Path) -> str:
        """Conserva el encuadre de entrada cuando la mejora es de preservación."""
        if self.image_edit_aspect_ratio.lower() != "auto":
            return self.image_edit_aspect_ratio
        with Image.open(image_path) as image:
            width, height = image.size
        ratio = width / height
        options = {
            "1:1": 1.0,
            "2:3": 2 / 3,
            "3:2": 3 / 2,
            "3:4": 3 / 4,
            "4:3": 4 / 3,
            "4:5": 4 / 5,
            "5:4": 5 / 4,
            "9:16": 9 / 16,
            "16:9": 16 / 9,
            "21:9": 21 / 9,
        }
        return min(options, key=lambda name: abs(options[name] - ratio))

    @staticmethod
    def _compact_status(result: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: result.get(key)
            for key in (
                "id",
                "status",
                "delayTime",
                "executionTime",
                "workerId",
                "retries",
                "error",
            )
            if key in result
        }
        output = result.get("output") or {}
        if isinstance(output, dict) and output:
            compact["output_fields"] = list(output.keys())
            compact["output"] = {
                key: output.get(key)
                for key in (
                    "cost",
                    "mime_type",
                    "original_bytes",
                    "delivery_recompressed",
                    "video_path",
                    "result",
                )
                if key in output
            }
        return compact

    @staticmethod
    def _image_candidate(value: Any) -> Any:
        if isinstance(value, dict):
            for key in (
                "image_url",
                "url",
                "image",
                "image_base64",
                "base64",
                "b64_json",
                "data",
                "result",
                "images",
                "results",
            ):
                candidate = value.get(key)
                if not candidate:
                    continue
                if key in {"images", "results"} and isinstance(candidate, list):
                    return RunpodClient._image_candidate(candidate[0]) if candidate else None
                return RunpodClient._image_candidate(candidate)
            return None
        if isinstance(value, list):
            return RunpodClient._image_candidate(value[0]) if value else None
        return value if isinstance(value, str) else None

    @classmethod
    def _extract_image_bytes(cls, output: dict[str, Any]) -> bytes:
        candidate = cls._image_candidate(output)
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return download_image(candidate)
        image_bytes = decode_base64_image(candidate)
        if image_bytes:
            return image_bytes
        raise RuntimeError(
            "Nano Banana terminó, pero no se encontró una imagen válida. "
            f"Campos disponibles: {list(output.keys())}"
        )

    def _poll(
        self,
        *,
        endpoint_id: str,
        job_id: str,
        timeout_seconds: int,
        status_path: pathlib.Path,
        label: str,
    ) -> dict[str, Any]:
        endpoint_url = f"{RUNPOD_API_ROOT}/{endpoint_id}"
        deadline = time.monotonic() + timeout_seconds
        previous_status = ""
        while time.monotonic() < deadline:
            result = self._request(endpoint_url + f"/status/{job_id}")
            compact = self._compact_status(result)
            compact["endpoint_id"] = endpoint_id
            write_json(status_path, compact)
            status = str(result.get("status", "UNKNOWN"))
            if status != previous_status:
                print(f"{label}: {status}", flush=True)
                previous_status = status
            if status == "COMPLETED":
                return result
            if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise RuntimeError(
                    f"{label} terminó con estado {status}: {result.get('error')}"
                )
            time.sleep(3 if label.startswith("Nano") else 10)
        raise TimeoutError(f"Se agotaron {timeout_seconds} segundos esperando {label}")

    def enhance_image(
        self,
        *,
        original_image_path: pathlib.Path,
        prompt: str,
        run_dir: pathlib.Path,
        job_file_name: str = "nano_banana_job.json",
        output_file_name: str = "enhanced_image.png",
    ) -> tuple[pathlib.Path, dict[str, Any]]:
        payload = {
            "input": {
                "images": [image_to_data_url(original_image_path)],
                "prompt": prompt,
                "resolution": self.image_edit_resolution,
                "output_format": self.image_edit_output_format,
                "enable_safety_checker": True,
                "aspect_ratio": self._input_aspect_ratio(original_image_path),
            }
        }
        endpoint_url = f"{RUNPOD_API_ROOT}/{self.image_edit_endpoint}"
        job = self._request(endpoint_url + "/run", payload=payload)
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"Nano Banana no devolvió un ID de trabajo: {job}")
        status_path = run_dir / job_file_name
        write_json(
            status_path,
            {"endpoint_id": self.image_edit_endpoint, "id": job_id, "status": "SUBMITTED"},
        )
        print(f"Trabajo Nano Banana enviado. ID: {job_id}", flush=True)
        result = self._poll(
            endpoint_id=self.image_edit_endpoint,
            job_id=job_id,
            timeout_seconds=self.image_edit_timeout_seconds,
            status_path=status_path,
            label="Nano Banana",
        )
        image_path = run_dir / output_file_name
        image_path.write_bytes(self._extract_image_bytes(result.get("output") or {}))
        compact = self._compact_status(result)
        compact["endpoint_id"] = self.image_edit_endpoint
        compact["local_image_bytes"] = image_path.stat().st_size
        write_json(status_path, compact)
        return image_path, compact

    def generate_video(
        self,
        *,
        image_paths: list[pathlib.Path],
        h3_prompt: str,
        run_dir: pathlib.Path,
    ) -> tuple[pathlib.Path, dict[str, Any]]:
        if not 1 <= len(image_paths) <= 5:
            raise ValueError(
                "MiniMax H3 debe recibir una imagen principal y hasta cuatro referencias"
            )
        payload = {
            "input": {
                "prompt": h3_prompt,
                "images": [image_to_data_url(path) for path in image_paths],
            },
            "policy": {
                "executionTimeout": self.video_timeout_seconds * 1000,
                "ttl": (self.video_timeout_seconds + 3600) * 1000,
            },
        }
        endpoint_url = f"{RUNPOD_API_ROOT}/{self.video_endpoint}"
        job = self._request(endpoint_url + "/run", payload=payload)
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"Runpod no devolvió un ID de trabajo: {job}")
        status_path = run_dir / "runpod_job.json"
        write_json(
            status_path,
            {"endpoint_id": self.video_endpoint, "id": job_id, "status": "SUBMITTED"},
        )
        print(f"Trabajo Runpod enviado. ID: {job_id}", flush=True)
        result = self._poll(
            endpoint_id=self.video_endpoint,
            job_id=job_id,
            timeout_seconds=self.video_timeout_seconds,
            status_path=status_path,
            label="Runpod video",
        )
        output = result.get("output") or {}
        encoded = output.get("video_base64")
        if not encoded:
            raise RuntimeError(
                "Runpod terminó, pero no devolvió output.video_base64. "
                f"Campos disponibles: {list(output) if isinstance(output, dict) else []}"
            )
        try:
            video_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise RuntimeError("Runpod devolvió video_base64 inválido") from error
        if len(video_bytes) < 12 or video_bytes[4:8] != b"ftyp":
            raise RuntimeError("Runpod devolvió datos que no parecen un MP4 válido")
        video_path = run_dir / "video.mp4"
        video_path.write_bytes(video_bytes)
        compact = self._compact_status(result)
        compact["endpoint_id"] = self.video_endpoint
        compact["local_video_bytes"] = len(video_bytes)
        write_json(status_path, compact)
        return video_path, compact

