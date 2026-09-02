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
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from coastal_crawler.config import Settings, get_settings
from coastal_crawler.db.models import Base

# Real-credential env vars that must never leak into a test's ``Settings``.
# A stale test that reaches a real API (with a real key) can burn quota and
# block for minutes on rate-limit backoff — see test_settings_reads_no_dotenv.
# DATABASE_URL / TEST_DATABASE_URL are deliberately absent: test_ocr_worker
# and the db_url fixture rely on them.
_ISOLATED_ENV_VARS = (
    "OPENALEX_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "SEMANTIC_SCHOLAR_QUERY",
    "SEMANTIC_SCHOLAR_QUERIES",
    "WILEY_API_KEY",
    "WILEY_ISSNS",
    "WILEY_SUBJECTS",
    "FILTER_MODEL",
    "FILTER_RELEVANCE_PROMPT",
    "DOC_LM_MODEL",
    "MEAS_LM_MODEL",
    "MEAS_LM_ENTITY_IDENTIFICATION_PROMPT",
    "JUDGE_MODEL",
    "JUDGE_PROBE_PATH",
)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build every ``Settings`` in the test suite from explicit kwargs only.

    Without this, ``Settings()`` loads the developer's real ``.env`` (its
    ``model_config`` sets ``env_file=".env"``) plus any exported credential
    env vars, so a test that forgets to mock an HTTP call silently reaches a
    real API with a real key. Neutralising the dotenv source and the
    credential vars makes test runs hermetic and deterministic.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in _ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a discovery test triggers the HTTP retry/backoff path.

    ``sources/http.get_with_retry`` only sleeps after a real ``429`` response,
    which a correctly-mocked test never produces. A call here therefore means
    the test is hitting a live API — the failure mode that made one stale test
    take ~7 minutes of rate-limit backoff. Turn it into an instant error.
    """

    def _fail(seconds: float) -> None:
        raise AssertionError(
            f"sources.http retry backoff slept {seconds}s — a test reached a "
            "real HTTP endpoint instead of a mock. Mock httpx.Client for this test."
        )

    monkeypatch.setattr("coastal_crawler.sources.http._sleep", _fail)


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
        s.execute(text("TRUNCATE papers, extractions, crawl_state RESTART IDENTITY CASCADE"))
        s.commit()
