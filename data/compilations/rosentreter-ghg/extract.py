#!/usr/bin/env python3
"""Rosentreter et al. coastal greenhouse-gas flux database -> compilation-contract tables.

Rosentreter, J.A., Laruelle, G.G., Bange, H.W., Bianchi, T.S., Busecke, J.J.M., Cai, W.-J., et al. (2023):
Coastal GHG data base (Rosentreter et al. 2023). figshare, doi:10.6084/m9.figshare.22351267.v1 (paper:
Nature Climate Change, 2023). Raw file (this directory): one workbook with a ``Metadata`` sheet (column
legend, incl. the ``type`` code lists), six data sheets and ``Surface_areas``.

Grain: ONE FACT PER DATA ROW (every row of the six data sheets has exactly one flux; asserted). Values are
carried exactly as reported, in the unit written in the flux column header (asserted equal to the declared
unit after whitespace normalisation). The file does not state a sign convention; the vegetation CO2 sheet is
mostly negative (uptake by the ecosystem), so positive is read as a flux to the atmosphere, but that is an
inference, not a statement of the source. Nothing is converted or capped; negative values are real fluxes and
are not flagged.

Sheet -> attribute (type/subfield, unit from the header):
  Estuaries_CO2           ghg_co2_estuary      C8 co2_flux   g CO2 m2 d-1
  Estuaries_CH4           ghg_ch4_estuary      C8 ch4_flux   mg CH4 m2 d-1
  Estuaries_N2O           ghg_n2o_estuary      C8 n2o_flux   mg N2O m2 d-1
  Coastal_vegetation_CO2  ghg_co2_vegetation   C8 co2_flux   g C m2 yr-1
  Coastal_vegetation_CH4  ghg_ch4_vegetation   C8 ch4_flux   mg CH4 m2 d-1
  Coastal_vegetation_N2O  ghg_n2o_vegetation   C8 n2o_flux   mg N2O m2 d-1
Estuary and vegetation fluxes are separate attributes because the exchange interface differs (water-air over
estuary water vs ecosystem-atmosphere exchange over vegetated coast); the CO2 vegetation unit is per year and
per C, the others per day.

Locations: ``type`` codes are decoded from the Metadata sheet's own legend (read and asserted, not hardcoded):
estuary 1 Tidal systems and deltas, 2 Lagoons, 3 Fjords -> ``geomorphic_setting`` (raw); vegetation 1
Mangroves, 2 Saltmarshes, 3 Seagrasses -> ``habitat_type`` (raw). Estuary sheets (water-air exchange over
estuary water) get ``habitat_type`` = the constant ``water column`` (the sheet's scope; flag
``habitat_from_scope``), vegetation sheets get the decoded vegetation label (a source label, no scope flag).
``site_name`` = the stripped ``site_name``; ``location_key`` = lat | long | site_name | habitat_type |
geomorphic_setting, so a site in the CO2, CH4 and N2O estuary sheets is one location. ``region`` (the RECCAP2
region code) is a column that is not loaded; it is recoverable through ``source_row_ref``.

Samples: one per data row (keyed sheet:row). The file states values are "averaged per site over
spatiotemporal gradients" for every row, so there is no year, depth or taxon.

Methods: the source gives no method per row; one ``method_dim`` row per attribute, ``method_id_null``;
``params`` records the interface, the source column header and the Metadata statement. The all-rows
statement that values are averages per site is a property of the whole file, so it is recorded here and in
the README and not as a per-fact flag (the registered ``source_site_averaged`` means an average over sites).

References: ``reference`` (author-year text, free-form) and ``full_citation`` (Nature-style entries
joined by ``; ``). The same ``reference`` string can stand for several different papers, so the study is
identified by the full citations: ``full_citation`` (whitespace-normalised) is split on ``; `` into one entry
per cited paper; a piece with a DOI (``doi:10...`` / ``doi.org/10...``) gets ``doi:<lower-case doi>``, else
``ref:<piece text>``; a row citing several papers gets the piece keys joined by ``+`` (rule 3.4), with one
citation row per piece. The number of pieces must equal the number of papers named in ``reference``
(``count_papers``: ``;`` parts, a year list ``(2010,2012)`` counting each year); the nine ``reference`` strings
where the free text and the split disagree are declared in ``DECLARED_PIECES`` with the mode that resolves them
(``split`` = use the split and assert its length, ``whole`` = keep the whole text as one piece) and the set of
disagreeing strings must equal the declared set exactly. ``pers_comm`` marks a row whose ``reference`` says
``pers. comm.`` (the Metadata says such data were personal communications in 2020 and have been published
since 2021; the full citation is then the published source).

Flags (informational; nothing is changed or dropped): zero_value; coords_integer_degree (both coordinates
whole degrees); pers_comm; habitat_from_scope (estuary sheets); duplicate_source_row (rows identical in ALL
columns of their sheet).

Provenance: ``source_row_ref`` = ``<file>[<sheet>]#data_row=<0-based data row>:<attribute>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons, load_label_maps  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "rosentreter-ghg"
SOURCE_NAME = "Rosentreter et al. coastal greenhouse-gas flux database (estuaries and coastal vegetation)"
SOURCE_DOI = "10.6084/m9.figshare.22351267.v1"
SOURCE_VERSION = "v1 (2023-05-05)"  # figshare record, read by the integration plan; the file has no version
SOURCE_LICENSE = "CC BY 4.0"  # figshare record, as above; the file states none
DATA_FILE = "database_coastalGHG_Rosentreter et al._figshare.xlsx"
CITATION = (
    "Rosentreter, J.A., Laruelle, G.G., Bange, H.W., Bianchi, T.S., Busecke, J.J.M., Cai, W.-J., et al. (2023): "
    "Coastal GHG data base (Rosentreter et al. 2023) [Dataset]. figshare, "
    "https://doi.org/10.6084/m9.figshare.22351267.v1 (paper: Nature Climate Change, 2023)"
)
METADATA_SHEET, AREAS_SHEET = "Metadata", "Surface_areas"
COLUMNS_FIXED = ["site_name", None, "lat", "long", "region", "type", "reference", "full_citation"]  # [1] = flux
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()  # every row is a fact
check_skip_reasons(ALLOWED_SKIP_REASONS)

ESTUARY_HABITAT_TYPE = "water column"  # the estuary sheets' scope (water-air exchange over estuary water)
ESTUARY_TYPES = {1: "Tidal systems and deltas", 2: "Lagoons", 3: "Fjords"}
VEGETATION_TYPES = {1: "Mangroves", 2: "Saltmarshes", 3: "Seagrasses"}
REGIONS = frozenset(range(1, 11))


@dataclass(frozen=True)
class Sheet:
    name: str
    family: str  # "estuary" | "vegetation"
    attribute: str
    flux_header: str
    units: str
    n_rows: int  # asserted: rows in the release
    interface: str


SHEETS: tuple[Sheet, ...] = (
    Sheet("Estuaries_CO2", "estuary", "ghg_co2_estuary", "flux (g CO2 m2 d-1)", "g CO2 m2 d-1", 204,
          "water-air exchange over estuary water"),
    Sheet("Estuaries_CH4", "estuary", "ghg_ch4_estuary", "flux (mg CH4 m2 d-1)", "mg CH4 m2 d-1", 157,
          "water-air exchange over estuary water"),
    Sheet("Estuaries_N2O", "estuary", "ghg_n2o_estuary", "flux (mg N2O m2 d-1)", "mg N2O m2 d-1", 123,
          "water-air exchange over estuary water"),
    Sheet("Coastal_vegetation_CO2", "vegetation", "ghg_co2_vegetation", "flux (g C m2 yr-1)", "g C m2 yr-1", 37,
          "ecosystem-atmosphere exchange over vegetated coast"),
    Sheet("Coastal_vegetation_CH4", "vegetation", "ghg_ch4_vegetation", "flux (mg CH4 m2 d-1)", "mg CH4 m2 d-1", 162,
          "ecosystem-atmosphere exchange over vegetated coast"),
    Sheet("Coastal_vegetation_N2O", "vegetation", "ghg_n2o_vegetation", "flux (mg N2O m2 d-1)", "mg N2O m2 d-1", 55,
          "ecosystem-atmosphere exchange over vegetated coast"),
)
SHEET_BY_NAME = {s.name: s for s in SHEETS}

# reference strings where the free text and the "; " split of full_citation disagree on the number of
# papers, with the mode that resolves each: (mode, number of citation pieces).
#   split: use the split, whose length must equal the number given
#   whole: the split is wrong (an author list joined by ";"; a typo in the text), keep one piece
DECLARED_PIECES: dict[str, tuple[str, int]] = {
    # the personal-communication part of the string has no citation of its own
    "Miyajima et al. (2009); T. Miyajima (pers. comm.)": ("split", 1),
    "Miyajima et al. (2009); T. Miyajima (pers. Comm.)": ("split", 1),
    # "(2016;." in the text hides the first separator: two of the three papers cannot be split reliably
    "Adiyanti et al. (2016); Jeffrey et al. (2018); Wells et al. (2020a)": ("whole", 1),
    # papers joined by a comma in `reference`
    "Burgos et al. (2018), Ferron et al. (2007)": ("split", 2),
    "Linto et al. (2014), Barnes et al. (2006)": ("split", 2),
    "Dutta et al. (2017), Ganguly et al. (2008)": ("split", 2),
    "Kreuzwieser et al. (2003), Allen et al. (2007,2010)": ("split", 3),
    # a PANGAEA citation whose authors are joined by ";"
    "CH4 data available at PANGAEA, DOI:10.1594/PANGAEA.355322": ("whole", 1),
    "CH4 data available at PANGAEA, DOI: 10.1594/PANGAEA.805244": ("whole", 1),
}

_DOI = re.compile(r"(?:doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s,;)]+)", re.IGNORECASE)
_PIECE_YEAR = re.compile(r"\(\s*\(?\s*((?:19|20)\d{2})[a-z]?\s*\)")
_PERS_COMM = re.compile(r"pers\.?\s*comm", re.IGNORECASE)
_YEAR_LIST = re.compile(r"\s*(?:\d{4}[a-z]?\s*,\s*)*\d{4}[a-z]?\s*")


def norm_ws(s: object) -> str:
    """NBSP and runs of whitespace -> one space, stripped."""
    if not isinstance(s, str):
        raise ContractError(f"expected a string, got {s!r}")
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _number(v: object, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        raise ContractError(f"{what}: non-numeric value {v!r}")
    x = float(v)
    if not math.isfinite(x):
        raise ContractError(f"{what}: non-finite value {v!r}")
    return x


def _int_code(v: object, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, np.integer)):
        raise ContractError(f"{what}: expected an integer code, got {v!r}")
    return int(v)


def _text(v: object, what: str) -> str:
    if not isinstance(v, str) or v.strip() == "":
        raise ContractError(f"{what}: blank or non-text value {v!r}")
    t = v.strip()
    if t.upper() in {"NA", "N/A", "NAN", "NULL", "NONE"}:
        raise ContractError(f"{what}: literal missing-value string {v!r}")
    return t


# ---------------------------------------------------------------- raw reading


def read_raw(path: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """The six data sheets and the Metadata sheet (no header); nothing is auto-NA'd."""
    xl = pd.ExcelFile(path)
    want = [METADATA_SHEET] + [s.name for s in SHEETS] + [AREAS_SHEET]
    if xl.sheet_names != want:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names} != {want}")
    data = {s.name: xl.parse(s.name, dtype=object, keep_default_na=False, na_values=[]) for s in SHEETS}
    meta = xl.parse(METADATA_SHEET, header=None, dtype=object, keep_default_na=False, na_values=[])
    return data, meta


