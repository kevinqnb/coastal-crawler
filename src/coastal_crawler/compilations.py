"""Shared contract + central builder for the compilations DuckDB star schema.

Each raw compilation under ``data/compilations/<name>/`` has its own
``extract.py`` that writes one parquet file per table below into
``<name>/extracted/``, using compilation-LOCAL string keys (``*_key``). This
module owns everything shared: the table contract (``TABLES``), validation of
what an extract.py produced, assignment of global integer surrogate keys (in
sorted natural-key order, so a rebuild from the same inputs gives identical
table contents), and the atomic write of ``compilations.duckdb``.

Distinct from the literature warehouse (``warehouse.py``): compilation values
are curated source values, not model output, and are never mixed into
``warehouse.duckdb``. Joining the two is a deliberate downstream step.

Fail loud: every violation of the contract raises ``ContractError``. There is
no skip-and-log path here; exclusions happen (and are reported) in each
compilation's extract.py, and the builder only sees what survived.

Pure transforms + DuckDB I/O only. pandas is used for the key assignment;
parquet is read/written through DuckDB (no pyarrow dependency).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd


class ContractError(Exception):
    """An extract.py output (or the build) violates the table contract."""


@dataclass(frozen=True)
class Col:
    name: str
    type: str  # DuckDB type string
    required: bool = False  # NOT NULL


@dataclass(frozen=True)
class FK:
    """(columns of this table) reference a parent table's natural key."""

    columns: tuple[str, ...]
    parent: str
    # nullable: a child row may leave the FK columns NULL (no parent). Only
    # rows with every FK column non-null are checked / resolved to a parent id.
    nullable: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    cols: tuple[Col, ...]
    natural_key: tuple[str, ...]  # unique within an extract output
    fks: tuple[FK, ...] = ()

    @property
    def col_names(self) -> list[str]:
        return [c.name for c in self.cols]


def _s(name: str, required: bool = False) -> Col:
    return Col(name, "VARCHAR", required)


def _d(name: str, required: bool = False) -> Col:
    return Col(name, "DOUBLE", required)


def _i(name: str, required: bool = False) -> Col:
    return Col(name, "INTEGER", required)


def _ls(name: str, required: bool = False) -> Col:
    return Col(name, "VARCHAR[]", required)


