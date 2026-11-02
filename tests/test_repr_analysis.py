"""Tests for repr_analysis.py on tiny hand-built fixtures (no DB, no GPU)."""

from __future__ import annotations

import numpy as np
import pytest

from coastal_crawler.repr_analysis import (
    fit_pca, fit_pca_project, group_baseline_centers, group_counts, load_baselines, load_occurrences, nested_mask,
    relative_to_baseline, tab20_pastel, validate_groups,
)


def test_nested_mask_marks_only_inner_longer_containment():
    # "salt marsh" = [0,10); inner "marsh" = [5,10); unrelated "estuary" = [20,27)
    starts = np.array([0, 5, 20]); ends = np.array([10, 10, 27])
    assert nested_mask(starts, ends).tolist() == [False, True, False]


def test_nested_mask_equal_spans_not_nested():
    assert nested_mask(np.array([0, 0]), np.array([5, 5])).tolist() == [False, False]


def test_validate_groups_ok_and_errors():
    terms = ["a", "b", "c"]
    assert validate_groups({"G": ["a", "b"], "H": ["c"]}, terms, []) == {"a": "G", "b": "G", "c": "H"}
    with pytest.raises(AssertionError):
        validate_groups({"G": ["a", "b"]}, terms, [])           # c ungrouped
    with pytest.raises(AssertionError):
        validate_groups({"G": ["a", "b"], "H": ["b", "c"]}, terms, [])  # b twice
    with pytest.raises(AssertionError):
        validate_groups({"G": ["a", "b", "c", "z"]}, terms, [])  # unknown z


def _write_shard(d, pid, labels, starts, ends, rep):
    n = len(labels)
    np.savez(d / f"{pid}.npz", layers=np.array([24]), labels=np.array(labels, dtype="<U20"),
             char_starts=np.array(starts), char_ends=np.array(ends),
             rep_layer_24=np.full((n, 3), rep, dtype=np.float32))


def test_load_occurrences_drops_nested_and_counts(tmp_path):
    # paper 1: "salt marsh"[0,10) + nested "marsh"[5,10) + "estuary"[20,27)
    _write_shard(tmp_path, 1, ["salt marsh", "marsh", "estuary"], [0, 5, 20], [10, 10, 27], 1.0)
    _write_shard(tmp_path, 2, [], [], [], 0.0)  # zero-occurrence paper
    _write_shard(tmp_path, 3, ["marsh"], [0], [5], 2.0)  # same text, not nested here
    occ = load_occurrences(tmp_path, [24])
    assert len(occ) == 3 and occ.n_dropped_nested == 1
    assert sorted(zip(occ.paper_id.tolist(), occ.label.tolist())) == [
        (1, "estuary"), (1, "salt marsh"), (3, "marsh")]
    t2g = {"salt marsh": "S", "marsh": "M", "estuary": "E"}
    assert group_counts(occ, t2g) == {"E": (1, 1), "M": (1, 1), "S": (1, 1)}


def test_load_occurrences_rejects_layer_mismatch(tmp_path):
    _write_shard(tmp_path, 1, ["a"], [0], [1], 1.0)
    with pytest.raises(AssertionError):
        load_occurrences(tmp_path, [28])


def test_relative_to_baseline_uses_own_surface_term():
    terms = ["x", "y"]
    base = np.array([[1.0, 0.0], [0.0, 10.0]], dtype=np.float32)
    reps = np.array([[2.0, 1.0], [3.0, 13.0]], dtype=np.float32)
    out = relative_to_baseline(reps, np.array(["x", "y"]), terms, base)
    assert out.tolist() == [[1.0, 1.0], [3.0, 3.0]]


def test_load_baselines_order_check(tmp_path):
    p = tmp_path / "iso.npz"
    np.savez(p, terms=np.array(["a", "b"]), layers=np.array([24]), rep_layer_24=np.zeros((2, 3), np.float32))
    assert load_baselines(p, ["a", "b"], [24])[24].shape == (2, 3)
    with pytest.raises(AssertionError):
        load_baselines(p, ["b", "a"], [24])


def test_fit_pca_known_answer_and_determinism():
    rng = np.random.default_rng(0)
    # variance only along axis 0 (std 5) and axis 1 (std 1); axis 2 constant
    x = np.stack([rng.normal(0, 5, 500), rng.normal(0, 1, 500), np.zeros(500)], axis=1)
    c1, e1 = fit_pca(x, 2); c2, e2 = fit_pca(x, 2)
    assert np.array_equal(c1, c2) and e1[0] > 0.9 and abs(e1.sum() - 1) < 1e-9
    assert abs(np.corrcoef(np.abs(c1[:, 0]), np.abs(x[:, 0]))[0, 1]) > 0.999


def test_tab20_pastel_is_lighter_than_tab20():
    assert tab20_pastel(1, 0.0) == ["#1f77b4"]       # tab20[0], no white
    assert tab20_pastel(1, 1.0) == ["#ffffff"]
    assert len(set(tab20_pastel(20, 0.35))) == 20


def test_fit_pca_project_matches_training_coords_and_mean_is_origin():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(50, 4))
    extra = np.stack([x[3], x.mean(axis=0)])
    c, _, e = fit_pca_project(x, 2, extra)
    assert np.allclose(e[0], c[3]) and np.allclose(e[1], 0, atol=1e-9)


def test_group_baseline_centers_means_only_terms_with_data():
    terms = ["a", "b", "c"]
    base = np.array([[1.0, 1.0], [3.0, 5.0], [100.0, 100.0]])
    out = group_baseline_centers(base, terms, {"G": ["a", "b"], "H": ["c", "z"]}, ["G", "H"], {"a", "b", "c"})
    assert out.tolist() == [[2.0, 3.0], [100.0, 100.0]]
    with pytest.raises(AssertionError):
        group_baseline_centers(base, terms, {"G": ["a"]}, ["G"], set())
