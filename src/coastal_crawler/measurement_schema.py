"""Entity schema and attribute catalogue for ExtractionLM extraction.

Tuned for a broad sweep over coastal-ecosystem field-measurement papers —
the same target population as ``FILTER_RELEVANCE_PROMPT`` in ``.env.example``.
``ATTRIBUTE_INFO_DICT`` below extracts exactly the variables enumerated in
that prompt, so anything that passed the relevance filter has a shot at
producing a real measurement here.

`EntitySchema`, `MeasurementEventSchema`, and `ATTRIBUTE_INFO_DICT` are
Python objects (not env vars) because `ExtractionLM` needs a real
pydantic model and a real dict, not a string. `MEAS_LM_ENTITY_IDENTIFICATION_PROMPT`
(prose describing what to identify) lives in `.env`/`Settings` instead,
alongside `filter_relevance_prompt`, since it's free text that's reasonable
to iterate on without a code change — see the drafted default in
`.env.example`. `build_direct_extraction_prompt()` below combines that
free-text prompt with ``MEASUREMENT_EVENT_PROMPT`` and ``ATTRIBUTE_INFO_DICT``
into the single combined prompt ``ExtractionLM`` needs, since
extraction is now one LLM call per document rather than a multi-step
pipeline with separate entity/attribute/event prompts.

- ``EntitySchema`` is the *ecosystem/site* identity — one record per
  distinct physical location, deduplicated across dates/treatments/sub-sites.
- ``MeasurementEventSchema`` is the *event* context (date, sub-location,
  sampling conditions) that distinguishes repeat measurements of the same
  site.
- ``DirectExtractionSchema`` is the flat per-record schema
  ``ExtractionLM`` actually extracts against: entity fields +
  event fields + attribute/value/units, combined via pydantic multiple
  inheritance (``EntitySchema``/``MeasurementEventSchema`` share no field
  names, so this merges cleanly without redeclaring anything).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Entity schema — one record per distinct coastal ecosystem/site
# ---------------------------------------------------------------------------


class EntitySchema(BaseModel):
    """One distinct sampled coastal ecosystem/site per paper."""

    name: str | None
    identifiers: str | None
    ecosystem_type: str | None
    location: str | None
    latitude: float | None
    longitude: float | None


# entity_identification_prompt is NOT defined here — it lives in .env as
# MEAS_LM_ENTITY_IDENTIFICATION_PROMPT (see build_measurement_adapter(),
# which reads it from Settings and passes it into build_direct_extraction_prompt()).
# See .env.example for a drafted prompt tuned to the EntitySchema fields above.


# ---------------------------------------------------------------------------
# Measurement event schema — date/sub-location/condition context that
# distinguishes repeat measurements of the same site
# ---------------------------------------------------------------------------


class MeasurementEventSchema(BaseModel):
    """Event-level fields that distinguish individual measurements within a site."""

    date: str | None
    sub_location: str | None
    additional_details: str | None


MEASUREMENT_EVENT_PROMPT = """EVENT FIELDS:
- date: The date the measurement was taken. Use one of the following formats depending on available precision:
  - Full date: "dd-mm-yyyy"
  - Month and year only: "mm-yyyy"
  - Season and year: "Spring yyyy", "Summer yyyy", "Fall yyyy", or "Winter yyyy"
  - Year only: "yyyy"
  Set to None if no date is stated for this measurement.
