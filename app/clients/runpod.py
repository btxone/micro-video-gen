from __future__ import annotations

import base64
import binascii
import hashlib
import json
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
        operation_key: str,
    ) -> dict[str, Any]:
        endpoint_url = f"{RUNPOD_API_ROOT}/{endpoint_id}"
        deadline = time.monotonic() + timeout_seconds
        previous_status = ""
        while time.monotonic() < deadline:
            result = self._request(endpoint_url + f"/status/{job_id}")
            compact = self._compact_status(result)
            compact.update(endpoint_id=endpoint_id, id=job_id, operation_key=operation_key)
            write_json(status_path, compact)
            status = str(result.get("status", "UNKNOWN"))
            if status != previous_status:
                print(f"{label}: {status}", flush=True)
                previous_status = status
            if status == "COMPLETED":
                return result
            if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise RuntimeError(f"{label} terminó con estado {status}: {result.get('error')}")
            time.sleep(3 if label.startswith("Nano") else 10)
        raise TimeoutError(f"Se agotaron {timeout_seconds} segundos esperando {label}")

    @staticmethod
    def _file_fingerprint(path: pathlib.Path) -> dict[str, Any]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return {"path": path.name, "sha256": digest.hexdigest(), "bytes": size}

    @staticmethod
    def _operation_key(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _read_job_status(path: pathlib.Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _resumable_job_id(
        previous: dict[str, Any] | None, operation_key: str, endpoint_id: str
    ) -> str | None:
        if not previous or previous.get("operation_key") != operation_key:
            return None
        if previous.get("endpoint_id") != endpoint_id or previous.get("status") in {
            "FAILED", "CANCELLED", "TIMED_OUT"
        }:
            return None
        return str(previous["id"]) if previous.get("id") else None

    @staticmethod
    def _is_valid_image_file(path: pathlib.Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _is_valid_mp4_file(path: pathlib.Path) -> bool:
        try:
            with path.open("rb") as video:
                header = video.read(12)
            return len(header) >= 12 and header[4:8] == b"ftyp"
        except OSError:
            return False

    def enhance_image(
        self,
        *,
        original_image_path: pathlib.Path,
        prompt: str,
        run_dir: pathlib.Path,
        style_reference_image_path: pathlib.Path | None = None,
        job_file_name: str = "nano_banana_job.json",
        output_file_name: str = "enhanced_image.png",
    ) -> tuple[pathlib.Path, dict[str, Any]]:
        image_paths = [original_image_path]
        if style_reference_image_path is not None:
            image_paths.append(style_reference_image_path)
        status_path = run_dir / job_file_name
        image_path = run_dir / output_file_name
        aspect_ratio = self._input_aspect_ratio(original_image_path)
        fingerprints = [self._file_fingerprint(path) for path in image_paths]
        operation_key = self._operation_key(
            {
                "operation": "nano_banana_edit",
                "images": [item["sha256"] for item in fingerprints],
                "prompt": prompt,
                "resolution": self.image_edit_resolution,
                "format": self.image_edit_output_format,
                "aspect_ratio": aspect_ratio,
            }
        )
        previous = self._read_job_status(status_path)
        if (
            previous
            and previous.get("operation_key") == operation_key
            and previous.get("status") == "COMPLETED"
            and self._is_valid_image_file(image_path)
        ):
            compact = dict(previous)
            compact["reused_completed_operation"] = True
            return image_path, compact

        write_json(
            run_dir / f"{status_path.stem}_payload_manifest.json",
            {
                "operation_key": operation_key,
                "provider": "runpod",
                "endpoint_id": self.image_edit_endpoint,
                "operation": "nano_banana_edit",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                "resolution": self.image_edit_resolution,
                "output_format": self.image_edit_output_format,
                "aspect_ratio": aspect_ratio,
                "images": [
                    {
                        "position": index + 1,
                        "role": "product_source" if index == 0 else "master_style_only",
                        "base64_bytes_estimate": 4 * ((fingerprints[index]["bytes"] + 2) // 3),
                        **fingerprints[index],
                    }
                    for index in range(len(fingerprints))
                ],
            },
        )
        job_id = self._resumable_job_id(previous, operation_key, self.image_edit_endpoint)
        if not job_id:
            payload = {
                "input": {
                    "images": [image_to_data_url(path) for path in image_paths],
                    "prompt": prompt,
                    "resolution": self.image_edit_resolution,
                    "output_format": self.image_edit_output_format,
                    "enable_safety_checker": True,
                    "aspect_ratio": aspect_ratio,
                }
            }
            job = self._request(f"{RUNPOD_API_ROOT}/{self.image_edit_endpoint}/run", payload=payload)
            job_id = job.get("id")
            if not job_id:
                raise RuntimeError("Nano Banana no devolvió un ID de trabajo")
            write_json(
                status_path,
                {"endpoint_id": self.image_edit_endpoint, "id": job_id, "status": "SUBMITTED", "operation_key": operation_key},
            )
            print(f"Trabajo Nano Banana enviado. ID: {job_id}", flush=True)
        result = self._poll(
            endpoint_id=self.image_edit_endpoint,
            job_id=job_id,
            timeout_seconds=self.image_edit_timeout_seconds,
            status_path=status_path,
            label="Nano Banana",
            operation_key=operation_key,
        )
        image_path.write_bytes(self._extract_image_bytes(result.get("output") or {}))
        compact = self._compact_status(result)
        compact.update(endpoint_id=self.image_edit_endpoint, id=job_id, operation_key=operation_key)
        compact["local_image_bytes"] = image_path.stat().st_size
        write_json(status_path, compact)
        return image_path, compact

    def generate_video(
        self,
        *,
        image_paths: list[pathlib.Path],
        h3_prompt: str,
        run_dir: pathlib.Path,
        job_file_name: str = "runpod_job.json",
        output_file_name: str = "video.mp4",
        picture_numbers: list[int] | None = None,
        reference_indices: list[int | None] | None = None,
    ) -> tuple[pathlib.Path, dict[str, Any]]:
        if not 1 <= len(image_paths) <= 5:
            raise ValueError("MiniMax H3 debe recibir una imagen principal y hasta cuatro referencias")
        ordered_pictures = picture_numbers or list(range(1, len(image_paths) + 1))
        ordered_indices = reference_indices or [None, *range(1, len(image_paths))]
        if len(ordered_pictures) != len(image_paths) or len(ordered_indices) != len(image_paths):
            raise ValueError("Los índices Ref2VA no coinciden con las imágenes")
        if ordered_pictures != list(range(1, len(image_paths) + 1)):
            raise ValueError("Las imágenes H3 deben conservar el orden contiguo Picture 1..5")
        if ordered_indices != [None, *range(1, len(image_paths))]:
            raise ValueError("reference_index debe seguir el orden original Picture 2..5")
        fingerprints = [self._file_fingerprint(path) for path in image_paths]
        operation_key = self._operation_key(
            {"operation": "minimax_h3_ref2va", "images": [item["sha256"] for item in fingerprints], "prompt": h3_prompt}
        )
        status_path = run_dir / job_file_name
        video_path = run_dir / output_file_name
        previous = self._read_job_status(status_path)
        if (
            previous
            and previous.get("operation_key") == operation_key
            and previous.get("status") == "COMPLETED"
            and self._is_valid_mp4_file(video_path)
        ):
            compact = dict(previous)
            compact["reused_completed_operation"] = True
            return video_path, compact

        write_json(
            run_dir / f"{status_path.stem}_payload_manifest.json",
            {
                "operation_key": operation_key,
                "provider": "runpod",
                "endpoint_id": self.video_endpoint,
                "operation": "minimax_h3_ref2va",
                "prompt_sha256": hashlib.sha256(h3_prompt.encode("utf-8")).hexdigest(),
                "estimated_image_base64_bytes": sum(
                    4 * ((item["bytes"] + 2) // 3) for item in fingerprints
                ),
                "prompt_utf8_bytes": len(h3_prompt.encode("utf-8")),
                "images": [
                    {
                        "position": index + 1,
                        "picture_number": ordered_pictures[index],
                        "reference_index": ordered_indices[index],
                        "role": "primary" if index == 0 else "supplementary_reference",
                        "base64_bytes_estimate": 4 * ((fingerprints[index]["bytes"] + 2) // 3),
                        **fingerprints[index],
                    }
                    for index in range(len(image_paths))
                ],
            },
        )
        job_id = self._resumable_job_id(previous, operation_key, self.video_endpoint)
        if not job_id:
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
            job = self._request(f"{RUNPOD_API_ROOT}/{self.video_endpoint}/run", payload=payload)
            job_id = job.get("id")
            if not job_id:
                raise RuntimeError("Runpod no devolvió un ID de trabajo")
            write_json(
                status_path,
                {"endpoint_id": self.video_endpoint, "id": job_id, "status": "SUBMITTED", "operation_key": operation_key},
            )
            print(f"Trabajo Runpod enviado. ID: {job_id}", flush=True)
        result = self._poll(
            endpoint_id=self.video_endpoint,
            job_id=job_id,
            timeout_seconds=self.video_timeout_seconds,
            status_path=status_path,
            label="Runpod video",
            operation_key=operation_key,
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
        video_path.write_bytes(video_bytes)
        compact = self._compact_status(result)
        compact.update(endpoint_id=self.video_endpoint, id=job_id, operation_key=operation_key)
        compact["local_video_bytes"] = len(video_bytes)
        write_json(status_path, compact)
        return video_path, compact

