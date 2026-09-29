from __future__ import annotations

import concurrent.futures
import pathlib
from typing import Any

from app.clients.gemini import GeminiClient
from app.clients.runpod import RunpodClient
from app.config import Settings
from app.http_client import write_json
from app.prompts import (
    DEFAULT_IMAGE_EDIT_PROMPT,
    STRICT_RETRY_PROMPT_SUFFIX,
    build_image_edit_prompt,
    build_ref2va_prompt,
)
from app.schemas import DishMetadata, MAX_REFERENCE_IMAGES


def render_h3_prompt(analysis: dict[str, Any]) -> str:
    required = {
        "title_plate",
        "description_plate",
        "menu_context_en",
        "main_subject",
        "preservation_description",
        "reference_descriptions",
    }
    missing = required - analysis.keys()
    if missing:
        raise ValueError("Faltan datos para completar el template: " + ", ".join(sorted(missing)))
    reference_descriptions = analysis["reference_descriptions"]
    if not isinstance(reference_descriptions, list) or len(reference_descriptions) > MAX_REFERENCE_IMAGES:
        raise ValueError("reference_descriptions debe contener entre cero y cuatro elementos")
    prompt = build_ref2va_prompt(
        title_plate=analysis["title_plate"],
        description_plate=analysis["description_plate"],
        menu_context_en=analysis["menu_context_en"],
        main_subject=analysis["main_subject"],
        preservation_description=analysis["preservation_description"],
        reference_descriptions=reference_descriptions,
    )
    for label in (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    ):
        if label not in prompt:
            raise ValueError(f"El template no contiene la sección obligatoria: {label}")
    return prompt


def _integrity_passed(integrity: dict[str, Any]) -> bool:
    return all(
        integrity.get(field) is True
        for field in (
            "food_preserved",
            "plate_preserved",
            "composition_preserved",
            "advertising_quality_improved",
            "no_menu_driven_visual_changes",
        )
    )


