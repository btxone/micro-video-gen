from __future__ import annotations

import datetime as dt
import json
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
        mime_type=mime,
        byte_size=path.stat().st_size,
        sha256=str(info["sha256"]),
        width=info.get("width"),
        height=info.get("height"),
        metadata_json={},
        approved=kind in {"original_image", "reference_image", "enhanced_image", "video"},
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


def record_input_artifact(session: Session, job: Job, path: Path, kind: str, settings: Settings | None = None) -> Artifact:
    item = _artifact_from_path(session, job, path, kind, path.parent, settings)
    session.commit()
    return item


def _mock_pipeline(*, job: Job, run_dir: Path, primary: Path, references: list[Path]) -> dict[str, Any]:
    enhanced = run_dir / "enhanced_image.png"
    with Image.open(primary) as image:
        image.convert("RGB").save(enhanced, format="PNG")
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
        "composition_preserved": True,
        "advertising_quality_improved": True,
        "no_menu_driven_visual_changes": True,
        "menu_context_consistent": True,
        "differences": [],
        "confidence": 1.0,
    }
    prompt = render_h3_prompt({**analysis, "title_plate": job.title_plate, "description_plate": job.description_plate})
    (run_dir / "image_analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (run_dir / "image_integrity_check.json").write_text(json.dumps(integrity), encoding="utf-8")
    (run_dir / "h3_prompt.txt").write_text(prompt, encoding="utf-8")
    return {"enhanced_image_path": str(enhanced), "video_path": str(video), "analysis": analysis, "integrity_check": integrity, "h3_prompt": prompt}


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
        reference_artifacts = [item for item in job.artifacts if item.kind == "reference_image"]
        primary = settings.outputs_dir / primary_artifact.object_key
        references = [settings.outputs_dir / item.object_key for item in reference_artifacts]
        inspect_image(primary, primary_artifact.mime_type)
        for path, item in zip(references, reference_artifacts):
            inspect_image(path, item.mime_type)
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
        _stage(job, PipelineStage.BUILD_PROMPT.value, "completed")
        _stage(job, PipelineStage.GENERATE_VIDEO.value, "completed")
        if settings.video_qc_enabled:
            _stage(job, PipelineStage.VIDEO_QC.value, "running")
            validate_mp4(Path(result["video_path"]))
            _stage(job, PipelineStage.VIDEO_QC.value, "completed")
        for path, kind in (
            (Path(result["enhanced_image_path"]), "enhanced_image"),
            (Path(result["video_path"]), "video"),
            (run_dir / "image_analysis.json", "analysis"),
            (run_dir / "image_integrity_check.json", "integrity_check"),
            (run_dir / "h3_prompt.txt", "h3_prompt"),
        ):
            if path.exists():
                _artifact_from_path(session, job, path, kind, run_dir, settings)
        for prompt_name in ("analysis_system", "integrity_system", "image_edit_default"):
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
            prompt_version=PromptRegistry.get("image_edit_default").version,
            prompt_snapshot=result.get("h3_prompt", ""),
            params={"reference_count": len(references)},
            duration_ms=elapsed_ms,
            response_summary={"mode": settings.pipeline_mode},
        )
        session.add(generation)
        _stage(job, PipelineStage.PUBLISH.value, "completed")
        job.status = JobStatus.COMPLETED.value
        job.current_stage = PipelineStage.PUBLISH.value
        job.error = None
        session.commit()
    except Exception as error:
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
            }
            for item in job.artifacts
        ],
    }
