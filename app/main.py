from __future__ import annotations

import pathlib
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.config import get_settings
from app.db import init_db
from app.api.jobs import router as jobs_router
from app.prompts import DEFAULT_IMAGE_EDIT_PROMPT
from app.schemas import (
    DishMetadata,
    GenerateVideoResponse,
    HealthResponse,
    MAX_REFERENCE_IMAGES,
)
from app.services.pipeline import run_pipeline
from app.services.input_validation import inspect_image


settings = get_settings()
settings.outputs_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="H3 Food Image-to-Video API",
    version="1.0.0",
    description=(
        "Pipeline image-to-image: Gemini describe/verifica, Nano Banana mejora "
        "y MiniMax H3 Ref2VA genera el video con hasta cuatro referencias opcionales."
    ),
)
app.mount("/artifacts", StaticFiles(directory=str(settings.outputs_dir)), name="artifacts")
app.include_router(jobs_router)
init_db()


async def _save_upload(image: UploadFile, destination: pathlib.Path, max_bytes: int) -> None:
    allowed_types = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if image.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="La imagen debe ser JPG, PNG o WebP")
    total = 0
    with destination.open("wb") as output:
        while chunk := await image.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="La imagen supera el límite permitido")
            output.write(chunk)
    try:
        inspect_image(destination, image.content_type)
    except ValueError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=str(error)) from error


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        image_to_image_only=True,
        vision_model=settings.gemini_vision_model,
        video_mode="ref2va",
        max_reference_images=MAX_REFERENCE_IMAGES,
    )


@app.post("/v1/generate-video", response_model=GenerateVideoResponse)
async def generate_video(
    request: Request,
    image: UploadFile = File(..., description="Imagen de referencia obligatoria"),
    reference_images: list[UploadFile] | None = File(
        default=None,
        description="Hasta cuatro imágenes de referencia opcionales; repetir este campo",
    ),
    title_plate: str = Form(
        ...,
        description="Nombre del plato tal como aparece en la carta del menú",
    ),
    description_plate: str = Form(
        ...,
        description="Descripción del plato escrita por el chef para la carta",
    ),
    image_edit_prompt: str = Form(
        DEFAULT_IMAGE_EDIT_PROMPT,
        description="Prompt para mejorar la imagen sin modificar la comida",
    ),
) -> GenerateVideoResponse:
    """Ejecuta el pipeline completo y devuelve URLs de los artefactos."""
    references = list(reference_images or [])
    if len(references) > MAX_REFERENCE_IMAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Se permiten como máximo {MAX_REFERENCE_IMAGES} imágenes de referencia",
        )
    try:
        metadata = DishMetadata(
            title_plate=title_plate,
            description_plate=description_plate,
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error

    try:
        settings.validate_runtime_credentials()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    job_id = uuid.uuid4().hex
    run_dir = settings.outputs_dir / job_id
    run_dir.mkdir(parents=True, exist_ok=False)
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
        image.content_type or "", ".bin"
    )
    original_image_path = run_dir / f"original_image{suffix}"
    reference_image_paths: list[pathlib.Path] = []
    try:
        await _save_upload(
            image,
            original_image_path,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
        for index, reference in enumerate(references, start=1):
            reference_suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }.get(reference.content_type or "", ".bin")
            reference_path = run_dir / f"reference_image_{index}{reference_suffix}"
            await _save_upload(
                reference,
                reference_path,
                max_bytes=settings.max_upload_mb * 1024 * 1024,
            )
            reference_image_paths.append(reference_path)
        result = await run_in_threadpool(
            run_pipeline,
            original_image_path=original_image_path,
            run_dir=run_dir,
            settings=settings,
            image_edit_prompt=image_edit_prompt,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
            reference_image_paths=reference_image_paths,
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        await image.close()
        for reference in references:
            await reference.close()

    base_url = str(request.base_url).rstrip("/")
    enhanced_name = pathlib.Path(result["enhanced_image_path"]).name
    return GenerateVideoResponse(
        job_id=result["job_id"],
        status="completed",
        title_plate=result["title_plate"],
        description_plate=result["description_plate"],
        menu_context_en=result["menu_context_en"],
        menu_image_alignment=result["menu_image_alignment"],
        menu_visual_conflicts=result["menu_visual_conflicts"],
        original_image_url=f"{base_url}/artifacts/{job_id}/{original_image_path.name}",
        enhanced_image_url=f"{base_url}/artifacts/{job_id}/{enhanced_name}",
        reference_image_urls=[
            f"{base_url}/artifacts/{job_id}/{pathlib.Path(path).name}"
            for path in result["reference_image_paths"]
        ],
        reference_count=result["reference_count"],
        video_mode=result["video_mode"],
        video_url=f"{base_url}/artifacts/{job_id}/video.mp4",
        analysis=result["analysis"],
        integrity_check=result["integrity_check"],
        h3_prompt=result["h3_prompt"],
        artifacts_dir=result["artifacts_dir"],
    )

