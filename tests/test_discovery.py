"""Tests for discovery sources and the discover() orchestrator.

All source tests use ``clean_db`` (not ``db_session``) because sources commit
per-page internally — rolling back after a commit would leave stale data.

HTTP calls are mocked via ``_mock_http`` (patches ``httpx.Client`` in the
source module). The ``_isolate_settings`` autouse fixture (conftest.py) keeps
every ``Settings`` here free of the real ``.env``, so a source only reaches an
API when a test explicitly configures a key AND mocks the client.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.config import Settings
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper
from coastal_crawler.sources.openalex import OpenAlexSource, _normalize_doi, _normalize_openalex_id
from coastal_crawler.sources.semantic_scholar import SemanticScholarSource
from coastal_crawler.sources.wiley import WileySource, _PAGE_SIZE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_settings(db_url: str) -> Settings:
    """A fully-configured Settings for the source tests.

    Every credential is set explicitly — the autouse isolation fixture blocks
    the real .env, so tests that expect a source to make a (mocked) call must
    supply its key here.
    """
    return Settings(
        database_url=db_url,
        openalex_topic_ids=["T12345", "T67890"],
        semantic_scholar_api_key="test-s2-key",
        semantic_scholar_query="coastal ecosystem",
        wiley_api_key="test-wiley-key",
        wiley_issns=["0028-0836"],
        enabled_sources=["openalex", "semantic_scholar", "wiley"],
    )


def _uid() -> str:
    return str(uuid.uuid4())[:8]


def _mock_http(mocker: Any, module_path: str, pages: list[dict[str, Any]]) -> MagicMock:
    """Patch ``httpx.Client`` in *module_path* to return *pages* in sequence."""
    responses = []
    for page_body in pages:
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = page_body
        r.raise_for_status = MagicMock()
        responses.append(r)

    mock_get = MagicMock(side_effect=responses)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = mock_get

    mocker.patch(f"{module_path}.httpx.Client", return_value=mock_client)
    return mock_get


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count(engine: Engine) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count(Paper.id))) or 0


def _watermark(engine: Engine, source: str) -> date | None:
    with Session(engine) as s:
        return store.get_watermark(source, s)


# ---------------------------------------------------------------------------
# Unit: normalisation helpers
# ---------------------------------------------------------------------------

class TestNormaliseDoi:
    def test_strips_prefix(self) -> None:
        assert _normalize_doi("https://doi.org/10.1/abc") == "10.1/abc"

    def test_passthrough_bare(self) -> None:
        assert _normalize_doi("10.1/abc") == "10.1/abc"

    def test_none_returns_none(self) -> None:
        assert _normalize_doi(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _normalize_doi("") is None


class TestNormaliseOpenAlexId:
    def test_strips_url(self) -> None:
        assert _normalize_openalex_id("https://openalex.org/W1234") == "W1234"

    def test_passthrough_bare(self) -> None:
        assert _normalize_openalex_id("W1234") == "W1234"

    def test_none_returns_none(self) -> None:
        assert _normalize_openalex_id(None) is None


# ---------------------------------------------------------------------------
# OpenAlex source
# ---------------------------------------------------------------------------

def _oa_result(**kwargs: Any) -> dict[str, Any]:
    uid = _uid()
    return {
        "id": f"https://openalex.org/W{uid}",
        "doi": f"https://doi.org/10.1/{uid}",
        "title": f"Coastal Paper {uid}",
        "open_access": {"oa_url": f"https://example.com/{uid}.pdf"},
        "publication_date": "2024-06-01",
        **kwargs,
    }


class TestOpenAlexSource:
    def test_single_page_inserts_papers(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(), _oa_result()]}],
        )
        with Session(clean_db) as session:
            source = OpenAlexSource(test_settings)
            n = source.fetch_since(None, session)
        assert n == 2
        assert _count(clean_db) == 2

    def test_normalises_doi(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(doi="https://doi.org/10.1/xyz")]}],
        )
        with Session(clean_db) as session:
            OpenAlexSource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.doi == "10.1/xyz"

    def test_normalises_openalex_id(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(**{"id": "https://openalex.org/W9999"})]}],
        )
        with Session(clean_db) as session:
            OpenAlexSource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.openalex_id == "W9999"

    def test_paper_without_oa_url_is_skipped(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        """OpenAlex results with no open-access PDF URL are dropped entirely
        (the source filters on ``r["oa_pdf_url"]`` before insert)."""
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(open_access={})]}],
        )
        with Session(clean_db) as session:
            n = OpenAlexSource(test_settings).fetch_since(None, session)
        assert n == 0
        assert _count(clean_db) == 0

    def test_multi_page_follows_cursor(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [
                {"meta": {"next_cursor": "cursor_abc"}, "results": [_oa_result()]},
                {"meta": {"next_cursor": None}, "results": [_oa_result()]},
            ],
        )
        with Session(clean_db) as session:
            n = OpenAlexSource(test_settings).fetch_since(None, session)
        assert n == 2
        assert mock_get.call_count == 2
        # second call must carry the cursor
        _, kwargs = mock_get.call_args_list[1]
        assert kwargs["params"]["cursor"] == "cursor_abc"

    def test_empty_results_returns_zero(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": []}],
        )
        with Session(clean_db) as session:
            n = OpenAlexSource(test_settings).fetch_since(None, session)
        assert n == 0
        assert _count(clean_db) == 0

    def test_watermark_updated(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(publication_date="2024-09-15")]}],
        )
        with Session(clean_db) as session:
            OpenAlexSource(test_settings).fetch_since(None, session)
        assert _watermark(clean_db, "openalex") == date(2024, 9, 15)

    def test_watermark_not_set_on_empty_page(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": []}],
        )
        with Session(clean_db) as session:
            OpenAlexSource(test_settings).fetch_since(None, session)
        assert _watermark(clean_db, "openalex") is None

    def test_filter_includes_topic_ids(
        self, test_settings: Settings
    ) -> None:
        source = OpenAlexSource(test_settings)
        f = source._build_filter(None)
        assert "topics.id:T12345|T67890" in f

    def test_filter_includes_watermark(self, test_settings: Settings) -> None:
        source = OpenAlexSource(test_settings)
        f = source._build_filter(date(2024, 3, 1))
        assert "from_publication_date:2024-03-01" in f

    def test_filter_always_includes_is_oa(self, test_settings: Settings) -> None:
        source = OpenAlexSource(test_settings)
        assert "is_oa:true" in source._build_filter(None)

    def test_duplicate_doi_skipped(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        shared_doi = f"https://doi.org/10.1/{_uid()}"
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [
                {"meta": {"next_cursor": None}, "results": [_oa_result(doi=shared_doi)]},
            ],
        )
        with Session(clean_db) as session:
            OpenAlexSource(test_settings).fetch_since(None, session)
        # second run — same DOI
        _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": [_oa_result(doi=shared_doi)]}],
        )
        with Session(clean_db) as session:
            n = OpenAlexSource(test_settings).fetch_since(None, session)
        assert n == 0
        assert _count(clean_db) == 1


# ---------------------------------------------------------------------------
# Semantic Scholar source
# ---------------------------------------------------------------------------

def _s2_paper(**kwargs: Any) -> dict[str, Any]:
    uid = _uid()
    return {
        "paperId": f"s2-{uid}",
        "externalIds": {"DOI": f"10.2/{uid}"},
        "title": f"S2 Paper {uid}",
        "openAccessPdf": {"url": f"https://pdfs.semanticscholar.org/{uid}.pdf"},
        "publicationDate": "2024-05-10",
        **kwargs,
    }


class TestSemanticScholarSource:
    def test_single_page_inserts_papers(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [_s2_paper(), _s2_paper()], "token": None}],
        )
        with Session(clean_db) as session:
            n = SemanticScholarSource(test_settings).fetch_since(None, session)
        assert n == 2

    def test_extracts_doi_from_external_ids(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [_s2_paper(**{"externalIds": {"DOI": "10.99/test"}})], "token": None}],
        )
        with Session(clean_db) as session:
            SemanticScholarSource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.doi == "10.99/test"

    def test_no_doi_uses_semantic_scholar_id(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [_s2_paper(**{"externalIds": {}, "paperId": "s2abc"})], "token": None}],
        )
        with Session(clean_db) as session:
            SemanticScholarSource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.doi is None
        assert paper.semantic_scholar_id == "s2abc"

    def test_multi_page_follows_token(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [
                {"data": [_s2_paper()], "token": "tok123"},
                {"data": [_s2_paper()], "token": None},
            ],
        )
        with Session(clean_db) as session:
            n = SemanticScholarSource(test_settings).fetch_since(None, session)
        assert n == 2
        assert mock_get.call_count == 2
        _, kwargs = mock_get.call_args_list[1]
        assert kwargs["params"]["token"] == "tok123"

    def test_no_api_key_returns_zero_without_calling(
        self, clean_db: Engine, db_url: str, mocker: Any
    ) -> None:
        """The bulk search endpoint requires a key; with none configured the
        source returns 0 and makes no request at all."""
        settings = Settings(database_url=db_url, semantic_scholar_query="coastal")
        mock_get = _mock_http(mocker, "coastal_crawler.sources.semantic_scholar", [])
        with Session(clean_db) as session:
            n = SemanticScholarSource(settings).fetch_since(None, session)
        assert n == 0
        mock_get.assert_not_called()

    def test_configured_query_sent_in_params(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [], "token": None}],
        )
        with Session(clean_db) as session:
            SemanticScholarSource(test_settings).fetch_since(None, session)
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["query"] == "coastal ecosystem"

    def test_watermark_updated(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [_s2_paper(**{"publicationDate": "2024-11-20"})], "token": None}],
        )
        with Session(clean_db) as session:
            SemanticScholarSource(test_settings).fetch_since(None, session)
        assert _watermark(clean_db, "semantic_scholar") == date(2024, 11, 20)

    def test_api_key_sent_in_header(
        self, clean_db: Engine, db_url: str, mocker: Any
    ) -> None:
        settings = Settings(
            database_url=db_url,
            semantic_scholar_api_key="my-secret-key",
            semantic_scholar_query="test",
        )
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.semantic_scholar",
            [{"data": [], "token": None}],
        )
        with Session(clean_db) as session:
            SemanticScholarSource(settings).fetch_since(None, session)
        _, kwargs = mock_get.call_args
        assert kwargs["headers"].get("x-api-key") == "my-secret-key"


# ---------------------------------------------------------------------------
# Wiley source (discovery via the CrossRef REST API)
# ---------------------------------------------------------------------------

def _crossref_work(**kwargs: Any) -> dict[str, Any]:
    """A CrossRef ``message.items[]`` entry as the Wiley source consumes it."""
    uid = _uid()
    work = {
        "DOI": f"10.1002/{uid}",
        "title": [f"Wiley Paper {uid}"],
        "abstract": f"<jats:p>Abstract for {uid}</jats:p>",
        "published": {"date-parts": [[2024, 7, 22]]},
        "link": [],
        "author": [{"given": "A.", "family": "Researcher"}],
    }
    work.update(kwargs)
    return work


def _crossref_page(items: list[dict[str, Any]], next_cursor: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"items": items}
    if next_cursor is not None:
        message["next-cursor"] = next_cursor
    return {"message": message}


class TestWileySource:
    def test_no_issns_raises(self, db_url: str) -> None:
        settings = Settings(database_url=db_url)
        with pytest.raises(ValueError, match="WILEY_ISSNS"):
            WileySource(settings)

    def test_missing_tdm_key_only_warns(self, db_url: str) -> None:
        """A missing WILEY_API_KEY is a download-time problem, not a discovery
        one — the source still constructs."""
        settings = Settings(database_url=db_url, wiley_issns=["0028-0836"])
        WileySource(settings)  # must not raise

    def test_single_page_inserts_papers(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([_crossref_work(), _crossref_work()])],
        )
        with Session(clean_db) as session:
            n = WileySource(test_settings).fetch_since(None, session)
        assert n == 2

    def test_doi_stored(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([_crossref_work(DOI="10.1002/wiley123")])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.doi == "10.1002/wiley123"

    def test_tdm_url_derived_from_doi_when_no_text_mining_link(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([_crossref_work(DOI="10.1002/mypaper", link=[])])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.oa_pdf_url is not None
        assert "api.wiley.com/onlinelibrary/tdm/" in paper.oa_pdf_url
        assert "10.1002/mypaper" in paper.oa_pdf_url

    def test_tdm_link_used_when_already_on_tdm_host(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        tdm_url = "https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1002/onhost"
        _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([
                _crossref_work(
                    DOI="10.1002/onhost",
                    link=[{"intended-application": "text-mining", "URL": tdm_url}],
                )
            ])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        with Session(clean_db) as session:
            paper = session.scalars(select(Paper)).one()
        assert paper.oa_pdf_url == tdm_url

    def test_multi_page_follows_cursor(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        # First page full (_PAGE_SIZE items) + a next-cursor → keep going;
        # second page partial → stop.
        page1 = [_crossref_work() for _ in range(_PAGE_SIZE)]
        page2 = [_crossref_work() for _ in range(3)]
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page(page1, next_cursor="CUR2"), _crossref_page(page2)],
        )
        with Session(clean_db) as session:
            n = WileySource(test_settings).fetch_since(None, session)
        assert n == _PAGE_SIZE + 3
        assert mock_get.call_count == 2
        _, kwargs = mock_get.call_args_list[1]
        assert kwargs["params"]["cursor"] == "CUR2"

    def test_watermark_applied_as_pub_date_filter(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(date(2024, 1, 1), session)
        _, kwargs = mock_get.call_args
        assert "from-pub-date:2024-01-01" in kwargs["params"]["filter"]

    def test_no_watermark_omits_pub_date_filter(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        _, kwargs = mock_get.call_args
        assert "from-pub-date" not in kwargs["params"]["filter"]

    def test_watermark_updated(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([_crossref_work(published={"date-parts": [[2024, 8, 30]]})])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        assert _watermark(clean_db, "wiley") == date(2024, 8, 30)

    def test_issn_filter_sent(
        self, clean_db: Engine, test_settings: Settings, mocker: Any
    ) -> None:
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.wiley",
            [_crossref_page([])],
        )
        with Session(clean_db) as session:
            WileySource(test_settings).fetch_since(None, session)
        _, kwargs = mock_get.call_args
        assert "issn:0028-0836" in kwargs["params"]["filter"]


# ---------------------------------------------------------------------------
# discover() orchestrator
# ---------------------------------------------------------------------------

@contextmanager
def _fake_session() -> Any:
    yield MagicMock()


class TestDiscover:
    def test_calls_enabled_sources(self, mocker: Any, db_url: str) -> None:
        from coastal_crawler import discovery

        settings = Settings(database_url=db_url, enabled_sources=["openalex"])
        mocker.patch("coastal_crawler.config.get_settings", return_value=settings)

        mock_source = MagicMock()
        mock_source.fetch_since.return_value = 5
        mocker.patch(
            "coastal_crawler.sources.openalex.OpenAlexSource",
            return_value=mock_source,
        )
        mocker.patch("coastal_crawler.discovery.get_session", _fake_session)
        mocker.patch("coastal_crawler.discovery.store.get_watermark", return_value=None)

        total = discovery.discover()

        assert total == 5
        mock_source.fetch_since.assert_called_once()

    def test_unknown_source_logs_warning_and_continues(
        self, mocker: Any, db_url: str
    ) -> None:
        from coastal_crawler import discovery

        settings = Settings(database_url=db_url, enabled_sources=["bogus_source"])
        mocker.patch("coastal_crawler.config.get_settings", return_value=settings)
        mock_log = mocker.patch("coastal_crawler.discovery.log")
        mocker.patch("coastal_crawler.discovery.get_session", _fake_session)

        result = discovery.discover()

        assert result == 0
        mock_log.warning.assert_called_once_with("unknown_source", source="bogus_source")

    def test_source_init_failure_logs_warning_and_continues(
        self, mocker: Any, db_url: str
    ) -> None:
        """Wiley with no ISSNs raises ValueError on construction; discover()
        logs it and moves on rather than crashing the whole run."""
        from coastal_crawler import discovery

        settings = Settings(database_url=db_url, enabled_sources=["wiley"])
        mocker.patch("coastal_crawler.config.get_settings", return_value=settings)
        mock_log = mocker.patch("coastal_crawler.discovery.log")
        mocker.patch("coastal_crawler.discovery.get_session", _fake_session)

        result = discovery.discover()

        assert result == 0
        mock_log.warning.assert_called_once()
        assert mock_log.warning.call_args[1]["source"] == "wiley"

    def test_since_overrides_watermark(
        self, mocker: Any, db_url: str, clean_db: Engine
    ) -> None:
        """When ``since`` is passed, all sources use it instead of the stored
        watermark."""
        from coastal_crawler import discovery

        settings = Settings(database_url=db_url, enabled_sources=["openalex"])
        mocker.patch("coastal_crawler.config.get_settings", return_value=settings)
        mock_get = _mock_http(
            mocker,
            "coastal_crawler.sources.openalex",
            [{"meta": {"next_cursor": None}, "results": []}],
        )

        @contextmanager
        def _real_session() -> Any:
            with Session(clean_db) as s:
                yield s

        mocker.patch("coastal_crawler.discovery.get_session", _real_session)

        discovery.discover(since=date(2023, 1, 1))

        _, kwargs = mock_get.call_args
        assert "from_publication_date:2023-01-01" in kwargs["params"]["filter"]
