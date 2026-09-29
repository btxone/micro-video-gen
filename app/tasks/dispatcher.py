from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.config import Settings
from app.services.job_service import run_job


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="h3-job")


def enqueue_job(job_id: str, settings: Settings | None = None) -> str:
    settings = settings or __import__("app.config", fromlist=["get_settings"]).get_settings()
    if settings.celery_enabled:
        from app.tasks.celery_app import run_job_task

        return str(run_job_task.delay(job_id).id)
    _executor.submit(run_job, job_id, settings)
    return f"local-{job_id}"