- sub_location: A more specific location within the site — a station, transect, plot, or zone code (e.g. "inlet zone", "T3", "upstream station") — if distinct from the site's own name/identifiers. Set to None if not applicable.
- additional_details: Any other distinguishing context not captured by date or sub_location — for example, treatment/condition (e.g. "high tide", "control", "fertilized", "post-storm"), depth, or sampling method. Keep this to one sentence or fewer. Set to None if not applicable.
"""


# ---------------------------------------------------------------------------
# Attribute catalogue — the same variables FILTER_RELEVANCE_PROMPT screens
# papers for, so anything that passes the filter has a shot at extraction.
# ---------------------------------------------------------------------------

ATTRIBUTE_INFO_DICT: dict[str, dict[str, Any]] = {
    "salinity": {
        "description": (
            "Salinity of the water column, typically measured in situ or from collected samples. "
            "This is NOT porewater salinity or soil salinity unless explicitly labeled as such."
        ),
        "units": ["psu", "ppt", "g/kg"],
    },
    "turbidity": {
        "description": (
            "Turbidity of the water, a measure of cloudiness caused by suspended particles, typically "
            "measured optically (e.g. nephelometric turbidity). This is NOT the same as suspended "
            "particulate matter concentration or light attenuation unless explicitly reported as turbidity."
        ),
        "units": ["NTU", "FTU", "FNU"],
    },
    "suspended_particulate_matter": {
        "description": (
            "Concentration of suspended particulate matter (SPM) in the water column, also referred to as "
            "total suspended solids (TSS) or suspended sediment concentration (SSC). This is NOT turbidity "
            "(an optical proxy) unless the paper explicitly converts one to the other."
        ),
        "units": ["mg/L", "g/L", "mg/m^3"],
    },
    "light_attenuation": {
        "description": (
            "Light attenuation coefficient (Kd) describing the rate of decrease of light intensity with "
            "depth in the water column. This is NOT Secchi depth unless explicitly converted; report the "
            "attenuation coefficient value itself."
        ),
        "units": ["m^-1", "per meter"],
    },
    "dissolved_oxygen": {
        "description": (
            "Dissolved oxygen (DO) concentration or saturation in the water column. This is NOT sediment "
            "oxygen demand (a flux, reported separately) and NOT biochemical/chemical oxygen demand (BOD/COD)."
        ),
        "units": ["mg/L", "mL/L", "μmol/L", "% saturation"],
    },
    "sediment_oxygen_demand": {
        "description": (
            "Sediment oxygen demand (SOD): the rate of oxygen consumption by sediments, typically measured "
            "via benthic chamber incubations. This is a flux, NOT a water-column dissolved oxygen "
            "concentration."
        ),
        "units": ["mmol O2/m^2/day", "g O2/m^2/day", "μmol O2/m^2/hr"],
    },
    "pco2": {
        "description": (
            "Partial pressure of CO2 (pCO2) in the water column or at the air-sea interface. This is NOT "
            "dissolved inorganic carbon (a concentration) or CO2 flux (a rate) unless explicitly stated as "
            "partial pressure."
        ),
        "units": ["µatm", "Pa", "ppm"],
    },
    "dissolved_inorganic_carbon": {
        "description": (
            "Dissolved inorganic carbon (DIC) concentration in the water column — the sum of dissolved "
            "CO2, bicarbonate, and carbonate. This is NOT dissolved organic carbon (DOC) or total "
            "alkalinity (a titration-based measure), even though the three co-occur in carbonate-system "
            "studies."
        ),
        "units": ["µmol/kg", "mmol/m^3", "mg/L"],
    },
    "total_alkalinity": {
        "description": (
            "Total alkalinity (TA) of the water column, a titration-based measure of buffering capacity. "
            "This is NOT dissolved inorganic carbon or pH, even though all are part of the carbonate "
            "system."
        ),
        "units": ["µmol/kg", "mmol/m^3", "meq/L"],
    },
    "co2_flux": {
        "description": (
            "Air-sea or sediment-water CO2 flux, i.e. the rate of CO2 exchange across an interface. This "
            "is a rate, NOT pCO2 or DIC (concentrations/pressures)."
        ),
        "units": ["mmol/m^2/day", "g C/m^2/yr", "µmol/m^2/s"],
    },
    "dissolved_organic_carbon": {
        "description": (
            "Dissolved organic carbon (DOC) concentration in the water column. This is NOT particulate "
            "organic carbon (POC) or dissolved inorganic carbon (DIC) unless explicitly labeled as such."
        ),
        "units": ["mg/L", "µmol/L", "mmol/m^3"],
    },
    "particulate_organic_carbon": {
        "description": (
            "Particulate organic carbon (POC) concentration in the water column or sediment. This is NOT "
            "dissolved organic carbon (DOC) or total suspended particulate matter (which may include "
            "inorganic material) unless explicitly labeled as organic carbon."
        ),
        "units": ["mg/L", "µmol/L", "mg/g", "% dry weight"],
    },
    "net_primary_production": {
        "description": (
            "Net primary production (NPP): gross primary production minus autotrophic respiration. This "
            "is NOT gross primary production (GPP) or community respiration unless explicitly labeled as "
            "net."
        ),
        "units": ["g C/m^2/day", "mmol C/m^2/day", "mg C/m^3/hr"],
    },
    "gross_primary_production": {
        "description": (
            "Gross primary production (GPP): total carbon fixation by primary producers before "
            "respiratory losses. This is NOT net primary production (NPP) unless explicitly labeled as "
            "gross."
        ),
        "units": ["g C/m^2/day", "mmol C/m^2/day", "mg C/m^3/hr"],
    },
    "chlorophyll": {
        "description": (
            "Chlorophyll-a (Chl-a) concentration in the water column, used as a proxy for phytoplankton "
            "biomass. This is NOT total chlorophyll, chlorophyll-b, chlorophyll-c, or pheophytin unless "
            "explicitly labeled as chlorophyll-a."
        ),
        "units": ["µg/L", "mg/L", "mg/m^3"],
    },
    "nitrate": {
        "description": (
            "Nitrate (NO3-) concentration in the water column. This is NOT total nitrogen, ammonium, or "
            "combined NO3-+NO2- unless explicitly labeled as nitrate alone."
        ),
        "units": ["µmol/L", "mg/L", "µg N/L"],
    },
    "ammonium": {
        "description": (
            "Ammonium (NH4+, sometimes reported as NH3) concentration in the water column. This is NOT "
            "total nitrogen or nitrate unless explicitly labeled as ammonium/ammonia alone."
        ),
        "units": ["µmol/L", "mg/L", "µg N/L"],
    },
    "total_nitrogen": {
        "description": (
            "Total nitrogen (TN) concentration in the water column, representing the sum of all nitrogen "
            "forms — both dissolved and particulate, including nitrate, nitrite, ammonium, and organic "
            "nitrogen. This must be the aggregate 'total nitrogen' value as explicitly reported in the "
            "source. This is NOT the same as individual nitrogen species (e.g. NO3- alone, NH4+ alone) "
            "unless they are explicitly labeled as total nitrogen."
        ),
        "units": ["µg/L", "mg/L", "µmol/L"],
    },
    "denitrification": {
        "description": (
            "Denitrification rate: the microbial reduction of nitrate/nitrite to N2 (or N2O), typically "
            "measured via incubation. This is a rate process, NOT a standing nitrogen concentration."
        ),
        "units": ["µmol N/m^2/hr", "mmol N/m^2/day", "µg N/kg/day"],
    },
    "nitrification": {
        "description": (
            "Nitrification rate: the microbial oxidation of ammonium to nitrite/nitrate, typically "
            "measured via incubation. This is a rate process, NOT a standing nitrogen concentration, and "
            "NOT denitrification (the reverse-direction process)."
        ),
        "units": ["µmol N/m^2/hr", "mmol N/m^2/day", "µg N/kg/day"],
    },
    "nitrogen_fixation": {
        "description": (
            "Nitrogen fixation rate: the biological conversion of N2 gas into bioavailable nitrogen, "
            "typically measured via acetylene reduction or 15N incubation assays. This is a rate process, "
            "distinct from denitrification and nitrification."
        ),
        "units": ["µmol N/m^2/hr", "nmol N2/m^2/hr", "mmol N/m^2/day"],
    },
    "phosphate": {
        "description": (
            "Phosphate (PO4^3-, also called soluble reactive phosphorus/SRP or dissolved reactive "
            "phosphorus/DRP) concentration in the water column. This is NOT total phosphorus unless "
            "explicitly labeled as such."
        ),
        "units": ["µmol/L", "mg/L", "µg P/L"],
    },
    "silicate": {
        "description": (
            "Silicate (SiO4^4-/Si(OH)4, also called dissolved silica or reactive silicate) concentration "
            "in the water column. This is a distinct nutrient from nitrogen and phosphorus species."
        ),
        "units": ["µmol/L", "mg/L", "µg Si/L"],
    },
}


# ---------------------------------------------------------------------------
# Unit normalization/conversion — scripts/build_warehouse.py's tiered unit
# conversion (see notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md
# "Approach" for the full design writeup). Live-queried raw `units` strings
# across all 23 attributes have ~600 distinct spellings, not the handful
# ATTRIBUTE_INFO_DICT lists — that list is prompt guidance for the LLM,
# never enforced at extraction time. Two tiers are implemented here, both
# zero domain/substance risk:
#
#   Tier 1 (normalize_unit_string): pure text normalization — Unicode
#   super/subscripts to ASCII, µ/μ unified, whitespace, "liter"/"day"/"hr"/
#   "yr" spelled out, `X^{-1}`/`X⁻¹`/`X-1` exponent forms all folded into
#   one slash-based form (`mg L⁻¹` -> `mg/L`), bare/prefixed Molar (`µM`)
#   expanded to `µmol/L`. No numeric conversion — just collapses spelling
#   variants of the same unit onto one string.
#
#   Tier 2 (split_numerator_prefix / convert_to_canonical): SI-prefix
#   scaling. Fires only when two normalized unit strings share the exact
#   same denominator chain (everything after the first `/`) AND their
#   numerator tokens reduce to the same recognized base name (mol/g/atm/eq)
#   after stripping a known metric prefix — e.g. `mmol/kg` -> `µmol/kg` is
#   safe (x1000) because "mol" is a real, dimension-preserving base and the
#   scaling doesn't depend on what's being measured. `µmol/L` is never
#   compared against `mg/L` this way (different base entirely — would need
#   a molar mass, which is genuinely substance-specific).
#
#   Everything else (molar<->mass conversions, volume<->mass-basis
#   conversions needing density, `%`/`% saturation`->concentration, non-SI
#   unit families) is Tier 3: left unconverted, so
#   scripts/build_warehouse.py skips and logs that row rather than
#   guessing at a domain-specific factor.
#
#   One named exception, decided with the user rather than assumed: salinity
#   (`psu`/`ppt`/`g/kg`/`‰`) is treated as numerically interchangeable
#   (factor 1.0) per standard oceanographic convention for seawater — see
#   SALINITY_EQUIVALENT_UNITS below and the build note.
# ---------------------------------------------------------------------------

_SUPERSCRIPT_TABLE = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "−": "-", "‑": "-",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
    "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₋": "-",
})

_EXPONENT_TOKEN_RE = re.compile(r"([A-Za-zµ]+)\^-(\d+)")


def normalize_unit_string(raw: str | None) -> str | None:
    """Tier 1: collapse spelling/formatting variants of a raw `units` string
    onto one normalized form. Pure text mechanics — no numeric conversion,
    no domain knowledge. Returns None for None/empty input."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    s = s.replace("μ", "µ")  # Greek mu (U+03BC) -> micro sign (U+00B5)
    s = s.replace(" ", " ").replace("\xa0", " ")
    s = s.translate(_SUPERSCRIPT_TABLE)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bliters?\b", "L", s, flags=re.IGNORECASE)
    s = re.sub(r"\blitres?\b", "L", s, flags=re.IGNORECASE)
    s = re.sub(r"\bday\b", "d", s, flags=re.IGNORECASE)
    s = re.sub(r"\bhr\b", "h", s, flags=re.IGNORECASE)
    s = re.sub(r"\byr\b", "y", s, flags=re.IGNORECASE)
    s = s.replace("^{", "^").replace("}", "")
    # Bare ASCII "u" used as a micro-sign substitute (e.g. "umol/L").
    s = re.sub(r"(?<![A-Za-z])u(?=mol\b|g\b|L\b|M\b|atom\b)", "µ", s)
    # Bare/prefixed Molar symbol ("µM", "nM", "M") -> "<prefix>mol/L".
    s = re.sub(r"\b(n|p|µ|m|c)?M\b", lambda m: (m.group(1) or "") + "mol/L", s)
    s = re.sub(r"\bper meter\b", "/m", s, flags=re.IGNORECASE)
    # "<unit letter>-<digits>" -> "<unit letter>^-<digits>" (negative
    # exponent), not a compound word like "µg-atom" (no trailing digit).
    s = re.sub(r"([A-Za-zµ])-(\d+)\b", r"\1^-\2", s)

    def _exponent_to_slash(m: re.Match[str]) -> str:
        unit, n = m.group(1), m.group(2)
        return f"/{unit}" if n == "1" else f"/{unit}^{n}"

    s = _EXPONENT_TOKEN_RE.sub(_exponent_to_slash, s)
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_SI_PREFIX_SCALE: dict[str, float] = {
    "p": 1e-12, "n": 1e-9, "µ": 1e-6, "m": 1e-3, "c": 1e-2, "d": 1e-1,
    "": 1.0, "da": 1e1, "h": 1e2, "k": 1e3, "M": 1e6, "G": 1e9,
}
_SI_BASE_UNIT_WHITELIST = ("mol", "g", "atm", "eq")
_SI_PREFIXES_BY_LEN = sorted(_SI_PREFIX_SCALE, key=len, reverse=True)


