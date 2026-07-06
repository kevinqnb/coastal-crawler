"""Entity schema and attribute catalogue for MeasurementLM extraction.

Tuned for a broad sweep over coastal-ecosystem field-measurement papers —
the same target population as ``FILTER_RELEVANCE_PROMPT`` in ``.env.example``.
``ATTRIBUTE_INFO_DICT`` below extracts exactly the variables enumerated in
that prompt, so anything that passed the relevance filter has a shot at
producing a real measurement here.

`EntitySchema`, `MeasurementEventSchema`, and `ATTRIBUTE_INFO_DICT` are
Python objects (not env vars) because `MeasurementLM` needs a real pydantic
model and a real dict, not a string. `MEAS_LM_ENTITY_IDENTIFICATION_PROMPT`
(prose describing what to identify) lives in `.env`/`Settings` instead,
alongside `filter_relevance_prompt`, since it's free text that's reasonable
to iterate on without a code change — see the drafted default in
`.env.example`.

See ``../scholarlm/experiments/configs/pond.py`` (sibling repo) for the
worked example this is modeled on (a different dataset — aquatic
ecosystems). Two things carried over from it:

- ``EntitySchema`` is the *ecosystem/site* identity — one record per
  distinct physical location, deduplicated across dates/treatments/sub-sites.
- ``MeasurementEventSchema`` is the *event* context (date, sub-location,
  sampling conditions) that distinguishes repeat measurements of the same
  site — this is what ``build_scholarlm_adapter()`` passes to
  ``MeasurementLM`` as ``measurement_event_schema``/``measurement_event_prompt``
  so date/sub-location actually get captured per measurement, not just
  once per site.
"""

from __future__ import annotations

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
# MEAS_LM_ENTITY_IDENTIFICATION_PROMPT (see build_scholarlm_adapter(), which
# reads it from Settings). See .env.example for a drafted prompt tuned to
# the EntitySchema fields above.


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
