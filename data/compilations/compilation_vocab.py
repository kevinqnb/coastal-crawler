"""Common vocabulary for the compilations layer: measurement types and subfields,
habitat / setting / salinity-zone / modification-class labels, label maps, and the
checks that tie a compilation's own attributes and labels to them.

Raw attributes, raw labels and ``units_raw`` are never replaced, only mapped. Two
mechanisms, both fail-loud:

* **Attribute declarations** (``ATTRIBUTES``): every fact attribute is declared with
  its measurement type, the subfields it may carry and the unit strings it may carry.
  A fact outside its declaration raises. A quantity with no standard type is
  ``other`` and needs a ``note``.
* **Label maps** (``compilation_label_maps.csv`` next to this file): raw label ->
  standard label, per (source, raw column, mapped column). Within a declared triple
  every distinct raw label needs a row (blank ``mapped_label`` = no equivalent, never
  a guess); an unlisted label raises. ``habitat`` must be declared for every source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


class VocabError(Exception):
    """A fact or label violates the common vocabulary."""


# ------------------------------------------------------------------ measurement types

# code -> (name, domain, kind)
MEASUREMENT_TYPES: dict[str, tuple[str, str, str]] = {
    "G1": ("Sediment texture", "geological", "state"),
    "G2": ("Accretion and carbon burial", "geological", "rate"),
    "C1": ("Sediment organic matter", "chemical", "state"),
    "C2": ("Porewater", "chemical", "state"),
    "C3": ("Sediment-water nutrient fluxes", "chemical", "rate"),
    "C4": ("Benthic nitrogen removal", "chemical", "rate"),
    "C5": ("Water-column nitrogen transformations", "chemical", "rate"),
    "C6": ("Sinking particle flux", "chemical", "rate"),
    "C7": ("Benthic N2 fixation", "chemical", "rate"),
    "C8": ("Greenhouse-gas fluxes", "chemical", "rate"),
    "B1": ("Benthic microalgal biomass", "biological", "state"),
    "B2": ("Foundation-organism biomass", "biological", "state"),
    "B3": ("Foundation-organism density and canopy", "biological", "state"),
    "B4": ("Benthic cover by group", "biological", "state"),
    "B5": ("Benthic metabolism", "biological", "rate"),
    "B6": ("Macrophyte production", "biological", "rate"),
    "B7": ("Calcification", "biological", "rate"),
    "B8": ("Pelagic primary production", "biological", "rate"),
    "B9": ("Pelagic respiration and net community production", "biological", "rate"),
    "B10": ("Macrofauna", "biological", "state"),
    "P1": ("Light attenuation", "physical", "state"),
    # Water-medium in-situ values reported beside a rate (INTEGRATION_PLAN 1.2). Kept here so
    # they can be set beside, or replaced by, the joined WOD/GLODAP layers later.
    "J1": ("Joined fields", "chemical", "state"),
}
OTHER = "other"

# Standard subfields per type. A subfield not listed here goes in EXTENSION_SUBFIELDS.
STANDARD_SUBFIELDS: dict[str, frozenset[str]] = {
    "G1": frozenset({"median_grain_size", "mean_grain_size", "mud_content", "sand_content",
                     "silt_content", "clay_content", "gravel_content", "sorting",
                     "textural_class"}),
    "G2": frozenset({"vertical_accretion", "surface_elevation_change", "mass_accumulation_rate",
                     "carbon_burial", "dating_method"}),
    "C1": frozenset({"organic_carbon", "loi_organic_matter", "total_nitrogen", "c_n_ratio",
                     "dry_bulk_density", "porosity", "water_content", "carbonate",
                     "carbon_stock"}),
    "C2": frozenset({"sulfide", "ammonium", "phosphate", "porewater_salinity",
                     "o2_penetration_depth", "redox_potential"}),
    "C3": frozenset({"ammonium_flux", "nitrate_nitrite_flux", "phosphate_flux", "din_flux",
                     "silicate_flux"}),
    "C4": frozenset({"denitrification", "anammox", "dnra", "denitrification_efficiency"}),
    "C5": frozenset({"ammonia_oxidation", "nitrite_oxidation", "n2_fixation",
                     "denitrification_anammox"}),
    "C6": frozenset({"poc_flux", "pon_flux", "total_mass_flux", "biogenic_silica_carbonate_flux",
                     "trap_depth"}),
    "C7": frozenset({"n2_fixation"}),
    "C8": frozenset({"ch4_flux", "n2o_flux", "co2_flux"}),
    "B1": frozenset({"sediment_chlorophyll_a", "pheopigments", "microalgal_carbon_biomass"}),
    "B2": frozenset({"above_ground_biomass", "below_ground_biomass", "total_biomass",
                     "root_shoot_ratio", "standing_dead_litter_biomass",
                     "kelp_macroalgal_biomass", "bivalve_biomass"}),
    "B3": frozenset({"shoot_or_stem_density", "tree_density", "kelp_density", "bivalve_density",
                     "canopy_height", "percent_cover", "basal_area", "stem_diameter",
                     "leaf_area_index", "reef_height", "dominant_species"}),
    "B4": frozenset({"live_hard_coral_cover", "macroalgal_cover", "turf_cca_invertebrate_cover",
                     "bare_substrate_cover", "rugosity", "survey_method"}),
    "B5": frozenset({"total_o2_uptake", "diffusive_o2_uptake", "gross_primary_production",
                     "net_ecosystem_production", "dic_flux", "production_respiration_ratio"}),
    "B6": frozenset({"above_ground_npp", "below_ground_npp", "litterfall", "wood_production",
                     "biomass_turnover_rate", "seagrass_seaweed_npp"}),
    "B7": frozenset({"net_ecosystem_calcification", "carbonate_budget"}),
    "B8": frozenset({"primary_production_per_volume", "depth_integrated_primary_production",
                     "chlorophyll_specific_production"}),
    "B9": frozenset({"community_respiration", "net_community_production"}),
    "B10": frozenset({"total_density", "total_biomass", "species_richness", "sieve_mesh_size"}),
    "P1": frozenset({"kd", "secchi_depth", "turbidity", "euphotic_depth",
                     "suspended_particulate_matter"}),
    "J1": frozenset({"nitrate", "nitrite", "ammonium", "phosphate", "silicate",
                     "nitrate_nitrite", "din", "ph", "dissolved_oxygen", "chlorophyll_a"}),
}

# Subfields beyond the standard ones: (type, subfield) -> reason.
EXTENSION_SUBFIELDS: dict[tuple[str, str], str] = {
    ("C1", "total_carbon"): "CCN fraction_carbon where carbon_type = total carbon (standard list has organic carbon only)",
    ("C1", "carbon_unspecified"): "CCN fraction_carbon where carbon_type is unknown or 'not specified'",
    ("B5", "advective_o2_uptake"): "SCOC AOU (advective); standard list has total and diffusive only",
    ("B3", "leaf_density"): "Foster leaf density (leaves m-2); standard B3 lists shoot/stem density",
    ("B6", "total_npp"): "Foster total production (above + below, may be derived upstream)",
    ("B6", "shoot_production"): "Foster per-shoot-compartment production",
    ("B6", "leaf_production"): "Foster leaf production",
    ("C3", "nitrite_flux"): "Boynton and Ray report nitrite flux separately; standard C3 has nitrate+nitrite only",
    ("C3", "nitrate_flux"): "Boynton and Ray report nitrate flux separately; standard C3 has nitrate+nitrite only",
    ("C4", "total_n2_production"): "Chang total N2 production (denitrification + anammox, column P14); not one of the standard C4 processes",
    ("C1", "soil_carbon_density"): "USGS wetlands soil carbon density (g/cm3); standard C1 has carbon_stock and dry_bulk_density, not a carbon density",
    ("C1", "total_inorganic_carbon"): "MOSAIC total inorganic carbon; standard C1 has carbonate (CaCO3) only",
}


def type_subfields(code: str) -> frozenset[str]:
    ext = {s for (t, s) in EXTENSION_SUBFIELDS if t == code}
    return STANDARD_SUBFIELDS.get(code, frozenset()) | ext


@dataclass(frozen=True)
class AttrDecl:
    """How a compilation's own attribute maps onto the standard types."""

    measurement_type: str  # key of MEASUREMENT_TYPES, or OTHER
    subfields: frozenset[str | None]  # allowed values of fact.subfield (None = no subfield)
    units: frozenset[str]  # allowed fact.units_raw strings, exactly as the source reports
    note: str | None = None  # required for OTHER