# Load order = dependency order (parents first).
TABLES: dict[str, Table] = {
    "source": Table(
        "source",
        (
            _s("source_key", True),
            _s("name", True),
            _s("version", True),
            _s("doi"),
            _s("access_date", True),
            _s("license"),
            _s("notes"),
        ),
        ("source_key",),
    ),
    "reference": Table(
        "reference",
        (_s("source_key", True), _s("reference_key", True)),
        ("source_key", "reference_key"),
        (FK(("source_key",), "source"),),
    ),
    "citation": Table(
        "citation",
        (
            _s("source_key", True),
            _s("citation_key", True),
            _s("reference_key", True),
            _s("bibliography_id"),
            _s("publication_type"),
            _s("doi"),
            _s("title"),
            _s("authors"),
            _i("year"),
            _s("url"),
        ),
        ("source_key", "citation_key"),
        (FK(("source_key", "reference_key"), "reference"),),
    ),
    "study_screening": Table(
        "study_screening",
        (
            _s("source_key", True),
            _s("study_key", True),
            _s("study_citation", True),
            _s("screening_phase"),
            _s("eligibility_phase"),
            _s("database_status"),
            _s("reference_key"),  # NULL when the study contributed no facts
        ),
        ("source_key", "study_key"),
        (
            FK(("source_key",), "source"),
            FK(("source_key", "reference_key"), "reference", nullable=True),
        ),
    ),
    "location": Table(
        "location",
        (
            _s("source_key", True),
            _s("location_key", True),
            _s("site_name"),
            _s("source_site_id"),
            _s("source_core_id", True),
            _d("latitude"),
            _d("longitude"),
            _d("position_accuracy"),
            _s("position_accuracy_units"),
            _s("position_method"),
            _s("country"),
            _s("admin_division"),
            _s("water_body"),  # ocean / sea (open-water compilations)
            _s("habitat_type"),
            _s("geomorphic_setting"),
            _s("salinity_class"),
            _s("salinity_class_level"),
            _s("vegetation_class"),
            _s("vegetation_class_level"),
            _s("tidal_position"),
            _s("tidal_position_level"),
            _ls("modification_status", True),
            _ls("dominant_species", True),
            _d("elevation"),
            _s("elevation_units"),
            _s("elevation_datum"),
            _s("elevation_method"),
        ),
        ("source_key", "location_key"),
        (FK(("source_key",), "source"),),
    ),
    "sample": Table(
        "sample",
        (
            _s("source_key", True),
            _s("sample_key", True),
            _s("source_sample_id"),
            _d("depth_min_cm"),
            _d("depth_max_cm"),
            _d("representative_depth_min_cm"),
            _d("representative_depth_max_cm"),
            _s("depth_notes"),
            _i("sampling_year"),
            _i("sampling_month"),
            _s("vegetated_or_bare"),
            _s("marsh_zone"),
            _s("sediment_type"),  # raw label
            _d("median_grain_size_um"),
            _s("photosynthetic_sediment"),  # raw label (microphytobenthos present?)
        ),
        ("source_key", "sample_key"),
        (FK(("source_key",), "source"),),
    ),
    "method": Table(
        "method",
        (
            _s("source_key", True),
            _s("method_key", True),
            _s("source_method_id"),
            _s("attribute", True),
            _s("method_status", True),
            _s("technique"),
            _s("coring_method"),
            _s("carbon_type"),
            _s("carbon_measured_or_modeled"),
            _s("fraction_carbon_method"),
            _s("carbonates_removed"),
            _s("carbonate_removal_method"),
            _s("conversion_applied"),
            _s("in_situ_ex_situ"),
            _s("light_dark"),
            _s("params"),  # JSON text
        ),
        ("source_key", "method_key"),
        (FK(("source_key",), "source"),),
    ),
    "fact": Table(
        "fact",
        (
            _s("source_key", True),
            _s("fact_key", True),
            _s("reference_key", True),
            _s("location_key", True),
            _s("sample_key", True),
            _s("method_key", True),
            _s("attribute", True),
            _d("value", True),
            _s("units_raw", True),
            _d("sd"),
            _d("se"),
            _i("n"),
            _ls("flags", True),
            _s("source_row_ref", True),
        ),
        ("source_key", "fact_key"),
        (
            FK(("source_key", "reference_key"), "reference"),
            FK(("source_key", "location_key"), "location"),
            FK(("source_key", "sample_key"), "sample"),
            FK(("source_key", "method_key"), "method"),
        ),
    ),
}

# Global table name for each contract table, and its surrogate-id column.
GLOBAL_NAMES = {
    "source": "source_dim",
    "reference": "reference_dim",
    "citation": "citation_dim",
    "study_screening": "study_screening_dim",
    "location": "location_dim",
    "sample": "sample_dim",
    "method": "method_dim",
    "fact": "measurement_fact",
}
ID_COLS = {
    "source": "source_id",
    "reference": "reference_id",
    "citation": "citation_id",
    "study_screening": "study_screening_id",
    "location": "location_id",
    "sample": "sample_id",
    "method": "method_id",
    "fact": "measurement_id",
}

_LIST_COLS = {
    (t.name, c.name) for t in TABLES.values() for c in t.cols if c.type.endswith("[]")
}


# --------------------------------------------------------------------------
# extract.py output: write / read / validate
# --------------------------------------------------------------------------


def empty_table(name: str) -> pd.DataFrame:
    """A zero-row table with the contract's columns (for compilations with no rows)."""
    return pd.DataFrame({c: pd.Series(dtype=object) for c in TABLES[name].col_names})


