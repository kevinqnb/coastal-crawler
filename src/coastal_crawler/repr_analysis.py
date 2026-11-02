"""Analysis of collected representations for the site's Representations tab.

Pure functions (no site/DB dependency): load shards, group terms, drop rows
nested inside a longer match, PCA, and the baseline-subtracted
"relative difference" transform. ``scripts/build_repr_plots.py`` calls these
and writes the JSON the site serves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sklearn.decomposition import PCA


@dataclass
class Occurrences:
    """All kept term occurrences, one row each (parallel arrays)."""

    paper_id: np.ndarray        # int64
    label: np.ndarray           # surface term, '<U..'
    char_start: np.ndarray      # int64, offsets into the CLEANED text
    char_end: np.ndarray
    reps: dict[int, np.ndarray]  # layer -> (n, d) float32
    n_dropped_nested: int

    def __len__(self) -> int:
        return len(self.paper_id)

    def take(self, idx: np.ndarray) -> "Occurrences":
        return Occurrences(
            self.paper_id[idx], self.label[idx], self.char_start[idx],
            self.char_end[idx], {l: r[idx] for l, r in self.reps.items()},
            self.n_dropped_nested,
        )


def read_terms_file(path: str | Path) -> list[str]:
    terms = [t.strip() for t in Path(path).read_text().splitlines() if t.strip()]
    assert len(terms) == len(set(terms)), "duplicate terms in terms file"
    return terms


def validate_groups(
    groups: dict[str, list[str]], terms: list[str], terms_without_data: list[str]
) -> dict[str, str]:
    """term -> group. Every term in exactly one group or terms_without_data."""
    term_to_group: dict[str, str] = {}
    for g, members in groups.items():
        for t in members:
            assert t not in term_to_group, f"term {t!r} is in two groups"
            term_to_group[t] = g
    assert not set(term_to_group) & set(terms_without_data), "term both grouped and without data"
    assigned = set(term_to_group) | set(terms_without_data)
    assert assigned == set(terms), (
        f"terms/groups mismatch: ungrouped={sorted(set(terms) - assigned)} "
        f"unknown={sorted(assigned - set(terms))}"
    )
    return term_to_group


def nested_mask(starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """True for rows whose span lies inside a strictly longer span (one paper)."""
    n = len(starts)
    mask = np.zeros(n, dtype=bool)
    for i in range(n):
        longer = (ends - starts) > (ends[i] - starts[i])
        mask[i] = bool(np.any(longer & (starts <= starts[i]) & (ends >= ends[i])))
    return mask


def load_occurrences(shard_dir: str | Path, layers: list[int]) -> Occurrences:
    """Read every shard; drop rows nested inside a longer match in the same paper."""
    files = sorted(Path(shard_dir).glob("*.npz"))
    assert files, f"no shards in {shard_dir}"
    pid, lab, cs, ce = [], [], [], []
    reps: dict[int, list[np.ndarray]] = {l: [] for l in layers}
    dropped = 0
    for f in files:
        z = np.load(f)
        assert list(z["layers"]) == list(layers), f"{f}: layers {z['layers']} != {layers}"
        n = len(z["labels"])
        if n == 0:
            continue
        s, e = z["char_starts"], z["char_ends"]
        assert len(s) == len(e) == n
        keep = ~nested_mask(s, e)
        dropped += int((~keep).sum())
        pid.append(np.full(int(keep.sum()), int(f.stem), dtype=np.int64))
        lab.append(z["labels"][keep])
        cs.append(s[keep])
        ce.append(e[keep])
        for l in layers:
            r = z[f"rep_layer_{l}"]
            assert r.shape[0] == n and np.isfinite(r).all()
            reps[l].append(r[keep])
    return Occurrences(
        np.concatenate(pid), np.concatenate(lab), np.concatenate(cs), np.concatenate(ce),
        {l: np.concatenate(v).astype(np.float32) for l, v in reps.items()}, dropped,
    )


def load_baselines(npz_path: str | Path, terms: list[str], layers: list[int]) -> dict[int, np.ndarray]:
    """layer -> (n_terms, d) isolated-term vectors, aligned to ``terms`` order."""
    z = np.load(npz_path, allow_pickle=False)
    assert [str(t) for t in z["terms"]] == terms, "isolated_terms order != terms file"
    assert list(z["layers"]) == list(layers)
    return {l: z[f"rep_layer_{l}"] for l in layers}


def group_counts(occ: Occurrences, term_to_group: dict[str, str]) -> dict[str, tuple[int, int]]:
    """group -> (n_rows, n_papers)."""
    groups = np.array([term_to_group[str(l)] for l in occ.label])
    out = {}
    for g in sorted(set(term_to_group.values())):
        m = groups == g
        out[g] = (int(m.sum()), int(len(np.unique(occ.paper_id[m]))))
    return out


def fit_pca(x: np.ndarray, n_components: int) -> tuple[np.ndarray, np.ndarray]:
    """Centered PCA via full SVD (deterministic). Returns (coords, explained_variance_ratio)."""
    assert x.ndim == 2 and x.shape[0] > n_components and np.isfinite(x).all()
    p = PCA(n_components=n_components, svd_solver="full")
    coords = p.fit_transform(x.astype(np.float64))
    return coords, p.explained_variance_ratio_


def fit_pca_project(
    x: np.ndarray, n_components: int, extra: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """As fit_pca, also projecting ``extra`` rows through the same fit."""
    assert x.ndim == 2 and extra.ndim == 2 and extra.shape[1] == x.shape[1]
    assert x.shape[0] > n_components and np.isfinite(x).all() and np.isfinite(extra).all()
    p = PCA(n_components=n_components, svd_solver="full")
    coords = p.fit_transform(x.astype(np.float64))
    return coords, p.explained_variance_ratio_, p.transform(extra.astype(np.float64))


def group_baseline_centers(
    baseline: np.ndarray, terms: list[str], groups: dict[str, list[str]],
    group_names: list[str], terms_with_data: set[str],
) -> np.ndarray:
    """(n_groups, d): mean isolated vector of each group's member terms that have data."""
    index = {t: i for i, t in enumerate(terms)}
    rows = []
    for g in group_names:
        members = [t for t in groups[g] if t in terms_with_data]
        assert members, f"group {g!r} has no member term with data"
        rows.append(baseline[[index[t] for t in members]].mean(axis=0))
    return np.stack(rows)


def relative_to_baseline(
    reps: np.ndarray, labels: np.ndarray, terms: list[str], baseline: np.ndarray
) -> np.ndarray:
    """Subtract each row's own surface-term isolated vector (same layer)."""
    index = {t: i for i, t in enumerate(terms)}
    assert baseline.shape == (len(terms), reps.shape[1])
    rows = np.array([index[str(l)] for l in labels])
    return reps - baseline[rows]


def tab20_pastel(n: int, white_mix: float) -> list[str]:
    """First n matplotlib tab20 colours, each blended toward white by white_mix."""
    from matplotlib import colormaps
    from matplotlib.colors import to_hex
    assert 0 <= white_mix <= 1
    cmap = colormaps["tab20"]
    assert n <= cmap.N
    return [
        to_hex(tuple((1 - white_mix) * c + white_mix for c in cmap(i)[:3])) for i in range(n)
    ]


def load_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())