def _d(t: str, subs: set, units: set[str], note: str | None = None) -> AttrDecl:
    return AttrDecl(t, frozenset(subs), frozenset(units), note)


# attribute -> declaration. Attribute names are global (compilation-prefixed where a name
# could clash); add one entry per attribute a new compilation emits.
ATTRIBUTES: dict[str, AttrDecl] = {
    # Coastal Carbon Library
    "dry_bulk_density": _d("C1", {"dry_bulk_density"}, {"gramsPerCubicCentimeter"}),
    "fraction_organic_matter": _d("C1", {"loi_organic_matter"}, {"dimensionless"}),
    "fraction_carbon": _d(
        "C1", {"organic_carbon", "total_carbon", "carbon_unspecified"}, {"dimensionless"}
    ),
    "pb210_cic_accretion_rate": _d("G2", {"vertical_accretion"}, {"centimeterPerYear"}),
    # SCOC
    "scoc_tou": _d("B5", {"total_o2_uptake"}, {"mmol O2 m-2 d-1"}),
    "scoc_dou": _d("B5", {"diffusive_o2_uptake"}, {"mmol O2 m-2 d-1"}),
    "scoc_aou": _d("B5", {"advective_o2_uptake"}, {"mmol O2 m-2 d-1"}),
    "scoc_unspecified": _d("B5", {None}, {"mmol O2 m-2 d-1"}),
    "scoc_median_grain_size": _d("G1", {"median_grain_size"}, {"um"}),
    # Foster seagrass (g DW m-2 ... spellings) and Strydom seagrass (PANGAEA spellings g/m**2 ...)
    "seagrass_biomass_above": _d("B2", {"above_ground_biomass"}, {"g DW m-2", "g/m**2"}),
    "seagrass_biomass_below": _d("B2", {"below_ground_biomass"}, {"g DW m-2", "g/m**2"}),
    "seagrass_biomass_total": _d("B2", {"total_biomass"}, {"g DW m-2", "g/m**2"}),
    "seagrass_shoot_density": _d("B3", {"shoot_or_stem_density"}, {"shoots m-2", "#/m**2"}),
    "seagrass_leaf_density": _d("B3", {"leaf_density"}, {"leaves m-2", "#/m**2"}),
    "seagrass_percent_cover": _d("B3", {"percent_cover"}, {"%"}),
    "seagrass_production_above": _d("B6", {"above_ground_npp"}, {"g DW m-2 d-1", "g/m**2/day"}),
    "seagrass_production_below": _d("B6", {"below_ground_npp"}, {"g DW m-2 d-1", "g/m**2/day"}),
    "seagrass_production_total": _d("B6", {"total_npp"}, {"g DW m-2 d-1", "g/m**2/day"}),
    "seagrass_production_shoot": _d("B6", {"shoot_production"}, {"g DW m-2 d-1", "g/m**2/day"}),
    "seagrass_production_leaf": _d("B6", {"leaf_production"}, {"g DW m-2 d-1", "g/m**2/day"}),
    # BenBioDen (stratma-bio): total biomass (four unit spellings, one per source row) and total density
    "benbio_biomass": _d("B10", {"total_biomass"}, {"g WM/ m2", "g DM/ m2", "g C/ m2", "g AFDM/ m2"}),
    "benden_density": _d("B10", {"total_density"}, {"ind/ m2"}),
    # USGS tidal saline wetlands (Feher et al.): one attribute per workbook sheet
    "usgs_canopy_height": _d("B3", {"canopy_height"}, {"m"}),
    "usgs_aboveground_biomass": _d("B2", {"above_ground_biomass"}, {"t/ha"}),
    "usgs_productivity": _d("B6", {"litterfall", "above_ground_npp", None}, {"t/ha/yr"}),
    "usgs_soil_c_density": _d("C1", {"soil_carbon_density"}, {"g/cm3"}),
    "usgs_soil_c_accum": _d("G2", {"carbon_burial"}, {"g/m2/yr"}),
    # Rosentreter et al. coastal GHG database: estuary (water-air) vs vegetated-coast fluxes are separate
    # attributes because the exchange interface differs; units are the sheet header spellings
    "ghg_co2_estuary": _d("C8", {"co2_flux"}, {"g CO2 m2 d-1"}),
    "ghg_ch4_estuary": _d("C8", {"ch4_flux"}, {"mg CH4 m2 d-1"}),
    "ghg_n2o_estuary": _d("C8", {"n2o_flux"}, {"mg N2O m2 d-1"}),
    "ghg_co2_vegetation": _d("C8", {"co2_flux"}, {"g C m2 yr-1"}),
    "ghg_ch4_vegetation": _d("C8", {"ch4_flux"}, {"mg CH4 m2 d-1"}),
    "ghg_n2o_vegetation": _d("C8", {"n2o_flux"}, {"mg N2O m2 d-1"}),
    # Chang et al. benthic N loss (units: header spellings, thin/no-break spaces as plain spaces)
    "chang_denitrification": _d("C4", {"denitrification"}, {"μmol N m−2 h−1"}),
    "chang_anammox": _d("C4", {"anammox"}, {"μmol N m−2 h−1"}),
    "chang_total_n2_production": _d("C4", {"total_n2_production"}, {"μmol N m−2 h−1"}),
    "chang_organic_carbon": _d("C1", {"organic_carbon"}, {"%"}),
    "chang_c_n_ratio": _d("C1", {"c_n_ratio"}, {"mol:mol"}),
    "chang_o2_penetration_depth": _d("C2", {"o2_penetration_depth"}, {"mm"}),
    # J1 joined fields (water-medium in-situ values); shared across compilations, each declares its own units
    "water_nitrate": _d("J1", {"nitrate"}, {"μM"}),
    "water_ammonium": _d("J1", {"ammonium"}, {"μM"}),
    "water_o2": _d("J1", {"dissolved_oxygen"}, {"μM"}),
}

