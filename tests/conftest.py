"""Shared pytest fixtures.

Database tests require a live PostgreSQL instance.  Set TEST_DATABASE_URL
in your environment (or .env) to enable them; tests are skipped otherwise.

    TEST_DATABASE_URL=postgresql://user:pass@localhost/crawler_test pytest

Two session fixtures are provided:

``db_session``
    Wraps each test in a transaction that is always rolled back.  Fast and
    fully isolated — no committed state leaks between tests.  Use this for
    the vast majority of storage tests.

``clean_db``
    Yields the raw engine for tests that require real committed transactions
    (e.g. SKIP LOCKED, which must be visible across independent connections).
    Truncates all tables after each test for cleanup.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from coastal_crawler.db.models import Base


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run database tests.")
    return url


@pytest.fixture(scope="session")
def db_engine(db_url: str) -> Engine:  # type: ignore[misc]
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    yield engine  # type: ignore[misc]
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Session:  # type: ignore[misc]
    """Yields a session that is always rolled back — tests never persist data."""
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session: Session = factory()
    yield session  # type: ignore[misc]
    session.rollback()
    session.close()


@pytest.fixture
def clean_db(db_engine: Engine) -> Engine:  # type: ignore[misc]
    """Yields the engine; truncates all tables after the test.

    Use only for tests that need real committed transactions (e.g. SKIP LOCKED).
    """
    yield db_engine  # type: ignore[misc]
    with Session(db_engine) as s:
        s.execute(
            text("TRUNCATE papers, extractions, crawl_state RESTART IDENTITY CASCADE")
        )
        s.commit()