def decode_type_codes(meta: pd.DataFrame) -> tuple[dict[int, str], dict[int, str]]:
    """The estuary and vegetation ``type`` legends, read from the Metadata sheet's two ``type`` rows."""
    rows = [i for i in range(len(meta)) if meta.iat[i, 0] == "type"]
    if len(rows) != 2:
        raise ContractError(f"{METADATA_SHEET}: expected two 'type' rows, found {len(rows)}")
    out = []
    for i, prefix in zip(rows, ("Estuary type (", "Coastal vegetation type (")):
        text = meta.iat[i, 1]
        if not isinstance(text, str) or not text.startswith(prefix) or not text.endswith(")"):
            raise ContractError(f"{METADATA_SHEET}: type legend {text!r} does not start with {prefix!r}")
        pairs = re.findall(r"(\d+)=\s*([^,)]+)", text)
        legend = {int(k): v.strip() for k, v in pairs}
        if len(legend) != len(pairs):
            raise ContractError(f"{METADATA_SHEET}: duplicate codes in {text!r}")
        out.append(legend)
    if out[0] != ESTUARY_TYPES or out[1] != VEGETATION_TYPES:
        raise ContractError(f"{METADATA_SHEET}: type legends {out} differ from the expected {ESTUARY_TYPES}, {VEGETATION_TYPES}")
    return out[0], out[1]


