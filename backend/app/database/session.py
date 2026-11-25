"""SQLAlchemy engine/session wiring and a connectivity probe.

The engine is created lazily so that importing this module never opens a
socket. That keeps unit tests and CLI commands usable even when PostgreSQL is
down; failures surface as a typed ``DatabaseStatus`` instead of a traceback.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all CivicBridge AI ORM models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class _LazyEngineProxy:
    """Backwards-friendly ``engine`` attribute without eager connections."""

    def __getattr__(self, item: str) -> Any:
        return getattr(get_engine(), item)


engine = _LazyEngineProxy()


@dataclass
class DatabaseStatus:
    """Outcome of a database connectivity probe."""

    connected: bool
    database_url: str
    server_version: str | None = None
    database_name: str | None = None
    database_owner: str | None = None
    current_user: str | None = None
    installed_extensions: list[str] = field(default_factory=list)
    missing_expected_extensions: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def extension_summary(self) -> str:
        return ", ".join(self.installed_extensions) if self.installed_extensions else "none"


EXPECTED_EXTENSIONS = ("pg_trgm", "uuid-ossp")


def check_database() -> DatabaseStatus:
    """Probe PostgreSQL: connectivity, identity, and usable extensions.

    Never raises: a failed connection is reported through the returned status.
    """
    status = DatabaseStatus(connected=False, database_url=settings.safe_database_url)

    try:
        with get_engine().connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
            row = connection.execute(
                text(
                    "SELECT current_database() AS db, current_user AS usr, "
                    "pg_get_userbyid(datdba) AS owner "
                    "FROM pg_database WHERE datname = current_database()"
                )
            ).one()
            extensions = [
                name
                for (name,) in connection.execute(
                    text("SELECT extname FROM pg_extension ORDER BY extname")
                )
            ]
    except Exception as exc:
        status.error = f"{type(exc).__name__}: {exc}".replace(settings.postgres_password, "***")
        logger.warning("PostgreSQL connectivity check failed: %s", status.error)
        return status

    status.connected = True
    status.server_version = str(version)
    status.database_name = row.db
    status.current_user = row.usr
    status.database_owner = row.owner
    status.installed_extensions = extensions
    status.missing_expected_extensions = [
        name for name in EXPECTED_EXTENSIONS if name not in extensions
    ]
    return status
