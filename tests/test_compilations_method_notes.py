"""Contract tests for ``method.notes`` (the source's own free-text comments, verbatim; ``params`` stays structured)."""

from __future__ import annotations

import duckdb

from coastal_crawler.compilations import (
    OPTIONAL_COLUMNS,
    TABLES,
    build_compilations,
    fill_optional_columns,
    read_extracted,
    write_extracted,
)
from tests.test_compilations_scoc import _build, _fixture_constants  # noqa: F401  (autouse fixture the SCOC fixture needs)

NOTE = "hourly rates multiplied to 24 h; data and lat/lon read from figure"


def test_column_is_in_the_contract_and_optional():
    cols = TABLES["method"].col_names
    assert "notes" in cols and cols.index("notes") == cols.index("params") + 1
    assert OPTIONAL_COLUMNS["method"] == ("notes",)


def test_fill_gives_null_and_keeps_supplied_notes():
    raw = _build()[0]
    assert "notes" not in raw["method"].columns
    filled = fill_optional_columns(raw)
    assert filled["method"]["notes"].isna().all()
    filled["method"].at[0, "notes"] = NOTE
    assert fill_optional_columns(filled)["method"].at[0, "notes"] == NOTE


def test_notes_roundtrip_verbatim_through_parquet_and_duckdb(tmp_path):
    t = fill_optional_columns(_build()[0])
    n_methods = len(t["method"])
    t["method"].at[0, "notes"] = NOTE
    mk = t["method"].at[0, "method_key"]
    out = tmp_path / "extracted"
    write_extracted(t, out)
    back = read_extracted(out)["method"]
    assert back["notes"].notna().sum() == 1 and back.loc[back["method_key"] == mk, "notes"].iloc[0] == NOTE
    db = tmp_path / "c.duckdb"
    build_compilations([out], db)
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("select method_key, notes from method_dim where notes is not null").fetchall()
    total = con.execute("select count(*) from method_dim").fetchone()[0]
    con.close()
    assert rows == [(mk, NOTE)] and total == n_methods
