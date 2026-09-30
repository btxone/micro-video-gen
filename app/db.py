from __future__ import annotations

from collections.abc import Generator

import re

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    url = get_settings().database_url
    # El entorno compartido puede traer asyncpg desde otro servicio. Este módulo
    # usa SQLAlchemy síncrono, por lo que normalizamos ese alias al driver psycopg.
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+asyncpg://")
    return url


engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False} if _engine_url().startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from app.domain.models import Artifact, CostEvent, Generation, Job, PromptVersion, StageRun  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # Older installations were initialized with create_all rather than Alembic. Add this
    # nullable ordering field in place so their persisted jobs remain readable after upgrade.
    columns = {column["name"] for column in inspect(engine).get_columns("artifacts")}
    if "reference_index" not in columns:
        try:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE artifacts ADD COLUMN reference_index INTEGER"))
        except SQLAlchemyError:
            # A second worker may have applied the same additive change during startup.
            columns = {column["name"] for column in inspect(engine).get_columns("artifacts")}
            if "reference_index" not in columns:
                raise
    index = next(
        item
        for item in Artifact.__table__.indexes
        if item.name == "ix_artifacts_job_kind_reference"
    )
    try:
        index.create(bind=engine, checkfirst=True)
    except SQLAlchemyError:
        if not any(
            item["name"] == index.name
            for item in inspect(engine).get_indexes("artifacts")
        ):
            raise
    with engine.begin() as connection:
        rows = connection.execute(
            select(Artifact.id, Artifact.kind, Artifact.object_key, Artifact.reference_index)
            .where(Artifact.reference_index.is_(None))
        ).all()
        for artifact_id, kind, object_key, _ in rows:
            value: int | None = None
            if kind in {"original_image", "enhanced_image", "normalized_image"}:
                value = 0
            elif kind in {"reference_image", "enhanced_reference_image", "normalized_reference_image"}:
                match = re.search(r"(?:reference_image|enhanced_reference)_(\d+)", str(object_key))
                if match:
                    value = int(match.group(1))
            if value is not None:
                connection.execute(
                    text("UPDATE artifacts SET reference_index = :idx WHERE id = :artifact_id"),
                    {"idx": value, "artifact_id": artifact_id},
                )


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