# Ray and Fulweiler oyster meta-analysis: one attribute per solute and per sheet. The workbook has no units; the paper
# (Nature Sustainability 4, 261-269, Methods) says the authors converted all sediment fluxes to umol m-2 h-1 and all
# oyster fluxes to umol per individual and hour (its Table 1 shows N2O and CH4 as nmol, ie x1000 of the file's values).
_RAY_TYPES = {  # solute -> (type, subfield)
    "n2": ("C4", "denitrification"), "nh4": ("C3", "ammonium_flux"), "n2o": ("C8", "n2o_flux"),
    "co2": ("C8", "co2_flux"), "po4": ("C3", "phosphate_flux"), "ch4": ("C8", "ch4_flux"),
    "si": ("C3", "silicate_flux"), "nox": ("C3", "nitrate_nitrite_flux"), "no3": ("C3", "nitrate_flux"),
    "no2": ("C3", "nitrite_flux"),
}
for _sol, (_t, _sub) in _RAY_TYPES.items():
    ATTRIBUTES[f"ray_sediment_{_sol}"] = _d(_t, {_sub}, {"µmol m−2 h−1"})
    if _sol not in ("co2", "si"):  # the oyster sheet has no CO2 or Si columns
        ATTRIBUTES[f"ray_oyster_{_sol}"] = _d(_t, {_sub}, {"µmol ind−1 h−1"})


