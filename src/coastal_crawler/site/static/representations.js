(function () {
  "use strict";
  const BASE = window.REPR_DATA_BASE;
  const FADED = 0.06, ON = 0.85, NORMAL = 0.7;
  const $ = (id) => document.getElementById(id);
  let meta = null, mode = "pca", selected = new Set();
  const cache = {};

  async function getJSON(name) {
    if (!cache[name]) {
      const r = await fetch(BASE + name);
      if (!r.ok) throw new Error("failed to load " + name + ": " + r.status);
      cache[name] = await r.json();
    }
    return cache[name];
  }

  function fmtEvr(a) { return a.map((v) => (100 * v).toFixed(1) + "%").join(", "); }

  function paperHTML(pid, term, start) {
    const p = meta.papers[String(pid)];
    const doi = p.doi ? ' &middot; <a href="https://doi.org/' + encodeURI(p.doi) + '">' + p.doi + "</a>" : "";
    const title = (p.title || "(untitled)").replace(/</g, "&lt;");
    return '<strong>' + term + '</strong> &mdash; <a href="/papers/' + pid + '">' + title + "</a>" + doi +
      '<br><span class="repr-note">paper ' + pid + ", character offset " + start + " in the cleaned text</span>";
  }

  // ---------------------------------------------------------------- legend
  function renderLegend(items, onToggle) {
    const el = $("repr-legend");
    el.innerHTML = "";
    items.forEach((it) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "repr-legend-item" +
        (selected.has(it.key) ? " selected" : "") +
        (selected.size && !selected.has(it.key) ? " faded" : "");
      b.innerHTML = '<span class="repr-swatch" style="background:' + it.color + '"></span><span>' +
        it.label + '</span><span class="repr-count">' + it.n + "</span>";
      b.addEventListener("click", () => onToggle(it.key));
      el.appendChild(b);
    });
  }

  function toggle(key) {
    if (selected.has(key)) selected.delete(key); else selected.add(key);
  }

  // -------------------------------------------------------------- PCA plot
  async function drawPCA() {
    const layer = $("repr-layer").value, dim = parseInt($("repr-dim").value, 10);
    const d = await getJSON("main_layer_" + layer + ".json");
    const evr = meta.main_evr[layer];
    $("repr-explain").textContent =
      "One PCA fit per layer on all points of the " + meta.main_groups.length +
      " main term groups (variance explained by PC1–3: " + fmtEvr(evr) + "). " +
      "Diamonds mark each group\u2019s isolated-term embedding (mean over its member terms), projected through the same fit. " +
      "Click a group at right to highlight it (click several to compare); click again to un-highlight.";
    const idx = meta.main_groups.map(() => []);
    d.group.forEach((g, i) => idx[g].push(i));
    const traces = meta.main_groups.map((g, gi) => {
      const ii = idx[gi];
      const t = {
        name: g, mode: "markers", hoverinfo: "text",
        text: ii.map((i) => meta.terms[d.term[i]]),
        x: ii.map((i) => d.x[i]), y: ii.map((i) => d.y[i]),
        marker: { size: dim === 3 ? 2.5 : 4, color: meta.groups[g].color, opacity: NORMAL },
        type: dim === 3 ? "scatter3d" : "scattergl",
      };
      if (dim === 3) t.z = ii.map((i) => d.z[i]);
      return t;
    });
    // One larger diamond per group: mean isolated-term embedding, projected through the same PCA fit.
    meta.main_groups.forEach((g, gi) => {
      const c = {
        name: g + " (isolated term)", mode: "markers", hoverinfo: "text",
        text: [g + ": mean isolated-term embedding of " + meta.groups[g].terms.join(", ")],
        x: [d.center.x[gi]], y: [d.center.y[gi]],
        marker: { size: dim === 3 ? 7 : 15, symbol: "diamond", color: meta.groups[g].color,
                  opacity: 1, line: { color: "#1c2b33", width: 2 } },
        type: dim === 3 ? "scatter3d" : "scattergl",
      };
      if (dim === 3) c.z = [d.center.z[gi]];
      traces.push(c);
    });
    const axis = (k) => ({ title: "PC" + k + " (" + (100 * evr[k - 1]).toFixed(1) + "%)", zeroline: false });
    const layout = dim === 3
      ? { margin: { l: 0, r: 0, t: 10, b: 0 }, showlegend: false,
          scene: { xaxis: axis(1), yaxis: axis(2), zaxis: axis(3) } }
      : { margin: { l: 55, r: 10, t: 10, b: 45 }, showlegend: false, hovermode: "closest",
          xaxis: axis(1), yaxis: axis(2), dragmode: "pan" };
    await Plotly.react("repr-plot", traces, layout, { responsive: true, scrollZoom: true });
    applyPCAHighlight();
    renderLegend(meta.main_groups.map((g) => ({
      key: g, label: g, color: meta.groups[g].color, n: meta.groups[g].n,
    })), (k) => { toggle(k); applyPCAHighlight(); drawLegendOnly(); });
  }

  function drawLegendOnly() {
    if (mode === "pca") {
      renderLegend(meta.main_groups.map((g) => ({
        key: g, label: g, color: meta.groups[g].color, n: meta.groups[g].n,
      })), (k) => { toggle(k); applyPCAHighlight(); drawLegendOnly(); });
    } else {
      relLegend();
    }
  }

  function applyPCAHighlight() {
    const op = meta.main_groups.map((g) => (!selected.size ? NORMAL : selected.has(g) ? ON : FADED));
    const cop = meta.main_groups.map((g) => (!selected.size || selected.has(g) ? 1 : FADED));
    Plotly.restyle("repr-plot", { "marker.opacity": op.concat(cop) });
  }

  // ------------------------------------------------------- relative plot
  let relState = null;

  async function drawRel() {
    const layer = $("repr-layer").value, g = $("repr-group").value;
    const all = await getJSON("relative_layer_" + layer + ".json");
    const d = all[g];
    if (!d) { $("repr-explain").textContent = "Too few points in this group for a PCA."; Plotly.purge("repr-plot"); return; }
    const gterms = meta.groups[g].terms;
    relState = { d, gterms, colors: {} };
    gterms.forEach((t, i) => (relState.colors[t] = meta.palette[i % meta.palette.length]));
    $("repr-explain").textContent =
      "Each point is one occurrence of a term in the group “" + g + "”, minus the isolated embedding of its own " +
      "surface term (same layer), then PCA'd on those differences only (PC1–2 explain " + fmtEvr(d.evr) + "). " +
      "Click a point to see its paper; click a term at right to highlight it." +
      (layer === String(meta.layers[0]) ? " Caution: this layer has rare very-large-norm vectors (both isolated and in-context) that can dominate PC1; compare with the last layer." : "");
    const idx = {};
    gterms.forEach((t) => (idx[t] = []));
    d.term.forEach((ti, i) => idx[meta.terms[ti]].push(i));
    const traces = gterms.map((t) => ({
      name: t, type: "scattergl", mode: "markers", hoverinfo: "text",
      text: idx[t].map(() => t), customdata: idx[t],
      x: idx[t].map((i) => d.x[i]), y: idx[t].map((i) => d.y[i]),
      marker: { size: 5, color: relState.colors[t], opacity: NORMAL },
    }));
    const axis = (k) => ({ title: "PC" + k + " (" + (100 * d.evr[k - 1]).toFixed(1) + "%)", zeroline: false });
    await Plotly.react("repr-plot", traces, {
      margin: { l: 55, r: 10, t: 10, b: 45 }, showlegend: false, hovermode: "closest",
      xaxis: axis(1), yaxis: axis(2), dragmode: "pan",
    }, { responsive: true, scrollZoom: true });
    $("repr-plot").removeAllListeners && $("repr-plot").removeAllListeners("plotly_click");
    $("repr-plot").on("plotly_click", (ev) => {
      const pt = ev.points[0], i = pt.customdata;
      $("repr-detail").innerHTML = paperHTML(d.paper[i], meta.terms[d.term[i]], d.start[i]);
    });
    applyRelHighlight();
    relLegend();
  }

  function relLegend() {
    if (!relState) return;
    const counts = {};
    relState.d.term.forEach((ti) => { const t = meta.terms[ti]; counts[t] = (counts[t] || 0) + 1; });
    renderLegend(relState.gterms.filter((t) => counts[t]).map((t) => ({
      key: t, label: t, color: relState.colors[t], n: counts[t],
    })), (k) => { toggle(k); applyRelHighlight(); relLegend(); });
  }

  function applyRelHighlight() {
    const op = relState.gterms.map((t) => (!selected.size ? NORMAL : selected.has(t) ? ON : FADED));
    Plotly.restyle("repr-plot", { "marker.opacity": op });
  }

  // ------------------------------------------------------------- wiring
  async function redraw() {
    $("dim-wrap").hidden = mode !== "pca";
    $("group-wrap").hidden = mode !== "rel";
    $("repr-detail").hidden = mode !== "rel";
    if (mode === "pca") await drawPCA(); else await drawRel();
  }

  function switchMode(m) {
    mode = m; selected = new Set();
    $("tab-pca").classList.toggle("active", m === "pca");
    $("tab-rel").classList.toggle("active", m === "rel");
    redraw();
  }

  async function init() {
    meta = await getJSON("meta.json");
    const nPapers = Object.keys(meta.papers).length;
    $("repr-summary").textContent =
      meta.id + ": " + Object.values(meta.groups).reduce((a, g) => a + g.n, 0).toLocaleString() +
      " term occurrences from " + nPapers.toLocaleString() + " papers (" + meta.n_dropped_nested.toLocaleString() +
      " occurrences nested inside a longer matched term were dropped).";
    meta.layers.forEach((l) => $("repr-layer").add(new Option("Layer " + l, l)));
    Object.keys(meta.groups).forEach((g) => $("repr-group").add(new Option(g + " (" + meta.groups[g].n + ")", g)));
    $("repr-layer").addEventListener("change", () => { redraw(); });
    $("repr-dim").addEventListener("change", redraw);
    $("repr-group").addEventListener("change", () => { selected = new Set(); redraw(); });
    $("tab-pca").addEventListener("click", () => switchMode("pca"));
    $("tab-rel").addEventListener("click", () => switchMode("rel"));
    await redraw();
  }

  init().catch((e) => { $("repr-summary").textContent = "Error: " + e.message; throw e; });
})();
