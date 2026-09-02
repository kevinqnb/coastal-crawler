"""Guards on Settings construction in the test environment.

The ``_isolate_settings`` autouse fixture (conftest.py) must keep every
``Settings`` built during the suite free of the developer's real ``.env`` and
exported credentials. If that isolation regresses (e.g. a pydantic-settings
upgrade changes how ``env_file=None`` behaves), a stale discovery test can
reach a real API with a real key and block for minutes on rate-limit backoff.
This is the standing check against exactly that.
"""

from __future__ import annotations

import os

import pytest

from coastal_crawler.config import Settings


def test_settings_reads_no_dotenv_or_credentials() -> None:
    s = Settings(database_url="postgresql://user:pass@localhost/x")
    assert s.semantic_scholar_api_key is None
    assert s.semantic_scholar_query is None
    assert s.wiley_api_key is None
    assert s.wiley_issns == []
    assert s.openalex_api_key is None
    assert s.filter_model is None
    assert s.doc_lm_model is None
    assert s.meas_lm_model is None


def test_dotenv_source_is_disabled() -> None:
    assert Settings.model_config.get("env_file") is None


def test_database_url_env_var_still_visible() -> None:
    """DATABASE_URL / TEST_DATABASE_URL are intentionally NOT isolated —
    test_ocr_worker and the db_url fixture depend on them."""
    os.environ["DATABASE_URL"] = "postgresql://sentinel@localhost/db"
    try:
        assert Settings().database_url == "postgresql://sentinel@localhost/db"
    finally:
        del os.environ["DATABASE_URL"]


def test_retry_backoff_is_blocked_in_tests() -> None:
    """The ``_no_retry_backoff`` autouse fixture replaces sources.http._sleep
    so any real rate-limit backoff during a test fails immediately instead of
    stalling for minutes. Keep the ``_sleep`` indirection in http.py that this
    relies on."""
    from coastal_crawler.sources import http

    with pytest.raises(AssertionError, match="real HTTP endpoint"):
        http._sleep(60.0)
