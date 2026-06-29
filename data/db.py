"""NGSAT database connection and session management."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import DatabaseConfig, load_config
from core.models import Base


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_engine_lock = Lock()
_session_lock = Lock()


def get_engine(config: DatabaseConfig | None = None) -> Engine:
    """Get or create the SQLAlchemy engine (thread-safe singleton).

    Args:
        config: Database config. If None, loads from .env.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            # Double-checked locking
            if _engine is None:
                if config is None:
                    config = load_config().database
                _engine = create_engine(
                    config.url,
                    echo=config.echo,
                    pool_pre_ping=True,
                    pool_size=10,
                    max_overflow=20,
                )
    return _engine


def get_session_factory() -> sessionmaker:
    """Get or create the session factory (singleton).

    Returns:
        SQLAlchemy sessionmaker.
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Context manager for database sessions.

    Usage:
        with db_session() as session:
            repo = Repository(session)
            repo.save_trade(record)

    Automatically commits on success, rolls back on exception.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database(config: DatabaseConfig | None = None) -> None:
    """Create all tables. Call once on startup.

    Args:
        config: Database config. If None, loads from .env.
    """
    engine = get_engine(config)
    Base.metadata.create_all(engine)
