from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
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


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
