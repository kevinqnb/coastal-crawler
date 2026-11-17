"""Contract tests for ``fact.value_comparator`` (censored values such as ``<0.02``) and its flag ``inequality``.

Hand-built fixture: the SCOC test fixture (7 facts); facts 1 and 2 are given a sign, expected results stated inline.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    ContractError,
    OPTIONAL_COLUMNS,
    TABLES,
    add_derived_labels,
    build_compilations,
    fill_optional_columns,
    read_extracted,
    validate_tables,
    write_extracted,
)
from tests.test_compilations_scoc import _build, _fixture_constants  # noqa: F401  (autouse fixture the SCOC fixture needs)


def _tables():
    tables = fill_optional_columns(_build()[0])
    return {k: v.copy() for k, v in tables.items()}


def _derived():
    """Tables as validate_tables sees them after write_extracted has added the location labels."""
    return add_derived_labels(_tables())


def _with_signs(tables, signs):
    """Set value_comparator and the matching flag on the facts at the given positions."""
    f = tables["fact"]
    for i, sign in signs.items():
        f.at[i, "value_comparator"] = sign
        f.at[i, "flags"] = list(f.at[i, "flags"]) + ["inequality"]
    return tables


def test_column_is_in_the_contract_and_optional():
    assert "value_comparator" in TABLES["fact"].col_names
    assert OPTIONAL_COLUMNS["fact"] == ("value_comparator",)


def test_fill_optional_columns_gives_null_and_keeps_supplied_values():
    raw = _build()[0]
    assert "value_comparator" not in raw["fact"].columns  # an older extract.py does not supply it
    filled = fill_optional_columns(raw)
    assert filled["fact"]["value_comparator"].isna().all() and len(filled["fact"]) == 7
    again = fill_optional_columns(_with_signs(filled, {1: "<"}))
    assert again["fact"]["value_comparator"].tolist().count("<") == 1  # a supplied column is left alone


def test_valid_signs_pass():
    t = _with_signs(_derived(), {1: "<", 2: ">"})
    validate_tables(t)


def test_sign_without_flag_raises():
    t = _derived()
    t["fact"].at[1, "value_comparator"] = "<"
    with pytest.raises(ContractError, match="inequality flag and value_comparator disagree on 1"):
        validate_tables(t)


def test_flag_without_sign_raises():
    t = _derived()
    t["fact"].at[1, "flags"] = list(t["fact"].at[1, "flags"]) + ["inequality"]
    with pytest.raises(ContractError, match="disagree on 1"):
        validate_tables(t)


@pytest.mark.parametrize("bad", ["<=", "=", "~", ""])
def test_unknown_sign_raises(bad):
    t = _with_signs(_derived(), {1: bad})
    with pytest.raises(ContractError, match="value_comparator values"):
        validate_tables(t)


def test_roundtrip_parquet_and_duckdb(tmp_path):
    t = _with_signs(_tables(), {1: "<", 2: ">"})
    out = tmp_path / "extracted"
    write_extracted(t, out)
    back = read_extracted(out)["fact"]
    assert back["value_comparator"].tolist().count("<") == 1 and back["value_comparator"].tolist().count(">") == 1
    assert back["value_comparator"].isna().sum() == 5
    db = tmp_path / "c.duckdb"
    build_compilations([out], db)
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "select value_comparator, list_contains(flags, 'inequality') from measurement_fact order by 1 nulls last"
    ).fetchall()
    con.close()
    assert [r for r in rows if r[0] is not None] == [("<", True), (">", True)]
    assert all(r[1] is False for r in rows if r[0] is None)


def test_stale_parquet_without_the_column_is_refused(tmp_path):
    """A parquet written before the column existed must not build silently (re-run extract.py)."""
    out = tmp_path / "extracted"
    write_extracted(_tables(), out)
    con = duckdb.connect()
    p = out / "fact.parquet"
    con.execute(f"COPY (SELECT * EXCLUDE (value_comparator) FROM read_parquet('{p}')) TO '{p}.new' (FORMAT parquet)")
    con.close()
    (out / "fact.parquet.new").replace(p)
    with pytest.raises(ContractError, match="schema mismatch"):
        read_extracted(out)
