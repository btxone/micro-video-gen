from __future__ import annotations

import pathlib
import uuid

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.domain.models import Job
from app.prompts import DEFAULT_IMAGE_EDIT_PROMPT
from app.schemas import DishMetadata, JobResponse, MAX_REFERENCE_IMAGES
from app.services.input_validation import MIME_SUFFIXES
from app.services.job_service import create_job, record_input_artifact, serialize_job
from app.tasks.dispatcher import enqueue_job


router = APIRouter(prefix="/v2", tags=["jobs"])
settings = get_settings()


async def _save_upload_v2(image: UploadFile, destination: pathlib.Path) -> None:
    allowed = set(MIME_SUFFIXES)
    if image.content_type not in allowed:
        raise HTTPException(status_code=415, detail="La imagen debe ser JPG, PNG o WebP")
    total = 0
    with destination.open("wb") as output:
        while chunk := await image.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_upload_mb * 1024 * 1024:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="La imagen supera el límite permitido")
            output.write(chunk)


@router.post("/jobs", response_model=JobResponse, status_code=202)
async def create_async_job(
    request: Request,
    image: UploadFile = File(...),
    reference_images: list[UploadFile] | None = File(default=None),
    title_plate: str = Form(...),
    description_plate: str = Form(...),
    image_edit_prompt: str = Form(DEFAULT_IMAGE_EDIT_PROMPT),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobResponse:
    references = list(reference_images or [])
    if len(references) > MAX_REFERENCE_IMAGES:
        raise HTTPException(status_code=422, detail=f"Se permiten como máximo {MAX_REFERENCE_IMAGES} imágenes de referencia")
    try:
        metadata = DishMetadata(title_plate=title_plate, description_plate=description_plate)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    if not image_edit_prompt.strip():
        raise HTTPException(status_code=422, detail="image_edit_prompt no puede estar vacío")

    session = SessionLocal()
    try:
        if idempotency_key:
            existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            if existing:
                return JobResponse.model_validate(serialize_job(session, existing.id, str(request.base_url).rstrip("/"), settings))
        job_id = uuid.uuid4().hex
        run_dir = settings.outputs_dir / job_id
        run_dir.mkdir(parents=True, exist_ok=False)
        job = create_job(
            session,
            job_id=job_id,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
            image_edit_prompt=image_edit_prompt.strip(),
            idempotency_key=idempotency_key,
            pipeline_version=settings.pipeline_version,
            enhance_references=settings.enhance_references_enabled,
        )
        suffix = MIME_SUFFIXES.get(image.content_type or "", ".bin")
        primary_path = run_dir / f"original_image{suffix}"
        await _save_upload_v2(image, primary_path)
        record_input_artifact(session, job, primary_path, "original_image", settings)
        for index, reference in enumerate(references, start=1):
            ref_suffix = MIME_SUFFIXES.get(reference.content_type or "", ".bin")
            ref_path = run_dir / f"reference_image_{index}{ref_suffix}"
            await _save_upload_v2(reference, ref_path)
            record_input_artifact(session, job, ref_path, "reference_image", settings)
        enqueue_job(job_id, settings)
        session.refresh(job)
        return JobResponse.model_validate(serialize_job(session, job_id, str(request.base_url).rstrip("/"), settings))
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        await image.close()
        for reference in references:
            await reference.close()
        session.close()


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_async_job(request: Request, job_id: str) -> JobResponse:
    session = SessionLocal()
    try:
        try:
            payload = serialize_job(session, job_id, str(request.base_url).rstrip("/"), settings)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job no encontrado") from error
        return JobResponse.model_validate(payload)
    finally:
        session.close()


@router.get("/jobs/{job_id}/artifacts")
def get_job_artifacts(request: Request, job_id: str) -> list[dict[str, object]]:
    session = SessionLocal()
    try:
        try:
            payload = serialize_job(session, job_id, str(request.base_url).rstrip("/"), settings)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job no encontrado") from error
        return payload["artifacts"]
    finally:
        session.close()


@router.post("/jobs/{job_id}/retry", response_model=JobResponse, status_code=202)
def retry_job(request: Request, job_id: str) -> JobResponse:
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        job.status = "queued"
        job.error = None
        job.current_stage = "queued"
        session.commit()
        enqueue_job(job_id, settings)
        return JobResponse.model_validate(serialize_job(session, job_id, str(request.base_url).rstrip("/"), settings))
    finally:
        session.close()


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(request: Request, job_id: str) -> JobResponse:
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        if job.status in {"queued", "running"}:
            job.status = "cancelled"
            job.error = "Cancelado por el cliente"
            session.commit()
        return JobResponse.model_validate(serialize_job(session, job_id, str(request.base_url).rstrip("/"), settings))
    finally:
        session.close()
