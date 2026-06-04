"""Database engine, session factory and schema bootstrap (synchronous SQLAlchemy 2.0).

Synchronous on purpose: FastAPI runs sync path operations in a threadpool, which keeps
the data layer simple, avoids the greenlet dependency, and swaps cleanly from SQLite to
Postgres/SQL Server by changing ``FINOPTIC_DATABASE_URL`` only.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from finoptic.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

_connect_args = {"check_same_thread": False} if _settings.is_sqlite else {}
engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables. Importing models here registers them on ``Base.metadata``."""
    from finoptic import models  # noqa: F401  (side-effect: register tables)

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session and always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
