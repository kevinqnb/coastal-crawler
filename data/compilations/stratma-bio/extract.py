#!/usr/bin/env python3
"""BenBioDen database (BenBIO biomass and BenDEN density) -> compilation-contract tables.

Stratmann, T., van Oevelen, D., Martinez Arbizu, P. et al. (2021). The BenBioDen database, a global database for meio-, macro- and
megabenthic biomass and densities. Dryad, doi:10.5061/dryad.gb5mkkwm6 (paper: Sci. Data 2020). Raw files (this directory), all
Windows-1252: ``BenBIO_database.csv`` (biomass), ``BenDEN_database.csv`` (density), and each database's screening record,
``List_of_studies_for_BenBio_database.txt`` and ``List_of_studies_for_BenDen_database.txt`` (tab-separated). One source
(``stratma-bio``), two data files.

Grain: one fact per data row (one community biomass or density at one station and size class), in the source's units. The four
biomass units (``g WM/ m2``, ``g DM/ m2``, ``g C/ m2``, ``g AFDM/ m2``) are the per-row ``unit`` column. The density unit is read from
the BenDEN header (``Density (ind/ m2)``). Both are asserted. Nothing is converted, and suspicious values are flagged.

Attributes, both type B10: ``benbio_biomass`` [total_biomass] and ``benden_density`` [total_density]. They are different quantities,
and the biomass unit differs per row. Standard labels via ``compilation_label_maps.csv``: habitat is subtidal soft sediment for every
``Depth range`` label (dataset scope, as SCOC). Setting is shelf for the shelf label, open ocean for the abyssal label, and blank
for near-shore and slope. A row whose ``Depth range`` is ``NA`` gets no habitat and no ``habitat_from_scope`` flag. Habitat is
never inferred from the depth number.

Exclusions: only a BenDEN row with no density (``no_density``, listed with its file in ``skip_report.jsonl``). A BenBIO row without
a biomass raises. Row accounting is asserted per file: source rows = facts + excluded rows.

Derived-field rules (the raw value is recoverable via ``fact.source_row_ref`` = ``<file>#data_row=<0-based row>:<attribute>``):
  * Strings are whitespace-stripped, and ``NA`` and blank become NULL. Numbers are parsed with ``float``, and a non-numeric value raises.
  * latitude and longitude are the source decimal degrees, asserted in range. BenDEN data row 48707 (station "North Pole") has
    latitude 90.012, beyond the pole. It is kept, flagged ``lat_beyond_pole`` and declared in ``KNOWN_LATITUDE_BEYOND_POLE``, and any
    other row outside [-90, 90] raises. Where only the longitude is blank (19 rows per file, one study) the latitude is kept, the
    longitude is NULL, and the facts get ``coords_missing``.
  * water_depth_m is ``Depth (m)`` (positive down, must be > 0), and elevation is NULL. habitat_type is ``Depth range`` (a depth
    class, raw), water_body is ``Ocean``, site_name is ``Region`` and position_method is ``Location`` (exact or approximated).
  * sample: one per data row (no sediment depth interval). taxon is ``Benthos`` (macro, meio or megabenthos), rank ``size_class``.
  * method: one per (reference, attribute, sampling gear, sieve lower, sieve upper, raw or converted, conversion reference).
    technique is the sampling gear. The sieve mesh strings (kept as text, e.g. ``0.300 and 0.500``), the raw or converted label and
    the conversion reference go to ``params``. method_status is ``matched`` when a gear is stated and ``method_id_null`` when it is
    NA (the source has no method id).
  * location_key = reference | region | ocean | lat | lon | depth | location label | depth range (raw strings), shared by both files,
    so the same station of one reference in BenBIO and BenDEN is one location. Ocean is in the key because two stations (BenDEN rows
    1210/1211 and 1285/1286) carry both ``Pacific Ocean`` and ``Arctic Ocean`` at identical coordinates, and they stay separate.
  * reference_key is ``ref:<Reference string>`` (whitespace collapsed, one trailing full stop dropped), the same in both files.
    Strings are never merged beyond that. Citation: bibliography_id and title are the string, and year and DOI are parsed by regex
    only when the string is a single citation (no ``; ``).

Study lists (``study_screening``): each file has its own list with that database's labels, and ``study_key`` is ``benbio_<n>`` or
``benden_<n>`` (n = 0-based list row). Two trailing BenBIO list rows are blank (asserted) and dropped. A reference string is matched
to its own database's list: the whole string by its case- and punctuation-insensitive form, else each ``; ``-separated part the same
way, else the part is resolved by ``CROSSWALK`` (a typo, an encoding loss, a journal abbreviation, or, for two papers, a different year
in the list) or declared in ``UNRESOLVED`` (no list entry). Any other part raises, and so does any included-but-uncited list entry not
declared in ``INCLUDED_UNUSED``. A list entry links to a reference only when exactly one reference string resolves to it as a whole.
``study_marked_excluded`` goes on facts citing an entry whose database status is not ``included`` (``KNOWN_USED_BUT_NOT_INCLUDED``;
any other raises).

Flags: zero_value, coords_missing, lat_lon_zero, lat_beyond_pole, coords_approximate (Location = approximated), depth_missing,
method_missing (no sampling gear), duplicate_source_row (every member of a group of rows identical in all columns of that file),
converted_by_source (BenBIO ``converted data``), study_marked_excluded, habitat_from_scope (the row has a Depth range label).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons, load_label_maps  # noqa: E402
from coastal_crawler.compilations import ContractError, write_extracted  # noqa: E402

SOURCE_KEY = "stratma-bio"
SOURCE_NAME = "BenBioDen database (benthic biomass and density)"
SOURCE_VERSION = "4 (2021-06-07)"  # Dryad version 4
SOURCE_DOI = "10.5061/dryad.gb5mkkwm6"
SOURCE_LICENSE = "CC0-1.0"
CITATION = (
    "Stratmann, T., van Oevelen, D., Martinez Arbizu, P. et al. (2021). The BenBioDen database, a global "
    "database for meio-, macro- and megabenthic biomass and densities [Dataset]. Dryad. "
    "https://doi.org/10.5061/dryad.gb5mkkwm6"
)
NULL_LITERALS = frozenset({"", "NA"})
ALLOWED_SKIP_REASONS = frozenset({"no_density"})
check_skip_reasons(ALLOWED_SKIP_REASONS)  # the local list must be a subset of the registry
DEPTH_NOTES = "benthic community biomass or density; no sediment depth interval"
BENTHOS = frozenset({"macrobenthos", "meiobenthos", "megabenthos"})
LOCATION_LABELS = frozenset({"exact location", "approximated location"})
RAW_CONVERTED = frozenset({"raw data", "converted data"})
BIO_UNITS = frozenset({"g WM/ m2", "g DM/ m2", "g C/ m2", "g AFDM/ m2"})
NO_WATER_CONTEXT = {  # in-situ water context columns on sample_dim this compilation does not have
    "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
    "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
}

BIO_COLUMNS = [
    "Region", "Ocean", "Latitude (N)", "Longitude (E)", "Location", "Depth (m)", "Depth range",
    "Biomass", "unit", "raw data/ converted data", "Benthos", "Sieve mesh size (mm) lower limit",
    "Sieve mesh size (mm) upper limit", "sampling gear", "Reference",
    "Reference for biomass conversion factors",
]
DEN_COLUMNS = [
    "Region", "Ocean", "Latitude (N)", "Longitude (E)", "Location", "Depth (m)", "Depth range",
    "Density (ind/ m2)", "Benthos", "Sieve mesh size (mm) lower limit",
    "Sieve mesh size (mm) upper limit", "Sampling gear", "Reference",
]
DEN_UNITS = "ind/ m2"  # read from the header "Density (ind/ m2)" and asserted
LIST_COLUMNS = ["Study reference", "Screening phase", "Eligibility phase"]  # + the database-status column
# common short names for the two data files
BIO_SHORT = ["region", "ocean", "lat", "lon", "locq", "depth", "drange", "value", "unit", "rawconv",
             "benthos", "sieve_lo", "sieve_hi", "gear", "ref", "convref"]
DEN_SHORT = ["region", "ocean", "lat", "lon", "locq", "depth", "drange", "value", "benthos",
             "sieve_lo", "sieve_hi", "gear", "ref"]

# ---- table descriptions
TABLES_SPEC = {
    "bio": {
        "data_file": "BenBIO_database.csv", "list_file": "List_of_studies_for_BenBio_database.txt",
        "columns": BIO_COLUMNS, "short": BIO_SHORT, "attribute": "benbio_biomass",
        "subfield": "total_biomass", "list_status_col": "BenBio database", "study_prefix": "benbio",
    },
    "den": {
        "data_file": "BenDEN_database.csv", "list_file": "List_of_studies_for_BenDen_database.txt",
        "columns": DEN_COLUMNS, "short": DEN_SHORT, "attribute": "benden_density",
        "subfield": "total_density", "list_status_col": "BenDEN database", "study_prefix": "benden",
    },
}

# ---- study-list resolution constants (found 2026-11-16; each is asserted, so drift raises)

# data part (prefix of its normalised text) -> list entries (prefix of their normalised text, or
# a ``(list row, prefix)`` pair is not needed: a prefix must match exactly ONE entry of the list in use).
CROSSWALK: list[tuple[str, list[str]]] = [
    # encoding loss in the list (alpha/beta -> '?'), accents, diacritics lost
    ("Baldrighi, E., Lavaleye, M., Aliani, S., Conversi, A. & Manini, E. Large spatial",
     ["Baldrighi, E., Lavaleye, M., Aliani, S., Conversi, A. & Manini, E. Large spatial"]),
    ("Cvitkovic, I. et al. Spatio-temporal", ["Cvitkovi?, I. et al. Spatio-temporal"]),
    ("Cvitkovic, I. et al. Structure of epibiontic", ["Cvitkovi?, I. et al. Structure of epibiontic"]),
    ("Ramirez-Llodra, E., Company", ["Ramírez-Llodrà, E., Company"]),
    ("Ürkmez, D., Sezgin", ["Ürkmez, D., Sezg"]),
    ("Warzocha, J., Gromisz, S., Wodzinowski, T. & Szymanek, L.",
     ["Warzocha, J., Gromisz, S., Wodzinowski, T. & Szymanek, L."]),
    # journal abbreviation / wording / typo in the list
    ("Itoh, M. et al. Bathymetric patterns of meiofaunal",
     ["Itoh, M. et al. Bathymetric patterns of meiofaunal"]),
    ("Klages, M., Deubel, H. & Rachor, E. Organic carbon", ["Klages, M., Deubel, H. & Rachor, E. Organic carbon"]),
    ("Macdonald, I. R., Bluhm, B. A., Iken, K., Gagaev, S. & Strong, S. Benthic",
     ["Macdonald, I. R., Bluhm, B. A., Iken, K., Gagaev, S. & Strong, S. Benthic"]),
    ("Parulekar, A. H., Ansari, Z. A. & Harkantra, S. N. Benthic fauna",
     ["Parulekar, A. H., Ansari, Z. A. & Harkantra, S. N. Benthic fauna"]),
    ("Parulekar, A. H., Harkantra, S. N., Ansari, Z. A. & Matondkar",
     ["Parulekar, A. H., Harkantra, S. N., Ansari, Z. A. & Matondkar"]),
    ("Tam Malaga, J. L. Variación latitudinal", ["Tam Malaga, J. L. Variación latitudinal"]),
    ("Dinet, A. Géochemie organique", ["Dinet, A. Géochemie organique"]),
    # the year in the LIST differs from the data (and from the paper's): 1996 vs 2004, 1975 vs 2003
    ("Piepenburg, D. & Schmid, M. K. Distribution, abundance",
     ["Piepenburg, D. & Schmid, M. K. Distribution, abundance"]),
    ("Grassle, J. F., Sanders, H. L., Hessler, R. R., Rowe, G. T. & McLellan, T. Pattern",
     ["Grassle, J. F., Sanders, H. L., Hessler, R. R., Rowe, G. T. & McLellan, T. Pattern"]),
    # one list entry holds two Soltwedel citations joined by '; '; the data cell cites them as parts
    ("Soltwedel, T. & Thiel, H. Biogenic sediment compounds",
     ["Soltwedel, T. & Thiel, H. Biogenic sediment compounds"]),
    ("Soltwedel, T. Meiobenthos distribution pattern",
     ["Soltwedel, T. & Thiel, H. Biogenic sediment compounds"]),
]
# data parts for which the database's own list has no entry (checked 2026-11-16: the nearest entries are
# different papers). Gilkinson 2005 (ICES J. Mar. Sci.) is NOT the list's Gilkinson 2003 (Cont. Shelf Res.).
UNRESOLVED: list[str] = [
    "Gilkinson, K. D. et al. Immediate impacts and recovery trajectories",
    "Gilkinson, K. Recent DFO",
    "Wei, C.-L. et al. Seafloor biodiversity of Canada's three oceans",
    "Archambault, P., unpublished (2009)",
    "Bluhm, B. A. HLY-05-02 Macrofauna Data",
]
# data rows whose latitude lies beyond the pole (found 2026-11-16: BenDEN data row 48707, station "North Pole",
# 90.012). The value is carried as reported; any other out-of-range latitude raises. Per table: row -> raw string.
KNOWN_LATITUDE_BEYOND_POLE: dict[str, dict[int, str]] = {"bio": {}, "den": {48707: "90.012"}}
# list entries the data cites although the list does not mark them 'included' (prefix, per table)
KNOWN_USED_BUT_NOT_INCLUDED: dict[str, list[str]] = {
    "bio": [],
    "den": ["Relexans, J.-C., Lin, R. G., Castel, J., Etcheber, H. & Laborde, P. Response of biota"],
}
# list entries marked 'included' that no data string resolves to, after CROSSWALK (prefix, per table)
INCLUDED_UNUSED: dict[str, list[str]] = {
    "bio": [],
    "den": [  # found 2026-11-16; no data reference string resolves to these eight BenDen list entries
        "Danovaro, R., Fabiano, M. & Vincx, M. Meiofauna response to the Agip Abruzzo",
        "Gal\u00e9ron, J. Megafauna abundance and biomass from trawl samples obtained during cruise DI226",
        "Gal\u00e9ron, J. Megafauna abundance and biomass from trawl samples obtained during cruise DI229",
        "Gal\u00e9ron, J. Megafauna abundance and biomass from trawl samples obtained during cruise DI231",
        "Gilkinson, K. D. et al. Immediate and longer-term impacts of hydraulic clam dredging",
        "Moreno, M. et al. Across shore variability",
        "Pfannkuche, O. et al. Compilation of scientific results of the EU project BENGAL",
        "Rutgers van der Loeff, M. M. & Lavaleye, M. S. S. Sediments, fauna and",
    ],
}


# ---------------------------------------------------------------- helpers


def _norm(s: str) -> str:
    """Collapse whitespace, drop one trailing period."""
    return re.sub(r"\s+", " ", s).strip().rstrip(".").strip()


def _canon(s: str) -> str:
    """Case- and punctuation-insensitive form used to match reference strings."""
    return re.sub(r"[^a-z0-9]", "", _norm(s).casefold())


def _clean(v: str) -> str | None:
    v = v.strip()
    return None if v in NULL_LITERALS else v


def _float(v: str | None, what: str) -> float:
    """Python's correctly-rounded float(); NaN for NULL; raise on non-numeric / non-finite."""
    if v is None:
        return float("nan")
    try:
        x = float(v)
    except ValueError as e:
        raise ContractError(f"{what}: non-numeric value {v!r}") from e
    if not np.isfinite(x):
        raise ContractError(f"{what}: non-finite value {v!r}")
    return x