# --- group 3 (boynton-flux, fulweiler-nfix, pessarrodona-seaweed, davis-calcification) -------------------------
def _j1(attr: str, subfield: str, units: set[str]) -> None:
    """Declare a shared J1 attribute, or add unit strings to it when another compilation declared it first."""
    old = ATTRIBUTES.get(attr)
    if old is not None:
        if old.measurement_type != "J1" or old.subfields != frozenset({subfield}):
            raise VocabError(f"{attr}: already declared differently")
        units = set(old.units) | units
    ATTRIBUTES[attr] = _d("J1", {subfield}, units)


# Boynton World Flux Data Set: units are the header spellings (ASCII "u"); the element basis of each flux is in Table 2
# of the workbook (O2, N, P, DSi), not in the header. pH has no header unit: "unitless" is Table 2's.
ATTRIBUTES["boynton_soc"] = _d("B5", {"total_o2_uptake"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_nh4_flux"] = _d("C3", {"ammonium_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_po4_flux"] = _d("C3", {"phosphate_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_no2_flux"] = _d("C3", {"nitrite_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_no3_flux"] = _d("C3", {"nitrate_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_no23_flux"] = _d("C3", {"nitrate_nitrite_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_dsi_flux"] = _d("C3", {"silicate_flux"}, {"umol/m2/hr"})
ATTRIBUTES["boynton_water_respiration"] = _d("B9", {"community_respiration"}, {"gO2/m2/day"})
ATTRIBUTES["boynton_sediment_chl_a"] = _d("B1", {"sediment_chlorophyll_a"}, {"mg/m2"})
ATTRIBUTES["boynton_poc"] = _d("C1", {"organic_carbon"}, {"%DW"})
ATTRIBUTES["boynton_pon"] = _d(OTHER, {None}, {"%DW"}, "particulate organic nitrogen, % of dry weight (Boynton Table 2: usually top 1 cm); not total N")
ATTRIBUTES["boynton_pp"] = _d(OTHER, {None}, {"%DW"}, "particulate phosphorus, % of dry weight (Boynton Table 2: usually top 1 cm)")
_j1("water_ammonium", "ammonium", {"uM"})
_j1("water_phosphate", "phosphate", {"uM"})
_j1("water_nitrate", "nitrate", {"uM"})
_j1("water_nitrate_nitrite", "nitrate_nitrite", {"uM"})
_j1("water_silicate", "silicate", {"uM"})
_j1("water_ph", "ph", {"unitless"})

# Fulweiler et al. N2 fixation (EDI edi.1759.2): rates are the source's converted molar values (four unit spellings, the same
# for the water-column and benthic attributes); J1 units are the verbatim ancillary-file unit cells (all ASCII).
_FULWEILER_RATE_UNITS = {"umol-n m-2 h-1", "umol-n l-1 h-1", "umol-n g-1 h-1", "umol-n m-3 h-1"}
ATTRIBUTES["fulweiler_n2fix_water"] = _d("C5", {"n2_fixation"}, _FULWEILER_RATE_UNITS)
ATTRIBUTES["fulweiler_n2fix_benthic"] = _d("C7", {"n2_fixation"}, _FULWEILER_RATE_UNITS)
_j1("water_ammonium", "ammonium", {"mg l-1", "mg-n l-1", "nh4-n mg l-1", "nmol l-1", "ug l-1", "ug-n l-1", "um", "umol l-1", "umol-n l-1"})
_j1("water_nitrate", "nitrate", {"g-no3 m-3", "mg l-1", "mg-n l-1", "nmol l-1", "no3-n mg l-1", "ug l-1", "ug-n l-1", "um", "umol", "umol l-1", "umol-n l-1"})
_j1("water_nitrite", "nitrite", {"um", "umol l-1", "umol-n l-1"})
_j1("water_nitrate_nitrite", "nitrate_nitrite", {"mg l-1", "mg m-3", "mg-n l-1", "mmol m-2", "nm", "ug l-1", "ug-n l-1", "um", "umol kg-1", "umol l-1"})
_j1("water_din", "din", {"ug l-1", "umol l-1"})
_j1("water_phosphate", "phosphate", {"mg l-1", "mg m-3", "mg-p l-1", "mmol m-2", "nm", "ug l-1", "ug-p l-1", "um", "umol", "umol kg-1", "umol l-1", "umol-p l-1", "umol-po4 l-1"})
_j1("water_o2", "dissolved_oxygen", {"%", "% sat", "mg l-1", "mg o2/l", "ml l-1", "um", "umol kg-1", "umol l-1"})
_j1("water_chl_a", "chlorophyll_a", {"fluor mg m-3", "mg m-2", "mg m-3", "mg-chla m-3", "ug cm-2", "ug l-1", "ug ml-1", "ug-chla l-1"})

# Pessarrodona et al. seaweed NPP: the source's harmonised value (column Avg_NPP_kg_C_m2_y); the unit is the header's.
ATTRIBUTES["pessarrodona_npp"] = _d("B6", {"seagrass_seaweed_npp"}, {"kg C m-2 y-1"})

# Davis et al. reef calcification and production (SEANOE 80022): units are the sheet's header spellings. The sheet does not say
# whether Pnet is on an O2 or a C basis.
ATTRIBUTES["davis_gnet"] = _d("B7", {"net_ecosystem_calcification"}, {"mmol m-2 d-1"})
ATTRIBUTES["davis_pnet"] = _d("B5", {"net_ecosystem_production"}, {"mmol m-2 d-1"})
ATTRIBUTES["davis_coral_cover"] = _d("B4", {"live_hard_coral_cover"}, {"%"})
ATTRIBUTES["davis_coralline_cover"] = _d("B4", {"turf_cca_invertebrate_cover"}, {"%"})


def check_attribute_decls() -> None:
    """Self-consistency of the declarations above (run by the tests)."""
    for attr, d in ATTRIBUTES.items():
        if d.measurement_type == OTHER:
            if not d.note:
                raise VocabError(f"{attr}: measurement_type 'other' needs a note")
            continue
        if d.measurement_type not in MEASUREMENT_TYPES:
            raise VocabError(f"{attr}: unknown measurement type {d.measurement_type!r}")
        legal = type_subfields(d.measurement_type)
        bad = {s for s in d.subfields if s is not None} - legal
        if bad:
            raise VocabError(f"{attr}: subfields {sorted(bad)} not in {d.measurement_type}'s "
                             f"standard subfields or EXTENSION_SUBFIELDS")
        if not d.units:
            raise VocabError(f"{attr}: no units declared")
    for (t, s) in EXTENSION_SUBFIELDS:
        if t not in MEASUREMENT_TYPES:
            raise VocabError(f"extension subfield for unknown type {t!r}")
        if s in STANDARD_SUBFIELDS[t]:
            raise VocabError(f"extension {t}.{s} duplicates a standard subfield")


def check_facts(fact: pd.DataFrame) -> None:
    """Every fact's attribute, type, subfield and units must match its declaration."""
    undeclared = set(fact["attribute"]) - set(ATTRIBUTES)
    if undeclared:
        raise VocabError(f"attributes with no declaration in ATTRIBUTES: {sorted(undeclared)}")
    for attr, g in fact.groupby("attribute", sort=True):
        d = ATTRIBUTES[str(attr)]
        bad_t = set(g["measurement_type"]) - {d.measurement_type}
        if bad_t:
            raise VocabError(f"{attr}: measurement_type {sorted(bad_t)} != declared {d.measurement_type}")
        subs = {None if pd.isna(s) else s for s in g["subfield"]}
        if subs - d.subfields:
            raise VocabError(f"{attr}: subfields {sorted(map(str, subs - d.subfields))} not declared "
                             f"(allowed {sorted(map(str, d.subfields))})")
        bad_u = set(g["units_raw"]) - d.units
        if bad_u:
            raise VocabError(f"{attr}: units_raw {sorted(bad_u)} not declared (allowed {sorted(d.units)})")


# ------------------------------------------------------------------ flags and skip reasons

# Fact flags (``fact.flags``) and skip-report reasons (``skip_report.jsonl``) are closed
# registries: a flag or reason not listed here raises (the builder checks every fact's flags;
# every extract.py checks its skip reasons). Add a name here, with its meaning, when a source
# needs it; the per-source README defines how that source applies it. Flags never change or
# drop a value.
FLAGS: dict[str, str] = {
    # used by the built compilations
    "negative_value": "value < 0 where the quantity cannot be negative",
    "zero_value": "value is exactly 0",
    "fraction_gt_1": "a fraction-valued quantity above 1",
    "dbd_gt_max": "dry bulk density above the extract's --max-dry-bulk-density",
    "depth_missing": "sediment/sample depth missing",
    "depth_inverted": "depth interval min > max",
    "duplicate_source_row": "row identical to another source row (per the source's own rule)",
    "carbon_type_unknown": "carbon type (organic vs total) not stated",
    "se_units_differ": "SE column is documented in a different unit from the value",
    "coords_missing": "no coordinates in the source",
    "coords_approximate": "source marks the position as approximated",
    "lon_wrapped": "longitude given on 0-360 and wrapped to [-180, 180]",
    "lat_lon_zero": "latitude and longitude both exactly 0",
    "method_missing": "no method information in the source row",
    "flux_type_missing": "flux type (TOU/DOU/AOU) not stated",
    "study_marked_excluded": "the source's own study list marks the study as not in the database",
    "year_missing": "sampling year missing",
    "total_ne_sum_of_parts": "total differs from the sum of its parts beyond tolerance",
    # D3: habitat label filled from a scope the source states for the whole dataset
    "habitat_from_scope": "location habitat derived from the dataset's stated scope, not from a per-row label",
    # named in INTEGRATION_PLAN.md for compilations not yet built (defined in each source README)
    "covariate_conflict": "one sample carries two different values of one J1 attribute",
    "reference_inferred": "reference attached by inference (location/dates), not stated by the source",
    "reference_unresolved": "source gives no resolvable reference for this study id",
    "sd_converted_from_se": "the source converted a reported SE to an SD",
    "converted_by_source": "the source converted this value (or its unit) from the original",
    "calculated_by_source": "the source calculated this value from other information",
    "negative_sd": "the source's SD is negative (impossible); kept as reported",
    "assigned_by_source": "value assigned by the source from a rule or habitat class, not measured (USGS salt-flat zeros)",
    "potential_rate": "rate is a potential rate (e.g. acetylene block), not an ambient rate",
    "source_site_averaged": "source value is an average over sites",
    "source_time_averaged": "source value is an average over time",
    "source_average_row": "source row is itself an average of other rows",
    "source_median": "source value is a median",
    "salinity_units_missing": "salinity value has no unit in the source",
    "coords_integer_degree": "coordinates are whole degrees",
    "lat_beyond_pole": "latitude outside [-90, 90] in the source; the value is kept as reported",
    "pers_comm": "value is a personal communication",
    "secondary_source": "value was taken from a secondary compilation named by the source",
    "secondary_compilation": "reference is an aggregated database, not the original study",
    "cited_secondary": "source marks the citation as 'cited in' another paper",
    "error_type_other": "error column is neither SD nor SE",
    "error_propagated": "error is a propagated SE",
    "error_type_missing": "the source gives an error value but no error type (SD or SE not stated)",
    "digitized_from_figure": "value was digitised from a figure",
    "replicate": "row is a replicate",
    "shallow_lt10m": "source selection code: in situ, depth < 10 m",
    "method_excluded": "source selection code: methodological exclusion",
    "low_o2_bottom_water": "source selection code: bottom-water O2 < 25 uM",
    "o2_units_ambiguous": "bottom-water O2 column mixes uM and % saturation",
}

SKIP_REASONS: dict[str, str] = {
    "depthseries_core_not_in_cores": "CCN depth row whose (study, site, core) matches no core",
    "empty_row": "source row with no content",
    "no_seagrass_measurement": "seagrass row with none of the measurement columns filled",
    "below_detection_limit": "value reported as below the detection limit (BDL); limit kept in the report",
    "not_measured": "value reported as not measured (NM)",
    "units_missing": "J1 value whose unit cell is blank (units are required)",
    "no_density": "density row with no density value",
}


def check_flags(fact: pd.DataFrame) -> None:
    """Every flag on every fact must be registered in FLAGS."""
    seen = {x for lst in fact["flags"] for x in lst}
    bad = seen - set(FLAGS)
    if bad:
        raise VocabError(f"flags not registered in FLAGS: {sorted(bad)}")


def check_skip_reasons(reasons: Iterable[str]) -> None:
    """Every skip-report reason must be registered in SKIP_REASONS."""
    bad = set(reasons) - set(SKIP_REASONS)
    if bad:
        raise VocabError(f"skip reasons not registered in SKIP_REASONS: {sorted(bad)}")


# ------------------------------------------------------------------ labels

# habitat -> bottom type. "Water column" has no bottom (bottom_type NULL); it is the only
# label that may carry a NULL bottom type.
HABITATS: dict[str, str | None] = {
    "Tidal marsh": "soft",
    "Mangrove forest": "soft",
    "Tidal flat": "soft",
    "Sandy beach": "soft",
    "Seagrass meadow": "soft",
    "Shellfish reef / bed": "biogenic",
    "Subtidal soft sediment": "soft",
    "Rocky shore and reef": "hard",
    "Kelp and macroalgal bed": "hard",
    "Coral reef": "hard_biogenic",
    "Water column": None,
}
SETTINGS = frozenset({"estuary", "coastal lagoon", "fjord", "river delta", "embayment",
                      "open coast", "shelf", "open ocean"})
# Venice system classes
SALINITY_ZONES = frozenset({"limnetic", "oligohaline", "mesohaline", "polyhaline",
                            "euhaline", "hyperhaline"})
MODIFICATION_CLASSES = frozenset({"natural", "restored", "created", "artificial"})

_MAPPED_VOCAB: dict[str, frozenset[str]] = {
    "habitat": frozenset(HABITATS),
    "setting": SETTINGS,
    "salinity_zone": SALINITY_ZONES,
    "modification_class": MODIFICATION_CLASSES,
}
# raw location column each mapped column may be fed from
_FEEDERS: dict[str, frozenset[str]] = {
    "habitat": frozenset({"habitat_type"}),
    "setting": frozenset({"salinity_class", "geomorphic_setting", "habitat_type"}),
    "salinity_zone": frozenset({"salinity_class"}),
    "modification_class": frozenset({"modification_status"}),
}
DERIVED_LOCATION_COLUMNS = ["habitat", "bottom_type", "setting", "salinity_zone", "modification_class"]

LABEL_MAP_FILE = Path(__file__).with_name("compilation_label_maps.csv")
_MAP_COLUMNS = ["source_key", "raw_column", "raw_label", "mapped_column", "mapped_label"]

LabelMaps = dict[tuple[str, str, str], dict[str, str | None]]


def load_label_maps(path: Path = LABEL_MAP_FILE) -> LabelMaps:
    """(source_key, raw_column, mapped_column) -> {raw_label: mapped_label | None}."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[], encoding="utf-8")
    if list(df.columns) != _MAP_COLUMNS:
        raise VocabError(f"{path.name}: columns {list(df.columns)} != {_MAP_COLUMNS}")
    out: LabelMaps = {}
    for r in df.itertuples(index=False):
        if r.mapped_column not in _MAPPED_VOCAB:
            raise VocabError(f"{path.name}: unknown mapped_column {r.mapped_column!r}")
        if r.raw_column not in _FEEDERS[r.mapped_column]:
            raise VocabError(f"{path.name}: {r.raw_column!r} cannot feed {r.mapped_column!r}")
        if r.mapped_label != "" and r.mapped_label not in _MAPPED_VOCAB[r.mapped_column]:
            raise VocabError(f"{path.name}: {r.mapped_label!r} is not a {r.mapped_column} label")
        m = out.setdefault((r.source_key, r.raw_column, r.mapped_column), {})
        if r.raw_label in m:
            raise VocabError(f"{path.name}: duplicate row {tuple(r)}")
        m[r.raw_label] = r.mapped_label or None
    return out


def _map_scalar(raw: pd.Series, mapping: dict[str, str | None], what: str) -> pd.Series:
    missing = sorted(set(raw.dropna()) - set(mapping))
    if missing:
        raise VocabError(f"{what}: raw labels with no row in the label map: {missing}")
    return pd.Series([None if pd.isna(v) else mapping[v] for v in raw], index=raw.index, dtype=object)


def derive_location_labels(location: pd.DataFrame, source_key: str, maps: LabelMaps) -> pd.DataFrame:
    """The five derived label columns for a location table (aligned to its index)."""
    out = pd.DataFrame(index=location.index)
    feeders = {m: [k for k in maps if k[0] == source_key and k[2] == m] for m in _MAPPED_VOCAB}
    if not feeders["habitat"]:
        raise VocabError(f"{source_key}: no habitat label map declared")
    for mc in ("habitat", "setting", "salinity_zone"):
        col = pd.Series([None] * len(location), index=location.index, dtype=object)
        for (_, rc, _m) in feeders[mc]:
            mapped = _map_scalar(location[rc], maps[(source_key, rc, mc)], f"{source_key} {rc}->{mc}")
            clash = col.notna() & mapped.notna() & (col != mapped)
            if clash.any():
                raise VocabError(f"{source_key}: {mc} fed with conflicting labels by two raw columns")
            col = col.where(col.notna(), mapped)
        out[mc] = col
    out["bottom_type"] = out["habitat"].map(lambda h: None if h is None else HABITATS[h]).astype(object)
    mod_feeds = feeders["modification_class"]
    if len(mod_feeds) > 1:
        raise VocabError(f"{source_key}: modification_class has several feeders")
    if mod_feeds:
        mp = maps[(source_key, "modification_status", "modification_class")]
        seen = {x for lst in location["modification_status"] for x in lst}
        missing = sorted(seen - set(mp))
        if missing:
            raise VocabError(f"{source_key} modification_status->modification_class: raw labels "
                             f"with no row in the label map: {missing}")
        out["modification_class"] = [
            sorted({mp[x] for x in lst if mp[x] is not None}) for lst in location["modification_status"]
        ]
    else:
        out["modification_class"] = [[] for _ in range(len(location))]
    return out[DERIVED_LOCATION_COLUMNS]


def check_location_labels(location: pd.DataFrame) -> None:
    """Derived label columns must hold standard labels only, with bottom_type matching habitat."""
    for col, vocab in (("habitat", frozenset(HABITATS)), ("setting", SETTINGS),
                       ("salinity_zone", SALINITY_ZONES)):
        bad = set(location[col].dropna()) - vocab
        if bad:
            raise VocabError(f"location.{col}: {sorted(bad)} not standard labels")
    bad_m = {x for lst in location["modification_class"] for x in lst} - MODIFICATION_CLASSES
    if bad_m:
        raise VocabError(f"location.modification_class: {sorted(bad_m)} not standard labels")
    want = location["habitat"].map(lambda h: None if h is None or pd.isna(h) else HABITATS[h])
    same = (want.isna() & location["bottom_type"].isna()) | (want == location["bottom_type"])
    if not same.all():
        raise VocabError("location.bottom_type does not match habitat")
