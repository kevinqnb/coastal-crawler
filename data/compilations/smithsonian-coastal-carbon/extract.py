#!/usr/bin/env python3
"""Coastal Carbon Library (CCN v1.7.0) -> compilation-contract tables.

Reads the raw CCN_*.csv files in this directory and writes the tables defined
in ``coastal_crawler.compilations.TABLES`` as parquet into ``extracted/``
(compilation-local keys only; ``scripts/build_compilations.py`` assigns global
ids and builds ``compilations.duckdb``).

Grain: ONE FACT PER VARIABLE MEASUREMENT = one non-null cell of
``dry_bulk_density`` / ``fraction_organic_matter`` / ``fraction_carbon`` in
CCN_depthseries.csv (measurement type C1), plus one fact per core with a
``pb210_cic_accretion_rate`` in CCN_cores.csv (measurement type G2; core-level, so its
sample row has no depth interval). Depth interval, year/month and core-level labels
are context in ``sample``/``location``. The other radiometric/dating columns are
out of scope. Values are carried exactly as reported (no unit conversion, no
cleaning); suspicious values are FLAGGED in ``fact.flags``, never changed or
dropped.

Fail loud. The only exclusions are depth-series rows whose
(study, site, core) key matches no row in CCN_cores.csv (no coordinates, no
labels, no date): they go to ``skip_report.jsonl`` with the reason from
``ALLOWED_SKIP_REASONS`` and, where one exists, a *suggested* case/spelling
match that is NOT applied. An unlisted exclusion reason raises.

Measurement types (``compilation_vocab.ATTRIBUTES``): dry_bulk_density ->
C1 dry_bulk_density; fraction_organic_matter -> C1 loi_organic_matter;
fraction_carbon -> C1 with ``subfield`` from the method row's ``carbon_type``
("organic carbon" -> organic_carbon, "total carbon" -> total_carbon, anything else,
including no matched method row -> carbon_unspecified); pb210_cic_accretion_rate -> G2
vertical_accretion (technique = 210Pb constant-initial-concentration model, from the
column name; the cores file has no method id, so method_status = method_id_null).
Standard labels (habitat, setting, salinity_zone, modification_class) come from
``compilation_label_maps.csv``, not from this file.

Accretion facts: value = ``pb210_cic_accretion_rate`` (centimeterPerYear per the structure
doc); ``se`` = ``pb210_cic_accretion_rate_se``, which the doc gives in a DIFFERENT unit
(millimetersPerYear). Nothing is converted: the fact is flagged ``se_units_differ`` and
``method.params`` records ``se_units``; max depth, r2 and notes go in ``method.params``
(one method row per core).

Derived-field rules (the raw value is always kept alongside in ``location``):
  * salinity_class / vegetation_class / tidal_position (= inundation_class):
    the core-level value; if the core has none, the site-level value from
    CCN_sites.csv ONLY when every site row for that (study, site) is non-null
    and identical (sites.csv repeats some site_ids, e.g. one per habitat).
    ``*_level`` records which one was used ("core" | "site").
  * vegetated_or_bare: ``unvegetated`` -> "bare";  emergent / forested /
    scrub shrub / seagrass / forested to shrub / forested to emergent ->
    "vegetated"; ``mudflat`` -> NULL (no stated vegetation status).
  * marsh_zone: tidal_position if habitat == "marsh" and tidal_position in
    {low, mid, high}; otherwise NULL.
  * modification_status: union of site-level and core-level CCN_impacts rows; rows whose
    impact_class is blank are dropped.
  * conversion_applied (fraction_carbon only): fraction_carbon_method if the
    study says carbon was "modeled" or the method contains "regression".

Usage:
    python extract.py --access-date YYYY-MM-DD --max-dry-bulk-density 3.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted

SOURCE_KEY = "smithsonian-coastal-carbon"
SOURCE_NAME = "Coastal Carbon Library"
SOURCE_VERSION = "1.7.0"
SOURCE_DOI = "10.25573/serc.21565671"  # from README.txt

ATTRIBUTES = ("dry_bulk_density", "fraction_organic_matter", "fraction_carbon")

ALLOWED_SKIP_REASONS = frozenset({"depthseries_core_not_in_cores"})

# in-situ water context columns on sample_dim that this compilation does not have
NO_WATER_CONTEXT: dict[str, Any] = {
    "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
    "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
}

ACCRETION_ATTR = "pb210_cic_accretion_rate"
ACCRETION_SE_COL = "pb210_cic_accretion_rate_se"
ACCRETION_TECHNIQUE = "210Pb constant initial concentration (CIC) model"
CARBON_SUBFIELD = {"organic carbon": "organic_carbon", "total carbon": "total_carbon"}

VEG_TO_VEGETATED_OR_BARE: dict[str, str | None] = {
    "unvegetated": "bare",
    "emergent": "vegetated",
    "forested": "vegetated",
    "scrub shrub": "vegetated",
    "seagrass": "vegetated",
    "forested to shrub": "vegetated",
    "forested to emergent": "vegetated",
    "mudflat": None,
}
MARSH_ZONES = frozenset({"low", "mid", "high"})

# Units the params keys below hard-code, asserted against the doc's own table.
EXPECTED_METHOD_UNITS = {
    "dry_bulk_density_temperature": "celsius",
    "dry_bulk_density_time": "hour",
    "dry_bulk_density_sample_volume": "centimeterCubed",
    "dry_bulk_density_sample_mass": "gram",
    "loss_on_ignition_temperature": "celsius",
    "loss_on_ignition_time": "hour",
    "loss_on_ignition_sample_volume": "centimeterCubed",
    "loss_on_ignition_sample_mass": "gram",
    "sediment_sieve_size": "millimeter",
}
_PARAM_KEYS: dict[str, dict[str, str]] = {
    # attribute -> {csv column: params key}
    "dry_bulk_density": {
        "dry_bulk_density_temperature": "temperature_celsius",
        "dry_bulk_density_time": "time_hour",
        "dry_bulk_density_sample_volume": "sample_volume_cm3",
        "dry_bulk_density_sample_mass": "sample_mass_gram",
        "dry_bulk_density_flag": "flag",
    },
    "fraction_organic_matter": {
        "loss_on_ignition_temperature": "temperature_celsius",
        "loss_on_ignition_time": "time_hour",
        "loss_on_ignition_sample_volume": "sample_volume_cm3",
        "loss_on_ignition_sample_mass": "sample_mass_gram",
        "loss_on_ignition_flag": "flag",
    },
    "fraction_carbon": {"carbon_profile_notes": "carbon_profile_notes"},
}
_COMMON_PARAM_KEYS = {
    "roots_flag": "roots_flag",
    "sediment_sieved_flag": "sediment_sieved_flag",
    "sediment_sieve_size": "sediment_sieve_size_millimeter",
    "compaction_flag": "compaction_flag",
}
_NUMERIC_PARAMS = {
    "dry_bulk_density_temperature",
    "dry_bulk_density_time",
    "dry_bulk_density_sample_volume",
    "dry_bulk_density_sample_mass",
    "loss_on_ignition_temperature",
    "loss_on_ignition_time",
    "loss_on_ignition_sample_volume",
    "loss_on_ignition_sample_mass",
    "sediment_sieve_size",
}


# ---------------------------------------------------------------- loading


def load_raw(raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Read every CSV as strings; only the literal "NA" / "" are missing."""
    out = {}
    for name in (
        "cores", "sites", "depthseries", "impacts", "methods", "species",
        "study_citations",
    ):
        out[name] = pd.read_csv(
            raw_dir / f"CCN_{name}.csv",
            dtype=str,
            na_values=["NA", ""],
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    version_text = (raw_dir / "version_information.txt").read_text(encoding="utf-8")
    first = re.search(r"## Synthesis Version (\S+)", version_text)
    if first is None or first.group(1) != SOURCE_VERSION:
        raise ContractError(
            f"newest version in version_information.txt is "
            f"{first.group(1) if first else None}, expected {SOURCE_VERSION}"
        )
    return out


def load_units(html_path: Path) -> dict[str, dict[str, str | None]]:
    """Parse the units column of CCN_database_structure.html's embedded JSON.

    Returns {"cores"|"depthseries"|"methods": {column: units-or-None}}.
    """
    text = html_path.read_text(encoding="utf-8")
    dec = json.JSONDecoder()
    found: dict[str, dict[str, str | None]] = {}
    for m in re.finditer(r'"data":\{"column name":\[', text):
        obj, _ = dec.raw_decode(text[m.start() + len('"data":'):])
        names = obj["column name"]
        if "core_notes" in names:
            table = "cores"
        elif "dry_bulk_density" in names and "depth_min" in names:
            table = "depthseries"
        elif "coring_method" in names:
            table = "methods"
        else:
            continue
        if table in found:
            raise ContractError(f"{table} documented twice in the structure html")
        found[table] = dict(zip(names, obj["units"]))
    if set(found) != {"cores", "depthseries", "methods"}:
        raise ContractError(f"structure html tables found: {sorted(found)}")
    return found


# ---------------------------------------------------------------- helpers


def _key(*cols: pd.Series) -> pd.Series:
    out = cols[0].fillna("")
    for c in cols[1:]:
        out = out + "|" + c.fillna("")
    return out


def _num(s: pd.Series, what: str) -> pd.Series:
    """Strings -> float64 with Python's correctly-rounded float().

    NOT pd.to_numeric: its fast parser is off by 1 ULP on 17-digit strings
    (found by the source-row round-trip check), which would silently alter
    values "carried exactly as reported".
    """

    def conv(v: Any) -> float:
        if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
            return float("nan")
        try:
            return float(v)
        except (ValueError, TypeError) as e:
            raise ContractError(f"{what}: non-numeric value {v!r}") from e

    return pd.Series([conv(v) for v in s], index=s.index, dtype="float64")


def _int(s: pd.Series, what: str) -> pd.Series:
    x = _num(s, what)
    if not (x.dropna() == x.dropna().round()).all():
        raise ContractError(f"{what}: non-integer value")
    return x.astype("Int64")


_RANGE = re.compile(r"\d+(\.\d+)?-\d+(\.\d+)?")


def _param_value(col: str, v: str) -> Any:
    """Numeric method parameters -> float; a reported range ("1-5") stays a string.

    Anything else non-numeric raises (the data has ranges in sample_mass/volume).
    """
    if col not in _NUMERIC_PARAMS:
        return v
    if _RANGE.fullmatch(v):
        return v
    try:
        return float(v)
    except ValueError as e:
        raise ContractError(f"methods.{col}: neither number nor range: {v!r}") from e


def _none(v: Any) -> Any:
    return None if v is pd.NA or (isinstance(v, float) and np.isnan(v)) else v


# ---------------------------------------------------------------- locations


def _build_location(
    raw: dict[str, pd.DataFrame], units: dict[str, dict[str, str | None]]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    cores = raw["cores"].copy()
    cores["location_key"] = _key(cores.study_id, cores.site_id, cores.core_id)
    if cores.location_key.duplicated().any():
        raise ContractError("CCN_cores: (study, site, core) not unique")
    n = len(cores)
    cores["site_key"] = _key(cores.study_id, cores.site_id)

    # --- site-level label fallback (only when unambiguous)
    sites = raw["sites"].copy()
    sites["site_key"] = _key(sites.study_id, sites.site_id)
    lab_cols = {
        "salinity_class": "salinity_class",
        "vegetation_class": "vegetation_class",
        "inundation_class": "tidal_position",
    }

    def agree(s: pd.Series) -> Any:
        return s.iloc[0] if s.notna().all() and s.nunique() == 1 else None

    site_lab = sites.groupby("site_key")[list(lab_cols)].agg(agree)
    site_lab.columns = [f"site_{c}" for c in site_lab.columns]
    cores = cores.merge(site_lab, how="left", left_on="site_key", right_index=True, validate="m:1")
    if len(cores) != n:
        raise ContractError("site-label join changed core count")
    report_levels: dict[str, dict[str, int]] = {}
    for raw_col, out_col in lab_cols.items():
        core_v, site_v = cores[raw_col], cores[f"site_{raw_col}"]
        cores[out_col] = core_v.where(core_v.notna(), site_v)
        cores[f"{out_col}_level"] = np.where(
            core_v.notna(), "core", np.where(site_v.notna(), "site", None)
        )
        cores[f"{out_col}_level"] = cores[f"{out_col}_level"].astype(object)
        report_levels[out_col] = {
            "core": int((cores[f"{out_col}_level"] == "core").sum()),
            "site": int((cores[f"{out_col}_level"] == "site").sum()),
        }
    unknown_veg = set(cores.vegetation_class.dropna()) - set(VEG_TO_VEGETATED_OR_BARE)
    if unknown_veg:
        raise ContractError(f"unmapped vegetation_class values: {sorted(unknown_veg)}")

    # --- impacts and species -> lists per core (assert no orphan-keys silently)
    imp = raw["impacts"].copy()
    imp = imp[imp.impact_class.notna()]  # blank impact_class: no information
    site_imp = imp[imp.core_id.isna()].groupby(_key(imp.study_id, imp.site_id)[imp.core_id.isna()]).impact_class.agg(set)
    core_imp = imp[imp.core_id.notna()].groupby(
        _key(imp.study_id, imp.site_id, imp.core_id)[imp.core_id.notna()]
    ).impact_class.agg(set)
    core_set = set(cores.location_key)
    site_set = set(cores.site_key)
    orphan_imp = (set(core_imp.index) - core_set) | (set(site_imp.index) - site_set)
    cores["modification_status"] = [
        sorted(site_imp.get(sk, set()) | core_imp.get(ck, set()))
        for sk, ck in zip(cores.site_key, cores.location_key)
    ]
    sp = raw["species"]
    sp_key = _key(sp.study_id, sp.site_id, sp.core_id)
    sp_by_core = sp.groupby(sp_key).species_code.agg(lambda s: sorted(set(s.dropna())))
    orphan_sp = set(sp_by_core.index) - core_set
    cores["dominant_species"] = [sp_by_core.get(ck, []) for ck in cores.location_key]

    # --- numerics + units
    cu = units["cores"]
    for col in ("position_accuracy", "elevation"):
        if cu.get(col) is None:
            raise ContractError(f"no documented units for CCN_cores.{col}")
    cores["latitude"] = _num(cores.latitude, "latitude")
    cores["longitude"] = _num(cores.longitude, "longitude")
    if not cores.latitude.dropna().between(-90, 90).all() or not cores.longitude.dropna().between(-180, 180).all():
        raise ContractError("latitude/longitude outside valid range")
    cores["position_accuracy"] = _num(cores.position_accuracy, "position_accuracy")
    cores["elevation"] = _num(cores.elevation, "elevation")
    cores["position_accuracy_units"] = np.where(cores.position_accuracy.notna(), cu["position_accuracy"], None)
    cores["elevation_units"] = np.where(cores.elevation.notna(), cu["elevation"], None)

    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": cores.location_key,
            "site_name": cores.site_id,
            "source_site_id": cores.site_id,
            "source_core_id": cores.core_id,
            "latitude": cores.latitude,
            "longitude": cores.longitude,
            "position_accuracy": cores.position_accuracy,
            "position_accuracy_units": cores.position_accuracy_units,
            "position_method": cores.position_method,
            "country": cores.country,
            "admin_division": cores.admin_division,
            "water_body": None,  # not in this compilation
            "habitat_type": cores.habitat,
            "geomorphic_setting": None,  # not in this compilation
            "bioregion": None,
            "marine_ecoregion": None,
            "salinity_class": cores.salinity_class,
            "salinity_class_level": cores.salinity_class_level,
            "vegetation_class": cores.vegetation_class,
            "vegetation_class_level": cores.vegetation_class_level,
            "tidal_position": cores.tidal_position,
            "tidal_position_level": cores.tidal_position_level,
            "modification_status": cores.modification_status,
            "dominant_species": cores.dominant_species,
            "elevation": cores.elevation,
            "elevation_units": cores.elevation_units,
            "elevation_datum": cores.elevation_datum,
            "elevation_method": cores.elevation_method,
            "water_depth_m": np.nan,  # not in this compilation
            "distance_to_shore_km": np.nan,
        }
    )
    report = {
        "label_levels": report_levels,
        "impacts_keys_not_in_cores": len(orphan_imp),
        "species_core_keys_not_in_cores": len(orphan_sp),
    }
    # carry what sample needs (year/month + derived sub-habitat)
    ctx = pd.DataFrame(
        {
            "location_key": cores.location_key,
            "sampling_year": _int(cores.year, "year"),
            "sampling_month": _int(cores.month, "month"),
            "habitat": cores.habitat,
            "tidal_position": cores.tidal_position,
            "vegetation_class": cores.vegetation_class,
        }
    )
    if not ctx.sampling_month.dropna().between(1, 12).all():
        raise ContractError("month outside 1..12")
    return loc, ctx, report


