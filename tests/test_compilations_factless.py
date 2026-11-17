"""Unit tests (rung 1) for contract change C1: ``--allow-factless <source_key>``.

Hand-built fixture: one source ``nofacts-src`` with 2 reference rows, 2 citation rows and
empty location/sample/method/fact tables. ``nofacts-src`` has no label-map rows, so without
the flag both the empty-fact check and the "no habitat label map declared" check fire.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    TABLES,
    ContractError,
    add_derived_labels,
    build_compilations,
    empty_table,
    read_extracted,
    validate_tables,
    write_extracted,
)
from coastal_crawler.compilations import vocab

SRC = "nofacts-src"
ALLOW = frozenset({SRC})


def _factless() -> dict[str, pd.DataFrame]:
    t = {n: empty_table(n) for n in TABLES}
    t["location"] = t["location"].drop(columns=vocab.DERIVED_LOCATION_COLUMNS)  # extract.py supplies raw labels only
    t["source"] = pd.DataFrame([{
        "source_key": SRC, "name": "n", "version": "v", "doi": None,
        "access_date": "2026-11-15", "license": None, "notes": "reference list only",
    }])
    t["reference"] = pd.DataFrame({"source_key": [SRC, SRC], "reference_key": ["ref:a 2001", "ref:b 2002"]})
    t["citation"] = pd.DataFrame({
        "source_key": [SRC, SRC], "citation_key": ["ref:a 2001", "ref:b 2002"],
        "reference_key": ["ref:a 2001", "ref:b 2002"], "bibliography_id": None,
        "publication_type": "article", "doi": None, "title": None, "authors": ["A", "B"],
        "year": [2001, 2002], "url": None, "openalex_id": None, "doi_resolved": None,
        "doi_resolution_source": None,
    })
    return t


def _labelled() -> dict[str, pd.DataFrame]:
    return add_derived_labels(_factless(), ALLOW)


def _one_fact() -> pd.DataFrame:
    row = {c.name: ("x" if c.type == "VARCHAR" else None) for c in TABLES["fact"].cols}
    row.update(source_key=SRC, value=1.0, flags=[], reference_key="ref:a 2001")
    return pd.DataFrame([row])


def test_empty_fact_table_raises_without_flag():
    with pytest.raises(ContractError, match="fact table is empty"):
        validate_tables(_labelled())


def test_no_habitat_map_raises_without_flag():
    with pytest.raises(vocab.VocabError, match="no habitat label map declared"):
        add_derived_labels(_factless())


def test_both_exemptions_with_flag():
    t = add_derived_labels(_factless(), ALLOW)
    assert len(t["location"]) == 0
    validate_tables(t, ALLOW)  # empty fact table accepted


def test_flag_for_other_source_does_not_exempt():
    with pytest.raises(ContractError, match="fact table is empty"):
        validate_tables(_labelled(), frozenset({"other"}))


def test_habitat_exemption_only_when_location_empty():
    loc = pd.DataFrame({"habitat_type": ["x"], "modification_status": [[]]})
    with pytest.raises(vocab.VocabError, match="no habitat label map declared"):
        vocab.derive_location_labels(loc, SRC, vocab.load_label_maps(), allow_factless=True)


def test_flag_raises_if_source_has_facts():
    # a real built compilation's extracted dir would be needed; use a synthetic fact row set
    t = _labelled()
    t["fact"] = _one_fact()
    with pytest.raises(ContractError):  # raises (FK / facts present), never silently accepted
        validate_tables(t, ALLOW)


def test_flag_raises_on_facts_check_itself(monkeypatch):
    # isolate the explicit "named but has facts" guard from the FK checks
    t = _labelled()
    t["fact"] = _one_fact()
    monkeypatch.setattr("coastal_crawler.compilations._check_fk", lambda *a, **k: None)
    with pytest.raises(ContractError, match="named in --allow-factless but has 1 fact rows"):
        validate_tables(t, ALLOW)


def test_write_read_build_roundtrip(tmp_path: Path):
    out = tmp_path / "ex"
    write_extracted(_factless(), out, ALLOW)
    with pytest.raises(ContractError):
        read_extracted(out)  # without the flag the build refuses
    back = read_extracted(out, ALLOW)
    assert len(back["reference"]) == 2 and len(back["citation"]) == 2 and len(back["fact"]) == 0
    db = tmp_path / "w.duckdb"
    with pytest.raises(ContractError, match="not being built"):
        build_compilations([out], db, ALLOW | {"typo"})
    counts = build_compilations([out], db, ALLOW)
    assert counts["measurement_fact"] == 0 and counts["reference_dim"] == 2 and counts["citation_dim"] == 2
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select count(*) from reference_dim").fetchone() == (2,)
    con.close()
    with pytest.raises(ContractError):
        build_compilations([out], tmp_path / "w2.duckdb")


def test_cli_flag_parses(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "ex"
    write_extracted(_factless(), out, ALLOW)
    db = tmp_path / "c.duckdb"
    env = {"PYTHONPATH": str(root / "src")}
    r = subprocess.run([sys.executable, str(root / "scripts/build_compilations.py"), "--out", str(db),
                        "--allow-factless", SRC, str(out)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, str(root / "scripts/build_compilations.py"), "--out", str(tmp_path / "d.duckdb"),
                        str(out)], capture_output=True, text=True, env=env)
    assert r.returncode != 0
