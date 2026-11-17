#!/usr/bin/env python3
"""MOSAIC v2.0 (Paradis et al.) PostgreSQL dump -> compilation-contract tables.

Paradis, S. et al. (2023): Modern Ocean Sediment Archive and Inventory of Carbon (MOSAIC), v2.0.
doi:10.5281/zenodo.8322094; ESSD preprint essd-2023-158; CC BY 4.0 (``docs/README.md``). The file we hold is
``Datasets/mosaic_ESSD``: a PostgreSQL custom-format archive of the database ``mosaic_2_ESSD`` (archive created
2023-04-25, i.e. before the Zenodo v2.0 release of 2023-09-06).

READING THE DUMP. A ``pg_restore`` binary new enough for the archive version is needed and its path is a REQUIRED argument
(``--pg-restore``): this script does not look one up and does not gate on a version number. It runs
``pg_restore --list`` (the archive header must say ``dbname: mosaic_2_ESSD`` and ``created at 2023-04-25``) and
``pg_restore --data-only -f <file>`` (plain-text ``COPY`` blocks), raises if either exits non-zero or writes to
stderr, and then raises unless the COPY blocks are exactly the 27 expected tables with the expected row counts
(``EXPECTED_ROWS``) and the expected column lists of the tables read here. The COPY text format is decoded in full
(``\\N`` is NULL only as the whole field; ``\\\\ \\n \\t \\r \\b \\f \\v``, octal and hex escapes).

GRAIN. One fact per NON-NULL VALUE CELL of ``sample_composition`` (every value column) and of ``sample_sediment_properties``
(dry bulk density, porosity, water content, gravel/sand/silt/clay/mud, mean/median/mode grain size). A cell is
identified by (table, sample_id, material_analyzed_id, replicate, column). The other 11 value tables (stable isotopes,
14C, biomarkers, rock-eval, ...), sediment ``surface_area`` and ``cumulative_dry_mass_g_cm2`` are NOT loaded (counted per
column in ``extract_report.json``). ``spatial_ref_sys`` is empty.

LINKING A CELL TO ITS METADATA. ``sample_metadata`` has one row per (sample, material, analysis, replicate, article,
contact); its ``sample_analysis_id`` is the analysis (column) named in ``sample_analysis_description``. A value cell's
metadata rows are the rows with the same (sample_id, material_analyzed_id, sample_analysis_id of the column,
replicate). They give the article(s), ``method``, ``method_details`` and ``sample_analysis_calculated``.
  * no such row -> the cell is skipped, reason ``no_metadata_row`` (no article can be attached; the replicate numbering of the
    value table and of ``sample_metadata`` disagree for these cells; nothing is borrowed from another replicate);
  * several rows with different articles -> composite ``reference_key`` ``article:<a>+article:<b>`` (sorted), one
    citation row per article (plan 3.4);
  * several rows that disagree on method / method_details / calculated -> ``method_status`` =
    ``ambiguous_duplicate_method_id``, technique NULL, every row in ``method.params``.
``method_status``: ``matched`` (method known), ``method_id_null`` (``method`` = ``_unknown``: technique NULL),
``ambiguous_duplicate_method_id`` (above). ``technique`` = ``method``; ``method_details`` is in ``params``.

ERRORS. Each ``*_error`` column has its own analysis id and metadata, and the source's descriptions (``DatasetS2.csv``,
the Excel template README) say only "Error of ..." - never SD or SE. Decision (owner): DEFAULT TO SD. The error is stored in
``fact.sd`` (``se`` stays NULL), the fact gets the flag ``error_type_missing``, ``method.notes`` says "error column assumed to
be a standard deviation; the source does not state SD or SE", and ``method.params`` keeps ``error_value``, ``error_column``,
``error_meaning`` (unstated) of the fact's own method row. An error
cell whose value cell is blank or skipped is skipped, reason ``error_without_fact``.

ATTRIBUTES. ``mosaic_*`` (see ``compilation_vocab.ATTRIBUTES``): organic carbon, total carbon, TIC, CaCO3, LOI organic matter,
total nitrogen, OC:TN, dry bulk density, porosity, water content (C1); gravel/sand/silt/clay(<4 um and <2 um)/mud, mean/
median/mode grain size (G1); organic and inorganic nitrogen, OC:ON, total and organic P, total S, biogenic silica (SiO2,
opal) as ``other``. Units are the source's (``sample_analysis_units``), verbatim: ``%``, ``g·cm-3``, ``-`` (porosity),
``µm`` (U+00B5). Blank unit (OC:TN, OC:ON ratios) -> ``(blank in source)``. ``clay_%_2microm`` has unit ``µm`` in the dump and
in DatasetS2 although it is a percentage content (its description is "Content of clay (< 2 um)", DatasetS1 sums it with the
other percentages to 100): declared as ``%`` (a documented correction of an obvious source error; the raw unit is in params).

MATERIAL ANALYSED. ``material_analyzed_id`` 1 is bulk sediment. Every other material (size or density fraction, TOC, ...) is
loaded but the fact gets the flag ``non_bulk_material`` and the material label is in ``method.params`` and the method key.
Material ``surface_area`` (108 organic-carbon values of 0.009-0.07 in a ``%`` column; the quantity is not a bulk percentage)
is skipped, reason ``material_unit_unclear``.

LOCATIONS (``geopoints``, one per core that has a fact). lat/lon; ``site_name`` = core_name; ``water_body`` = ``seas``;
``bioregion`` = Longhurst province; ``geomorphic_setting`` = ``geomorphological_site`` raw (label map: shelf, estuary, fjord
only). DEPTH: ``water_depth_m`` is loaded ONLY where ``water_depth_source_id`` = 1 (given in the manuscript); id 2 (extracted
from GEBCO 2020, derived) is not loaded (plan rule 3.5). HABITAT: raw ``habitat_type`` = ``marine sediment core`` (-> Subtidal
soft sediment by dataset scope) only for cores whose loaded depth is >= the REQUIRED ``--min-subtidal-depth-m``; NULL for
shallower cores and for cores with no loaded depth. Facts at such locations get ``habitat_from_scope``. ``position_method``
records ``georeferenced_coordinates`` (1 = obtained by georeferencing a map, 0 = from the source; tutorial wording).

SAMPLES (``samples``, one per sample that has a fact): depth_min/max = upper/bottom depth, representative depth = the
source's ``sample_depth_average_cm`` (kept as given; it is not always the midpoint). Sampling year and month from the core:
``sampling_year``/``sampling_month`` columns; where the year (month) is NULL and ``sampling_date`` is present it is taken from
the date. Cores where the two disagree are listed in the report (the columns win).

REFERENCES: ``reference_key`` = ``article:<article_id>`` (own id; composite ``a+b`` for cells with several articles);
citation: DOI (as stored), title, year, authors from ``authorship`` ordered by ``author_id`` (the dump has no author position).

FLAGS: zero_value, negative_value, outside_source_qc_range (outside the min/max of DatasetS1 for that column),
calculated_by_source (any metadata row says the value was calculated), replicate (replicate number > 1), non_bulk_material,
habitat_from_scope, year_missing, depth_missing (no upper, bottom or average depth). Nothing is changed or dropped.

PROVENANCE: ``source_row_ref`` = ``mosaic_ESSD[<table>]#data_row=<0-based row of that table's COPY block in pg_restore
--data-only order>:<column>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD --pg-restore /path/to/pg_restore --min-subtidal-depth-m 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "paradis-mosaic"
SOURCE_NAME = "MOSAIC v2.0 (Modern Ocean Sediment Archive and Inventory of Carbon)"
SOURCE_DOI = "10.5281/zenodo.8322094"
SOURCE_VERSION = "v2.0 (Zenodo 2023-09-06); dump mosaic_2_ESSD, archive created 2023-04-25"
SOURCE_LICENSE = "CC BY 4.0"
DUMP_FILE = "mosaic_ESSD"
DUMP_DBNAME = "mosaic_2_ESSD"
DUMP_CREATED = "2023-04-25"
CITATION = (
    "Paradis, S. et al. (2023): Modern Ocean Sediment Archive and Inventory of Carbon (MOSAIC), v2.0 [Dataset]. Zenodo, "
    "https://doi.org/10.5281/zenodo.8322094 (ESSD preprint essd-2023-158). CC BY 4.0 per the repository docs/README.md."
)

ALLOWED_SKIP_REASONS = frozenset({"no_metadata_row", "error_without_fact", "material_unit_unclear"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

# ------------------------------------------------------------------ dump gate

EXPECTED_ROWS: dict[str, int] = {
    "articles": 368, "authors": 1139, "authorship": 1819, "geopoints": 20851, "sample_14C": 3141,
    "sample_alcohols": 82, "sample_alkanes": 782, "sample_alkenones": 165, "sample_amino_acids": 91,
    "sample_analysis_description": 216, "sample_analysis_type": 13, "sample_biomarkers_CuO": 290,
    "sample_biomarkers_CuO_grouped": 1530, "sample_composition": 35218, "sample_fatty_acids": 271,
    "sample_isotopes": 12955, "sample_material_analyzed": 104, "sample_metadata": 199429,
    "sample_rock_eval_pyrolysis": 1022, "sample_sediment_properties": 14520, "sample_sterols": 174,
    "samples": 39487, "sampling_campaign": 706, "sampling_method": 12, "sampling_nexus": 964,
    "source_additional_data": 2, "spatial_ref_sys": 0,
}
VALUE_TABLES = (  # the 13 sample_* tables whose non-key columns are measurement cells
    "sample_composition", "sample_sediment_properties", "sample_isotopes", "sample_14C", "sample_alcohols",
    "sample_alkanes", "sample_alkenones", "sample_amino_acids", "sample_biomarkers_CuO",
    "sample_biomarkers_CuO_grouped", "sample_fatty_acids", "sample_rock_eval_pyrolysis", "sample_sterols",
)
KEY_COLS = ("sample_id", "material_analyzed_id", "replicate")
EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "articles": ("article_id", "title", "doi", "year", "journal", "comments"),
    "authors": ("author_id", "author_lastname", "author_firstname"),
    "authorship": ("article_id", "author_id"),
    "geopoints": ("core_id", "core_name", "latitude", "longitude", "sampling_date", "water_depth_m",
                  "water_depth_source_id", "core_length_cm", "sampling_method_id", "sampling_campaign_id", "country",
                  "longhurst_provinces_full", "exclusive_economics_zone", "seas", "ocean", "core_comment",
                  "geomorphological_site", "entry_user_name", "geopoints_point", "marcats", "georeferenced_coordinates",
                  "sampling_year", "sampling_month", "sampling_day"),
    "sample_analysis_description": ("sample_analysis_id", "sample_analysis", "sample_analysis_units",
                                    "sample_analysis_description", "sample_analysis_type_id"),
    "sample_analysis_type": ("sample_analysis_type_id", "sample_analysis_type", "sample_analysis_type_description"),
    "sample_material_analyzed": ("material_analyzed_id", "material_analyzed"),
    "sample_metadata": ("sample_id", "material_analyzed_id", "sample_analysis_id", "sample_exclusivity_clause",
                        "sample_core_id", "sample_analysis_calculated", "sample_article_id", "sample_contact_person_id",
                        "entry_user_name", "replicate", "method", "method_details"),
    "samples": ("sample_id", "sample_name", "sample_depth_upper_cm", "sample_depth_bottom_cm",
                "sample_depth_average_cm", "sample_comment", "core_id"),
    "source_additional_data": ("water_depth_source_id", "water_depth_source_desc"),
}
WATER_DEPTH_MANUSCRIPT, WATER_DEPTH_GEBCO = "1", "2"
# the only (sample, analysis) pairs whose metadata core differs from samples.core_id: sample 10544 (14C analyses,
# not loaded). A new mismatch raises.
KNOWN_CORE_MISMATCH_SAMPLES = frozenset({"10544"})
EXPECTED_CORE_MISMATCH_ROWS = 4

# ------------------------------------------------------------------ phase 1 mapping


@dataclass(frozen=True)
class Col:
    attribute: str
    error_column: str | None = None
    raw_unit_note: str | None = None


COMPOSITION: dict[str, Col] = {
    "total_carbon_%": Col("mosaic_total_carbon", "total_carbon_%_error"),
    "total_organic_carbon_%": Col("mosaic_organic_carbon", "total_organic_carbon_%_error"),
    "OM_LOI_%": Col("mosaic_loi_organic_matter", "OM_LOI_%_error"),
    "total_inorganic_carbon_%": Col("mosaic_total_inorganic_carbon", "total_inorganic_carbon_%_error"),
    "CaCO3_%": Col("mosaic_caco3", "CaCO3_%_error"),
    "organic_nitrogen_%": Col("mosaic_organic_nitrogen", "organic_nitrogen_%_error"),
    "inorganic_nitrogen_%": Col("mosaic_inorganic_nitrogen", "inorganic_nitrogen_%_error"),
    "total_nitrogen_%": Col("mosaic_total_nitrogen", "total_nitrogen_%_error"),
    "OC:ON_ratio": Col("mosaic_oc_on_ratio", "OC:ON_ratio_error"),
    "OC:TN_ratio": Col("mosaic_oc_tn_ratio", "OC:TN_ratio_error"),
    "SiO2_%": Col("mosaic_biogenic_silica", "SiO2_%_error"),
    "P_total_%": Col("mosaic_total_phosphorus", "P_total_%_error"),
    "P_org_%": Col("mosaic_organic_phosphorus", "P_org_%_error"),
    "S_total_%": Col("mosaic_total_sulphur"),
}
SEDIMENT: dict[str, Col] = {
    "dry_bulk_density_g_cm3": Col("mosaic_dry_bulk_density"),
    "porosity": Col("mosaic_porosity"),
    "water_content_%": Col("mosaic_water_content"),
    "gravel_%": Col("mosaic_gravel_content"),
    "sand_%": Col("mosaic_sand_content"),
    "silt_%": Col("mosaic_silt_content"),
    "clay_%_4microm": Col("mosaic_clay_lt4um"),
    "clay_%_2microm": Col("mosaic_clay_lt2um", None, "dump and DatasetS2 give unit 'µm' for this percentage column"),
    "mud_silt_clay_%": Col("mosaic_mud_content"),
    "mean_grain_size_microm": Col("mosaic_mean_grain_size", "mean_grain_size_microm_error"),
    "median_grain_size_microm": Col("mosaic_median_grain_size"),
    "mode_grain_size_microm": Col("mosaic_mode_grain_size"),
}
LOADED: dict[str, dict[str, Col]] = {"sample_composition": COMPOSITION, "sample_sediment_properties": SEDIMENT}
# value columns the extract knows about but does not load, with the reason (every other column of an unloaded table is
# "not_loaded: family")
SEDIMENT_NOT_LOADED = {
    "surface_area_m2_g": "not in the plan's phase-1 list",
    "surface_area_m2_g_error": "not in the plan's phase-1 list",
    "cumulative_dry_mass_g_cm2": "no sample_analysis_description row (no analysis id, so no metadata link)",
}
UNITS_BLANK = "(blank in source)"
SKIP_MATERIAL_LABELS = frozenset({"surface_area"})
BULK_MATERIAL = "bulk"
NA = "blank in source"
ERROR_MEANING = "unstated: the source only says 'Error of ...' (SD or SE not given)"
SD_ASSUMPTION_NOTE = "error column assumed to be a standard deviation; the source does not state SD or SE"

# ------------------------------------------------------------------ pg_restore wrapper and COPY parser


def run_pg_restore(binary: Path, dump: Path, out_sql: Path) -> dict:
    """Run ``pg_restore --list`` and ``--data-only -f out_sql``; return the version and header text. Raises on any problem."""
    if not binary.is_file():
        raise ContractError(f"pg_restore binary {binary} does not exist")
    res: dict[str, str] = {}
    for label, cmd in (
        ("version", [str(binary), "--version"]),
        ("list", [str(binary), "--list", str(dump)]),
        ("data", [str(binary), "--data-only", "-f", str(out_sql), str(dump)]),
    ):
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if p.returncode != 0 or p.stderr.strip():
            raise ContractError(f"{' '.join(cmd)} failed (exit {p.returncode}): {p.stderr.strip()[:2000]}")
        res[label] = p.stdout.strip()
    header = [ln for ln in res["list"].splitlines() if ln.startswith(";")]
    text = "\n".join(header)
    if f"dbname: {DUMP_DBNAME}" not in text:
        raise ContractError(f"archive header does not name dbname {DUMP_DBNAME}: {text[:500]}")
    if not re.search(rf"Archive created at {DUMP_CREATED}\b", text):
        raise ContractError(f"archive was not created at {DUMP_CREATED}: {text[:500]}")
    return {"pg_restore_version": res["version"], "archive_header": header}


_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}
_ESC_RE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|[0-7]{1,3}|.)", re.S)


def _unescape(m: re.Match) -> str:
    g = m.group(1)
    if g[0] == "x" and len(g) > 1:
        code = int(g[1:], 16)
    elif g[0] in "01234567":
        code = int(g, 8)
    else:
        return _ESCAPES.get(g, g)  # any other backslashed character stands for itself (COPY text format)
    if code >= 128:
        raise ContractError(f"COPY escape {m.group(0)!r} is not ASCII; the dump is expected to carry UTF-8 text")
    return chr(code)


def decode_field(f: str) -> str | None:
    if f == "\\N":
        return None
    return _ESC_RE.sub(_unescape, f) if "\\" in f else f


_COPY_HDR = re.compile(r"^COPY (\S+) \((.*)\) FROM stdin;$")
_COL_TOK = re.compile(r'"((?:[^"]|"")*)"|([^,\s][^,]*)')


def parse_copy_sql(lines) -> dict[str, tuple[list[str], list[list[str | None]]]]:
    """Plain-text pg_restore output -> {table: (columns, rows)} for every COPY block (schema prefix removed)."""
    out: dict[str, tuple[list[str], list[list[str | None]]]] = {}
    cur: tuple[str, list[str], list[list[str | None]]] | None = None
    for line in lines:
        line = line.rstrip("\n")
        if cur is None:
            m = _COPY_HDR.match(line)
            if m:
                name = m.group(1).split(".")[-1].strip('"')
                cols = [a.replace('""', '"') if a else b for a, b in _COL_TOK.findall(m.group(2))]
                if name in out:
                    raise ContractError(f"two COPY blocks for {name}")
                cur = (name, cols, [])
            continue
        if line == "\\.":
            out[cur[0]] = (cur[1], cur[2])
            cur = None
            continue
        f = line.split("\t")
        if len(f) != len(cur[1]):
            raise ContractError(f"{cur[0]}: row with {len(f)} fields, header has {len(cur[1])}")
        cur[2].append([decode_field(x) for x in f])
    if cur is not None:
        raise ContractError(f"COPY block for {cur[0]} is not terminated")
    return out


def check_dump_tables(tables: dict) -> None:
    if set(tables) != set(EXPECTED_ROWS):
        raise ContractError(f"COPY tables differ from expected: extra {sorted(set(tables) - set(EXPECTED_ROWS))}, "
                            f"missing {sorted(set(EXPECTED_ROWS) - set(tables))}")
    for name, n in EXPECTED_ROWS.items():
        if len(tables[name][1]) != n:
            raise ContractError(f"{name}: {len(tables[name][1])} rows, expected {n}")
    for name, cols in EXPECTED_COLUMNS.items():
        if tuple(tables[name][0]) != cols:
            raise ContractError(f"{name}: columns {tables[name][0]} differ from the expected list")
    for name in VALUE_TABLES:
        cols = tables[name][0]
        if tuple(cols[:2]) != ("sample_id", "material_analyzed_id") or "replicate" not in cols:
            raise ContractError(f"{name}: key columns not as expected: {cols[:3]}")
        if len(set(cols)) != len(cols):
            raise ContractError(f"{name}: duplicate column names")


# ------------------------------------------------------------------ helpers


def _num(s: str | None, what: str) -> float | None:
    if s is None:
        return None
    try:
        v = float(s)
    except ValueError as e:
        raise ContractError(f"{what}: not a number: {s!r}") from e
    if not math.isfinite(v):
        raise ContractError(f"{what}: non-finite {s!r}")
    return v


def _int(s: str | None, what: str) -> int | None:
    if s is None:
        return None
    if not re.fullmatch(r"-?\d+", s):
        raise ContractError(f"{what}: not an integer: {s!r}")
    return int(s)


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    return t or None


def _unit_for(attr: str, col: str, source_unit: str | None, has_note: bool) -> str:
    """The declared units string for a column. It must equal the source's ``sample_analysis_units`` (or the explicit
    blank marker where the source's is blank), except where a documented correction applies (``Col.raw_unit_note``)."""
    decl = ATTRIBUTE_DECL[attr].units
    if len(decl) != 1:
        raise ContractError(f"{attr}: expected exactly one declared unit, got {sorted(decl)}")
    unit = next(iter(decl))
    if not has_note:
        want = UNITS_BLANK if not source_unit else source_unit
        if unit != want:
            raise ContractError(f"{col}: declared unit {unit!r} differs from the source's {source_unit!r}")
    return unit