# ---------------------------------------------------------------- references


def count_papers(reference: str) -> int:
    """Papers named by a ``reference`` string: ``;`` parts, each counting its year list ``(2010,2012)``."""
    n = 0
    for p in (x.strip() for x in reference.split(";")):
        m = re.search(r"\(([^)]*)\)\s*$", p)
        n += len(m.group(1).split(",")) if m and _YEAR_LIST.fullmatch(m.group(1)) else 1
    return n


def split_pieces(reference: str, full_citation: str) -> list[str]:
    """Citation pieces of one row: the ``; `` split of the normalised full_citation, checked against the
    paper count of ``reference``; a declared exception is applied by its mode."""
    pieces = full_citation.split("; ")
    if any(p.strip() == "" for p in pieces):
        raise ContractError(f"empty citation piece in {full_citation[:80]!r}")
    expected = count_papers(reference)
    if reference in DECLARED_PIECES:
        mode, n = DECLARED_PIECES[reference]
        if mode == "whole":
            return [full_citation]
        if mode != "split" or len(pieces) != n:
            raise ContractError(f"{reference!r}: declared {DECLARED_PIECES[reference]} but the split has {len(pieces)} pieces")
        return pieces
    if len(pieces) != expected:
        raise ContractError(
            f"{reference!r}: full_citation splits into {len(pieces)} pieces but the reference names {expected} paper(s)"
        )
    return pieces


