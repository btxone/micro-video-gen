from __future__ import annotations

import concurrent.futures
import pathlib
import shutil
from typing import Any

from app.clients.gemini import GeminiClient
from app.clients.runpod import RunpodClient
from app.config import Settings
from app.http_client import write_json
from app.prompts import (
    DEFAULT_IMAGE_EDIT_PROMPT,
    STRICT_RETRY_PROMPT_SUFFIX,
    build_image_edit_prompt,
    build_reference_image_edit_prompt,
    build_ref2va_prompt,
)
from app.schemas import DishMetadata, MAX_REFERENCE_IMAGES
from app.services.video_qc import validate_mp4, video_qc_passed
from app.services.reference_manifest import create_reference_manifest
from app.services.input_validation import inspect_image


def render_h3_prompt(
    analysis: dict[str, Any],
    *,
    duration_seconds: float = 5.0,
    retry_feedback: list[str] | None = None,
) -> str:
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
        duration_seconds=duration_seconds,
        retry_feedback=retry_feedback,
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


def _integrity_passed(
    integrity: dict[str, Any], *, min_confidence: float = 0.75
) -> bool:
    confidence = integrity.get("confidence")
    return all(
        integrity.get(field) is True
        for field in (
            "food_preserved",
            "plate_preserved",
            "product_composition_preserved",
            "background_professional",
            "background_consistent_with_master",
            "advertising_quality_improved",
            "no_menu_driven_visual_changes",
        )
    ) and isinstance(confidence, (int, float)) and float(confidence) >= min_confidence


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

    manifest = create_reference_manifest(
        primary_path=original_image_path,
        reference_paths=references,
        run_dir=run_dir,
        settings=settings,
        job_id=run_dir.name,
    )
    write_json(
        run_dir / "request_metadata.json",
        {
            "title_plate": metadata.title_plate,
            "description_plate": metadata.description_plate,
            "video_mode": "ref2va",
            "reference_count": len(references),
            "reference_images": [image.original_path.name for image in manifest.references],
            "provider_images": [image.provider_path.relative_to(run_dir).as_posix() for image in manifest.images],
            "reference_order": [
                {"picture_number": image.picture_number, "reference_index": image.reference_index}
                for image in manifest.images
            ],
        },
    )

    # La visión y la edición reciben copias normalizadas del mismo manifest; los originales
    # permanecen intactos para conservar la entrada y la trazabilidad.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        analysis_future = executor.submit(
            gemini.analyze_image,
            manifest.primary.provider_path,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
            reference_image_paths=[image.provider_path for image in manifest.references],
        )
        enhancement_future = executor.submit(
            runpod.enhance_image,
            original_image_path=manifest.primary.provider_path,
            prompt=enriched_image_prompt,
            run_dir=run_dir,
        )
        analysis, analysis_response_id = analysis_future.result()
        enhanced_image_path, enhancement_status = enhancement_future.result()

    write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
    manifest.primary.enhanced_path = enhanced_image_path
    enhanced_info = inspect_image(enhanced_image_path, "image/png", max_pixels=settings.max_image_pixels)
    manifest.primary.enhanced_sha256 = str(enhanced_info["sha256"])
    manifest.primary.enhanced_bytes = enhanced_image_path.stat().st_size
    for entry, description in zip(manifest.references, analysis["reference_descriptions"], strict=True):
        entry.description = description
    analysis["reference_descriptions_by_picture"] = [
        {
            "picture_number": entry.picture_number,
            "reference_index": entry.reference_index,
            "provider_sha256": entry.provider_sha256,
            "description": entry.description,
        }
        for entry in manifest.references
    ]
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
    manifest.save(run_dir / "reference_manifest.json")

    if analysis["menu_image_alignment"] == "conflicting":
        raise RuntimeError(
            "La descripción del menú entra en conflicto con la imagen: "
            + "; ".join(analysis["menu_visual_conflicts"])
        )

    integrity_attempt = 1
    while True:
        integrity, integrity_response_id = gemini.verify_integrity(
            manifest.primary.provider_path,
            enhanced_image_path,
            title_plate=metadata.title_plate,
            description_plate=metadata.description_plate,
        )
        write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
        integrity_record = {
            "provider": "gemini",
            "model": settings.gemini_vision_model,
            "response_id": integrity_response_id,
            "attempt": integrity_attempt,
            "passed": _integrity_passed(
                integrity, min_confidence=settings.image_qc_min_confidence
            ),
            **integrity,
        }
        write_json(run_dir / "image_integrity_check.json", integrity_record)
        if integrity_record["passed"]:
            break
        if integrity_attempt >= max(1, settings.image_max_attempts):
            raise RuntimeError(
                "La imagen principal mejorada no pasó la verificación: "
                + "; ".join(integrity.get("violations") or integrity.get("differences") or [])
            )
        integrity_attempt += 1
        enhanced_image_path, enhancement_status = runpod.enhance_image(
            original_image_path=manifest.primary.provider_path,
            prompt=enriched_image_prompt + STRICT_RETRY_PROMPT_SUFFIX,
            run_dir=run_dir,
            job_file_name=f"nano_banana_primary_attempt_{integrity_attempt}.json",
            output_file_name=f"enhanced_image_attempt_{integrity_attempt}.png",
        )
        write_json(
            run_dir / f"image_enhancement_primary_attempt_{integrity_attempt}.json",
            enhancement_status,
        )

    manifest.primary.enhanced_path = enhanced_image_path
    enhanced_info = inspect_image(
        enhanced_image_path,
        "image/png",
        max_pixels=settings.max_image_pixels,
    )
    manifest.primary.enhanced_sha256 = str(enhanced_info["sha256"])
    manifest.primary.enhanced_bytes = enhanced_image_path.stat().st_size
    manifest.primary.integrity = integrity_record
    manifest.save(run_dir / "reference_manifest.json")

    enhanced_reference_paths: list[pathlib.Path] = []
    reference_integrity_records: list[dict[str, Any]] = []
    if references and not settings.enhance_references_enabled:
        raise RuntimeError(
            "ENHANCE_REFERENCES_ENABLED debe permanecer activo cuando se proporcionan referencias; "
            "el pipeline no enviará fondos originales a H3."
        )
    reference_prompt = build_reference_image_edit_prompt(
        title_plate=metadata.title_plate,
        description_plate=metadata.description_plate,
    )
    for entry in manifest.references:
        reference_path = entry.provider_path
        index = entry.reference_index
        assert index is not None
        reference_attempt = 1
        while True:
            prompt = reference_prompt + (
                STRICT_RETRY_PROMPT_SUFFIX if reference_attempt > 1 else ""
            )
            enhanced_reference_path, reference_status = runpod.enhance_image(
                original_image_path=reference_path,
                style_reference_image_path=enhanced_image_path,
                prompt=prompt,
                run_dir=run_dir,
                job_file_name=f"nano_banana_reference_{index}_attempt_{reference_attempt}.json",
                output_file_name=f"enhanced_reference_{index}_attempt_{reference_attempt}.png",
            )
            reference_integrity, reference_response_id = gemini.verify_integrity(
                reference_path,
                enhanced_reference_path,
                title_plate=metadata.title_plate,
                description_plate=metadata.description_plate,
                master_style_image_path=enhanced_image_path,
            )
            write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
            reference_record = {
                "provider": "gemini",
                "model": settings.gemini_vision_model,
                "response_id": reference_response_id,
                "reference_index": index,
                "picture_number": entry.picture_number,
                "attempt": reference_attempt,
                "passed": _integrity_passed(
                    reference_integrity,
                    min_confidence=settings.image_qc_min_confidence,
                ),
                "runpod": reference_status,
                **reference_integrity,
            }
            write_json(
                run_dir / f"image_integrity_reference_{index}.json",
                reference_record,
            )
            if reference_record["passed"]:
                enhanced_reference_paths.append(enhanced_reference_path)
                reference_integrity_records.append(reference_record)
                entry.enhanced_path = enhanced_reference_path
                entry.enhanced_sha256 = str(
                    inspect_image(
                        enhanced_reference_path,
                        "image/png",
                        max_pixels=settings.max_image_pixels,
                    )["sha256"]
                )
                entry.enhanced_bytes = enhanced_reference_path.stat().st_size
                entry.integrity = reference_record
                manifest.save(run_dir / "reference_manifest.json")
                break
            if reference_attempt >= max(1, settings.image_max_attempts):
                raise RuntimeError(
                    f"La referencia {index} no pudo adoptar el set profesional sin alterar "
                    "el producto: "
                    + "; ".join(
                        reference_integrity.get("violations")
                        or reference_integrity.get("differences")
                        or []
                    )
                )
            reference_attempt += 1

    reference_set_record: dict[str, Any] | None = None
    if manifest.references:
        reference_set, reference_set_response_id = gemini.verify_reference_set(
            master_style_image_path=enhanced_image_path,
            reference_original_paths=[item.provider_path for item in manifest.references],
            enhanced_reference_paths=enhanced_reference_paths,
        )
        write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
        reference_set_record = {
            "provider": "gemini",
            "model": settings.gemini_vision_model,
            "response_id": reference_set_response_id,
            "passed": (
                reference_set["all_food_preserved"]
                and reference_set["backgrounds_consistent"]
                and reference_set["lighting_consistent"]
                and reference_set["professional_set"]
                and all(
                    item["food_preserved"] and item["background_consistent"]
                    for item in reference_set["reference_checks"]
                )
                and reference_set["confidence"] >= settings.image_qc_min_confidence
            ),
            **reference_set,
        }
        for entry, group_check in zip(
            manifest.references,
            reference_set_record["reference_checks"],
            strict=True,
        ):
            entry.integrity = {
                **(entry.integrity or {}),
                "reference_set_check": group_check,
            }
        manifest.save(run_dir / "reference_manifest.json")
        write_json(run_dir / "reference_set_integrity_check.json", reference_set_record)
        if not reference_set_record["passed"]:
            failed_flags = [
                name
                for name in (
                    "all_food_preserved",
                    "backgrounds_consistent",
                    "lighting_consistent",
                    "professional_set",
                )
                if not reference_set.get(name)
            ]
            raise RuntimeError(
                "El control conjunto no aprobó el set. Campos fallidos: "
                + ", ".join(failed_flags)
                + ". Incidencias: "
                + "; ".join(
                    issue
                    for item in reference_set.get("reference_checks", [])
                    for issue in item.get("issues", [])
                )
            )

    manifest.validate(require_enhanced=True)
    prompt_input = {
        **analysis,
        "title_plate": metadata.title_plate,
        "description_plate": metadata.description_plate,
    }
    prompt_path = run_dir / "h3_prompt.txt"
    video_statuses: list[dict[str, Any]] = []
    video_qc_record: dict[str, Any] | None = None
    retry_feedback: list[str] = []
    video_path: pathlib.Path | None = None
    h3_prompt = ""
    for video_attempt in range(1, max(1, settings.video_max_attempts) + 1):
        h3_prompt = render_h3_prompt(
            prompt_input,
            duration_seconds=settings.video_duration_seconds,
            retry_feedback=retry_feedback,
        )
        prompt_path.write_text(h3_prompt + "\n", encoding="utf-8")
        (run_dir / f"h3_prompt_attempt_{video_attempt}.txt").write_text(
            h3_prompt + "\n", encoding="utf-8"
        )
        candidate_video_path, video_status = runpod.generate_video(
            image_paths=[image.enhanced_path for image in manifest.images if image.enhanced_path],
            h3_prompt=h3_prompt,
            run_dir=run_dir,
            job_file_name=(
                "runpod_job.json"
                if video_attempt == 1
                else f"runpod_video_attempt_{video_attempt}.json"
            ),
            output_file_name=f"video_attempt_{video_attempt}.mp4",
            picture_numbers=[image.picture_number for image in manifest.images],
            reference_indices=[image.reference_index for image in manifest.images],
        )
        validate_mp4(candidate_video_path)
        video_statuses.append(video_status)
        if not settings.video_qc_enabled:
            video_path = candidate_video_path
            break
        video_qc, video_qc_response_id = gemini.verify_video(
            candidate_video_path,
            enhanced_image_path,
            title_plate=metadata.title_plate,
            expected_duration_seconds=settings.video_duration_seconds,
        )
        write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
        video_qc_record = {
            "provider": "gemini",
            "model": settings.gemini_vision_model,
            "response_id": video_qc_response_id,
            "attempt": video_attempt,
            "passed": video_qc_passed(
                video_qc, min_confidence=settings.video_qc_min_confidence
            ),
            **video_qc,
        }
        write_json(run_dir / f"video_qc_attempt_{video_attempt}.json", video_qc_record)
        write_json(run_dir / "video_qc.json", video_qc_record)
        if video_qc_record["passed"]:
            video_path = candidate_video_path
            break
        retry_feedback = list(video_qc.get("violations") or [])
        if video_qc.get("observed_motion"):
            retry_feedback.append("Observed motion was: " + video_qc["observed_motion"])

    if video_path is None:
        raise RuntimeError(
            "El video no pasó el QC orbital después de los reintentos permitidos: "
            + "; ".join(retry_feedback)
        )
    final_video_path = run_dir / "video.mp4"
    if video_path != final_video_path:
        shutil.copyfile(video_path, final_video_path)
    video_path = final_video_path
    video_status = video_statuses[-1]
    write_json(run_dir / "gemini_file_uploads.json", gemini.file_upload_events)
    manifest.save(run_dir / "reference_manifest.json")
    result = {
        "status": "completed",
        "title_plate": metadata.title_plate,
        "description_plate": metadata.description_plate,
        "video_mode": "ref2va",
        "reference_count": len(references),
        "reference_images": [str(image.original_path) for image in manifest.references],
        "enhanced_reference_images": [str(path) for path in enhanced_reference_paths],
        "reference_manifest": str(run_dir / "reference_manifest.json"),
        "normalized_image": str(manifest.primary.provider_path),
        "normalized_reference_images": [str(image.provider_path) for image in manifest.references],
        "reference_set_integrity_check": (
            str(run_dir / "reference_set_integrity_check.json") if reference_set_record else None
        ),
        "reference_manifest_path": str(run_dir / "reference_manifest.json"),
        "gemini_file_upload_log_path": str(run_dir / "gemini_file_uploads.json"),
        "provider_payload_manifest_paths": [
            str(path) for path in sorted(run_dir.glob("*_payload_manifest.json"))
        ],
        "menu_context_en": analysis["menu_context_en"],
        "menu_image_alignment": analysis["menu_image_alignment"],
        "menu_visual_conflicts": analysis["menu_visual_conflicts"],
        "original_image": str(original_image_path),
        "enhanced_image": str(enhanced_image_path),
        "analysis": str(run_dir / "image_analysis.json"),
        "integrity_check": str(run_dir / "image_integrity_check.json"),
        "reference_integrity_checks": [
            str(run_dir / f"image_integrity_reference_{item['reference_index']}.json")
            for item in reference_integrity_records
        ],
        "video_qc": str(run_dir / "video_qc.json") if video_qc_record else None,
        "h3_prompt": str(prompt_path),
        "video": str(video_path),
        "runpod": video_status,
        "video_attempts": video_statuses,
    }
    write_json(run_dir / "result.json", result)
    return {
        "job_id": run_dir.name,
        "original_image_path": str(original_image_path),
        "enhanced_image_path": str(enhanced_image_path),
        "video_path": str(video_path),
        "reference_manifest_path": str(run_dir / "reference_manifest.json"),
        "normalized_image_path": str(manifest.primary.provider_path),
        "normalized_reference_image_paths": [str(item.provider_path) for item in manifest.references],
        "reference_set_integrity_check": reference_set_record,
        "reference_manifest_path": str(run_dir / "reference_manifest.json"),
        "gemini_file_upload_log_path": str(run_dir / "gemini_file_uploads.json"),
        "provider_payload_manifest_paths": [
            str(path) for path in sorted(run_dir.glob("*_payload_manifest.json"))
        ],
        "title_plate": metadata.title_plate,
        "description_plate": metadata.description_plate,
        "video_mode": "ref2va",
        "reference_count": len(references),
        "reference_image_paths": [str(image.original_path) for image in manifest.references],
        "enhanced_reference_image_paths": [str(path) for path in enhanced_reference_paths],
        "menu_context_en": analysis["menu_context_en"],
        "menu_image_alignment": analysis["menu_image_alignment"],
        "menu_visual_conflicts": analysis["menu_visual_conflicts"],
        "analysis": analysis,
        "integrity_check": integrity_record,
        "reference_integrity_checks": reference_integrity_records,
        "video_qc": video_qc_record,
        "h3_prompt": h3_prompt,
        "artifacts_dir": str(run_dir),
    }