# ---------------------------------------------------------------- methods


def _method_rows(
    methods: pd.DataFrame, facts_key: pd.DataFrame
) -> pd.DataFrame:
    """One method row per distinct (study, method_id, attribute) used by a fact."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rec in methods.drop_duplicates().to_dict("records"):
        by_key.setdefault((rec["study_id"], rec["method_id"]), []).append(
            {k: _none(v) for k, v in rec.items()}
        )
    out = []
    for study, mid, attr in facts_key.itertuples(index=False, name=None):
        mid_n = None if mid == "" else mid
        row: dict[str, Any] = {
            "source_key": SOURCE_KEY,
            "method_key": f"{study}|{mid}|{attr}",
            "source_method_id": mid_n,
            "attribute": attr,
            "technique": None, "coring_method": None, "carbon_type": None,
            "carbon_measured_or_modeled": None, "fraction_carbon_method": None,
            "carbonates_removed": None, "carbonate_removal_method": None,
            "conversion_applied": None, "in_situ_ex_situ": None, "light_dark": None,
            "incubation_type": None, "experimental_status": None,
            "params": None,
        }
        cands = by_key.get((study, mid), []) if mid_n is not None else []
        if mid_n is None:
            row["method_status"] = "method_id_null"
        elif not cands:
            row["method_status"] = "method_id_unmatched"
        elif len(cands) > 1:
            row["method_status"] = "ambiguous_duplicate_method_id"
            row["params"] = json.dumps({"candidates": cands}, sort_keys=True)
        else:
            row["method_status"] = "matched"
            m = cands[0]
            row["coring_method"] = m["coring_method"]
            params: dict[str, Any] = {}
            for col, key in _COMMON_PARAM_KEYS.items():
                if m[col] is not None:
                    params[key] = _param_value(col, m[col])
            for col, key in _PARAM_KEYS[attr].items():
                if m[col] is not None:
                    params[key] = _param_value(col, m[col])
            row["params"] = json.dumps(params, sort_keys=True)
            if attr == "dry_bulk_density":
                row["technique"] = m["dry_bulk_density_flag"]
            elif attr == "fraction_organic_matter":
                row["technique"] = "loss on ignition"
            else:
                row["technique"] = m["fraction_carbon_method"]
                row["carbon_type"] = m["fraction_carbon_type"]
                row["carbon_measured_or_modeled"] = m["carbon_measured_or_modeled"]
                row["fraction_carbon_method"] = m["fraction_carbon_method"]
                row["carbonates_removed"] = m["carbonates_removed"]
                row["carbonate_removal_method"] = m["carbonate_removal_method"]
                fcm = m["fraction_carbon_method"]
                if m["carbon_measured_or_modeled"] == "modeled" or (
                    fcm is not None and "regression" in fcm
                ):
                    row["conversion_applied"] = fcm
        out.append(row)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- accretion (G2)


def _accretion(
    cores_raw: pd.DataFrame, ctx: pd.DataFrame, units: dict[str, dict[str, str | None]]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Core-level 210Pb CIC accretion facts: (sample, method, fact) rows.

    One fact per core with a rate. The rate is in the doc's centimeterPerYear, its SE in
    the doc's (different) millimetersPerYear; both are carried as reported, the SE fact
    is flagged ``se_units_differ``.
    """
    cu = units["cores"]
    rate_units, se_units = cu.get(ACCRETION_ATTR), cu.get(ACCRETION_SE_COL)
    if rate_units is None or se_units is None:
        raise ContractError("no documented units for the accretion columns")
    cores = cores_raw.reset_index(drop=True).copy()
    cores["row_idx"] = np.arange(len(cores))
    cores["location_key"] = _key(cores.study_id, cores.site_id, cores.core_id)
    if (cores[ACCRETION_SE_COL].notna() & cores[ACCRETION_ATTR].isna()).any():
        raise ContractError("accretion SE without a rate")
    keep = ["row_idx", "location_key", "study_id", ACCRETION_ATTR, ACCRETION_SE_COL,
            "pb210_cic_max_depth", "pb210_cic_r2", "pb210_cic_notes"]
    sub = cores.loc[cores[ACCRETION_ATTR].notna(), keep].merge(
        ctx, on="location_key", how="left", validate="1:1"
    )  # ctx carries the label-fallback vegetation_class / tidal_position
    if len(sub) != int(cores[ACCRETION_ATTR].notna().sum()):
        raise ContractError("accretion/context join changed row count")
    sample_key = sub.location_key + "|core_level"
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": sample_key,
            "source_sample_id": None,
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": "core-level value; no depth interval",
            "sampling_year": sub.sampling_year,
            "sampling_month": sub.sampling_month,
            "vegetated_or_bare": sub.vegetation_class.map(VEG_TO_VEGETATED_OR_BARE),
            "marsh_zone": np.where(
                (sub.habitat == "marsh") & sub.tidal_position.isin(MARSH_ZONES), sub.tidal_position, None
            ),
            "sediment_type": None,
            "photosynthetic_sediment": None,
            "taxon": None,
            "taxon_rank": None,
            **NO_WATER_CONTEXT,
        }
    )
    method_key = sub.location_key + "|" + ACCRETION_ATTR
    params = []
    for md, r2, nt in zip(sub.pb210_cic_max_depth, sub.pb210_cic_r2, sub.pb210_cic_notes):
        p: dict[str, Any] = {"se_units": se_units}
        if _none(md) is not None:
            p["max_depth_cm"] = float(md)  # doc units: centimeter
        if _none(r2) is not None:
            p["r2"] = float(r2)
        if _none(nt) is not None:
            p["notes"] = nt
        params.append(json.dumps(p, sort_keys=True))
    if cu.get("pb210_cic_max_depth") != "centimeter":
        raise ContractError("pb210_cic_max_depth is not documented in centimeter")
    method = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "method_key": method_key,
            "source_method_id": None,
            "attribute": ACCRETION_ATTR,
            "method_status": "method_id_null",  # cores file has no method id
            "technique": ACCRETION_TECHNIQUE,
            "coring_method": None, "carbon_type": None, "carbon_measured_or_modeled": None,
            "fraction_carbon_method": None, "carbonates_removed": None,
            "carbonate_removal_method": None, "conversion_applied": None,
            "in_situ_ex_situ": None, "light_dark": None,
            "incubation_type": None, "experimental_status": None,
            "params": params,
        }
    )
    val = _num(sub[ACCRETION_ATTR], ACCRETION_ATTR)
    se = _num(sub[ACCRETION_SE_COL], ACCRETION_SE_COL)
    flags = []
    for v, e in zip(val, se):
        f = []
        if v < 0:
            f.append("negative_value")
        if not np.isnan(e):
            f.append("se_units_differ")
        flags.append(f)
    fact = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "fact_key": sample_key + "|" + ACCRETION_ATTR,
            "reference_key": sub.study_id,
            "location_key": sub.location_key,
            "sample_key": sample_key,
            "method_key": method_key,
            "attribute": ACCRETION_ATTR,
            "measurement_type": ATTRIBUTE_DECL[ACCRETION_ATTR].measurement_type,
            "subfield": "vertical_accretion",
            "value": val,
            "units_raw": rate_units,
            "sd": np.nan,
            "se": se,
            "n": pd.array([pd.NA] * len(sub), dtype="Int64"),
            "flags": flags,
            "source_row_ref": "CCN_cores.csv#data_row=" + sub.row_idx.astype(str) + ":" + ACCRETION_ATTR,
        }
    )
    return sample, method, fact


