from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.domain.enums import JobStatus, PipelineStage
from app.domain.models import Artifact, Generation, Job, PromptVersion, StageRun
from app.prompt_registry import PromptRegistry
from app.services.idempotency import file_digest
from app.services.input_validation import inspect_image
from app.services.pipeline import render_h3_prompt, run_pipeline
from app.services.video_qc import validate_mp4
from app.storage.factory import build_storage


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _stage(job: Job, name: str, status: str, *, error: str | None = None) -> StageRun:
    existing = next((item for item in job.stages if item.stage == name and item.status == "running"), None)
    item = existing or StageRun(job_id=job.id, stage=name, status=status, attempt=1)
    if existing:
        item.status = status
    if status == "running":
        item.started_at = dt.datetime.now(dt.timezone.utc)
    if status in {"completed", "failed", "cancelled"}:
        item.finished_at = dt.datetime.now(dt.timezone.utc)
    item.error = error
    if not existing:
        job.stages.append(item)
    job.current_stage = name
    return item


def _artifact_from_path(
    session: Session,
    job: Job,
    path: Path,
    kind: str,
    base_dir: Path,
    settings: Settings | None = None,
    *,
    reference_index: int | None = None,
    source_artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    relative = path.relative_to(base_dir).as_posix()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".json": "application/json",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")
    info = inspect_image(path, mime) if mime.startswith("image/") else {
        "sha256": file_digest(path), "width": None, "height": None, "mime_type": mime
    }
    if settings and settings.storage_backend.lower() == "s3":
        build_storage(settings).put_file(path, f"{job.id}/{relative}")
    item = Artifact(
        job_id=job.id,
        kind=kind,
        object_key=f"{job.id}/{relative}",
        source_artifact_id=source_artifact_id,
        reference_index=reference_index,
        mime_type=mime,
        byte_size=path.stat().st_size,
        sha256=str(info["sha256"]),
        width=info.get("width"),
        height=info.get("height"),
        metadata_json={
            **(metadata or {}),
            **(
                {"picture_number": reference_index + 1}
                if reference_index is not None
                else {}
            ),
        },
        approved=kind in {
            "original_image",
            "reference_image",
            "enhanced_image",
            "enhanced_reference_image",
            "video",
            "video_qc",
        },
    )
    session.add(item)
    return item


def create_job(
    session: Session,
    *,
    job_id: str,
    title_plate: str,
    description_plate: str,
    image_edit_prompt: str,
    idempotency_key: str | None,
    pipeline_version: str,
    enhance_references: bool = False,
) -> Job:
    if idempotency_key:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing
    job = Job(
        id=job_id,
        status=JobStatus.QUEUED.value,
        current_stage=PipelineStage.QUEUED.value,
        title_plate=title_plate,
        description_plate=description_plate,
        image_edit_prompt=image_edit_prompt,
        idempotency_key=idempotency_key,
        pipeline_version=pipeline_version,
        enhance_references=enhance_references,
    )
    session.add(job)
    session.commit()
    return job


def record_input_artifact(
    session: Session,
    job: Job,
    path: Path,
    kind: str,
    settings: Settings | None = None,
    *,
    reference_index: int | None = None,
) -> Artifact:
    item = _artifact_from_path(
        session,
        job,
        path,
        kind,
        path.parent,
        settings,
        reference_index=reference_index,
    )
    session.commit()
    return item


def _mock_pipeline(*, job: Job, run_dir: Path, primary: Path, references: list[Path]) -> dict[str, Any]:
    enhanced = run_dir / "enhanced_image.png"
    with Image.open(primary) as image:
        image.convert("RGB").save(enhanced, format="PNG")
    enhanced_references: list[Path] = []
    for index, reference in enumerate(references, start=1):
        enhanced_reference = run_dir / f"enhanced_reference_{index}_attempt_1.png"
        with Image.open(reference) as image:
            image.convert("RGB").save(enhanced_reference, format="PNG")
        enhanced_references.append(enhanced_reference)
    video = run_dir / "video.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mock-qa-video")
    analysis = {
        "category": "food",
        "main_subject": job.title_plate,
        "preservation_description": "the exact visible dish, texture, garnishes, plate, and lighting",
        "menu_context_en": job.description_plate,
        "menu_image_alignment": "consistent",
        "menu_visual_conflicts": [],
        "reference_descriptions": [f"supplementary reference {i}" for i, _ in enumerate(references, 1)],
    }
    integrity = {
        "food_preserved": True,
        "plate_preserved": True,
        "product_composition_preserved": True,
        "background_professional": True,
        "background_consistent_with_master": True,
        "advertising_quality_improved": True,
        "no_menu_driven_visual_changes": True,
        "menu_context_consistent": True,
        "differences": [],
        "violations": [],
        "confidence": 1.0,
    }
    video_qc = {
        "valid_video": True,
        "camera_orbit_detected": True,
        "full_360_orbit_completed": True,
        "camera_only_motion": True,
        "plate_stationary": True,
        "food_preserved": True,
        "background_consistent": True,
        "no_visible_morphing": True,
        "start_end_view_aligned": True,
        "advertising_quality": True,
        "observed_motion": "mock 360-degree orbit",
        "violations": [],
        "confidence": 1.0,
        "passed": True,
    }
    prompt = render_h3_prompt(
        {**analysis, "title_plate": job.title_plate, "description_plate": job.description_plate},
        duration_seconds=5.0,
    )
    (run_dir / "image_analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (run_dir / "image_integrity_check.json").write_text(json.dumps(integrity), encoding="utf-8")
    (run_dir / "video_qc.json").write_text(json.dumps(video_qc), encoding="utf-8")
    (run_dir / "h3_prompt.txt").write_text(prompt, encoding="utf-8")
    return {
        "enhanced_image_path": str(enhanced),
        "enhanced_reference_image_paths": [str(path) for path in enhanced_references],
        "video_path": str(video),
        "analysis": analysis,
        "integrity_check": integrity,
        "reference_integrity_checks": [],
        "video_qc": video_qc,
        "h3_prompt": prompt,
    }


def run_job(job_id: str, settings: Settings | None = None) -> None:
    settings = settings or __import__("app.config", fromlist=["get_settings"]).get_settings()
    session = SessionLocal()
    job = session.get(Job, job_id)
    if not job:
        session.close()
        return
    run_dir = settings.outputs_dir / job_id
    try:
        if job.status == JobStatus.CANCELLED.value:
            return
        job.status = JobStatus.RUNNING.value
        _stage(job, PipelineStage.VALIDATE_INPUTS.value, "running")
        session.commit()
        primary_artifact = next(item for item in job.artifacts if item.kind == "original_image")
        reference_artifacts = sorted(
            (item for item in job.artifacts if item.kind == "reference_image"),
            key=_artifact_reference_sort_key,
        )
        primary = settings.outputs_dir / primary_artifact.object_key
        references = [settings.outputs_dir / item.object_key for item in reference_artifacts]
        inspect_image(primary, primary_artifact.mime_type, max_pixels=settings.max_image_pixels)
        for path, item in zip(references, reference_artifacts):
            inspect_image(path, item.mime_type, max_pixels=settings.max_image_pixels)
        _stage(job, PipelineStage.VALIDATE_INPUTS.value, "completed")
        session.commit()

        _stage(job, PipelineStage.ANALYZE_IMAGE.value, "running")
        _stage(job, PipelineStage.ENHANCE_PRIMARY.value, "running")
        session.commit()
        started = time.monotonic()
        if settings.pipeline_mode.lower() == "mock":
            result = _mock_pipeline(job=job, run_dir=run_dir, primary=primary, references=references)
        else:
            result = run_pipeline(
                original_image_path=primary,
                run_dir=run_dir,
                settings=settings,
                image_edit_prompt=job.image_edit_prompt,
                title_plate=job.title_plate,
                description_plate=job.description_plate,
                reference_image_paths=references,
            )
        session.refresh(job)
        if job.status == JobStatus.CANCELLED.value:
            return
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _stage(job, PipelineStage.ANALYZE_IMAGE.value, "completed")
        _stage(job, PipelineStage.ENHANCE_PRIMARY.value, "completed")
        _stage(job, PipelineStage.VERIFY_INTEGRITY.value, "completed")
        if references:
            _stage(job, PipelineStage.ENHANCE_REFERENCES.value, "completed")
        _stage(job, PipelineStage.BUILD_PROMPT.value, "completed")
        video_stage = _stage(job, PipelineStage.GENERATE_VIDEO.value, "completed")
        video_stage.provider_job_id = (result.get("runpod") or {}).get("id")
        if settings.video_qc_enabled:
            _stage(job, PipelineStage.VIDEO_QC.value, "running")
            validate_mp4(Path(result["video_path"]))
            if not (result.get("video_qc") or {}).get("passed"):
                raise RuntimeError("El resultado no pasó el QC orbital")
            _stage(job, PipelineStage.VIDEO_QC.value, "completed")
        primary_source_id = primary_artifact.id
        reference_source_ids = {
            item.reference_index: item.id for item in reference_artifacts
        }
        normalized_primary_id = primary_source_id
        normalized_reference_ids: dict[int, str] = {}
        normalized_primary = result.get("normalized_image_path")
        if normalized_primary and Path(normalized_primary).is_file():
            normalized_artifact = _artifact_from_path(
                session,
                job,
                Path(normalized_primary),
                "normalized_image",
                run_dir,
                settings,
                reference_index=0,
                source_artifact_id=primary_source_id,
            )
            session.flush()
            normalized_primary_id = normalized_artifact.id
        for index, path in enumerate(result.get("normalized_reference_image_paths", []), start=1):
            normalized_reference = Path(path)
            if normalized_reference.is_file():
                normalized_artifact = _artifact_from_path(
                    session,
                    job,
                    normalized_reference,
                    "normalized_reference_image",
                    run_dir,
                    settings,
                    reference_index=index,
                    source_artifact_id=reference_source_ids.get(index),
                )
                session.flush()
                normalized_reference_ids[index] = normalized_artifact.id
        for path, kind in (
            (Path(result["enhanced_image_path"]), "enhanced_image"),
            (Path(result["video_path"]), "video"),
            (run_dir / "image_analysis.json", "analysis"),
            (run_dir / "image_integrity_check.json", "integrity_check"),
            (run_dir / "reference_set_integrity_check.json", "reference_set_integrity_check"),
            (run_dir / "reference_manifest.json", "reference_manifest"),
            (run_dir / "gemini_file_uploads.json", "gemini_file_uploads"),
            (run_dir / "h3_prompt.txt", "h3_prompt"),
            (run_dir / "video_qc.json", "video_qc"),
        ):
            if path.exists():
                is_primary_image = kind == "enhanced_image"
                _artifact_from_path(
                    session,
                    job,
                    path,
                    kind,
                    run_dir,
                    settings,
                    reference_index=0 if is_primary_image else None,
                    source_artifact_id=normalized_primary_id if is_primary_image else None,
                )
        for index, path in enumerate(result.get("enhanced_reference_image_paths", []), start=1):
            enhanced_reference_path = Path(path)
            if enhanced_reference_path.exists():
                _artifact_from_path(
                    session,
                    job,
                    enhanced_reference_path,
                    "enhanced_reference_image",
                    run_dir,
                    settings,
                    reference_index=index,
                    source_artifact_id=normalized_reference_ids.get(
                        index, reference_source_ids.get(index)
                    ),
                )
        provider_diagnostics = {
            *run_dir.glob("nano_banana*.json"),
            *run_dir.glob("runpod*.json"),
            *run_dir.glob("*_payload_manifest.json"),
            *run_dir.glob("image_enhancement*.json"),
            *run_dir.glob("image_integrity_reference_*.json"),
            *run_dir.glob("video_qc_attempt_*.json"),
        }
        for diagnostic_path in sorted(provider_diagnostics):
            reference_match = re.search(r"(?:reference|reference_image)_(\d+)", diagnostic_path.name)
            reference_index = int(reference_match.group(1)) if reference_match else None
            _artifact_from_path(
                session,
                job,
                diagnostic_path,
                "provider_diagnostic",
                run_dir,
                settings,
                reference_index=reference_index,
                source_artifact_id=(
                    normalized_reference_ids.get(reference_index)
                    if reference_index is not None
                    else None
                ),
                metadata={"diagnostic_file": diagnostic_path.name},
            )
        for prompt_name in (
            "analysis_system",
            "integrity_system",
            "reference_set_integrity_system",
            "image_edit_default",
            "video_qc_system",
        ):
            snapshot = PromptRegistry.get(prompt_name)
            already = session.scalar(
                select(PromptVersion).where(
                    PromptVersion.name == snapshot.name,
                    PromptVersion.version == snapshot.version,
                )
            )
            if not already:
                session.add(
                    PromptVersion(
                        name=snapshot.name,
                        version=snapshot.version,
                        sha256=snapshot.sha256,
                        content=snapshot.content,
                        schema_json=snapshot.schema,
                    )
                )
        generation = Generation(
            job_id=job.id,
            provider="mock" if settings.pipeline_mode.lower() == "mock" else "pipeline",
            model=settings.video_endpoint,
            endpoint=settings.video_endpoint,
            provider_job_id=(result.get("runpod") or {}).get("id"),
            prompt_version=PromptRegistry.get("image_edit_default").version,
            prompt_snapshot=result.get("h3_prompt", ""),
            params={"reference_count": len(references)},
            duration_ms=elapsed_ms,
            response_summary={
                "mode": settings.pipeline_mode,
                "video_provider_status": result.get("runpod"),
                "reference_count": len(references),
            },
        )
        session.add(generation)
        _stage(job, PipelineStage.PUBLISH.value, "completed")
        job.status = JobStatus.COMPLETED.value
        job.current_stage = PipelineStage.PUBLISH.value
        job.error = None
        session.commit()
    except Exception as error:
        session.rollback()
        job = session.get(Job, job_id)
        if job is None:
            session.close()
            return
        job.status = JobStatus.FAILED.value
        job.error = str(error)
        if job.current_stage:
            _stage(job, job.current_stage, "failed", error=str(error))
        session.commit()
    finally:
        session.close()


def serialize_job(session: Session, job_id: str, base_url: str, settings: Settings | None = None) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if not job:
        raise KeyError(job_id)
    return {
        "job_id": job.id,
        "status": job.status,
        "current_stage": job.current_stage,
        "title_plate": job.title_plate,
        "description_plate": job.description_plate,
        "pipeline_version": job.pipeline_version,
        "reference_count": sum(1 for item in job.artifacts if item.kind == "reference_image"),
        "error": job.error,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "stages": [
            {
                "stage": item.stage,
                "status": item.status,
                "attempt": item.attempt,
                "provider_job_id": item.provider_job_id,
                "error": item.error,
                "started_at": _iso(item.started_at),
                "finished_at": _iso(item.finished_at),
            }
            for item in job.stages
        ],
        "artifacts": [
            {
                "id": item.id,
                "kind": item.kind,
                "url": (
                    build_storage(settings).url(item.object_key, base_url)
                    if settings and settings.storage_backend.lower() == "s3"
                    else f"{base_url.rstrip('/')}/artifacts/{item.object_key}"
                ),
                "mime_type": item.mime_type,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
                "approved": item.approved,
                "reference_index": item.reference_index,
                "picture_number": (
                    item.reference_index + 1 if item.reference_index is not None else None
                ),
                "source_artifact_id": item.source_artifact_id,
            }
            for item in sorted(job.artifacts, key=_artifact_reference_sort_key)
        ],
    }


def _artifact_reference_sort_key(item: Artifact) -> tuple[int, int, str]:
    """Stable API and pipeline ordering, including artifacts created before migration."""
    if item.reference_index is not None:
        order = item.reference_index
    else:
        order = (item.metadata_json or {}).get("reference_index")
        if not isinstance(order, int):
            match = re.search(r"(?:reference_image|enhanced_reference)_(\d+)", item.object_key)
            order = int(match.group(1)) if match else -1
        if order == -1 and item.kind in {"original_image", "enhanced_image", "normalized_image"}:
            order = 0
    return (order if isinstance(order, int) else 99, item.created_at.timestamp(), item.kind)
