from __future__ import annotations

from celery import Celery

from app.config import get_settings


settings = get_settings()
celery_app = Celery("h3_food_api", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_track_started=True, task_serializer="json", result_serializer="json", accept_content=["json"])


@celery_app.task(name="h3.run_job")
def run_job_task(job_id: str) -> None:
    from app.services.job_service import run_job

    run_job(job_id)