def piece_doi(piece: str) -> str | None:
    found = {m.rstrip(".").lower() for m in _DOI.findall(piece)}
    if len(found) > 1:
        raise ContractError(f"citation piece with several DOIs {sorted(found)}: {piece[:80]!r}")
    return next(iter(found)) if found else None


# ---------------------------------------------------------------- main build


def build_tables(
    data: dict[str, pd.DataFrame], meta: pd.DataFrame, access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    est_types, veg_types = decode_type_codes(meta)
    frames = []
    n_source_rows = 0
    for sh in SHEETS:
        raw = data[sh.name]
        want_cols = [c if c is not None else sh.flux_header for c in COLUMNS_FIXED]
        if list(raw.columns) != want_cols:
            raise ContractError(f"{sh.name}: unexpected columns {list(raw.columns)}")
        m = re.fullmatch(r"flux \((.+)\)", raw.columns[1])
        if not m or norm_ws(m.group(1)) != sh.units:
            raise ContractError(f"{sh.name}: header unit is not {sh.units!r}")
        if len(raw) != sh.n_rows:
            raise ContractError(f"{sh.name}: {len(raw)} data rows, expected {sh.n_rows}")
        d = pd.DataFrame({"row_idx": np.arange(len(raw)), "sheet": sh.name, "attribute": sh.attribute})
        d["dup"] = raw.astype(str).duplicated(keep=False).to_numpy()  # raw cells, before cleaning
        d["site"] = [norm_ws(_text(v, f"{sh.name}.site_name")) for v in raw["site_name"]]
        d["value"] = [_number(v, f"{sh.name}.{sh.flux_header}") for v in raw[sh.flux_header]]
        d["lat_v"] = [_number(v, f"{sh.name}.lat") for v in raw["lat"]]
        d["lon_v"] = [_number(v, f"{sh.name}.long") for v in raw["long"]]
        if not (d.lat_v.between(-90, 90).all() and d.lon_v.between(-180, 180).all()):
            raise ContractError(f"{sh.name}: coordinates outside the valid range")
        codes = [_int_code(v, f"{sh.name}.type") for v in raw["type"]]
        legend = est_types if sh.family == "estuary" else veg_types
        if set(codes) - set(legend):
            raise ContractError(f"{sh.name}: unknown type codes {set(codes) - set(legend)}")
        d["type_label"] = [legend[c] for c in codes]
        reg = [_int_code(v, f"{sh.name}.region") for v in raw["region"]]
        if set(reg) - REGIONS:
            raise ContractError(f"{sh.name}: unknown region codes {set(reg) - REGIONS}")
        if sh.family == "estuary":
            d["habitat_type"], d["geomorphic_setting"] = ESTUARY_HABITAT_TYPE, d["type_label"]
        else:
            d["habitat_type"], d["geomorphic_setting"] = d["type_label"], None
        d["ref"] = [norm_ws(_text(v, f"{sh.name}.reference")) for v in raw["reference"]]
        d["fc"] = [norm_ws(_text(v, f"{sh.name}.full_citation")) for v in raw["full_citation"]]
        frames.append(d)
        n_source_rows += len(d)
    allrows = pd.concat(frames, ignore_index=True)
    allrows["geomorphic_setting"] = allrows["geomorphic_setting"].astype(object).where(
        allrows["geomorphic_setting"].notna(), None
    )

    # ---- references: pieces, keys, citations
    disagree = {r for r, fc in set(zip(allrows.ref, allrows.fc)) if len(fc.split("; ")) != count_papers(r)}
    if disagree != set(DECLARED_PIECES):
        raise ContractError(
            f"reference strings where the split and the paper count disagree differ from the declared set: "
            f"undeclared {sorted(disagree - set(DECLARED_PIECES))}, declared but agreeing "
            f"{sorted(set(DECLARED_PIECES) - disagree)}"
        )
    row_pieces: list[list[str]] = []
    for r in allrows.itertuples(index=False):
        row_pieces.append(split_pieces(r.ref, r.fc))
    allrows["pieces"] = row_pieces
    piece_info: dict[str, dict] = {}
    for ps in row_pieces:
        for p in ps:
            if p not in piece_info:
                doi = piece_doi(p)
                piece_info[p] = {"doi": doi, "key": f"doi:{doi}" if doi else f"ref:{p}"}
    ref_keys, ref_pieces = [], {}
    for ps in row_pieces:
        keys = [piece_info[p]["key"] for p in ps]
        if len(set(keys)) != len(keys):
            raise ContractError(f"two pieces of one row resolve to one key: {keys}")
        rk = "+".join(keys)
        ref_keys.append(rk)
        ref_pieces.setdefault(rk, ps)
        if ref_pieces[rk] != ps:
            raise ContractError(f"one reference key with two different piece lists: {rk[:80]!r}")
    allrows["reference_key"] = ref_keys
    # one reference may be cited under several `reference` strings (e.g. case variants): carry all
    labels_of: dict[str, set[str]] = {}
    for rk, lab in zip(allrows.reference_key, allrows.ref):
        labels_of.setdefault(rk, set()).add(lab)
    cit_rows = []
    for rk in sorted(ref_pieces):
        label = sorted(labels_of[rk])[0]
        for k, p in enumerate(ref_pieces[rk]):
            years = _PIECE_YEAR.findall(p)
            cit_rows.append(
                {
                    "source_key": SOURCE_KEY,
                    "citation_key": f"{rk}|{k}",
                    "reference_key": rk,
                    "bibliography_id": label,
                    "publication_type": None,
                    "doi": piece_info[p]["doi"],
                    "title": p,
                    "authors": None,
                    "year": int(years[-1]) if years else None,
                    "url": None,
                    "openalex_id": None,
                    "doi_resolved": None,
                    "doi_resolution_source": None,
                }
            )
    citation = pd.DataFrame(cit_rows)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": sorted(ref_pieces)})

    # ---- location
    allrows["location_key"] = (
        allrows.lat_v.map(lambda x: repr(float(x))) + "|" + allrows.lon_v.map(lambda x: repr(float(x))) + "|"
        + allrows.site + "|" + allrows.habitat_type + "|" + allrows.geomorphic_setting.fillna("")
    )
    first = allrows.drop_duplicates("location_key").sort_values("location_key")
    n = len(first)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": first.location_key,
            "site_name": first.site,
            "source_site_id": None,
            "source_core_id": first.location_key,  # the source has no site id
            "latitude": first.lat_v,
            "longitude": first.lon_v,
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": None,
            "country": None,
            "admin_division": None,
            "water_body": None,
            "habitat_type": first.habitat_type,
            "geomorphic_setting": first.geomorphic_setting,
            "bioregion": None,
            "marine_ecoregion": None,
            "salinity_class": None,
            "salinity_class_level": None,
            "vegetation_class": None,
            "vegetation_class_level": None,
            "tidal_position": None,
            "tidal_position_level": None,
            "modification_status": [[] for _ in range(n)],
            "dominant_species": [[] for _ in range(n)],
            "elevation": np.nan,
            "elevation_units": None,
            "elevation_datum": None,
            "elevation_method": None,
            "water_depth_m": np.nan,
            "distance_to_shore_km": np.nan,
        }
    ).reset_index(drop=True)

    # ---- sample (one per data row)
    allrows["sample_key"] = allrows.sheet + ":row" + allrows.row_idx.astype(str)
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": allrows.sample_key,
            "source_sample_id": None,
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": "site average over spatiotemporal gradients; no sediment depth",
            "sampling_year": pd.array([None] * len(allrows), dtype="Int64"),
            "sampling_month": pd.array([None] * len(allrows), dtype="Int64"),
            "taxon": None,
            "taxon_rank": None,
            "vegetated_or_bare": None,
            "marsh_zone": None,
            "sediment_type": None,
            "photosynthetic_sediment": None,
            "sample_depth_m": np.nan,
            "water_temperature_c": np.nan,
            "water_salinity": np.nan,
            "water_salinity_units": None,
            "bottom_water_o2": np.nan,
            "bottom_water_o2_units": None,
        }
    ).reset_index(drop=True)

    # ---- methods: one per attribute (the source gives none per row)
    method = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "method_key": sh.attribute,
                "source_method_id": None,
                "attribute": sh.attribute,
                "method_status": "method_id_null",
                "technique": None,
                "coring_method": None,
                "carbon_type": None,
                "carbon_measured_or_modeled": None,
                "fraction_carbon_method": None,
                "carbonates_removed": None,
                "carbonate_removal_method": None,
                "conversion_applied": None,
                "in_situ_ex_situ": None,
                "light_dark": None,
                "incubation_type": None,
                "experimental_status": None,
                "params": json.dumps(
                    {
                        "interface": sh.interface,
                        "flux_column": sh.flux_header,
                        "sheet": sh.name,
                        "metadata_statement": "data extracted from publications / data publisher and averaged per "
                        "site over spatiotemporal gradients",
                    },
                    sort_keys=True,
                ),
            }
            for sh in SHEETS
        ]
    )

    # ---- facts
    maps = load_label_maps()
    hmap = maps[(SOURCE_KEY, "habitat_type", "habitat")]
    scope_labels = {ESTUARY_HABITAT_TYPE}
    bad = sorted(x for x in scope_labels if hmap.get(x) is None)
    if bad:  # habitat_from_scope claims a scope-derived habitat, so the label must map to one
        raise ContractError(f"habitat_type labels without a habitat in the label map: {bad}")
    rows = []
    for r in allrows.itertuples(index=False):
        sh = SHEET_BY_NAME[r.sheet]
        decl = ATTRIBUTE_DECL[r.attribute]
        flags: list[str] = []
        if sh.family == "estuary":
            flags.append("habitat_from_scope")
        if r.value == 0:
            flags.append("zero_value")
        if _PERS_COMM.search(r.ref):
            flags.append("pers_comm")
        if r.dup:
            flags.append("duplicate_source_row")
        if r.lat_v == round(r.lat_v) and r.lon_v == round(r.lon_v):
            flags.append("coords_integer_degree")
        rows.append(
            {
                "source_key": SOURCE_KEY,
                "fact_key": f"{r.sample_key}:{r.attribute}",
                "reference_key": r.reference_key,
                "location_key": r.location_key,
                "sample_key": r.sample_key,
                "method_key": r.attribute,
                "attribute": r.attribute,
                "measurement_type": decl.measurement_type,
                "subfield": next(iter(decl.subfields)),
                "value": float(r.value),
                "units_raw": sh.units,
                "sd": np.nan,
                "se": np.nan,
                "n": None,
                "flags": sorted(flags),
                "source_row_ref": f"{DATA_FILE}[{r.sheet}]#data_row={int(r.row_idx)}:{r.attribute}",
            }
        )
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": SOURCE_VERSION,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": SOURCE_LICENSE,
                "notes": CITATION + " Version and license are from the figshare record (the workbook states neither).",
            }
        ]
    )
    tables = {
        "source": source,
        "reference": reference,
        "citation": citation,
        "study_screening": empty_table("study_screening"),
        "location": loc,
        "sample": sample,
        "method": method,
        "fact": fact,
    }
    skips: list[dict] = []

    # ---- accounting
    if len(fact) + len(skips) != n_source_rows:
        raise ContractError(f"facts {len(fact)} + skipped {len(skips)} != source rows {n_source_rows}")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key),
        ("reference", fact.reference_key, reference.reference_key),
        ("method", fact.method_key, method.method_key),
        ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for f in fact["flags"]:
        for x in f:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    fcs_per_ref = allrows.groupby("ref").fc.nunique()
    pcs = [p for ps in row_pieces for p in ps]
    report = {
        "source_rows": n_source_rows,
        "source_rows_by_sheet": {s.name: int((allrows.sheet == s.name).sum()) for s in SHEETS},
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "rows_excluded": len(skips),
        "samples": len(sample),
        "locations": len(loc),
        "locations_by_habitat_type": loc.habitat_type.value_counts().sort_index().to_dict(),
        "locations_estuary_by_geomorphic_setting": loc.geomorphic_setting.value_counts().sort_index().to_dict(),
        "methods": len(method),
        "references": len(reference),
        "citations": len(citation),
        "flag_counts": flag_counts,
        "flag_counts_by_sheet": {
            s.name: {fl: int(sum(fl in x for x in g["flags"])) for fl in sorted({y for x in g["flags"] for y in x})}
            for s in SHEETS
            for g in [fact[fact.attribute == s.attribute]]
        },
        "value_summary_by_attribute": {
            a: {"min": float(g.value.min()), "max": float(g.value.max()), "negative": int((g.value < 0).sum()),
                "zero": int((g.value == 0).sum())}
            for a, g in fact.groupby("attribute")
        },
        "distinct_reference_strings": int(allrows.ref.nunique()),
        "distinct_reference_strings_by_sheet": {s.name: int(allrows.ref[allrows.sheet == s.name].nunique()) for s in SHEETS},
        "distinct_full_citation_texts": int(allrows.fc.nunique()),
        "reference_strings_with_several_full_citation_texts": int((fcs_per_ref > 1).sum()),
        "full_citation_texts_under_several_reference_strings": int((allrows.groupby("fc").ref.nunique() > 1).sum()),
        "distinct_citation_pieces": len(set(pcs)),
        "pieces_with_doi": sum(1 for p in set(pcs) if piece_info[p]["doi"]),
        "distinct_dois": len({piece_info[p]["doi"] for p in set(pcs) if piece_info[p]["doi"]}),
        "rows_with_several_pieces": int(sum(len(ps) > 1 for ps in row_pieces)),
        "pieces_per_row": {int(k): int(v) for k, v in pd.Series([len(ps) for ps in row_pieces]).value_counts().sort_index().items()},
        "declared_piece_exceptions": {k: list(v) for k, v in sorted(DECLARED_PIECES.items())},
        "reference_strings_with_pers_comm": sorted({r for r in allrows.ref if _PERS_COMM.search(r)}),
        "type_legends": {"estuary": {str(k): v for k, v in est_types.items()}, "vegetation": {str(k): v for k, v in veg_types.items()}},
        "region_codes_not_loaded": sorted({int(c) for c in pd.concat([data[s.name]["region"] for s in SHEETS])}),
        "duplicate_source_rows_flagged": int(allrows["dup"].sum()),
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
    data, meta = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(data, meta, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
