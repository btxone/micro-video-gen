from __future__ import annotations

from pathlib import Path

import boto3

from app.config import Settings


class S3ArtifactStorage:
    def __init__(self, settings: Settings):
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET es obligatorio cuando STORAGE_BACKEND=s3")
        self.bucket = settings.s3_bucket
        self.expiration = 3600
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )

    def put_file(self, source: Path, object_key: str) -> str:
        self.client.upload_file(str(source), self.bucket, object_key)
        return object_key

    def local_path(self, object_key: str) -> Path:
        raise RuntimeError("Los artefactos S3 no tienen una ruta local")

    def url(self, object_key: str, base_url: str = "") -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=self.expiration,
        )
