"""Helpers shared by the group-3 compilations (boynton-flux, fulweiler-nfix, pessarrodona-seaweed,
davis-calcification). Pure functions, no I/O. Nothing here touches the contract code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Hashable, Iterable, Sequence

import numpy as np

from _j1_collapse import J1Cell, collapse_j1
from coastal_crawler.compilations import ContractError

_NBSP = ("\xa0", " ", " ", " ")


def norm_ws(s: str) -> str:
    """Whitespace normalised: no-break / thin spaces become plain spaces, runs collapse, ends stripped."""
    for ch in _NBSP:
        s = s.replace(ch, " ")
    return re.sub(r"\s+", " ", s).strip()


def blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip() == "")


def is_num(v: object) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def number(v: object, what: str) -> float:
    if not is_num(v) or not math.isfinite(float(v)):  # type: ignore[arg-type]
        raise ContractError(f"{what}: non-numeric value {v!r}")
    return float(v)  # type: ignore[arg-type]


def text(v: object) -> str | None:
    """Blank -> None; a number written as a cell becomes its plain text."""
    if blank(v):
        return None
    if isinstance(v, str):
        return norm_ws(v)
    if is_num(v):
        f = float(v)  # type: ignore[arg-type]
        return str(int(f)) if f == int(f) else repr(f)
    raise ContractError(f"unexpected cell {v!r}")


def classify(v: object, what: str, codes: dict[str, str]) -> tuple[str, float | None]:
    """('blank' | <code kind> | 'num', value). ``codes`` maps a lower-cased stripped string to a kind
    (e.g. ``{"nd": "nd"}``). Any other string raises."""
    if blank(v):
        return "blank", None
    if is_num(v):
        return "num", number(v, what)
    if isinstance(v, str):
        k = codes.get(v.strip().lower())
        if k is not None:
            return k, None
    raise ContractError(f"{what}: unrecognised non-numeric value {v!r}")


def method_row(source_key: str, method_key: str, attribute: str, *, status: str, technique: str | None = None,
               in_situ_ex_situ: str | None = None, light_dark: str | None = None,
               incubation_type: str | None = None, experimental_status: str | None = None,
               conversion_applied: str | None = None, params: str = "{}", notes: str | None = None) -> dict:
    return {
        "source_key": source_key, "method_key": method_key, "source_method_id": None, "attribute": attribute,
        "method_status": status, "technique": technique, "coring_method": None, "carbon_type": None,
        "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
        "carbonate_removal_method": None, "conversion_applied": conversion_applied,
        "in_situ_ex_situ": in_situ_ex_situ, "light_dark": light_dark, "incubation_type": incubation_type,
        "experimental_status": experimental_status, "params": params, "notes": notes,
    }


def count_flags(flag_lists: Iterable[Sequence[str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for fl in flag_lists:
        for x in fl:
            out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def collapse_covariates(
    cells: Iterable[tuple[Hashable, str, float, int]],
) -> tuple[list[tuple[Hashable, str, float, int, bool]], int]:
    """J1 rule 1 (INTEGRATION_PLAN 1.2): one fact per (sample, attribute, distinct value), via ``_j1_collapse.collapse_j1``.

    ``cells`` are (sample_key, attribute, value, source_row) in source row order; ``value`` is a number, or a tuple whose
    first element is the number and whose remaining elements (e.g. the unit) must also match for two values to be the same
    (a value in two units is two values). Returns (kept [(sample_key, attribute, value, source_row, conflict)] in first-seen
    order, number collapsed); ``len(cells) == len(kept) + collapsed`` is asserted by ``collapse_j1``.
    """
    j1 = []
    for order, (sk, attr, val, row) in enumerate(cells):
        number, variant = (val[0], tuple(val[1:])) if isinstance(val, tuple) else (val, ())
        j1.append(J1Cell(order, sk, attr, number, f"row {row}", (val, row), variant))
    facts, counts = collapse_j1(j1)
    kept = [(f.cell.sample_key, f.cell.attribute, f.cell.payload[0], f.cell.payload[1], f.covariate_conflict) for f in facts]  # type: ignore[index]
    return kept, counts["collapsed_repeats"]