# ------------------------------------------------------------------ main build


def build_tables(
    dump: dict,
    s1: pd.DataFrame,
    access_date: str,
    min_subtidal_depth_m: float,
    article_ids: frozenset[str] | None = None,
    extra_report: dict | None = None,
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    check_dump_tables(dump)

    def rows(name: str) -> list[list[str | None]]:
        return dump[name][1]

    def idx(name: str) -> dict[str, int]:
        return {c: i for i, c in enumerate(dump[name][0])}

    # ---- lookups
    desc_i = idx("sample_analysis_description")
    desc = {r[desc_i["sample_analysis"]]: r for r in rows("sample_analysis_description")}
    if len(desc) != len(rows("sample_analysis_description")):
        raise ContractError("duplicate sample_analysis names")
    type_name = {r[0]: r[1] for r in rows("sample_analysis_type")}
    material = {r[0]: r[1] for r in rows("sample_material_analyzed")}
    depth_src = {r[0]: r[1] for r in rows("source_additional_data")}
    if depth_src != {"1": "Water depth provided in manuscript", "2": "Water depth extracted from GEBCO 2020"}:
        raise ContractError(f"water depth sources changed: {depth_src}")
    articles = {r[0]: r for r in rows("articles")}
    author = {r[0]: r for r in rows("authors")}
    by_article: dict[str, list[str]] = defaultdict(list)
    for a_id, au_id in rows("authorship"):
        if au_id not in author or a_id not in articles:
            raise ContractError(f"authorship row ({a_id}, {au_id}) has no author or article")
        by_article[a_id].append(au_id)
    gi = idx("geopoints")
    cores = {r[gi["core_id"]]: r for r in rows("geopoints")}
    if len(cores) != EXPECTED_ROWS["geopoints"]:
        raise ContractError("duplicate core_id")
    si = idx("samples")
    samples = {r[si["sample_id"]]: r for r in rows("samples")}
    if len(samples) != EXPECTED_ROWS["samples"]:
        raise ContractError("duplicate sample_id")
    for r in samples.values():
        if r[si["core_id"]] not in cores:
            raise ContractError(f"sample {r[si['sample_id']]} has unknown core {r[si['core_id']]}")

    # ---- metadata index
    mi = idx("sample_metadata")
    meta: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    mismatch: list[tuple[str, str]] = []
    for r in rows("sample_metadata"):
        if r[mi["sample_exclusivity_clause"]] != "0":
            raise ContractError("sample_exclusivity_clause != 0: restricted data present")
        if r[mi["sample_article_id"]] not in articles:
            raise ContractError(f"metadata article {r[mi['sample_article_id']]} not in articles")
        sid = r[mi["sample_id"]]
        if sid not in samples:
            raise ContractError(f"metadata sample {sid} not in samples")
        if samples[sid][si["core_id"]] != r[mi["sample_core_id"]]:
            mismatch.append((sid, r[mi["sample_analysis_id"]]))
        if r[mi["sample_analysis_calculated"]] not in ("0", "1"):
            raise ContractError(f"sample_analysis_calculated {r[mi['sample_analysis_calculated']]!r}")
        meta[(sid, r[mi["material_analyzed_id"]], r[mi["sample_analysis_id"]], r[mi["replicate"]])].append(
            {"article": r[mi["sample_article_id"]], "method": r[mi["method"]], "method_details": r[mi["method_details"]],
             "calculated": int(r[mi["sample_analysis_calculated"]])})
    if {s for s, _ in mismatch} != KNOWN_CORE_MISMATCH_SAMPLES or len(mismatch) != EXPECTED_CORE_MISMATCH_ROWS:
        raise ContractError(f"metadata core mismatches changed: {mismatch}")
    n_calc_rows = sum(1 for r in rows("sample_metadata") if r[mi["sample_analysis_calculated"]] == "1")

    # ---- the column maps: attribute declarations, S1 limits, analysis ids
    s1_lim = {r["variables"]: (None if pd.isna(r["min_value"]) else float(r["min_value"]),
                               None if pd.isna(r["max_value"]) else float(r["max_value"])) for _, r in s1.iterrows()}
    aid_of: dict[tuple[str, str], str] = {}
    for tbl, cmap in LOADED.items():
        for col, spec in cmap.items():
            for c in (col, spec.error_column):
                if c is None:
                    continue
                if c not in dump[tbl][0]:
                    raise ContractError(f"{tbl}: expected column {c!r} missing")
                if c not in desc:
                    raise ContractError(f"{c}: no sample_analysis_description row")
                if type_name[desc[c][desc_i["sample_analysis_type_id"]]] != tbl:
                    raise ContractError(f"{c}: analysis type is not {tbl}")
                aid_of[(tbl, c)] = desc[c][desc_i["sample_analysis_id"]]
            if spec.attribute not in ATTRIBUTE_DECL:
                raise ContractError(f"{spec.attribute} not declared in ATTRIBUTES")
    err_cols = {(t, s.error_column) for t, cm in LOADED.items() for s in cm.values() if s.error_column}

    # ---- core-level derived values
    def core_info(cid: str) -> dict:
        r = cores[cid]
        lat, lon = _num(r[gi["latitude"]], "latitude"), _num(r[gi["longitude"]], "longitude")
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ContractError(f"core {cid}: coordinates {lat}, {lon}")
        src = r[gi["water_depth_source_id"]]
        if src not in (WATER_DEPTH_MANUSCRIPT, WATER_DEPTH_GEBCO):
            raise ContractError(f"core {cid}: water_depth_source_id {src!r}")
        raw_depth = _num(r[gi["water_depth_m"]], "water_depth_m")
        if raw_depth is not None and raw_depth < 0:
            raise ContractError(f"core {cid}: negative water depth {raw_depth}")
        depth = raw_depth if src == WATER_DEPTH_MANUSCRIPT else None
        date = r[gi["sampling_date"]]
        yr, mo = _int(r[gi["sampling_year"]], "sampling_year"), _int(r[gi["sampling_month"]], "sampling_month")
        d_yr = d_mo = None
        if date is not None:
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date)
            if not m:
                raise ContractError(f"core {cid}: sampling_date {date!r}")
            d_yr, d_mo = int(m.group(1)), int(m.group(2))
        year = yr if yr is not None else d_yr
        month = mo if mo is not None else d_mo
        if month is not None and not 1 <= month <= 12:
            raise ContractError(f"core {cid}: month {month}")
        georef = r[gi["georeferenced_coordinates"]]
        if georef not in ("0", "1"):
            raise ContractError(f"core {cid}: georeferenced_coordinates {georef!r}")
        site = r[gi["geomorphological_site"]]
        eligible = depth is not None and depth >= min_subtidal_depth_m
        return {
            "lat": lat, "lon": lon, "depth": depth, "depth_raw": raw_depth, "depth_src": src, "year": year, "month": month,
            "year_from_date": yr is None and d_yr is not None, "month_from_date": mo is None and d_mo is not None,
            "date_disagrees": (yr is not None and d_yr is not None and yr != d_yr) or (mo is not None and d_mo is not None and mo != d_mo),
            "georef": georef, "site": site, "habitat_scope": eligible,
        }

    core_cache: dict[str, dict] = {}

    # ---- the cell pass
    counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    nonnull: dict[tuple[str, str], int] = {}
    facts: list[dict] = []
    skips: list[dict] = []
    methods: dict[str, dict] = {}
    used_samples: set[str] = set()
    used_cores: set[str] = set()
    used_refs: set[str] = set()
    n_multi_article = n_ambiguous_method = n_calc_conflict = 0
    n_nonbulk = 0
    for tbl in VALUE_TABLES:
        cols, trows = dump[tbl]
        ci = {c: i for i, c in enumerate(cols)}
        value_cols = [c for c in cols if c not in KEY_COLS]
        for c in value_cols:
            nonnull[(tbl, c)] = sum(1 for r in trows if r[ci[c]] is not None)
        if tbl not in LOADED:
            for c in value_cols:
                counts[(tbl, c)]["not_loaded: table not loaded"] = nonnull[(tbl, c)]
            continue
        cmap = LOADED[tbl]
        known = set(cmap) | {s.error_column for s in cmap.values() if s.error_column}
        extra = [c for c in value_cols if c not in known]
        if tbl == "sample_sediment_properties":
            if set(extra) != set(SEDIMENT_NOT_LOADED):
                raise ContractError(f"{tbl}: unhandled columns {sorted(set(extra) ^ set(SEDIMENT_NOT_LOADED))}")
        elif extra:
            raise ContractError(f"{tbl}: columns with no mapping: {extra}")
        for c in extra:
            counts[(tbl, c)][f"not_loaded: {SEDIMENT_NOT_LOADED[c]}"] = nonnull[(tbl, c)]
        for row_no, r in enumerate(trows):
            sid, mat, rep = r[ci["sample_id"]], r[ci["material_analyzed_id"]], r[ci["replicate"]]
            if sid not in samples or mat not in material:
                raise ContractError(f"{tbl} row {row_no}: unknown sample {sid} or material {mat}")
            _int(rep, "replicate")
            outcome: dict[str, str] = {}
            for col, spec in cmap.items():
                txt = r[ci[col]]
                if txt is None:
                    continue
                key = (sid, mat, aid_of[(tbl, col)], rep)
                mrows = meta.get(key)
                ref = f"{DUMP_FILE}[{tbl}]#data_row={row_no}:{col}"
                if mrows is None and article_ids is not None:
                    outcome[col] = "outside_subset"
                    continue
                if mrows is None:
                    skips.append({"reason": "no_metadata_row", "table": tbl, "data_row": row_no, "column": col,
                                  "sample_id": sid, "material_analyzed_id": mat, "replicate": rep, "value": txt,
                                  "detail": "no sample_metadata row on (sample, material, analysis, replicate)"})
                    outcome[col] = "skipped: no_metadata_row"
                    continue
                arts = sorted({m["article"] for m in mrows})
                if article_ids is not None and not (set(arts) & article_ids):
                    outcome[col] = "outside_subset"
                    continue
                if material[mat] in SKIP_MATERIAL_LABELS:
                    skips.append({"reason": "material_unit_unclear", "table": tbl, "data_row": row_no, "column": col,
                                  "sample_id": sid, "material_analyzed_id": mat, "replicate": rep, "value": txt,
                                  "detail": f"material analysed is {material[mat]!r}; the value is not a bulk percentage"})
                    outcome[col] = "skipped: material_unit_unclear"
                    continue
                value = _num(txt, f"{tbl} row {row_no} {col}")
                assert value is not None
                outcome[col] = "fact"
                # error (value cell exists, error attached in params)
                err_val = None  # (stored as fact.sd, see ERRORS above)
                if spec.error_column and r[ci[spec.error_column]] is not None:
                    err_val = _num(r[ci[spec.error_column]], f"{tbl} row {row_no} {spec.error_column}")
                # metadata combination
                triples = sorted({(m["method"], m["method_details"] or "", m["calculated"]) for m in mrows})
                if len(arts) > 1:
                    n_multi_article += 1
                if len(triples) > 1:
                    n_ambiguous_method += 1
                if len({t[2] for t in triples}) > 1:
                    n_calc_conflict += 1
                calculated = any(t[2] == 1 for t in triples)
                if len(triples) == 1:
                    meth, details, _ = triples[0]
                    technique = None if meth == "_unknown" else meth
                    status = "method_id_null" if technique is None else "matched"
                else:
                    technique, status = None, "ambiguous_duplicate_method_id"
                source_unit = desc[col][desc_i["sample_analysis_units"]]
                units = _unit_for(spec.attribute, col, source_unit, spec.raw_unit_note is not None)
                # method row: one per distinct (attribute, material, replicate, metadata combination, error)
                mkey = json.dumps([spec.attribute, mat, rep, triples, err_val], separators=(",", ":"))
                if mkey not in methods:
                    params = {
                        "table": tbl, "column": col, "material_analyzed": material[mat], "material_analyzed_id": mat,
                        "replicate": int(rep), "unit_in_source": source_unit,
                        "metadata_rows": [{"method": t[0], "method_details": t[1] or None, "calculated": t[2]} for t in triples],
                        "article_ids": arts,
                    }
                    if spec.raw_unit_note:
                        params["unit_note"] = spec.raw_unit_note
                    if err_val is not None:
                        params["error_column"] = spec.error_column
                        params["error_value"] = err_val
                        params["error_meaning"] = ERROR_MEANING
                    methods[mkey] = {
                        "source_key": SOURCE_KEY, "method_key": mkey, "source_method_id": None, "attribute": spec.attribute,
                        "method_status": status, "technique": technique, "coring_method": None, "carbon_type": None,
                        "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                        "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None,
                        "light_dark": None, "incubation_type": None, "experimental_status": None,
                        "notes": SD_ASSUMPTION_NOTE if err_val is not None else None,
                        "params": json.dumps(params, sort_keys=True),
                    }
                cid = samples[sid][si["core_id"]]
                if cid not in core_cache:
                    core_cache[cid] = core_info(cid)
                ci_ = core_cache[cid]
                sr = samples[sid]
                up, bot, avg = (_num(sr[si[k]], k) for k in ("sample_depth_upper_cm", "sample_depth_bottom_cm", "sample_depth_average_cm"))
                if up is not None and bot is not None and up > bot:
                    raise ContractError(f"sample {sid}: upper depth above bottom")
                flags = []
                if value == 0:
                    flags.append("zero_value")
                if value < 0:
                    flags.append("negative_value")
                lim = s1_lim.get(col)
                if lim is not None and ((lim[0] is not None and value < lim[0]) or (lim[1] is not None and value > lim[1])):
                    flags.append("outside_source_qc_range")
                if err_val is not None:
                    flags.append("error_type_missing")
                if calculated:
                    flags.append("calculated_by_source")
                if int(rep) > 1:
                    flags.append("replicate")
                if material[mat] != BULK_MATERIAL:
                    flags.append("non_bulk_material")
                    n_nonbulk += 1
                if ci_["habitat_scope"]:
                    flags.append("habitat_from_scope")
                if ci_["year"] is None:
                    flags.append("year_missing")
                if up is None and bot is None and avg is None:
                    flags.append("depth_missing")
                rkey = "+".join(f"article:{a}" for a in arts)
                used_refs.add(rkey)
                used_samples.add(sid)
                used_cores.add(cid)
                decl = ATTRIBUTE_DECL[spec.attribute]
                facts.append({
                    "source_key": SOURCE_KEY, "fact_key": f"{tbl}:{sid}:{mat}:{rep}:{col}", "reference_key": rkey,
                    "location_key": f"core:{cid}", "sample_key": f"sample:{sid}", "method_key": mkey,
                    "attribute": spec.attribute, "measurement_type": decl.measurement_type,
                    "subfield": next(iter(decl.subfields)), "value": value, "units_raw": units, "sd": np.nan if err_val is None else err_val, "se": np.nan,
                    "n": None, "flags": sorted(flags), "source_row_ref": ref,
                })
                counts[(tbl, col)]["fact"] += 1
                if err_val is not None:
                    counts[(tbl, spec.error_column)]["error_attached"] += 1
                    outcome[spec.error_column] = "error_attached"
            # remaining non-null cells of this row: errors whose value cell gave no fact, and any cell not yet classified
            for col, o in outcome.items():
                if o != "fact" and o != "error_attached":
                    counts[(tbl, col)][o] += 1
            for col, spec in cmap.items():
                if spec.error_column and r[ci[spec.error_column]] is not None and spec.error_column not in outcome:
                    val_outcome = outcome.get(col)
                    if val_outcome == "outside_subset" or (val_outcome is None and article_ids is not None):
                        counts[(tbl, spec.error_column)]["outside_subset"] += 1
                        continue
                    why = "its value cell is blank" if r[ci[col]] is None else f"its value cell was skipped ({val_outcome})"
                    skips.append({"reason": "error_without_fact", "table": tbl, "data_row": row_no, "column": spec.error_column,
                                  "sample_id": sid, "material_analyzed_id": mat, "replicate": rep,
                                  "value": r[ci[spec.error_column]], "detail": why})
                    counts[(tbl, spec.error_column)]["skipped: error_without_fact"] += 1

    # ---- accounting: every non-null cell of every value table has exactly one outcome
    acct = {}
    for (tbl, col), n in sorted(nonnull.items()):
        got = sum(counts[(tbl, col)].values())
        if got != n:
            raise ContractError(f"accounting {tbl}.{col}: {n} non-null cells but outcomes sum to {got}: {dict(counts[(tbl, col)])}")
        acct[f"{tbl}.{col}"] = {"non_null_cells": n, **dict(sorted(counts[(tbl, col)].items()))}
    if len(skips) != sum(v for c in counts.values() for k, v in c.items() if k.startswith("skipped")):
        raise ContractError("skip report length differs from the skipped outcomes")
    if not facts:
        raise ContractError("no facts")

    # ---- dimensions
    fact = pd.DataFrame(facts)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    method = pd.DataFrame(sorted(methods.values(), key=lambda r: r["method_key"]))

    ref_keys = sorted(used_refs)
    cit_rows = []
    art_used = sorted({a for k in ref_keys for a in k.replace("article:", "").split("+")}, key=int)
    for k in ref_keys:
        for a in k.replace("article:", "").split("+"):
            ar = articles[a]
            names = []
            for au in sorted(by_article.get(a, []), key=int):
                last, first = author[au][1], author[au][2]
                names.append(", ".join(x for x in (last, first) if x))
            cit_rows.append({
                "source_key": SOURCE_KEY, "citation_key": f"{k}|article:{a}", "reference_key": k, "bibliography_id": a,
                "publication_type": None, "doi": ar[2], "title": _clean(ar[1]), "authors": "; ".join(names) or None,
                "year": _int(ar[3], "article year"), "url": None, "openalex_id": None, "doi_resolved": None,
                "doi_resolution_source": None,
            })
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})
    citation = pd.DataFrame(cit_rows)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")

    core_ids = sorted(used_cores, key=int)
    lrows = []
    for cid in core_ids:
        ci_ = core_cache[cid]
        r = cores[cid]
        lrows.append({
            "source_key": SOURCE_KEY, "location_key": f"core:{cid}", "site_name": _clean(r[gi["core_name"]]),
            "source_site_id": None, "source_core_id": cid, "latitude": ci_["lat"], "longitude": ci_["lon"],
            "position_accuracy": np.nan, "position_accuracy_units": None,
            "position_method": ("coordinates georeferenced from a map" if ci_["georef"] == "1" else "coordinates from the source"),
            "country": _clean(r[gi["country"]]), "admin_division": None, "water_body": _clean(r[gi["seas"]]),
            "habitat_type": "marine sediment core" if ci_["habitat_scope"] else None,
            "geomorphic_setting": _clean(ci_["site"]), "bioregion": _clean(r[gi["longhurst_provinces_full"]]),
            "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None, "vegetation_class": None,
            "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
            "modification_status": [], "dominant_species": [], "elevation": np.nan, "elevation_units": None,
            "elevation_datum": None, "elevation_method": None,
            "water_depth_m": np.nan if ci_["depth"] is None else ci_["depth"], "distance_to_shore_km": np.nan,
        })
    loc = pd.DataFrame(lrows)

    smp_ids = sorted(used_samples, key=int)
    srows = []
    for sid in smp_ids:
        sr = samples[sid]
        ci_ = core_cache[sr[si["core_id"]]]
        up, bot, avg = (_num(sr[si[k]], k) for k in ("sample_depth_upper_cm", "sample_depth_bottom_cm", "sample_depth_average_cm"))
        srows.append({
            "source_key": SOURCE_KEY, "sample_key": f"sample:{sid}", "source_sample_id": sid,
            "depth_min_cm": np.nan if up is None else up, "depth_max_cm": np.nan if bot is None else bot,
            "representative_depth_min_cm": np.nan if avg is None else avg,
            "representative_depth_max_cm": np.nan if avg is None else avg, "depth_notes": None,
            "sampling_year": ci_["year"], "sampling_month": ci_["month"], "taxon": None, "taxon_rank": None,
            "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
            "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
            "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        })
    sample = pd.DataFrame(srows)
    sample["sampling_year"] = pd.array(list(sample["sampling_year"]), dtype="Int64")
    sample["sampling_month"] = pd.array(list(sample["sampling_month"]), dtype="Int64")

    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + f" Habitat is set from the dataset scope only for cores whose manuscript depth is >= "
                 f"{min_subtidal_depth_m} m (extract_report.json min_subtidal_depth_m).",
    }])
    tables = {"source": source, "reference": reference, "citation": citation,
              "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
              "method": method, "fact": fact}

    for what, a, b in (("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
                       ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key)):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    if fact.fact_key.duplicated().any():
        raise ContractError("duplicate fact_key")

    # ---- report
    flag_counts: Counter = Counter(x for fl in fact["flags"] for x in fl)
    all_cores = {cid: core_info(cid) for cid in cores}
    src1 = [c for c in all_cores.values() if c["depth_src"] == WATER_DEPTH_MANUSCRIPT]
    alld = [c["depth_raw"] for c in all_cores.values() if c["depth_raw"] is not None]
    d1 = [c["depth"] for c in src1 if c["depth"] is not None]

    def n_le(vals, t, strict=False):
        return int(sum(1 for v in vals if (v < t if strict else v <= t)))

    depth_report = {
        "cores": len(cores),
        "cores_depth_source_manuscript": len(src1),
        "cores_depth_source_gebco_not_loaded": len(all_cores) - len(src1),
        "all_depths_at_0_le1_le5_le10_le200": [n_le(alld, 0), n_le(alld, 1), n_le(alld, 5), n_le(alld, 10), n_le(alld, 200)],
        "manuscript_depths_at_0_le1_le5_le10_le200": [n_le(d1, 0), n_le(d1, 1), n_le(d1, 5), n_le(d1, 10), n_le(d1, 200)],
        "manuscript_depth_cores_below_threshold": n_le(d1, min_subtidal_depth_m, strict=True),
        "manuscript_depth_cores_at_or_above_threshold": len(d1) - n_le(d1, min_subtidal_depth_m, strict=True),
        "cores_with_habitat_from_scope": sum(1 for c in all_cores.values() if c["habitat_scope"]),
        "cores_without_habitat": sum(1 for c in all_cores.values() if not c["habitat_scope"]),
        "cores_with_fact": len(loc),
        "cores_with_fact_and_habitat": int(loc.habitat_type.notna().sum()),
    }
    outcome_totals: Counter = Counter()
    for c in counts.values():
        outcome_totals.update(c)
    nn_total = sum(nonnull.values())
    fam_totals = {}
    for tbl in VALUE_TABLES:
        t = Counter()
        for (tb, col), c in counts.items():
            if tb == tbl:
                t.update(c)
        fam_totals[tbl] = {"non_null_cells": sum(v for (tb, _), v in nonnull.items() if tb == tbl), **dict(sorted(t.items()))}
    skip_counts = Counter(s["reason"] for s in skips)
    report = {
        "min_subtidal_depth_m": min_subtidal_depth_m,
        "subset_article_ids": None if article_ids is None else sorted(article_ids, key=int),
        "dump_tables_rows": {k: len(v[1]) for k, v in sorted(dump.items())},
        "facts": len(fact),
        "skipped": len(skips),
        "skipped_by_reason": dict(sorted(skip_counts.items())),
        "error_cells_attached_to_facts": int(sum(v for k, v in outcome_totals.items() if k == "error_attached")),
        "non_null_cells_in_value_tables": nn_total,
        "outcome_totals": dict(sorted(outcome_totals.items())),
        "accounting_closes": nn_total == sum(outcome_totals.values()),
        "accounting_by_family": fam_totals,
        "accounting_by_column": acct,
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_table": {k: int(v) for k, v in fact.fact_key.str.split(":").str[0].value_counts().sort_index().items()},
        "flag_counts": dict(sorted(flag_counts.items())),
        "facts_non_bulk_material": n_nonbulk,
        "facts_by_material_analyzed": {
            k: int(v) for k, v in pd.Series([json.loads(methods[m]["params"])["material_analyzed"] for m in fact.method_key]).value_counts().sort_index().items()
        },
        "facts_with_several_articles": n_multi_article,
        "facts_with_metadata_rows_disagreeing_on_method": n_ambiguous_method,
        "facts_with_metadata_rows_disagreeing_on_calculated": n_calc_conflict,
        "metadata_rows_calculated_in_dump": n_calc_rows,
        "method_status_counts": method.method_status.value_counts().sort_index().to_dict(),
        "metadata_core_mismatch_rows (sample_id, analysis_id; all 14C, not loaded)": mismatch,
        "locations": len(loc), "samples": len(sample), "methods": len(method), "references": len(reference),
        "articles_cited": len(art_used), "articles_in_dump": len(articles), "citations": len(citation),
        "citations_without_doi": int(citation.doi.isna().sum()), "citations_without_title": int(citation.title.isna().sum()),
        "citations_without_authors": int(citation.authors.isna().sum()),
        "depth": depth_report,
        "geomorphic_setting_raw_counts_over_locations": loc.geomorphic_setting.fillna("(none)").value_counts().sort_index().to_dict(),
        "cores_gebco_depth_with_fact": int(sum(1 for cid in core_ids if core_cache[cid]["depth_src"] == WATER_DEPTH_GEBCO)),
        "cores_year_from_date": sorted(c for c in core_ids if core_cache[c]["year_from_date"]),
        "cores_month_from_date": len([c for c in core_ids if core_cache[c]["month_from_date"]]),
        "cores_date_disagrees_with_year_or_month (columns win)": sorted(c for c in core_ids if core_cache[c]["date_disagrees"]),
        "cores_georeferenced_from_map": int(sum(1 for cid in core_ids if core_cache[cid]["georef"] == "1")),
        "samples_average_depth_not_midpoint": int(sum(
            1 for sid in smp_ids
            if all(samples[sid][si[k]] is not None for k in ("sample_depth_upper_cm", "sample_depth_bottom_cm", "sample_depth_average_cm"))
            and abs(float(samples[sid][si["sample_depth_average_cm"]]) -
                    (float(samples[sid][si["sample_depth_upper_cm"]]) + float(samples[sid][si["sample_depth_bottom_cm"]])) / 2) > 0.01)),
        "samples_without_depth": int(fact[fact["flags"].map(lambda x: "depth_missing" in x)].sample_key.nunique()),
        "geopoints_columns_not_loaded": ["core_length_cm", "sampling_method_id", "sampling_campaign_id", "exclusive_economics_zone",
                                         "ocean", "core_comment", "entry_user_name", "geopoints_point", "marcats", "sampling_day"],
        "value_summary_by_attribute": {
            a: {"min": float(g.value.min()), "max": float(g.value.max()), "n": int(len(g))} for a, g in fact.groupby("attribute")
        },
    }
    if extra_report:
        report.update(extra_report)
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    ap.add_argument("--pg-restore", required=True, type=Path, help="path to a pg_restore binary that can read the archive")
    ap.add_argument("--min-subtidal-depth-m", required=True, type=float,
                    help="a core gets habitat 'Subtidal soft sediment' (from dataset scope) only if its manuscript water depth is >= this")
    ap.add_argument("--article-ids", nargs="+", default=None,
                    help="debug subset (staged-gate rung 3): only cells whose metadata cites one of these article ids; "
                         "every other cell is counted as outside_subset")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    if not math.isfinite(args.min_subtidal_depth_m) or args.min_subtidal_depth_m < 0:
        raise SystemExit("--min-subtidal-depth-m must be a finite number >= 0")
    dump_path = args.raw_dir / "Datasets" / DUMP_FILE
    s1 = pd.read_csv(args.raw_dir / "Datasets" / "DatasetS1.csv", dtype=str)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out_dir, prefix=".pg_restore_") as td:
        sql = Path(td) / "mosaic_data.sql"
        info = run_pg_restore(args.pg_restore, dump_path, sql)
        with open(sql, encoding="utf-8") as fh:
            dump = parse_copy_sql(fh)
    info["dump_sha256"] = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    tables, skips, report = build_tables(
        dump, s1, args.access_date, args.min_subtidal_depth_m,
        None if args.article_ids is None else frozenset(args.article_ids), info)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps({k: v for k, v in report.items() if not k.startswith("accounting_by_column")}, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
