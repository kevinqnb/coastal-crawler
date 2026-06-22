"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from coastal_crawler.config import get_settings
        _engine = create_engine(get_settings().database_url)
    return _engine


def _get_factory() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _factory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a transactional database session; commit on success, rollback on error."""
    session: Session = _get_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