# ---------------------------------------------------------------- main build


def build_tables(
    raw: dict[str, pd.DataFrame],
    units: dict[str, dict[str, str | None]],
    access_date: str,
    max_dry_bulk_density: float,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]], dict[str, Any]]:
    for col, want in EXPECTED_METHOD_UNITS.items():
        if units["methods"].get(col) != want:
            raise ContractError(f"doc units for methods.{col} = {units['methods'].get(col)!r}, expected {want!r}")
    for a in ATTRIBUTES:
        if units["depthseries"].get(a) is None:
            raise ContractError(f"no documented units for depthseries.{a}")

    loc, ctx, loc_report = _build_location(raw, units)

    ds = raw["depthseries"].copy()
    ds["row_idx"] = np.arange(len(ds))
    ds["location_key"] = _key(ds.study_id, ds.site_id, ds.core_id)
    dup_mask = ds.drop(columns=["row_idx"]).duplicated(keep="first")  # later copies
    in_cores = ds.location_key.isin(set(loc.location_key))

    # ---- exclusions (orphans), with suggestions that are NOT applied
    skips: list[dict[str, Any]] = []
    core_lc: dict[tuple[str, str], list[str]] = {}
    for st, co, lk in zip(raw["cores"].study_id, raw["cores"].core_id, loc.location_key):
        core_lc.setdefault((st, co.lower()), []).append(lk)
    orph = ds[~in_cores]
    for r in orph.itertuples(index=False):
        n_meas = sum(pd.notna(getattr(r, a)) for a in ATTRIBUTES)
        sugg = core_lc.get((r.study_id, str(r.core_id).lower()), [])
        skips.append(
            {
                "depthseries_row": int(r.row_idx),
                "location_key": r.location_key,
                "reason": "depthseries_core_not_in_cores",
                "n_measurements": int(n_meas),
                "suggested_location_keys_not_applied": sugg,
            }
        )
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons: {bad}")
    inc = ds[in_cores].copy()

    # ---- long form: one row per non-null measurement cell
    pieces = []
    for a in ATTRIBUTES:
        p = inc[inc[a].notna()]
        pieces.append(
            pd.DataFrame(
                {
                    "row_idx": p.row_idx, "attribute": a, "value": _num(p[a], a),
                    "location_key": p.location_key, "study_id": p.study_id,
                    "method_id": p.method_id, "dup": dup_mask[p.index],
                    "depth_min": p.depth_min, "depth_max": p.depth_max,
                }
            )
        )
    long = pd.concat(pieces, ignore_index=True)
    expected_facts = int(sum(inc[a].notna().sum() for a in ATTRIBUTES))
    if len(long) != expected_facts:
        raise ContractError(f"fact count {len(long)} != non-null cells {expected_facts}")
    if len(long) == 0:
        raise ContractError("no measurements")

    # ---- sample dim (only rows with >=1 fact)
    srows = inc[inc.row_idx.isin(set(long.row_idx))].copy()
    srows = srows.merge(ctx, on="location_key", how="left", validate="m:1")
    if len(srows) != srows.row_idx.nunique() or srows.sampling_year.shape[0] != len(set(long.row_idx)):
        raise ContractError("sample/context join changed row count")
    srows["sample_key"] = srows.location_key + "|row" + srows.row_idx.astype(str)
    srows["vegetated_or_bare"] = srows.vegetation_class.map(VEG_TO_VEGETATED_OR_BARE)
    srows["marsh_zone"] = np.where(
        (srows.habitat == "marsh") & srows.tidal_position.isin(MARSH_ZONES), srows.tidal_position, None
    )
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": srows.sample_key,
            "source_sample_id": srows.sample_id,
            "depth_min_cm": _num(srows.depth_min, "depth_min"),
            "depth_max_cm": _num(srows.depth_max, "depth_max"),
            "representative_depth_min_cm": _num(srows.representative_depth_min, "representative_depth_min"),
            "representative_depth_max_cm": _num(srows.representative_depth_max, "representative_depth_max"),
            "depth_notes": srows.depth_interval_notes,
            "sampling_year": srows.sampling_year,
            "sampling_month": srows.sampling_month,
            "vegetated_or_bare": srows.vegetated_or_bare,
            "marsh_zone": srows.marsh_zone,
            "sediment_type": None,  # not in this compilation
            "photosynthetic_sediment": None,
            "taxon": None,
            "taxon_rank": None,
            **NO_WATER_CONTEXT,
        }
    )
    if units["depthseries"]["depth_min"] != "centimeter" or units["depthseries"]["depth_max"] != "centimeter":
        raise ContractError("depth units are not centimeter; column names would lie")
    skey_by_row = dict(zip(srows.row_idx, srows.sample_key))

    # ---- methods
    long["method_id"] = long.method_id.fillna("")
    mkeys = long[["study_id", "method_id", "attribute"]].drop_duplicates()
    method = _method_rows(raw["methods"], mkeys)
    method_status = dict(zip(method.method_key, method.method_status))
    carbon_type = dict(zip(method.method_key, method.carbon_type))

    # ---- fact
    long["sample_key"] = long.row_idx.map(skey_by_row)
    long["method_key"] = long.study_id + "|" + long.method_id + "|" + long.attribute
    d_min = _num(long.depth_min, "depth_min")
    d_max = _num(long.depth_max, "depth_max")
    unit_of = {a: units["depthseries"][a] for a in ATTRIBUTES}
    flags: list[list[str]] = []
    subfields: list[str | None] = []
    for att, val, dup, lo, hi, mk in zip(
        long.attribute, long.value, long.dup, d_min, d_max, long.method_key
    ):
        f: list[str] = []
        if val < 0:
            f.append("negative_value")
        if att != "dry_bulk_density" and val > 1:
            f.append("fraction_gt_1")
        if att == "dry_bulk_density" and val > max_dry_bulk_density:
            f.append("dbd_gt_max")
        if np.isnan(lo) or np.isnan(hi):
            f.append("depth_missing")
        elif hi < lo:
            f.append("depth_inverted")
        if dup:
            f.append("duplicate_source_row")
        if att == "fraction_carbon" and (
            method_status[mk] != "matched" or carbon_type[mk] is None
        ):
            f.append("carbon_type_unknown")
        flags.append(f)
        if att == "dry_bulk_density":
            subfields.append("dry_bulk_density")
        elif att == "fraction_organic_matter":
            subfields.append("loi_organic_matter")
        else:  # fraction_carbon: by the method row's carbon_type; unknown stays unspecified
            subfields.append(CARBON_SUBFIELD.get(carbon_type[mk], "carbon_unspecified"))
    fact = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "fact_key": long.sample_key + "|" + long.attribute,
            "reference_key": long.study_id,
            "location_key": long.location_key,
            "sample_key": long.sample_key,
            "method_key": long.method_key,
            "attribute": long.attribute,
            "measurement_type": long.attribute.map(lambda a: ATTRIBUTE_DECL[a].measurement_type),
            "subfield": subfields,
            "value": long.value,
            "units_raw": long.attribute.map(unit_of),
            "sd": np.nan, "se": np.nan, "n": pd.array([pd.NA] * len(long), dtype="Int64"),
            "flags": flags,
            "source_row_ref": "CCN_depthseries.csv#data_row=" + long.row_idx.astype(str) + ":" + long.attribute,
        }
    )

    # ---- accretion (G2) facts, core level
    fact_method_status = fact.method_key.map(method_status).value_counts().to_dict()
    n_depth_facts = len(fact)
    acc_sample, acc_method, acc_fact = _accretion(raw["cores"], ctx, units)
    sample = pd.concat([sample, acc_sample], ignore_index=True)
    method = pd.concat([method, acc_method], ignore_index=True)
    fact = pd.concat([fact, acc_fact], ignore_index=True)
    flags = [list(f) for f in fact["flags"]]

    # ---- reference / citation (only studies that have facts)
    studies = sorted(set(fact.reference_key))
    cit = raw["study_citations"]
    cit = cit[cit.study_id.isin(studies)]
    n_cit_exact_dups = int(cit.duplicated().sum())
    cit = cit.drop_duplicates().copy()  # exact duplicate rows only; conflicts still raise below
    if set(cit.study_id) != set(studies):
        raise ContractError(f"studies with facts but no citation: {sorted(set(studies) - set(cit.study_id))[:5]}")
    cit["citation_key"] = cit.study_id + "|" + cit.bibliography_id.fillna("")
    if cit.bibliography_id.isna().any():
        raise ContractError("citation row without bibliography_id")
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": cit.citation_key,
            "reference_key": cit.study_id,
            "bibliography_id": cit.bibliography_id,
            "publication_type": cit.publication_type,
            "doi": cit.doi,
            "title": cit.title,
            "authors": cit.author,
            "year": _int(cit.year, "citation year"),
            "url": cit.url,
            "openalex_id": None,
            "doi_resolved": None,
            "doi_resolution_source": None,
        }
    )
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": studies})

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": SOURCE_VERSION,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": None,
                "notes": "Tidal-wetland soil carbon profiles; facts = dry_bulk_density, "
                "fraction_organic_matter, fraction_carbon (C1) and core-level 210Pb CIC accretion (G2); "
                "other radiometric/dating columns out of scope.",
            }
        ]
    )

    # location: keep all cores (even ones with no facts); the builder permits it
    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": empty_table("study_screening"),
        "location": loc.reset_index(drop=True), "sample": sample, "method": method, "fact": fact,
    }
    flag_counts: dict[str, int] = {}
    for f in flags:
        for x in f:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().to_dict(),
        "samples": len(sample),
        "locations": len(loc),
        "methods": len(method),
        "method_status": method.method_status.value_counts().to_dict(),
        "fact_method_status": fact_method_status,
        "accretion_facts": len(acc_fact),
        "flag_counts": flag_counts,
        "citation_exact_duplicate_rows_collapsed": n_cit_exact_dups,
        "depthseries_rows": len(ds),
        "depthseries_rows_excluded": len(skips),
        "facts_excluded": int(sum(s["n_measurements"] for s in skips)),
        "depthseries_rows_without_measurements": int((inc[list(ATTRIBUTES)].isna().all(axis=1)).sum()),
        **loc_report,
    }
    # accounting: every non-null measurement cell is either a fact or excluded
    total_cells = int(sum(ds[a].notna().sum() for a in ATTRIBUTES))
    if n_depth_facts + report["facts_excluded"] != total_cells or len(fact) != n_depth_facts + len(acc_fact):
        raise ContractError("fact + excluded != total non-null measurement cells")
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the CCN files were downloaded, YYYY-MM-DD")
    ap.add_argument("--max-dry-bulk-density", type=float, required=True, help="g/cm3; above this a fact is flagged dbd_gt_max")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")

    raw = load_raw(args.raw_dir)
    units = load_units(args.raw_dir / "CCN_database_structure.html")
    tables, skips, report = build_tables(raw, units, args.access_date, args.max_dry_bulk_density)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