def _read(path: Path, sep: str) -> pd.DataFrame:
    # every field as string; nothing is auto-NA'd; the files are Windows-1252 (decoded strictly)
    df = pd.read_csv(path, sep=sep, dtype=str, encoding="cp1252", keep_default_na=False, na_values=[])
    _assert_no_replacement_char(df, path.name)
    return df


def _assert_no_replacement_char(df: pd.DataFrame, what: str) -> None:
    """No U+FFFD (a replacement character means the text was already damaged or mis-decoded)."""
    bad = [c for c in df.columns if df[c].str.contains("\ufffd", regex=False).any()]
    if bad:
        raise ContractError(f"{what}: U+FFFD in columns {bad}")


_YEAR = re.compile(r"\((\d{4})\)")
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def _assert_habitat_from_scope(labels: set[str]) -> None:
    """``habitat_from_scope`` goes on every fact whose location has a ``habitat_type``, so every such
    label must map to a habitat in the label map."""
    m = load_label_maps().get((SOURCE_KEY, "habitat_type", "habitat"), {})
    bad = sorted(x for x in labels if x not in m or m[x] is None)
    if bad:
        raise ContractError(f"habitat_type labels without a habitat in the label map: {bad}")


# ---------------------------------------------------------------- study lists / references


def build_studies(lst: pd.DataFrame, table: str) -> tuple[pd.DataFrame, int]:
    """The study list of one database, with its fully blank rows dropped (asserted). Returns
    (studies, n_blank_rows_dropped)."""
    spec = TABLES_SPEC[table]
    want = LIST_COLUMNS + [spec["list_status_col"]]
    if list(lst.columns) != want:
        raise ContractError(f"{spec['list_file']}: unexpected columns {list(lst.columns)}")
    blank = (lst.apply(lambda c: c.str.strip()) == "").all(axis=1)
    if list(lst.index[blank]) != list(range(len(lst) - int(blank.sum()), len(lst))):
        raise ContractError(f"{spec['list_file']}: blank rows are not all at the end")
    kept = lst[~blank]
    if (kept["Study reference"].str.strip() == "").any():
        raise ContractError(f"{spec['list_file']}: a non-blank row with an empty citation")
    studies = pd.DataFrame(
        {
            "study_key": [f"{spec['study_prefix']}_{i:04d}" for i in kept.index],
            "list_row": kept.index.to_numpy(),
            "study_citation": [re.sub(r"\s+", " ", x).strip() for x in kept["Study reference"]],
            "screening_phase": [_clean(x) for x in kept["Screening phase"]],
            "eligibility_phase": [_clean(x) for x in kept["Eligibility phase"]],
            "database_status": [_clean(x) for x in kept[spec["list_status_col"]]],
        }
    )
    studies["norm"] = studies.study_citation.map(_norm)
    studies["canon"] = studies.study_citation.map(_canon)
    if studies.canon.duplicated().any():
        raise ContractError(f"{spec['list_file']}: two entries share a canonical form")
    if studies.database_status.isna().any():
        raise ContractError(f"{spec['list_file']}: an entry without a database status")
    return studies, int(blank.sum())


