"""Entity schema and attribute catalogue for MeasurementLM extraction.

*** PLACEHOLDER — fill in for the coastal-ecosystem domain before running
`coastal-crawler extract` for real. ***

`EntitySchema` and `ATTRIBUTE_INFO_DICT` are Python objects (not env vars)
because `MeasurementLM` needs a real pydantic model and a real dict, not a
string. `meas_lm_entity_identification_prompt` (the free-text prompt
describing what to identify) lives in `.env`/`Settings` instead, alongside
`filter_relevance_prompt`, since it's prose that's reasonable to iterate on
without a code change.

See ``../scholarlm/experiments/configs/pond.py`` (sibling repo) for a
complete worked example of this exact shape for a different dataset (aquatic
ecosystems) — use it as a reference for the level of detail expected in
`ATTRIBUTE_INFO_DICT` descriptions/units.

If your entity schema includes coordinates, add `latitude`/`longitude`
fields below and set `EXTRACTION_LAT_FIELD`/`EXTRACTION_LON_FIELD` in
`.env` to their field names. The reference pond.py example has no
coordinate fields, so they're omitted here by default.
"""

from __future__ import annotations

from pydantic import BaseModel


class EntitySchema(BaseModel):
    """One distinct sampled coastal ecosystem/site per paper.

    TODO: replace these placeholder fields with the real entity fields for
    your domain (e.g. site name, ecosystem type, location).
    """

    name: str | None
    location: str | None


# TODO: fill in one entry per measurable attribute for the coastal domain,
# e.g.:
#   "salinity": {"description": "Water salinity at the sampling site.", "units": "psu"},
ATTRIBUTE_INFO_DICT: dict[str, dict[str, str]] = {}
