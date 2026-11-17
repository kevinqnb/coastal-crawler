"""Helpers shared by the group-4 (water-column) compilations: tang-nitrification, shao-nfix, mouw-carbon,
francesco-phytoplankton, jorgensen-oxygen.

Kept apart from the per-compilation ``extract.py`` files. The J1 collapse rule itself lives in ``_j1_collapse.py`` (shared
with group 3's compilations); ``J1Cell`` and ``collapse_j1`` are re-exported here. Pure functions, no I/O.
"""

from __future__ import annotations

import math
import re

import numpy as np

from _j1_collapse import J1Cell, J1Fact, collapse_j1  # noqa: F401  (re-exported: the extract.py files import them from here)
from coastal_crawler.compilations import ContractError


def norm_ws(s: str) -> str:
    """Whitespace normalisation: no-break / thin spaces to plain spaces, runs collapsed, ends stripped."""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ").replace(" ", " ")).strip()


def is_blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip() == "")


def is_num(v: object) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def number(v: object, what: str) -> float:
    if not is_num(v) or not math.isfinite(float(v)):  # type: ignore[arg-type]
        raise ContractError(f"{what}: non-numeric value {v!r}")
    return float(v)  # type: ignore[arg-type]