def _entries_with_prefix(studies: pd.DataFrame, prefix: str) -> list[str]:
    return list(studies.study_key[studies.norm.str.startswith(prefix)])


def resolve_references(
    refs: set[str], studies: pd.DataFrame
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict, set[str]]:
    """data reference string (normalised) -> sorted list-entry keys, and -> unresolved parts.

    A whole string is matched on its canonical form; otherwise each ``; ``-part is matched the same way,
    then by CROSSWALK, then it must match an UNRESOLVED prefix, else raise."""
    by_canon = dict(zip(studies.canon, studies.study_key))
    resolved: dict[str, list[str]] = {}
    unresolved: dict[str, list[str]] = {}
    how = {"whole": 0, "parts": 0, "crosswalk_parts": 0, "unresolved_parts": 0}
    composite: set[str] = set()  # strings resolved part by part (two or more parts)
    crosswalk_hits: set[str] = set()
    unresolved_hits: set[str] = set()
    for r in sorted(refs):
        if _canon(r) in by_canon:
            resolved[r] = [by_canon[_canon(r)]]
            how["whole"] += 1
            continue
        keys: list[str] = []
        left: list[str] = []
        split = r.split("; ")
        if len(split) > 1:
            composite.add(r)
        for part in split:
            p = _norm(part)
            if _canon(p) in by_canon:
                keys.append(by_canon[_canon(p)])
                how["parts"] += 1
                continue
            hit = [(dp, lp) for dp, lp in CROSSWALK if p.startswith(dp)]
            if len(hit) > 1:
                raise ContractError(f"CROSSWALK: {p[:60]!r} matches {len(hit)} data prefixes")
            if hit:
                dp, lps = hit[0]
                for lp in lps:
                    m = _entries_with_prefix(studies, lp)
                    if len(m) != 1:
                        raise ContractError(
                            f"CROSSWALK list prefix {lp!r} matches {len(m)} entries of {studies.study_key.iloc[0][:6]}"
                        )
                    keys.append(m[0])
                crosswalk_hits.add(dp)
                how["crosswalk_parts"] += 1
                continue
            uhit = [u for u in UNRESOLVED if p.startswith(u)]
            if uhit:
                left.append(p)
                unresolved_hits.add(uhit[0])
                how["unresolved_parts"] += 1
                continue
            raise ContractError(f"reference part matches no list entry and is in no constant: {p[:100]!r}")
        resolved[r] = sorted(set(keys))
        if left:
            unresolved[r] = left
    how["crosswalk_prefixes_hit"] = sorted(crosswalk_hits)  # type: ignore[assignment]
    how["unresolved_prefixes_hit"] = sorted(unresolved_hits)  # type: ignore[assignment]
    return resolved, unresolved, how, composite


