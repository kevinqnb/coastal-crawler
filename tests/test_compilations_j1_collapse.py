"""The shared J1 collapse rule (INTEGRATION_PLAN 1.2 rule 1): ``data/compilations/_j1_collapse.py``, used directly by the
water-column compilations and through ``_g3_common.collapse_covariates`` by Boynton and Fulweiler.

Hand-built cells with expected output stated in comments, and an equivalence test against a frozen copy of the
pre-unification group-3 algorithm, so the merge of the two implementations provably changed no behaviour.
"""

from __future__ import annotations

import math
import random

import pytest

from coastal_crawler.compilations import ContractError  # also puts data/compilations on sys.path
from _g3_common import collapse_covariates  # noqa: E402
from _g4_common import J1Cell as G4J1Cell  # noqa: E402
from _g4_common import collapse_j1 as g4_collapse_j1  # noqa: E402
from _j1_collapse import J1Cell, collapse_j1  # noqa: E402


def cell(order, sample, attr, value, variant=()):
    return J1Cell(order, sample, attr, value, f"row {order}", None, variant)


def test_g4_names_are_the_shared_implementation():
    assert G4J1Cell is J1Cell and g4_collapse_j1 is collapse_j1


def test_repeats_collapse_into_the_first_cell():
    # sample S1 / nitrate: 1.5 seen at orders 0, 2, 3 -> one fact (first cell), 2 repeats collapsed
    facts, counts = collapse_j1([cell(0, "S1", "water_nitrate", 1.5), cell(1, "S2", "water_nitrate", 1.5),
                                 cell(2, "S1", "water_nitrate", 1.5), cell(3, "S1", "water_nitrate", 1.5)])
    assert [(f.cell.order, f.cell.sample_key, f.covariate_conflict, f.n_repeats) for f in facts] == [
        (0, "S1", False, 2), (1, "S2", False, 0)]
    assert counts == {"cells": 4, "facts": 2, "collapsed_repeats": 2, "conflict_groups": 0, "conflict_facts": 0}


def test_two_different_values_are_both_kept_and_flagged():
    # S1 / nitrate carries 1.5, 2.0, 1.5: two facts (orders 0 and 1), both conflict, one repeat collapsed
    facts, counts = collapse_j1([cell(0, "S1", "water_nitrate", 1.5), cell(1, "S1", "water_nitrate", 2.0),
                                 cell(2, "S1", "water_nitrate", 1.5), cell(3, "S1", "water_ph", 8.0)])
    assert [(f.cell.order, f.covariate_conflict) for f in facts] == [(0, True), (1, True), (3, False)]
    assert counts == {"cells": 4, "facts": 3, "collapsed_repeats": 1, "conflict_groups": 1, "conflict_facts": 2}


def test_variant_must_match_too():
    # same number, different sign: not the same value (a "<0.02" is not a "0.02")
    facts, _ = collapse_j1([cell(0, "S", "a", 0.02, ("<",)), cell(1, "S", "a", 0.02, ()), cell(2, "S", "a", 0.02, ("<",))])
    assert [(f.cell.order, f.covariate_conflict, f.n_repeats) for f in facts] == [(0, True, 1), (1, True, 0)]


def test_input_order_does_not_matter_only_the_order_field():
    a = [cell(0, "S", "a", 1.0), cell(1, "S", "a", 2.0)]
    fa, _ = collapse_j1(a)
    fb, _ = collapse_j1(list(reversed(a)))
    assert [f.cell.order for f in fa] == [f.cell.order for f in fb] == [0, 1]


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_non_finite_value_raises(bad):
    with pytest.raises(ContractError, match="non-finite"):
        collapse_j1([cell(0, "S", "a", bad)])


# ---- group-3 adapter: (sample_key, attribute, value-or-(value, unit), source_row)

def test_adapter_returns_the_callers_own_objects_in_first_seen_order():
    cells = [(("loc", 1), "water_nitrate", (1.5, "um"), 7), (("loc", 1), "water_nitrate", (1.5, "um"), 9),
             (("loc", 1), "water_nitrate", (1.5, "mg l-1"), 11), ("S2", "water_ph", 7.9, 12)]
    kept, collapsed = collapse_covariates(cells)
    # the same number in two units is two values (conflict); the repeat in the same unit collapses into row 7
    assert kept == [(("loc", 1), "water_nitrate", (1.5, "um"), 7, True), (("loc", 1), "water_nitrate", (1.5, "mg l-1"), 11, True),
                    ("S2", "water_ph", 7.9, 12, False)]
    assert collapsed == 1


def _frozen_group3_collapse(cells):
    """The group-3 algorithm exactly as it was before the two implementations were merged."""
    cells = list(cells)
    first, order, values_of = {}, [], {}
    for sk, attr, val, row in cells:
        k = (sk, attr, val)
        if k not in first:
            first[k] = row
            order.append(k)
        values_of.setdefault((sk, attr), set()).add(val)
    kept = [(sk, attr, val, first[(sk, attr, val)], len(values_of[(sk, attr)]) > 1) for sk, attr, val in order]
    return kept, len(cells) - len(kept)


@pytest.mark.parametrize("with_units", [False, True])
def test_adapter_equals_the_frozen_group3_algorithm_on_random_cells(with_units):
    rng = random.Random(20261117)
    for _ in range(200):
        n = rng.randint(0, 40)
        cells = []
        for row in range(n):
            val = float(rng.choice([0.0, 0.5, 1.0, 1.5, 2.0, 7.9]))
            if with_units:
                val = (val, rng.choice(["um", "mg l-1"]))
            cells.append((f"S{rng.randint(0, 5)}", rng.choice(["water_nitrate", "water_ph"]), val, rng.randint(0, 1000) + row))
        assert collapse_covariates(cells) == _frozen_group3_collapse(cells)
