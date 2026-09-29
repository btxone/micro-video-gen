"""Prueba directa del endpoint MiniMax H3 con una principal y hasta cuatro refs."""

from __future__ import annotations

import argparse
import pathlib
import time

from app.clients.runpod import RunpodClient
from app.config import Settings
from app.services.pipeline import render_h3_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=pathlib.Path, required=True)
    parser.add_argument(
        "--reference",
        type=pathlib.Path,
        action="append",
        default=[],
        help="Referencia opcional; repetir hasta cuatro veces.",
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if len(args.reference) > 4:
        raise SystemExit("Se permiten como máximo cuatro --reference")
    image_paths = [args.primary, *args.reference]
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        raise SystemExit("No existen estos archivos: " + ", ".join(missing))

    settings = Settings()
    if not settings.runpod_api_key.strip():
        raise SystemExit("Falta RUNPOD_API_KEY")
    run_dir = args.output_dir or (
        settings.outputs_dir / f"qa_ref2va_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    prompt = render_h3_prompt(
        {
            "title_plate": args.title,
            "description_plate": args.description,
            "menu_context_en": args.description,
            "main_subject": "plated food product",
            "preservation_description": (
                "food, ingredients, plate, textures, arrangement, crop, and composition"
            ),
            "reference_descriptions": [
                f"supplementary food reference supplied as {path.name}"
                for path in args.reference
            ],
        }
    )
    (run_dir / "h3_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    video_path, status = RunpodClient(settings).generate_video(
        image_paths=image_paths,
        h3_prompt=prompt,
        run_dir=run_dir,
    )
    print(f"QA_COMPLETED video={video_path}")
    print(
        "QA_RESULT "
        f"images={len(image_paths)} status={status.get('status')} "
        f"bytes={video_path.stat().st_size}"
    )


if __name__ == "__main__":
    main()