def write_extracted(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Validate then write one parquet per table into ``out_dir``."""
    validate_tables(tables)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in TABLES.items():
        df = tables[name][spec.col_names]
        con = duckdb.connect()
        _create_typed(con, "t", spec)
        _insert_df(con, "t", df, spec)
        tmp = out_dir / f".{name}.parquet.tmp"
        con.execute(f"COPY t TO '{tmp}' (FORMAT parquet)")
        con.close()
        os.replace(tmp, out_dir / f"{name}.parquet")


def read_extracted(in_dir: Path) -> dict[str, pd.DataFrame]:
    """Read every contract table from ``in_dir``; type-check against the contract."""
    out: dict[str, pd.DataFrame] = {}
    for name, spec in TABLES.items():
        path = in_dir / f"{name}.parquet"
        if not path.exists():
            raise ContractError(f"missing extracted table: {path}")
        con = duckdb.connect()
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
        got = [(r[0], r[1]) for r in desc]
        want = [(c.name, c.type) for c in spec.cols]
        if got != want:
            raise ContractError(
                f"{path}: schema mismatch.\n  got:  {got}\n  want: {want}"
            )
        df = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
        con.close()
        out[name] = df
    validate_tables(out)
    return out


def _create_typed(con: duckdb.DuckDBPyConnection, name: str, spec: Table) -> None:
    cols = ", ".join(f'"{c.name}" {c.type}' for c in spec.cols)
    con.execute(f'CREATE TABLE "{name}" ({cols})')


def _insert_df(
    con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame, spec: Table
) -> None:
    """Insert with explicit casts; python lists in list columns, None for NULL."""
    if len(df) == 0:
        return
    cols = spec.col_names
    ph = ", ".join("?" for _ in cols)
    rows = []
    for rec in df[cols].itertuples(index=False, name=None):
        rows.append(tuple(_py(v, spec.name, c) for v, c in zip(rec, cols)))
    con.executemany(f'INSERT INTO "{name}" VALUES ({ph})', rows)


def _py(v: object, table: str, col: str) -> object:
    if (table, col) in _LIST_COLS:
        if v is None:
            return None
        return [str(x) for x in v]  # type: ignore[attr-defined]
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):  # numpy scalar
        return v.item()  # type: ignore[attr-defined]
    return v


def validate_tables(tables: dict[str, pd.DataFrame]) -> None:
    """Assert the contract: columns, NOT NULLs, key uniqueness, FK integrity."""
    for name, spec in TABLES.items():
        if name not in tables:
            raise ContractError(f"missing table {name!r}")
        df = tables[name]
        if list(df.columns) != spec.col_names:
            # allow reordered but complete
            if set(df.columns) != set(spec.col_names):
                raise ContractError(
                    f"{name}: columns {list(df.columns)} != contract {spec.col_names}"
                )
        for c in spec.cols:
            if c.required and df[c.name].isna().any():
                raise ContractError(f"{name}.{c.name}: NULL in a required column")
        dup = df.duplicated(list(spec.natural_key), keep=False)
        if dup.any():
            raise ContractError(
                f"{name}: {int(dup.sum())} rows share a natural key "
                f"{spec.natural_key}, e.g. {df[dup][list(spec.natural_key)].iloc[0].tolist()}"
            )
    if len(tables["source"]) != 1:
        raise ContractError(
            f"an extract.py must produce exactly 1 source row, got {len(tables['source'])}"
        )
    src = tables["source"]["source_key"].iloc[0]
    for name, spec in TABLES.items():
        if name == "source":
            continue
        bad = tables[name]["source_key"] != src
        if bad.any():
            raise ContractError(f"{name}: source_key differs from source table")
        for fk in spec.fks:
            _check_fk(tables, name, fk)
    if len(tables["fact"]) == 0:
        raise ContractError("fact table is empty")
    unused_methods = set(tables["method"]["method_key"]) - set(tables["fact"]["method_key"])
    if unused_methods:
        raise ContractError(f"{len(unused_methods)} method rows no fact references")
    unused_samples = set(tables["sample"]["sample_key"]) - set(tables["fact"]["sample_key"])
    if unused_samples:
        raise ContractError(f"{len(unused_samples)} sample rows no fact references")


def _check_fk(tables: dict[str, pd.DataFrame], child: str, fk: FK) -> None:
    parent_spec = TABLES[fk.parent]
    child_cols = list(fk.columns)
    parent_cols = list(parent_spec.natural_key)
    if fk.parent == "source":
        parent_cols = ["source_key"]
    # column names differ between child fk columns and parent key only for
    # same-named keys, which the contract guarantees (reference_key etc.)
    parent_keys = set(map(tuple, tables[fk.parent][parent_cols].itertuples(index=False)))
    child_df = tables[child][child_cols]
    if fk.nullable:
        child_df = child_df[child_df.notna().all(axis=1)]
    child_keys = set(map(tuple, child_df.itertuples(index=False)))
    missing = child_keys - parent_keys
    if missing:
        raise ContractError(
            f"{child}: {len(missing)} FK values {child_cols} not in {fk.parent}, "
            f"e.g. {sorted(missing)[0]}"
        )


# --------------------------------------------------------------------------
# Central build: global surrogate keys + DuckDB
# --------------------------------------------------------------------------


def assign_global_keys(
    per_source: list[dict[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    """Concatenate compilations and assign integer ids in sorted natural-key order.

    Returns global tables keyed by contract name; dims carry ``<x>_id`` and
    ``source_id``; the fact carries the resolved FK ids. Every merge asserts
    it neither dropped nor duplicated rows.
    """
    if not per_source:
        raise ContractError("no compilations given")
    keys = [t["source"]["source_key"].iloc[0] for t in per_source]
    if len(set(keys)) != len(keys):
        raise ContractError(f"duplicate source_key across compilations: {keys}")

    cat = {
        name: pd.concat([t[name] for t in per_source], ignore_index=True)
        for name in TABLES
    }
    out: dict[str, pd.DataFrame] = {}
    for name, spec in TABLES.items():
        df = cat[name].sort_values(list(spec.natural_key), kind="stable").reset_index(
            drop=True
        )
        df.insert(0, ID_COLS[name], range(1, len(df) + 1))
        out[name] = df

    # source_id onto everything
    src_ids = out["source"][["source_key", "source_id"]]
    for name in TABLES:
        if name == "source":
            continue
        out[name] = _merge_checked(out[name], src_ids, ["source_key"], f"{name}->source")

    # FK ids
    for name, spec in TABLES.items():
        for fk in spec.fks:
            if fk.parent == "source":
                continue
            parent_id = ID_COLS[fk.parent]
            pk = list(TABLES[fk.parent].natural_key)
            lookup = out[fk.parent][pk + [parent_id]]
            out[name] = _merge_checked(
                out[name], lookup, pk, f"{name}->{fk.parent}", left_on=list(fk.columns),
                nullable=fk.nullable,
            )
    return out


def _merge_checked(
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: list[str],
    what: str,
    left_on: list[str] | None = None,
    nullable: bool = False,
) -> pd.DataFrame:
    n = len(left)
    lo = left_on or on
    merged = left.merge(right, how="left", left_on=lo, right_on=on, validate="m:1")
    if len(merged) != n:
        raise ContractError(f"{what}: merge changed row count {n} -> {len(merged)}")
    new_cols = [c for c in right.columns if c not in on]
    unresolved = merged[new_cols].isna().any(axis=1)
    if nullable:
        # only rows whose FK columns are all NULL may stay unresolved
        unresolved &= left[lo].notna().all(axis=1).to_numpy()
        for c in new_cols:
            merged[c] = merged[c].astype("Int64")
    if unresolved.any():
        raise ContractError(f"{what}: unresolved foreign key")
    return merged  # how="left" preserves the (already id-sorted) left order


def _global_columns(name: str) -> list[Col]:
    """Column list for the global table: id, source_id, FK ids, then payload."""
    spec = TABLES[name]
    fk_cols = {c for fk in spec.fks for c in fk.columns}
    cols: list[Col] = [Col(ID_COLS[name], "INTEGER", True)]
    if name != "source":
        cols.append(Col("source_id", "INTEGER", True))
    for fk in spec.fks:
        if fk.parent == "source":
            continue
        cols.append(Col(ID_COLS[fk.parent], "INTEGER", not fk.nullable))
    for c in spec.cols:
        if name == "fact":
            # facts keep only their own measurement columns + trace ref
            if c.name in fk_cols or c.name in {"fact_key", "source_key"}:
                continue
        elif c.name == "source_key" and name != "source":
            continue
        elif c.name in fk_cols and c.name != "source_key":
            continue  # replaced by the parent id
        cols.append(c)
    return cols


def global_ddl() -> list[str]:
    ddl = []
    for name in TABLES:
        cols = _global_columns(name)
        lines = []
        for c in cols:
            nn = " NOT NULL" if c.required else ""
            pk = " PRIMARY KEY" if c.name == ID_COLS[name] else ""
            lines.append(f'"{c.name}" {c.type}{nn}{pk}')
        for fk in TABLES[name].fks:
            if fk.parent == "source":
                lines.append(
                    f'FOREIGN KEY ("source_id") REFERENCES "{GLOBAL_NAMES["source"]}" ("source_id")'
                )
            else:
                pid = ID_COLS[fk.parent]
                lines.append(
                    f'FOREIGN KEY ("{pid}") REFERENCES "{GLOBAL_NAMES[fk.parent]}" ("{pid}")'
                )
        body = ",\n    ".join(lines)
        ddl.append(f'CREATE OR REPLACE TABLE "{GLOBAL_NAMES[name]}" (\n    {body}\n)')
    return ddl


def write_duckdb(
    path: Path,
    global_tables: dict[str, pd.DataFrame],
    expected_fact_rows: int,
) -> None:
    """Write all global tables in one transaction to a temp file, rename into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_str)
    tmp.unlink()  # duckdb must create the file itself
    try:
        con = duckdb.connect(str(tmp))
        con.execute("BEGIN")
        for stmt in global_ddl():
            con.execute(stmt)
        for name in TABLES:
            cols = _global_columns(name)
            df = global_tables[name][[c.name for c in cols]]
            ph = ", ".join("?" for _ in cols)
            rows = [
                tuple(_py(v, name, c.name) for v, c in zip(rec, cols))
                for rec in df.itertuples(index=False, name=None)
            ]
            if rows:
                con.executemany(
                    f'INSERT INTO "{GLOBAL_NAMES[name]}" VALUES ({ph})', rows
                )
        got = con.execute(f'SELECT count(*) FROM "{GLOBAL_NAMES["fact"]}"').fetchone()
        assert got is not None
        if got[0] != expected_fact_rows:
            raise ContractError(
                f"fact rows in DuckDB {got[0]} != expected {expected_fact_rows}"
            )
        con.execute("COMMIT")
        con.close()
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def build_compilations(extracted_dirs: list[Path], out_path: Path) -> dict[str, int]:
    """Read each extract.py output dir, build global tables, write the DuckDB file."""
    per_source = [read_extracted(d) for d in extracted_dirs]
    glob = assign_global_keys(per_source)
    expected = sum(len(t["fact"]) for t in per_source)
    if len(glob["fact"]) != expected:
        raise ContractError(f"global fact rows {len(glob['fact'])} != sum {expected}")
    write_duckdb(out_path, glob, expected)
    return {GLOBAL_NAMES[n]: len(glob[n]) for n in TABLES}
