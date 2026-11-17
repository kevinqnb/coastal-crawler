"""The J1 collapse rule (INTEGRATION_PLAN 1.2 rule 1), written once for every compilation that carries water
chemistry beside a rate: boynton-flux, fulweiler-nfix (via ``_g3_common.collapse_covariates``) and tang-nitrification,
shao-nfix, francesco-phytoplankton (via ``_g4_common``, which re-exports ``J1Cell`` and ``collapse_j1``).

Pure functions, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from coastal_crawler.compilations import ContractError


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
