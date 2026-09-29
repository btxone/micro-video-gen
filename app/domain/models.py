from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued")
    title_plate: Mapped[str] = mapped_column(String(120))
    description_plate: Mapped[str] = mapped_column(Text)
    image_edit_prompt: Mapped[str] = mapped_column(Text)
    master_selection_mode: Mapped[str] = mapped_column(String(32), default="primary")
    enhance_references: Mapped[bool] = mapped_column(Boolean, default=False)
    pipeline_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    stages: Mapped[list[StageRun]] = relationship(back_populates="job", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="job", cascade="all, delete-orphan")
    generations: Mapped[list[Generation]] = relationship(back_populates="job", cascade="all, delete-orphan")


class StageRun(Base):
    __tablename__ = "stage_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    operation_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="stages")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(1024))
    source_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="artifacts")


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="generations")


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    schema_json: Mapped[dict[str, Any]] = mapped_column("schema", JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CostEvent(Base):
    __tablename__ = "cost_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(128))
    units: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