# ---------------------------------------------------------------- one data file


def prepare(raw: pd.DataFrame, table: str) -> tuple[pd.DataFrame, np.ndarray, list[dict]]:
    """Clean one data file into the working frame; returns (frame of ALL rows, dup mask, skips)."""
    spec = TABLES_SPEC[table]
    if list(raw.columns) != spec["columns"]:
        raise ContractError(f"{spec['data_file']}: unexpected columns {list(raw.columns)}")
    if table == "den":
        m = re.fullmatch(r"Density \((.*)\)", raw.columns[7])
        if not m or m.group(1) != DEN_UNITS:
            raise ContractError(f"{spec['data_file']}: density header is not 'Density ({DEN_UNITS})'")
    d = raw.copy()
    d.columns = spec["short"]
    d["row_idx"] = np.arange(len(d))  # 0-based data row, as read by pandas
    d["table"] = table
    stripped = d[spec["short"]].apply(lambda c: c.str.strip())
    dup = stripped.duplicated(keep=False).to_numpy()  # identical in every column of this file
    if (stripped == "").all(axis=1).any():
        raise ContractError(f"{spec['data_file']}: a fully blank row")
    for c in spec["short"]:
        d[c] = pd.Series([_clean(v) for v in d[c]], index=d.index, dtype=object)
    for c in ("ref", "region", "ocean", "lat", "locq", "benthos"):
        if d[c].isna().any():
            raise ContractError(f"{spec['data_file']}: NULL {c!r} in rows {d.row_idx[d[c].isna()].tolist()[:5]}")
    if set(d.locq) - LOCATION_LABELS:
        raise ContractError(f"unknown Location labels {set(d.locq) - LOCATION_LABELS}")
    if set(d.benthos) - BENTHOS:
        raise ContractError(f"unknown Benthos labels {set(d.benthos) - BENTHOS}")
    skips: list[dict] = []
    if table == "bio":
        if d.value.isna().any():
            raise ContractError("BenBIO: a row without a biomass")
        if set(d.unit.dropna()) - BIO_UNITS or d.unit.isna().any():
            raise ContractError(f"BenBIO: unknown or missing units {set(d.unit) - BIO_UNITS}")
        if set(d.rawconv.dropna()) - RAW_CONVERTED or d.rawconv.isna().any():
            raise ContractError(f"BenBIO: unknown raw/converted labels {set(d.rawconv) - RAW_CONVERTED}")
    else:
        d["unit"] = DEN_UNITS
        d["rawconv"] = None
        d["convref"] = None
        for i in d.row_idx[d.value.isna()]:
            skips.append({"file": spec["data_file"], "data_row": int(i), "reason": "no_density", "n_measurements": 0})
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    d["value_v"] = [_float(v, "value") for v in d.value]
    d["lat_v"] = [_float(v, "latitude") for v in d.lat]
    d["lon_v"] = [_float(v, "longitude") for v in d.lon]
    d["depth_v"] = [_float(v, "depth") for v in d.depth]
    beyond = {int(i): d.lat[i] for i in d.row_idx[d.lat_v.abs() > 90]}
    if beyond != KNOWN_LATITUDE_BEYOND_POLE[table]:
        raise ContractError(f"latitude outside [-90, 90] in rows {beyond} (known: {KNOWN_LATITUDE_BEYOND_POLE[table]})")
    if not (d.lon_v.min() >= -180 and d.lon_v.max() <= 180):
        raise ContractError("longitude outside [-180, 180]")
    if (d.depth_v.dropna() <= 0).any():
        raise ContractError("non-positive depth (m)")
    if (d.value_v.dropna() < 0).any():
        raise ContractError("negative biomass or density")
    d["ref_norm"] = d.ref.map(_norm)
    return d, dup, skips


