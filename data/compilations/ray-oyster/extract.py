#!/usr/bin/env python3
"""Ray and Fulweiler oyster meta-analysis data -> compilation-contract tables.

Ray, N.E., Fulweiler, R.W. (2020): Meta-analysis of oyster impacts on coastal biogeochemistry. figshare,
doi:10.6084/m9.figshare.12488753.v1 (paper: Nature Sustainability 4, 261-269, 2021, doi:10.1038/s41893-020-00644-9). Raw file (this
directory): ``oyster_meta - excel for figshare.xlsx`` with sheets ``Overview`` (empty), ``sediment fluxes`` and ``oyster fluxes``.

Grain: one fact per (row, solute, arm) with a mean. ``sediment fluxes`` has paired columns for a control and an oyster arm
(``Mean X Ctrl``, ``SD X Ctrl``, ``n X Ctrl`` and the ``Oys`` set) for 10 solutes. ``oyster fluxes`` has one set per solute (the
oysters themselves, no control) for 8. The fact value is the mean, with ``sd`` and ``n`` from the paired columns (every mean has both,
asserted, and an sd or n without a mean raises). Values are carried as reported.

Units: the file has none. The paper's Methods say all sediment fluxes were converted to umol m-2 h-1 and all oyster fluxes to umol per
individual and hour (per-gram rates with 2.93 g per individual). The paper's Table 1 prints N2O and CH4 oyster fluxes in nmol, which
are the file's numbers times 1000 (the Saccostrea glomerata N2O of 0.067662 is 67.66 there), so the file is in umol too.
  sediment: umol m-2 h-1 for every solute (attributes ray_sediment_<solute>)
  oyster:   umol ind-1 h-1 for every solute (attributes ray_oyster_<solute>)
Solute -> type/subfield: N2 C4 denitrification; NH4 C3 ammonium_flux; PO4 (``P``) C3 phosphate_flux; Si C3 silicate_flux; NOx C3
nitrate_nitrite_flux; NO3 C3 nitrate_flux and NO2 C3 nitrite_flux (extensions); N2O, CO2, CH4 C8. ``n`` is an integer. Where the authors
give 10.5 (the median of a range), n is NULL and the number is in ``method.params.n_reported``.

Control or oyster: ``method.experimental_status`` is the raw label ``control`` or ``oyster`` (sediment sheet) and ``oyster (the oysters'
own flux; no control)`` (oyster sheet). Both are loaded, and filtering is downstream. The N2 method column (n2_ar, n2/ar, IPT,
acetylene) is the ``technique`` of N2 facts only (other solutes are method_id_null). The one row whose note says the denitrification
is a potential rate (acetylene block; NH4 by ammonification assays) gets ``potential_rate`` on its N2 and NH4 facts. Every ``notes``
text is carried verbatim in ``method.params.note`` on that row's facts.

The authors converted every SE to an SD and every per-gram rate to per individual, but the file does not say which rows, so there is no
per-fact ``sd_converted_from_se`` or ``converted_by_source`` flag (it would be wrong for rows reported as SD). ``method.params.sd_note``
says it for every fact. The 3 rows named ``... - IPT`` hold only the isotope-pairing N2 pair of the study named before them and have no
species, coordinates or habitat. They are not merged into that study.

Locations: ``Coordinates`` is one string ``lat, lon`` (parsed strictly; blank gives ``coords_missing``). habitat_type is ``Habitat`` raw
on the sediment sheet (Reef maps to shellfish reef / bed, Aquaculture and Mesocosm to blank) and NULL on the oyster sheet. location_key
= lat | lon | habitat (a row without coordinates: ``nocoords|<abbreviated_name>|<habitat>``). Samples: one per data row, taxon is
``Species`` (rank ``species``). ``Year`` is the publication year, so there is no sampling_year.

References: ``reference_key`` = ``ref:<abbreviated_name>`` (the file's study label, whitespace-normalised). Different labels are
different references even for one paper, and nothing is merged. A label occurring twice must have identical title, authors and year
(asserted). The citation holds title, authors and year (the journal has no contract column and is recoverable through
``source_row_ref``).

Flags (nothing is changed or dropped): coords_missing, zero_value, potential_rate, negative_sd (the one declared negative SD, Erler 2017
nitrate, kept as reported), duplicate_source_row (rows identical in all columns of their sheet). Negative fluxes are real and not flagged.

Provenance: ``source_row_ref`` = ``<file>[<sheet>]#data_row=<0-based row>:<attribute>:<arm>`` (``arm`` is ``ctrl`` or ``oys`` on the
sediment sheet, ``oyster`` on the oyster sheet).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "ray-oyster"
SOURCE_NAME = "Ray and Fulweiler oyster meta-analysis (sediment and oyster fluxes)"
SOURCE_DOI = "10.6084/m9.figshare.12488753.v1"
SOURCE_VERSION = "v1 (2020-10-08)"  # figshare record, read by the integration plan; the file has no version
SOURCE_LICENSE = "CC BY 4.0"  # figshare record, as above
DATA_FILE = "oyster_meta - excel for figshare.xlsx"
CITATION = (
    "Ray, N.E., Fulweiler, R.W. (2020): Meta-analysis of oyster impacts on coastal biogeochemistry [Dataset]. figshare, "
    "https://doi.org/10.6084/m9.figshare.12488753.v1 (paper: Nature Sustainability 4, 261-269, 2021)"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()  # every mean is a fact
check_skip_reasons(ALLOWED_SKIP_REASONS)

SED_SHEET, OY_SHEET, OVERVIEW = "sediment fluxes", "oyster fluxes", "Overview"
SED_SOLUTES = {"n2": "n2", "nh4": "nh4", "n2o": "n2o", "co2": "co2", "p": "po4", "ch4": "ch4", "si": "si",
               "nox": "nox", "no3": "no3", "no2": "no2"}  # header token -> attribute solute key
OY_SOLUTES = ("n2", "n2o", "nh4", "po4", "ch4", "nox", "no3", "no2")
SED_META = ["abbreviated_name", "Publication title", "Authors", "Journal", "Year", "Species", "Coordinates", "Habitat",
            "n2_method"]
OY_META = ["abbreviated_name", "Publication title", "Authors", "Journal", "Year", "Species", "Location",
           "Denitrification Method"]
SED_UNITS, OY_UNITS = "µmol m−2 h−1", "µmol ind−1 h−1"
STATUS = {"ctrl": "control", "oys": "oyster", "oyster": "oyster (the oysters' own flux; no control)"}
HABITATS = frozenset({"Reef", "Aquaculture", "Mesocosm"})
SED_N_ROWS, OY_N_ROWS = 37, 17
# the one row whose note says the rate is a potential (acetylene block, ammonification assays): solutes it covers
POTENTIAL_NOTE_START = "In this study, all data was reported in large figures."
POTENTIAL_SOLUTES = frozenset({"n2", "nh4"})
SD_NOTE = ("the authors converted every reported SE to an SD and per-gram oyster rates to per individual; the file "
           "does not say which rows")
# the one negative SD in the file (Erler et al. 2017, nitrate): kept as reported with the flag ``negative_sd``;
# any other negative SD raises, and this one must be present
DECLARED_NEGATIVE_SD = frozenset({(OY_SHEET, 15, "no3")})
_COORD = re.compile(r"(-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)")


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip() == "")


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def _number(v: object, what: str) -> float:
    if not _is_num(v) or not math.isfinite(float(v)):  # type: ignore[arg-type]
        raise ContractError(f"{what}: non-numeric value {v!r}")
    return float(v)  # type: ignore[arg-type]


def _text(v: object, what: str) -> str | None:
    if _blank(v):
        return None
    if not isinstance(v, str):
        raise ContractError(f"{what}: expected text, got {v!r}")
    t = norm_ws(v)
    if t.upper() in {"NA", "N/A", "NAN", "NULL", "NONE"}:
        raise ContractError(f"{what}: literal missing-value string {v!r}")
    return t


# ---------------------------------------------------------------- raw reading


def read_raw(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    if xl.sheet_names != [OVERVIEW, SED_SHEET, OY_SHEET]:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    if xl.parse(OVERVIEW, header=None).shape != (0, 0):
        raise ContractError(f"{OVERVIEW} is no longer empty")
    kw = dict(dtype=object, keep_default_na=False, na_values=[])
    return xl.parse(SED_SHEET, **kw), xl.parse(OY_SHEET, **kw)  # type: ignore[arg-type]


def triples(df: pd.DataFrame, first: int, last: int, sheet: str) -> list[dict]:
    """The (mean, sd, n) column triples between column indexes ``first`` and ``last`` (exclusive)."""
    cols = list(df.columns)
    if (last - first) % 3:
        raise ContractError(f"{sheet}: {last - first} value columns is not a multiple of 3")
    out = []
    for k in range(first, last, 3):
        m, s, n = cols[k:k + 3]
        if sheet == SED_SHEET:
            tm, ts, tn = m.lower().split(), s.lower().split(), n.lower().split()
            if not (len(tm) == 3 and tm[0] == "mean" and ts == ["sd", tm[1], tm[2]] and tn == ["n", tm[1], tm[2]]):
                raise ContractError(f"{sheet}: columns {m!r}, {s!r}, {n!r} are not a mean / SD / n triple")
            if tm[1] not in SED_SOLUTES or tm[2] not in ("ctrl", "oys"):
                raise ContractError(f"{sheet}: unknown solute or arm in {m!r}")
            out.append({"solute": SED_SOLUTES[tm[1]], "arm": tm[2], "cols": (m, s, n)})
        else:
            sol = m.lower().removesuffix("_flux_mean")
            if not (m.lower() == f"{sol}_flux_mean" and s.lower() == f"{sol}_flux_sd" and n.lower() == f"{sol}_flux_n"):
                raise ContractError(f"{sheet}: columns {m!r}, {s!r}, {n!r} are not a mean / SD / n triple")
            if sol not in OY_SOLUTES:
                raise ContractError(f"{sheet}: unknown solute in {m!r}")
            out.append({"solute": sol, "arm": "oyster", "cols": (m, s, n)})
    return out


# ---------------------------------------------------------------- main build


def build_tables(sed: pd.DataFrame, oy: pd.DataFrame, access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(sed.columns[:9]) != SED_META or sed.columns[-1] != "notes":
        raise ContractError(f"{SED_SHEET}: unexpected columns {list(sed.columns)}")
    if list(oy.columns[:8]) != OY_META:
        raise ContractError(f"{OY_SHEET}: unexpected columns {list(oy.columns)}")
    if len(sed) != SED_N_ROWS or len(oy) != OY_N_ROWS:
        raise ContractError(f"row counts {len(sed)} / {len(oy)} differ from {SED_N_ROWS} / {OY_N_ROWS}")
    sheets = [
        {"df": sed, "name": SED_SHEET, "short": "sed", "trip": triples(sed, 9, len(sed.columns) - 1, SED_SHEET),
         "coord": "Coordinates", "habitat": "Habitat", "method": "n2_method", "units": SED_UNITS, "prefix": "ray_sediment_"},
        {"df": oy, "name": OY_SHEET, "short": "oy", "trip": triples(oy, 8, len(oy.columns), OY_SHEET),
         "coord": "Location", "habitat": None, "method": "Denitrification Method", "units": OY_UNITS, "prefix": "ray_oyster_"},
    ]
    facts, samples, locs, methods, cits = [], [], {}, {}, {}
    n_mean_cells = 0
    n_nonint_n = 0
    seen_neg: set[tuple[str, int, str]] = set()
    row_info = []
    for sh in sheets:
        df = sh["df"]
        dup = df.astype(str).duplicated(keep=False).to_numpy()
        for i in range(len(df)):
            r = df.iloc[i]
            name = _text(r["abbreviated_name"], f"{sh['name']}.abbreviated_name")
            if name is None:
                raise ContractError(f"{sh['name']} row {i}: blank abbreviated_name")
            title, authors = _text(r["Publication title"], "title"), _text(r["Authors"], "authors")
            _text(r["Journal"], "journal")  # read and validated; no contract column for it
            year = None if _blank(r["Year"]) else int(_number(r["Year"], "Year"))
            species = _text(r["Species"], "species")
            coord = _text(r[sh["coord"]], "coordinates")
            habitat = _text(r[sh["habitat"]], "habitat") if sh["habitat"] else None
            if habitat is not None and habitat not in HABITATS:
                raise ContractError(f"{sh['name']} row {i}: unknown Habitat {habitat!r}")
            tech = _text(r[sh["method"]], "n2 method")
            note = _text(r["notes"], "notes") if sh["short"] == "sed" else None
            if coord is None:
                lat = lon = None
            else:
                m = _COORD.fullmatch(coord)
                if not m:
                    raise ContractError(f"{sh['name']} row {i}: coordinates {coord!r} are not 'lat, lon'")
                lat, lon = float(m.group(1)), float(m.group(2))
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ContractError(f"{sh['name']} row {i}: coordinates outside the valid range")
            lk = (f"{lat!r}|{lon!r}|{habitat or ''}" if coord else f"nocoords|{name}|{habitat or ''}")
            locs.setdefault(lk, {"lat": lat, "lon": lon, "habitat": habitat})
            ck = f"ref:{name}"
            rec = (title, authors, year)
            if ck in cits and cits[ck] != rec:
                raise ContractError(f"{name!r} occurs twice with different title, authors or year")
            cits.setdefault(ck, rec)
            skey = f"{sh['short']}:row{i}"
            row_n_facts = 0
            for t in sh["trip"]:
                mc, sc, nc = t["cols"]
                mv, sv, nv = r[mc], r[sc], r[nc]
                if _blank(mv):
                    if not (_blank(sv) and _blank(nv)):
                        raise ContractError(f"{sh['name']} row {i}: SD or n without a mean in {mc!r}")
                    continue
                n_mean_cells += 1
                row_n_facts += 1
                if _blank(sv) or _blank(nv):
                    raise ContractError(f"{sh['name']} row {i}: mean without SD and n in {mc!r}")
                value, sd, nn = _number(mv, mc), _number(sv, sc), _number(nv, nc)
                neg_sd = sd < 0
                if neg_sd:
                    if (sh["name"], i, t["solute"]) not in DECLARED_NEGATIVE_SD:
                        raise ContractError(f"{sh['name']} row {i}: undeclared negative SD in {sc!r}")
                    seen_neg.add((sh["name"], i, t["solute"]))
                if nn <= 0:
                    raise ContractError(f"{sh['name']} row {i}: non-positive n in {nc!r}")
                n_int = int(nn) if nn == int(nn) else None
                if n_int is None:
                    n_nonint_n += 1
                solute = t["solute"]
                attr = sh["prefix"] + solute
                decl = ATTRIBUTE_DECL[attr]
                if sh["units"] not in decl.units:
                    raise ContractError(f"{attr}: unit {sh['units']!r} not declared")
                status = STATUS[t["arm"]]
                technique = tech if solute == "n2" else None
                potential = note is not None and note.startswith(POTENTIAL_NOTE_START) and solute in POTENTIAL_SOLUTES
                flags = []
                if coord is None:
                    flags.append("coords_missing")
                if value == 0:
                    flags.append("zero_value")
                if potential:
                    flags.append("potential_rate")
                if neg_sd:
                    flags.append("negative_sd")
                if dup[i]:
                    flags.append("duplicate_source_row")
                extra = f"row{i}" if (note is not None or n_int is None) else ""
                mkey = f"{sh['short']}|{attr}|{status}|{technique or ''}|{extra}"
                if mkey not in methods:
                    params = {"sheet": sh["name"], "units_source": "paper (Methods); the file has none",
                              "sd_note": SD_NOTE, "mean_column": mc, "sd_column": sc, "n_column": nc,
                              "note": note}
                    if n_int is None:
                        params["n_reported"] = nn
                    if potential:
                        params["potential_rate_note"] = "the note says this rate is a potential (acetylene block / assays)"
                    methods[mkey] = {
                        "source_key": SOURCE_KEY, "method_key": mkey, "source_method_id": None, "attribute": attr,
                        "method_status": "matched" if technique else "method_id_null", "technique": technique,
                        "coring_method": None, "carbon_type": None, "carbon_measured_or_modeled": None,
                        "fraction_carbon_method": None, "carbonates_removed": None, "carbonate_removal_method": None,
                        "conversion_applied": None, "in_situ_ex_situ": None, "light_dark": None, "incubation_type": None,
                        "experimental_status": status, "params": json.dumps(params, sort_keys=True),
                    }
                facts.append(
                    {
                        "source_key": SOURCE_KEY,
                        "fact_key": f"{skey}:{attr}:{t['arm']}",
                        "reference_key": ck,
                        "location_key": lk,
                        "sample_key": skey,
                        "method_key": mkey,
                        "attribute": attr,
                        "measurement_type": decl.measurement_type,
                        "subfield": next(iter(decl.subfields)),
                        "value": value,
                        "units_raw": sh["units"],
                        "sd": sd,
                        "se": np.nan,
                        "n": n_int,
                        "flags": sorted(flags),
                        "source_row_ref": f"{DATA_FILE}[{sh['name']}]#data_row={i}:{attr}:{t['arm']}",
                    }
                )
            if row_n_facts == 0:
                raise ContractError(f"{sh['name']} row {i} ({name}) has no mean at all")
            samples.append((skey, species))
            row_info.append((sh["short"], i, name, habitat, coord is None, note is not None, tech))

    if seen_neg != DECLARED_NEGATIVE_SD:
        raise ContractError(f"declared negative SDs {sorted(DECLARED_NEGATIVE_SD - seen_neg)} not found")
    fact = pd.DataFrame(facts)
    fact["n"] = pd.array(list(fact["n"]), dtype="Int64")
    method = pd.DataFrame(sorted(methods.values(), key=lambda r: r["method_key"]))
    lk_sorted = sorted(locs)
    n = len(lk_sorted)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "location_key": lk_sorted, "site_name": None, "source_site_id": None,
            "source_core_id": lk_sorted,
            "latitude": [np.nan if locs[k]["lat"] is None else locs[k]["lat"] for k in lk_sorted],
            "longitude": [np.nan if locs[k]["lon"] is None else locs[k]["lon"] for k in lk_sorted],
            "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None, "country": None,
            "admin_division": None, "water_body": None, "habitat_type": [locs[k]["habitat"] for k in lk_sorted],
            "geomorphic_setting": None, "bioregion": None, "marine_ecoregion": None, "salinity_class": None,
            "salinity_class_level": None, "vegetation_class": None, "vegetation_class_level": None,
            "tidal_position": None, "tidal_position_level": None, "modification_status": [[] for _ in range(n)],
            "dominant_species": [[] for _ in range(n)], "elevation": np.nan, "elevation_units": None,
            "elevation_datum": None, "elevation_method": None, "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
        }
    )
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "sample_key": [s for s, _ in samples], "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": "study-level mean flux; no sediment depth interval",
            "sampling_year": pd.array([None] * len(samples), dtype="Int64"),
            "sampling_month": pd.array([None] * len(samples), dtype="Int64"),
            "taxon": [sp for _, sp in samples], "taxon_rank": ["species" if sp else None for _, sp in samples],
            "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
            "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
            "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        }
    )
    ref_keys = sorted(cits)
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "citation_key": [f"{k}|{k}" for k in ref_keys], "reference_key": ref_keys,
            "bibliography_id": [k[len("ref:"):] for k in ref_keys], "publication_type": None, "doi": None,
            "title": [cits[k][0] for k in ref_keys], "authors": [cits[k][1] for k in ref_keys],
            "year": pd.array([cits[k][2] for k in ref_keys], dtype="Int64"), "url": None, "openalex_id": None,
            "doi_resolved": None, "doi_resolution_source": None,
        }
    )
    source = pd.DataFrame(
        [{"source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
          "access_date": access_date, "license": SOURCE_LICENSE,
          "notes": CITATION + " Version and license are from the figshare record (the workbook states neither)."}]
    )
    tables = {"source": source, "reference": reference, "citation": citation,
              "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
              "method": method, "fact": fact}
    skips: list[dict] = []

    if len(fact) != n_mean_cells:
        raise ContractError(f"facts {len(fact)} != mean cells {n_mean_cells}")
    for what, a, b in (("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
                       ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key)):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    same_title: dict[str, list[str]] = {}
    for k in ref_keys:
        if cits[k][0]:
            same_title.setdefault(cits[k][0].lower(), []).append(k[len("ref:"):])
    report = {
        "source_rows": {"sediment fluxes": SED_N_ROWS, "oyster fluxes": OY_N_ROWS},
        "facts": len(fact),
        "mean_cells": n_mean_cells,
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_sheet_arm": {
            k: int(v) for k, v in fact.source_row_ref.str.replace(r"^.*\[(.+)\]#.*:(\w+)$", r"\1 / \2", regex=True).value_counts().sort_index().items()
        },
        "facts_with_non_integer_n": n_nonint_n,
        "rows_with_note": sum(1 for r in row_info if r[5]),
        "rows_without_coordinates": sum(1 for r in row_info if r[4]),
        "samples": len(sample), "locations": len(loc),
        "locations_by_habitat_type": loc.habitat_type.fillna("(none)").value_counts().sort_index().to_dict(),
        "methods": len(method), "references": len(reference), "citations": len(citation),
        "citations_without_title": int(citation.title.isna().sum()),
        "references_sharing_a_title": {t: v for t, v in sorted(same_title.items()) if len(v) > 1},
        "flag_counts": flag_counts,
        "n2_techniques_sediment": fact[(fact.attribute == "ray_sediment_n2")].method_key.str.split("|").str[3].value_counts().to_dict(),
        "value_summary_by_attribute": {
            a: {"min": float(g.value.min()), "max": float(g.value.max()), "negative": int((g.value < 0).sum()),
                "zero": int((g.value == 0).sum())} for a, g in fact.groupby("attribute")
        },
    }
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    sed, oy = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(sed, oy, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