def split_numerator_prefix(token: str) -> tuple[float, str] | None:
    """`'mmol'` -> `(1e-3, 'mol')`, `'µg'` -> `(1e-6, 'g')`. Restricted to a
    small base-unit whitelist so this never fires on a token that merely
    happens to start with a prefix-like letter (e.g. "meq"'s "m" is a real
    SI milli- prefix on base "eq"; "mol" itself must not be misread as
    prefix "m" + base "ol"). Returns None if no whitelisted base matches."""
    for prefix in _SI_PREFIXES_BY_LEN:
        if token.startswith(prefix):
            base = token[len(prefix):]
            if base in _SI_BASE_UNIT_WHITELIST:
                return _SI_PREFIX_SCALE[prefix], base
    return None


# Attribute -> canonical unit, taken as ATTRIBUTE_INFO_DICT's first listed
# unit (done_when item 2's "one canonical unit per attribute"), normalized
# once here so every comparison is normalized-vs-normalized.
def _canonical_unit(raw: str) -> str:
    normalized = normalize_unit_string(raw)
    assert normalized is not None, f"empty canonical unit string: {raw!r}"
    return normalized


CANONICAL_UNITS: dict[str, str] = {
    attr: _canonical_unit(info["units"][0])
    for attr, info in ATTRIBUTE_INFO_DICT.items()
    if info.get("units")
}

