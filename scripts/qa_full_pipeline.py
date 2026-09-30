"""Ejecuta un QA real de imagen principal + referencias + H3 + QC orbital."""

from __future__ import annotations

import argparse
import json
import pathlib
import time

from app.config import Settings
from app.services.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--reference", type=pathlib.Path, action="append", default=[])
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if len(args.reference) > 4:
        raise SystemExit("Se permiten como máximo cuatro --reference")
    paths = [args.image, *args.reference]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("No existen estos archivos: " + ", ".join(missing))

    settings = Settings(pipeline_mode="real")
    settings.validate_runtime_credentials()
    run_dir = args.output_dir or (
        settings.outputs_dir / f"qa_full_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    result = run_pipeline(
        original_image_path=args.image,
        reference_image_paths=args.reference,
        run_dir=run_dir,
        settings=settings,
        title_plate=args.title,
        description_plate=args.description,
    )
    summary = {
        "status": "completed",
        "job_id": result["job_id"],
        "enhanced_image": result["enhanced_image_path"],
        "enhanced_references": result["enhanced_reference_image_paths"],
        "video": result["video_path"],
        "image_integrity": result["integrity_check"],
        "reference_integrity": result["reference_integrity_checks"],
        "video_qc": result["video_qc"],
        "artifacts_dir": result["artifacts_dir"],
    }
    print("QA_FULL_RESULT=" + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
