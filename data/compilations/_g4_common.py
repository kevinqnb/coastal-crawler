"""Helpers shared by the group-4 (water-column) compilations: tang-nitrification, shao-nfix, mouw-carbon,
francesco-phytoplankton, jorgensen-oxygen.

Kept apart from the per-compilation ``extract.py`` files so the J1 collapse rule (INTEGRATION_PLAN 1.2) is written
once for this group. Pure functions, no I/O.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

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


@dataclass(frozen=True)
class J1Cell:
    """One non-blank, numeric J1 cell, in the fixed raw order (``order`` is the position in that order)."""

    order: int
    sample_key: str
    attribute: str
    value: float
    ref: str  # source_row_ref of the raw cell
    payload: object = None  # whatever the caller needs back (row id, reference, ...)
    variant: tuple = ()  # extra identity of the value (e.g. comparator, sd): cells collapse only if value AND variant are equal


@dataclass(frozen=True)
class J1Fact:
    cell: J1Cell  # the first raw cell that carries this (sample, attribute, value)
    covariate_conflict: bool
    n_repeats: int  # raw cells collapsed into this fact, this cell excluded


def collapse_j1(cells: Sequence[J1Cell]) -> tuple[list[J1Fact], dict]:
    """INTEGRATION_PLAN 1.2 rule 1: one fact per (sample_key, attribute, distinct value).

    A J1 value belongs to the sample, not to the rate row. Cells of one (sample_key, attribute) with exactly equal
    values (no tolerance) collapse into the first of them in ``order``; if the group holds two or more different
    values, one fact per different value is kept and each carries ``covariate_conflict``. Nothing is averaged or
    dropped. Returns the facts (in ``order`` of their representative cell) and counts.
    """
    groups: dict[tuple[str, str], dict[tuple, list[J1Cell]]] = {}
    for c in sorted(cells, key=lambda c: c.order):
        if not math.isfinite(c.value):
            raise ContractError(f"J1 cell {c.ref}: non-finite value")
        groups.setdefault((c.sample_key, c.attribute), {}).setdefault((c.value, c.variant), []).append(c)
    facts: list[J1Fact] = []
    n_conflict_groups = 0
    n_conflict_facts = 0
    for g in groups.values():
        conflict = len(g) > 1
        if conflict:
            n_conflict_groups += 1
            n_conflict_facts += len(g)
        for members in g.values():
            facts.append(J1Fact(members[0], conflict, len(members) - 1))
    facts.sort(key=lambda f: f.cell.order)
    counts = {
        "cells": len(cells),
        "facts": len(facts),
        "collapsed_repeats": sum(f.n_repeats for f in facts),
        "conflict_groups": n_conflict_groups,
        "conflict_facts": n_conflict_facts,
    }
    if counts["cells"] != counts["facts"] + counts["collapsed_repeats"]:
        raise ContractError(f"J1 collapse does not account for every cell: {counts}")
    return facts, counts