def run_pipeline(
    *,
    original_image_path: pathlib.Path,
    run_dir: pathlib.Path,
    settings: Settings,
    image_edit_prompt: str | None = None,
    title_plate: str,
    description_plate: str,
    reference_image_paths: list[pathlib.Path] | None = None,
) -> dict[str, Any]:
    """Ejecuta image-to-image → Gemini → MiniMax H3 Ref2VA."""
    settings.validate_runtime_credentials()
    references = list(reference_image_paths or [])
    if len(references) > MAX_REFERENCE_IMAGES:
        raise ValueError(f"Se permiten como máximo {MAX_REFERENCE_IMAGES} imágenes de referencia")
    gemini = GeminiClient(settings)
    runpod = RunpodClient(settings)
    metadata = DishMetadata(
        title_plate=title_plate,
        description_plate=description_plate,
    )
    base_prompt = (image_edit_prompt or DEFAULT_IMAGE_EDIT_PROMPT).strip()
    if not base_prompt:
        raise ValueError("image_edit_prompt no puede estar vacío")
    enriched_image_prompt = build_image_edit_prompt(
        base_prompt,
        title_plate=metadata.title_plate,
        description_plate=metadata.description_plate,
    )

    write_json(
        run_dir / "request_metadata.json",
        {
            "title_plate": metadata.title_plate,
            "description_plate": metadata.description_plate,
            "video_mode": "ref2va",
            "reference_count": len(references),
            "reference_images": [str(path) for path in references],
        },
    )

    # Las dos operaciones leen la misma imagen original y se ejecutan en paralelo.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        analysis_future = executor.submit(
            gemini.analyze_image,
            original_image_path,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
            reference_image_paths=references,
        )
        enhancement_future = executor.submit(
            runpod.enhance_image,
            original_image_path=original_image_path,
            prompt=enriched_image_prompt,
            run_dir=run_dir,
        )
        analysis, analysis_response_id = analysis_future.result()
        enhanced_image_path, enhancement_status = enhancement_future.result()

    write_json(
        run_dir / "image_analysis.json",
        {
            "provider": "gemini",
            "model": settings.gemini_vision_model,
            "response_id": analysis_response_id,
            "title_plate": metadata.title_plate,
            "description_plate": metadata.description_plate,
            **analysis,
        },
    )
    write_json(run_dir / "image_enhancement.json", enhancement_status)

    if analysis["menu_image_alignment"] == "conflicting":
        raise RuntimeError(
            "La descripción del menú entra en conflicto con la imagen: "
            + "; ".join(analysis["menu_visual_conflicts"])
        )

    integrity, integrity_response_id = gemini.verify_integrity(
        original_image_path,
        enhanced_image_path,
        title_plate=metadata.title_plate,
        description_plate=metadata.description_plate,
    )
    integrity_attempt = 1
    if not _integrity_passed(integrity):
        enhanced_image_path, enhancement_status = runpod.enhance_image(
            original_image_path=original_image_path,
            prompt=enriched_image_prompt + STRICT_RETRY_PROMPT_SUFFIX,
            run_dir=run_dir,
            job_file_name="nano_banana_retry_job.json",
            output_file_name="enhanced_image_retry.png",
        )
        write_json(run_dir / "image_enhancement_retry.json", enhancement_status)
        integrity, integrity_response_id = gemini.verify_integrity(
            original_image_path,
            enhanced_image_path,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
        )
        integrity_attempt = 2

    integrity_record = {
        "provider": "gemini",
        "model": settings.gemini_vision_model,
        "response_id": integrity_response_id,
        "attempt": integrity_attempt,
        "passed": _integrity_passed(integrity),
        **integrity,
    }
    write_json(run_dir / "image_integrity_check.json", integrity_record)
    if not integrity_record["passed"]:
        raise RuntimeError(
            "La imagen mejorada no pasó la verificación de preservación: "
            + str(integrity)
        )

    h3_prompt = render_h3_prompt(
        {
            **analysis,
            "title_plate": metadata.title_plate,
            "description_plate": metadata.description_plate,
        }
    )
    prompt_path = run_dir / "h3_prompt.txt"
    prompt_path.write_text(h3_prompt + "\n", encoding="utf-8")

    video_path, video_status = runpod.generate_video(
        image_paths=[enhanced_image_path, *references],
        h3_prompt=h3_prompt,
        run_dir=run_dir,
    )
    result = {
        "status": "completed",
        "title_plate": metadata.title_plate,
        "description_plate": metadata.description_plate,
        "video_mode": "ref2va",
        "reference_count": len(references),
        "reference_images": [str(path) for path in references],
        "menu_context_en": analysis["menu_context_en"],
        "menu_image_alignment": analysis["menu_image_alignment"],
        "menu_visual_conflicts": analysis["menu_visual_conflicts"],
        "original_image": str(original_image_path),
        "enhanced_image": str(enhanced_image_path),
        "analysis": str(run_dir / "image_analysis.json"),
        "integrity_check": str(run_dir / "image_integrity_check.json"),
        "h3_prompt": str(prompt_path),
        "video": str(video_path),
        "runpod": video_status,
    }
    write_json(run_dir / "result.json", result)
    return {
        "job_id": run_dir.name,
        "original_image_path": str(original_image_path),
        "enhanced_image_path": str(enhanced_image_path),
        "video_path": str(video_path),
        "title_plate": metadata.title_plate,
        "description_plate": metadata.description_plate,
        "video_mode": "ref2va",
        "reference_count": len(references),
        "reference_image_paths": [str(path) for path in references],
        "menu_context_en": analysis["menu_context_en"],
        "menu_image_alignment": analysis["menu_image_alignment"],
        "menu_visual_conflicts": analysis["menu_visual_conflicts"],
        "analysis": analysis,
        "integrity_check": integrity_record,
        "h3_prompt": h3_prompt,
        "artifacts_dir": str(run_dir),
    }

