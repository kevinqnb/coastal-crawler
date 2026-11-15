"""Tests for the file-output sample extraction stage (extract_sample.py).

The headline property is isolation: running the stage must leave every DB table
exactly as it found it."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult
from coastal_crawler.db import store
from coastal_crawler.extract_sample import read_paper_ids, run_extract_sample


@pytest.fixture
def sample_db(clean_db: Engine, mocker: Any) -> Engine:
    @contextmanager  # type: ignore[misc]
    def _test_get_session():
        session = Session(clean_db)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    mocker.patch("coastal_crawler.extract_sample.get_session", _test_get_session)
    return clean_db


class _FakeAdapter:
    """One record per paper whose text contains 'value'; paper whose text
    contains 'boom' gets an error string. Records every batch it is given."""

    def __init__(self) -> None:
        self.truncated_docs: set[int] = set()
        self.exhausted_calls: dict[str, set[int]] = {}
        self.step_seconds: dict[str, float] = {"entities": 1.0}
        self.batches: list[list[str]] = []

    def extract_batch(self, ocr_texts: list[str]) -> list[Any]:
        self.batches.append(list(ocr_texts))
        self.exhausted_calls = {"text_value_extraction": {0}} if len(self.batches) == 1 else {}
        out: list[Any] = []
        for t in ocr_texts:
            if "boom" in t:
                out.append("extraction call exhausted its retries")
            elif "value" in t:
                out.append([ExtractionResult(schema_name="s", model_version="v", data={"value": 1.0})])
            else:
                out.append([])
        return out


def _seed(engine: Engine, text_dir: Path, texts: dict[int, str], *, status: str = "extracted") -> None:
    with Session(engine) as s:
        for pid, body in texts.items():
            s.execute(
                text("INSERT INTO papers (id, doi, title, status, metadata) VALUES (:id, :doi, 't', :st, '{}')"),
                {"id": pid, "doi": f"10.1/{pid}", "st": status},
            )
            store.upsert_paper_ocr_context(pid, body.replace("\x00", "�"), s)
            (text_dir / f"{pid}.txt").write_text(body, encoding="utf-8")
        s.commit()


def _snapshot(engine: Engine) -> dict[str, Any]:
    with Session(engine) as s:
        return {
            "papers": s.execute(text("SELECT id, status, error, extracted_at FROM papers ORDER BY id")).all(),
            "extractions": s.execute(text("SELECT count(*) FROM extractions")).scalar(),
            "contexts": s.execute(text("SELECT paper_id, md5(context) FROM paper_ocr_context ORDER BY paper_id")).all(),
        }


def test_writes_files_and_leaves_database_untouched(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    texts = {1: "has a value", 2: "nothing here", 3: "boom"}
    _seed(sample_db, text_dir, texts)
    before = _snapshot(sample_db)
    adapter = _FakeAdapter()

    result = run_extract_sample(
        run_id="r1", paper_ids=[1, 2, 3], text_dir=text_dir, output_dir=tmp_path / "out", adapter=adapter, chunk_size=2
    )

    assert result == (3, 1, 1, 1)  # papers, records, failed, unattributable exhausted calls
    assert _snapshot(sample_db) == before
    assert [len(b) for b in adapter.batches] == [2, 1]

    run_dir = tmp_path / "out" / "r1"
    lines = [json.loads(l) for l in (run_dir / "records.jsonl").read_text().splitlines()]
    assert [(l["paper_id"], l["error"] is None, len(l["records"])) for l in lines] == [(1, True, 1), (2, True, 0), (3, False, 0)]
    timing = [json.loads(l) for l in (run_dir / "timing.jsonl").read_text().splitlines()]
    assert [t["paper_ids"] for t in timing] == [[1, 2], [3]]
    assert timing[0]["exhausted_calls"] == {"text_value_extraction": [0]}
    assert timing[0]["step_seconds"] == {"entities": 1.0}
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["paper_ids"] == [1, 2, 3] and manifest["text"]["1"]["chars"] == len(texts[1])
    assert json.loads((run_dir / "summary.json").read_text())["records"] == 1


def test_existing_run_directory_is_a_hard_error(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "x"})
    (tmp_path / "out" / "r1").mkdir(parents=True)
    adapter = _FakeAdapter()

    with pytest.raises(FileExistsError):
        run_extract_sample(run_id="r1", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=adapter, chunk_size=1)
    assert adapter.batches == []


def test_non_extracted_paper_is_rejected_before_any_model_call(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "x"}, status="table_clean_done")
    adapter = _FakeAdapter()

    with pytest.raises(AssertionError, match="extracted"):
        run_extract_sample(run_id="r", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=adapter, chunk_size=1)
    assert adapter.batches == []


def test_text_drift_from_production_context_is_rejected(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "original"})
    (text_dir / "1.txt").write_text("table-clean was rerun", encoding="utf-8")
    adapter = _FakeAdapter()

    with pytest.raises(AssertionError, match="differs from the OCR context"):
        run_extract_sample(run_id="r", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=adapter, chunk_size=1)
    assert adapter.batches == []


def test_nul_bytes_compare_after_sanitization(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "a\x00b value"})
    adapter = _FakeAdapter()

    run_extract_sample(run_id="r", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=adapter, chunk_size=1)

    assert adapter.batches == [["a\x00b value"]]  # model sees the raw file text, as in production


def test_unknown_and_duplicate_ids_are_rejected(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "x"})
    kw: dict[str, Any] = dict(run_id="r", text_dir=text_dir, output_dir=tmp_path / "out", adapter=_FakeAdapter(), chunk_size=1)
    with pytest.raises(AssertionError, match="unknown paper ids"):
        run_extract_sample(paper_ids=[1, 99], **kw)
    with pytest.raises(AssertionError, match="duplicates"):
        run_extract_sample(paper_ids=[1, 1], **kw)


def test_read_paper_ids_skips_comment_lines(tmp_path: Path) -> None:
    f = tmp_path / "l.csv"
    f.write_text("# seed=1 n=2\npaper_id,note\n5,a\n7,b\n")
    assert read_paper_ids(f) == [5, 7]


class _NumpyAdapter(_FakeAdapter):
    """Pipeline mode's table-value step leaks numpy scalars into record data."""

    def __init__(self, payload: Any) -> None:
        super().__init__()
        self.payload = payload

    def extract_batch(self, ocr_texts: list[str]) -> list[Any]:
        return [[ExtractionResult(schema_name="s", model_version="v", data={"value": self.payload}, provenance={"page": np.int64(3)})] for _ in ocr_texts]


def test_numpy_scalars_in_records_are_written_as_plain_numbers(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "x"})
    run_extract_sample(
        run_id="r1", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=_NumpyAdapter(np.int64(7)), chunk_size=1
    )
    rec = json.loads((tmp_path / "out" / "r1" / "records.jsonl").read_text())["records"][0]
    assert rec["data"] == {"value": 7} and rec["provenance"] == {"page": 3}


def test_unknown_unserializable_type_still_fails_loudly(sample_db: Engine, tmp_path: Path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    _seed(sample_db, text_dir, {1: "x"})
    with pytest.raises(TypeError):
        run_extract_sample(
            run_id="r1", paper_ids=[1], text_dir=text_dir, output_dir=tmp_path / "out", adapter=_NumpyAdapter(object()), chunk_size=1
        )