# Decided with the user (2026-08-12, see the build note): salinity's 3
# ATTRIBUTE_INFO_DICT units plus the equally-common '‰' (per-mille, the
# same notation as "ppt" by definition) are numerically interchangeable
# for seawater. This is the one substance-specific tier-2-adjacent
# conversion this build makes, and it's explicit rather than folded into
# the generic SI-prefix logic above.
SALINITY_EQUIVALENT_UNITS: frozenset[str] = frozenset(
    _canonical_unit(u) for u in ("psu", "PSU", "ppt", "g/kg", "‰")
)


def convert_to_canonical(attribute: str, raw_units: str | None) -> tuple[str, float] | None:
    """Return `(canonical_units, factor)` such that
    `quantity_canonical = quantity_raw_float * factor`, or None if
    `raw_units` can't be converted (unknown attribute, no canonical unit
    on record, or a genuine Tier 3 case) — the caller skips and logs.
    """
    canonical = CANONICAL_UNITS.get(attribute)
    if canonical is None:
        return None
    normalized = normalize_unit_string(raw_units)
    if normalized is None:
        return None
    if attribute == "salinity" and normalized in SALINITY_EQUIVALENT_UNITS:
        return canonical, 1.0
    if normalized == canonical:
        return canonical, 1.0
    c_parts = normalized.split("/", 1)
    k_parts = canonical.split("/", 1)
    if len(c_parts) != 2 or len(k_parts) != 2 or c_parts[1] != k_parts[1]:
        return None
    candidate_split = split_numerator_prefix(c_parts[0])
    canonical_split = split_numerator_prefix(k_parts[0])
    if candidate_split is None or canonical_split is None:
        return None
    candidate_scale, candidate_base = candidate_split
    canonical_scale, canonical_base = canonical_split
    if candidate_base != canonical_base:
        return None
    return canonical, candidate_scale / canonical_scale


