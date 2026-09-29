"""QA black-box del endpoint asíncrono.

Uso local mock (servidor iniciado con PIPELINE_MODE=mock):
  python scripts/qa_v2.py --base-url http://127.0.0.1:8000 --image ./dish.png

Uso real: inicia el API con credenciales y PIPELINE_MODE=real; el script solo
envía la imagen y espera el job, nunca imprime secretos.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import time
import urllib.request
import uuid
from pathlib import Path


def multipart(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = "----H3QA" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    for field, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_json(url: str, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if content_type:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--title", default="Dish QA")
    parser.add_argument("--description", default="A plated dish prepared for automated QA validation.")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if len(args.reference) > 4:
        raise SystemExit("--reference acepta como máximo cuatro imágenes")
    files = [("image", args.image), *[("reference_images", path) for path in args.reference]]
    body, content_type = multipart(
        {"title_plate": args.title, "description_plate": args.description}, files
    )
    payload = request_json(
        args.base_url.rstrip("/") + "/v2/jobs",
        method="POST",
        body=body,
        content_type=content_type,
        headers={"Idempotency-Key": "qa-v2-" + uuid.uuid4().hex},
    )
    job_id = payload["job_id"]
    print(f"submitted job={job_id} references={payload['reference_count']}")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        payload = request_json(args.base_url.rstrip("/") + f"/v2/jobs/{job_id}")
        print(f"status={payload['status']} stage={payload['current_stage']}")
        if payload["status"] in {"completed", "failed", "cancelled"}:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if payload["status"] != "completed":
                raise SystemExit(1)
            return
        time.sleep(5)
    raise SystemExit("timeout esperando el job")


if __name__ == "__main__":
    main()
