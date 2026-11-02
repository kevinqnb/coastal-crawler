#!/usr/bin/env python3
"""Build the plot data for the site's Representations tab.

    python scripts/build_repr_plots.py configs/repr-analysis/<id>.yaml

Reads the finished repr shards + isolated-term baselines named in the config,
drops rows nested inside a longer match, and writes JSON to
Settings.repr_site_dir/<id>/ :
  meta.json              groups, colours, layers, explained variance, paper titles
  main_layer_<L>.json    PCA of the main groups (one fit per layer, 3 comps)
  relative_layer_<L>.json per group: PCA of (rep - own surface term's isolated rep)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

from coastal_crawler.config import get_settings
from coastal_crawler.repr_analysis import (
    fit_pca, fit_pca_project, group_baseline_centers, group_counts, load_baselines, load_config, load_occurrences,
    read_terms_file, relative_to_baseline, tab20_pastel, validate_groups,
)


def _r(a: np.ndarray, d: int) -> list:
    return np.round(a, d).tolist()


def main(cfg_path: str) -> None:
    cfg = load_config(cfg_path)
    st = get_settings()
    layers = cfg["layers"]
    dec = cfg["coord_decimals"]
    terms = read_terms_file(cfg["terms_file"])
    t2g = validate_groups(cfg["groups"], terms, cfg["terms_without_data"])
    main_groups = cfg["main_groups"]
    assert len(main_groups) <= cfg["max_main_groups"]
    assert set(main_groups) <= set(cfg["groups"]) and len(set(main_groups)) == len(main_groups)

    root = Path(st.repr_dir)
    occ = load_occurrences(root / cfg["shards_run"] / "shards", layers)
    base = load_baselines(root / cfg["terms_run"] / "isolated_terms.npz", terms, layers)
    present = set(map(str, np.unique(occ.label)))
    assert present == set(terms) - set(cfg["terms_without_data"]), "terms with data != config"
    grp = np.array([t2g[str(l)] for l in occ.label])
    term_idx = np.array([terms.index(str(l)) for l in occ.label])
    counts = group_counts(occ, t2g)
    assert sum(n for n, _ in counts.values()) == len(occ)

    colors = dict(zip(main_groups, tab20_pastel(len(main_groups), cfg["pastel_white_mix"])))
    out = Path(st.repr_site_dir) / cfg["id"]
    out.mkdir(parents=True, exist_ok=True)

    pids = sorted(set(map(int, occ.paper_id)))
    con = duckdb.connect(st.warehouse_path, read_only=True)
    rows = con.execute(
        f"SELECT paper_id, title, doi FROM paper_dim WHERE paper_id IN ({','.join('?' * len(pids))})", pids
    ).fetchall()
    assert len(rows) == len(pids), "papers missing from warehouse paper_dim"
    papers = {str(i): {"title": t, "doi": d} for i, t, d in rows}

    meta = {
        "id": cfg["id"], "layers": layers, "terms": terms, "main_groups": main_groups,
        "groups": {g: {"n": counts[g][0], "n_papers": counts[g][1], "terms": cfg["groups"][g],
                       "color": colors.get(g)} for g in cfg["groups"]},
        "main_evr": {}, "palette": tab20_pastel(20, cfg["pastel_white_mix"]), "papers": papers, "n_dropped_nested": occ.n_dropped_nested,
    }

    main_mask = np.isin(grp, main_groups)
    for l in layers:
        centers = group_baseline_centers(base[l], terms, cfg["groups"], main_groups, present)
        c, evr, cc = fit_pca_project(occ.reps[l][main_mask], cfg["pca_components"], centers)
        meta["main_evr"][str(l)] = _r(evr, 4)
        gi = {g: i for i, g in enumerate(main_groups)}
        (out / f"main_layer_{l}.json").write_text(json.dumps({
            "group": [gi[g] for g in grp[main_mask]],
            "term": term_idx[main_mask].tolist(),
            "paper": occ.paper_id[main_mask].tolist(),
            "start": occ.char_start[main_mask].tolist(),
            "x": _r(c[:, 0], dec), "y": _r(c[:, 1], dec), "z": _r(c[:, 2], dec),
            "center": {"x": _r(cc[:, 0], dec), "y": _r(cc[:, 1], dec), "z": _r(cc[:, 2], dec)},
        }, separators=(",", ":")))

        rel = {}
        for g in cfg["groups"]:
            m = grp == g
            if m.sum() <= cfg["relative_components"]:
                continue
            d = relative_to_baseline(occ.reps[l][m], occ.label[m], terms, base[l])
            c, evr = fit_pca(d, cfg["relative_components"])
            rel[g] = {
                "evr": _r(evr, 4), "term": term_idx[m].tolist(),
                "paper": occ.paper_id[m].tolist(), "start": occ.char_start[m].tolist(),
                "x": _r(c[:, 0], dec), "y": _r(c[:, 1], dec),
            }
        (out / f"relative_layer_{l}.json").write_text(json.dumps(rel, separators=(",", ":")))

    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"wrote {out}: {len(occ)} rows, {int(main_mask.sum())} in main plot")


if __name__ == "__main__":
    main(sys.argv[1])