# ---------------------------------------------------------------- main build


def build_tables(
    raws: dict[str, pd.DataFrame], lists: dict[str, pd.DataFrame], access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    frames, dups, skips, studies, resolved_by, unresolved_by, how_by, composite_by = {}, {}, [], {}, {}, {}, {}, {}
    blank_list_rows = {}
    for t in ("bio", "den"):
        frames[t], dups[t], sk = prepare(raws[t], t)
        skips += sk
        studies[t], blank_list_rows[t] = build_studies(lists[t], t)
        refs_used = set(frames[t].ref_norm[frames[t].value_v.notna()])
        resolved_by[t], unresolved_by[t], how_by[t], composite_by[t] = resolve_references(refs_used, studies[t])
    for t in ("bio", "den"):
        s = studies[t]
        used = {k for ks in resolved_by[t].values() for k in ks}
        included = set(s.study_key[s.database_status == "included"])
        want_not_incl = {m for p in KNOWN_USED_BUT_NOT_INCLUDED[t] for m in _entries_with_prefix(s, p)}
        want_unused = {m for p in INCLUDED_UNUSED[t] for m in _entries_with_prefix(s, p)}
        if len(want_not_incl) != len(KNOWN_USED_BUT_NOT_INCLUDED[t]) or len(want_unused) != len(INCLUDED_UNUSED[t]):
            raise ContractError(f"{t}: a KNOWN_USED_BUT_NOT_INCLUDED / INCLUDED_UNUSED prefix is not one entry")
        if (used - included) != want_not_incl or (included - used) != want_unused:
            cit = dict(zip(s.study_key, s.study_citation))
            raise ContractError(
                f"{t}: studies used by the data vs marked included in the list:\n"
                f"  used-not-included {sorted((k, cit[k][:70]) for k in used - included)} "
                f"(allowed {sorted(want_not_incl)})\n"
                f"  included-unused {sorted((k, cit[k][:70]) for k in included - used)} "
                f"(allowed {sorted(want_unused)})"
            )
    for k in ("bio", "den"):
        for p in KNOWN_USED_BUT_NOT_INCLUDED[k]:
            m = studies[k][studies[k].norm.str.startswith(p)]
            if (m.database_status == "included").any():
                raise ContractError(f"{p!r} is marked included")
    hit_cw = set(how_by["bio"]["crosswalk_prefixes_hit"]) | set(how_by["den"]["crosswalk_prefixes_hit"])
    if hit_cw != {dp for dp, _ in CROSSWALK}:
        raise ContractError(f"CROSSWALK entries that match nothing: {sorted({dp for dp, _ in CROSSWALK} - hit_cw)}")
    hit_un = set(how_by["bio"]["unresolved_prefixes_hit"]) | set(how_by["den"]["unresolved_prefixes_hit"])
    if hit_un != set(UNRESOLVED):
        raise ContractError(f"UNRESOLVED entries that match nothing: {sorted(set(UNRESOLVED) - hit_un)}")

    # ---- all facts' working rows (skipped rows excluded)
    parts = []
    for t in ("bio", "den"):
        d = frames[t]
        d = d.assign(dup=dups[t])
        parts.append(d[d.value_v.notna()])
    d = pd.concat(parts, ignore_index=True)
    d["attribute"] = d.table.map({t: TABLES_SPEC[t]["attribute"] for t in TABLES_SPEC})
    d["data_file"] = d.table.map({t: TABLES_SPEC[t]["data_file"] for t in TABLES_SPEC})
    d["reference_key"] = "ref:" + d.ref_norm
    d["sample_key"] = d.table + d.row_idx.astype(str)
    s1 = lambda s: s.fillna("")  # noqa: E731
    d["location_key"] = (
        d.reference_key + "|" + d.region + "|" + d.ocean + "|" + s1(d.lat) + "|" + s1(d.lon)
        + "|" + s1(d.depth) + "|" + d.locq + "|" + s1(d.drange)
    )
    d["method_key"] = (
        d.reference_key + "|" + d.attribute + "|" + s1(d.gear) + "|" + s1(d.sieve_lo) + "|"
        + s1(d.sieve_hi) + "|" + s1(d.rawconv) + "|" + s1(d.convref)
    )
    loc_attrs = ["ocean", "lat_v", "lon_v", "depth_v", "locq", "drange"]
    nun = d.groupby("location_key")[loc_attrs].nunique(dropna=False)
    if (nun > 1).any().any():
        raise ContractError("a location_key has inconsistent attributes; widen the key")

    # ---- references: one per normalised string, shared by the two files
    ref_strings = sorted(set(d.ref_norm))
    ref_keys = ["ref:" + r for r in ref_strings]
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})

    def year(s: str) -> int | None:
        m = _YEAR.findall(s)
        return int(m[-1]) if m else None

    def doi(s: str) -> str | None:
        m = _DOI.search(s)
        return m.group(0).rstrip(".;,").lower() if m else None

    single = [("; " not in r) for r in ref_strings]
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": ref_keys,
            "reference_key": ref_keys,
            "bibliography_id": ref_strings,
            "publication_type": None,
            "doi": [doi(r) if one else None for r, one in zip(ref_strings, single)],
            "title": ref_strings,
            "authors": None,
            "year": pd.array([year(r) if one else None for r, one in zip(ref_strings, single)], dtype="Int64"),
            "url": None,
            "openalex_id": None,
            "doi_resolved": None,
            "doi_resolution_source": None,
        }
    )

    # ---- study_screening: link an entry to a reference only if exactly one string resolves to it as a whole
    screening_parts = []
    link_report = {}
    for t in ("bio", "den"):
        s = studies[t]
        refs_of: dict[str, set[str]] = {}
        for r, ks in resolved_by[t].items():
            # a string that resolves, as a whole, to exactly one entry (not a multi-part composite)
            if len(ks) == 1 and r not in unresolved_by[t] and r not in composite_by[t]:
                refs_of.setdefault(ks[0], set()).add(r)
        link = {k: next(iter(v)) for k, v in refs_of.items() if len(v) == 1}
        used = {k for ks in resolved_by[t].values() for k in ks}
        link_report[t] = {
            "list_entries": len(s), "linked_to_a_reference": len(link),
            "used_only_inside_composite_or_shared": len(used - set(link)),
            "not_used_by_any_fact": len(set(s.study_key) - used),
            "entries_used_by_more_than_one_string": sorted(k for k, v in refs_of.items() if len(v) > 1),
        }
        screening_parts.append(
            pd.DataFrame(
                {
                    "source_key": SOURCE_KEY,
                    "study_key": s.study_key,
                    "study_citation": s.study_citation,
                    "screening_phase": s.screening_phase,
                    "eligibility_phase": s.eligibility_phase,
                    "database_status": s.database_status,
                    "reference_key": ["ref:" + link[k] if k in link else None for k in s.study_key],
                }
            )
        )
    screening = pd.concat(screening_parts, ignore_index=True)
    screening["reference_key"] = screening.reference_key.where(screening.reference_key.notna(), None)
    status_of = {t: dict(zip(studies[t].study_key, studies[t].database_status)) for t in studies}

    # ---- location (one per location_key)
    first = d.drop_duplicates("location_key").sort_values("location_key")
    n = len(first)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": first.location_key,
            "site_name": first.region,
            "source_site_id": None,
            "source_core_id": first.location_key,  # the source has no core/station id
            "latitude": first.lat_v,
            "longitude": first.lon_v,
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": first.locq,
            "country": None,
            "admin_division": None,
            "water_body": first.ocean,
            "habitat_type": first.drange,
            "geomorphic_setting": None,
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
            "elevation": np.nan,  # water depth is water_depth_m, not an elevation
            "elevation_units": None,
            "elevation_datum": None,
            "elevation_method": None,
            "water_depth_m": first.depth_v,
            "distance_to_shore_km": np.nan,
        }
    ).reset_index(drop=True)

    # ---- sample (one per data row)
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": d.sample_key,
            "source_sample_id": None,
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": DEPTH_NOTES,
            "sampling_year": pd.array([None] * len(d), dtype="Int64"),
            "sampling_month": pd.array([None] * len(d), dtype="Int64"),
            "taxon": d.benthos,
            "taxon_rank": "size_class",
            "vegetated_or_bare": None,
            "marsh_zone": None,
            "sediment_type": None,
            "photosynthetic_sediment": None,
            **NO_WATER_CONTEXT,
        }
    )

    # ---- method
    mfirst = d.drop_duplicates("method_key").sort_values("method_key")
    params = []
    for r in mfirst.itertuples(index=False):
        p = {"sieve_lower_mm_raw": r.sieve_lo, "sieve_upper_mm_raw": r.sieve_hi}
        if r.table == "bio":
            p["raw_or_converted"] = r.rawconv
            p["conversion_reference"] = r.convref
        params.append(json.dumps(p, sort_keys=True))
    method = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "method_key": mfirst.method_key,
            "source_method_id": None,
            "attribute": mfirst.attribute,
            "method_status": np.where(mfirst.gear.isna(), "method_id_null", "matched"),
            "technique": mfirst.gear,
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
            "params": params,
        }
    )

    # ---- fact
    _assert_habitat_from_scope(set(d.drange.dropna()))
    not_incl = {t: {k for k, v in status_of[t].items() if v != "included"} for t in studies}
    flags: list[list[str]] = []
    for r in d.itertuples(index=False):
        f: list[str] = []
        if r.drange is not None:
            f.append("habitat_from_scope")  # every Depth range label maps to Subtidal soft sediment
        if r.value_v == 0:
            f.append("zero_value")
        if abs(r.lat_v) > 90:
            f.append("lat_beyond_pole")  # only the declared KNOWN_LATITUDE_BEYOND_POLE rows get here
        if np.isnan(r.lat_v) or np.isnan(r.lon_v):
            f.append("coords_missing")
        elif r.lat_v == 0 and r.lon_v == 0:
            f.append("lat_lon_zero")
        if r.locq == "approximated location":
            f.append("coords_approximate")
        if r.depth is None:
            f.append("depth_missing")
        if r.gear is None:
            f.append("method_missing")
        if r.dup:
            f.append("duplicate_source_row")
        if r.rawconv == "converted data":
            f.append("converted_by_source")
        if not_incl[r.table] & set(resolved_by[r.table][r.ref_norm]):
            f.append("study_marked_excluded")
        flags.append(sorted(f))
    fact = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "fact_key": d.sample_key + ":" + d.attribute,
            "reference_key": d.reference_key,
            "location_key": d.location_key,
            "sample_key": d.sample_key,
            "method_key": d.method_key,
            "attribute": d.attribute,
            "measurement_type": d.attribute.map(lambda a: ATTRIBUTE_DECL[a].measurement_type),
            "subfield": d.table.map({t: TABLES_SPEC[t]["subfield"] for t in TABLES_SPEC}),
            "value": d.value_v,
            "units_raw": d.unit,
            "sd": np.nan,
            "se": np.nan,
            "n": pd.array([None] * len(d), dtype="Int64"),
            "flags": flags,
            "source_row_ref": d.data_file + "#data_row=" + d.row_idx.astype(str) + ":" + d.attribute,
        }
    )

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": SOURCE_VERSION,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": SOURCE_LICENSE,
                "notes": CITATION + " Two data files (BenBIO biomass, BenDEN density) and one study "
                "list per file; not limited to coastal sediments (shore to abyssal).",
            }
        ]
    )
    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": screening, "location": loc, "sample": sample, "method": method, "fact": fact,
    }

    # ---- accounting
    n_rows = {t: len(frames[t]) for t in frames}
    n_facts = {t: int((d.table == t).sum()) for t in frames}
    n_skip = {t: sum(1 for s in skips if s["file"] == TABLES_SPEC[t]["data_file"]) for t in frames}
    for t in frames:
        if n_facts[t] + n_skip[t] != n_rows[t]:
            raise ContractError(f"{t}: facts {n_facts[t]} + excluded {n_skip[t]} != source rows {n_rows[t]}")
        if n_facts[t] != int(frames[t].value_v.notna().sum()):
            raise ContractError(f"{t}: fact count != non-blank value cells")
    if len(fact) != sum(n_facts.values()):
        raise ContractError("fact table size != sum of per-file facts")
    if set(fact.reference_key) != set(reference.reference_key):
        raise ContractError("reference rows and fact references differ")
    if set(fact.sample_key) != set(sample.sample_key):
        raise ContractError("sample rows and fact samples differ")

    flag_counts: dict[str, dict[str, int]] = {t: {} for t in frames}
    for t, fl in zip(d.table, flags):
        for x in fl:
            flag_counts[t][x] = flag_counts[t].get(x, 0) + 1
    ref_both = sorted(set(frames["bio"].ref_norm[frames["bio"].value_v.notna()])
                      & set(frames["den"].ref_norm[frames["den"].value_v.notna()]))
    canon_groups: dict[str, set[str]] = {}
    for r in ref_strings:
        canon_groups.setdefault(_canon(r), set()).add(r)
    canon_collisions = sorted(sorted(v) for v in canon_groups.values() if len(v) > 1)
    changed_by_norm = sorted({r for t in frames for r in frames[t].ref if r != _norm(r)})
    raw_with_conv_ref = frames["bio"][(frames["bio"].rawconv == "raw data") & frames["bio"].convref.notna()]
    report = {
        "source_rows": n_rows,
        "facts": len(fact),
        "facts_by_file": n_facts,
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "bio_facts_by_unit": fact[fact.attribute == "benbio_biomass"].units_raw.value_counts().sort_index().to_dict(),
        "rows_excluded": n_skip,
        "samples": len(sample),
        "locations": len(loc),
        "methods": len(method),
        "method_status": method.method_status.value_counts().to_dict(),
        "references": len(reference),
        "references_in_both_files": len(ref_both),
        "reference_strings_equal_after_dropping_case_and_punctuation_but_not_merged": canon_collisions,
        "raw_reference_strings_changed_by_whitespace_or_period_normalisation": len(changed_by_norm),
        "composite_reference_strings": sum(1 for r in ref_strings if "; " in r),
        "reference_strings_resolved_part_by_part": {t: len(composite_by[t]) for t in composite_by},
        "citations": len(citation),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "study_screening": len(screening),
        "study_screening_with_reference": int(screening.reference_key.notna().sum()),
        "study_lists": link_report,
        "list_rows_dropped_as_fully_blank": blank_list_rows,
        "reference_resolution": {t: {k: v for k, v in how_by[t].items()} for t in how_by},
        "reference_strings_with_unresolved_parts": {t: unresolved_by[t] for t in unresolved_by},
        "bio_raw_data_rows_with_a_conversion_reference": len(raw_with_conv_ref),
        "bio_converted_rows_by_conversion_reference_state": {
            "NA": int(((frames["bio"].rawconv == "converted data") & frames["bio"].convref.isna()).sum()),
            "given": int(((frames["bio"].rawconv == "converted data") & frames["bio"].convref.notna()).sum()),
        },
        "rows_with_latitude_beyond_the_pole": KNOWN_LATITUDE_BEYOND_POLE,
        "rows_with_latitude_but_no_longitude": {
            t: int((frames[t].lon.isna() & frames[t].lat.notna()).sum()) for t in frames
        },
        "flag_counts": flag_counts,
    }
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the files were downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    raws = {t: _read(args.raw_dir / TABLES_SPEC[t]["data_file"], ",") for t in TABLES_SPEC}
    lists = {t: _read(args.raw_dir / TABLES_SPEC[t]["list_file"], "\t") for t in TABLES_SPEC}
    tables, skips, report = build_tables(raws, lists, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str)
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