# ---------------------------------------------------------------------------
# Direct extraction schema/prompt — ExtractionLM (single LLM call
# per document, extracting entity + event + attribute/value/units together).
# ---------------------------------------------------------------------------


class DirectExtractionSchema(EntitySchema, MeasurementEventSchema):
    """Flat per-record schema for ExtractionLM.

    Combines entity fields, event fields, and the measurement itself
    (attribute/value/units) into one item per (entity, event, attribute)
    record — the shape ExtractionLM's single extraction call
    produces one JSON item per.
    """

    attribute: str
    value: str | None
    units: str | None


def _format_attribute_list() -> str:
    lines = []
    for idx, (name, info) in enumerate(ATTRIBUTE_INFO_DICT.items(), 1):
        units = ", ".join(info.get("units", []))
        units_part = f" Units: {units}." if units else ""
        lines.append(f"{idx}. {name} — {info['description']}{units_part}")
    return "\n".join(lines)


def build_direct_extraction_prompt(entity_identification_prompt: str) -> str:
    """Combine entity/event/attribute guidance into ExtractionLM's
    dataset-specific direct_extraction_prompt.

    ``entity_identification_prompt`` is the free-text prompt from
    ``Settings.meas_lm_entity_identification_prompt`` (``.env``). This
    appends the event-field prompt and a numbered attribute list built from
    ``ATTRIBUTE_INFO_DICT`` — reusing the existing descriptions/units rather
    than duplicating them into a second hand-written prompt.
    """
    return (
        f"{entity_identification_prompt}\n\n"
        f"{MEASUREMENT_EVENT_PROMPT}\n\n"
        "ATTRIBUTES TO EXTRACT:\n"
        "For each entity and measurement event, extract a value for any of the "
        f"following attributes if directly measured and reported:\n\n{_format_attribute_list()}\n"
    )
