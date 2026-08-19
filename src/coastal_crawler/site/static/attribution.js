// Per-measurement attribution context (page.html). Clicking a measurement
// box that has attribution data swaps the page's single OCR-text panel to
// that extraction's own snippet, tokens colored by a per-snippet min/max
// scale for whichever attribution method is toggled. Deselecting (clicking
// the same box again) restores the whole-page OCR text this panel showed
// on load. See notes/coastal-crawler/builds/2026-08-19-judge-attribution-display-01.md.
(() => {
  const dataEl = document.getElementById("attribution-data");
  if (!dataEl) return; // no judged extractions on this page — nothing to wire up

  const attributionData = JSON.parse(dataEl.textContent);
  const rootStyle = getComputedStyle(document.documentElement);
  const HIGH_RGB = rootStyle.getPropertyValue("--attr-high-rgb").trim();
  const LOW_RGB = rootStyle.getPropertyValue("--attr-low-rgb").trim();

  const snippetBody = document.getElementById("snippet-body");
  const snippetTitle = document.getElementById("snippet-title");
  const toggleButtons = document.querySelectorAll(".method-btn");
  const boxes = document.querySelectorAll(".measurement-box.has-attribution");

  const defaultBodyHtml = snippetBody.innerHTML;
  const defaultTitleHtml = snippetTitle.innerHTML;

  let currentMethod = "probe"; // matches page.html's default-active button
  let selectedId = null;

  // t=0.5 (score at the snippet's own midpoint between min and max) is
  // uncolored; alpha fades to 1 at either extreme. Guards max===min (every
  // score identical, e.g. a one-token snippet) by leaving everything
  // uncolored rather than dividing by zero.
  function colorFor(score, min, max) {
    if (max === min) return null;
    const t = (score - min) / (max - min);
    const alpha = Math.abs(t - 0.5) * 2;
    if (alpha < 0.02) return null;
    const rgb = t > 0.5 ? HIGH_RGB : LOW_RGB;
    return `rgba(${rgb}, ${alpha.toFixed(2)})`;
  }

  function renderTokens(tokens, scores) {
    const min = Math.min(...scores);
    const max = Math.max(...scores);
    const frag = document.createDocumentFragment();
    tokens.forEach((token, i) => {
      const span = document.createElement("span");
      span.className = "attribution-token";
      span.textContent = token;
      const color = colorFor(scores[i], min, max);
      if (color) span.style.backgroundColor = color;
      frag.appendChild(span);
    });
    return frag;
  }

  function render() {
    if (selectedId === null) {
      snippetBody.innerHTML = defaultBodyHtml;
      snippetTitle.innerHTML = defaultTitleHtml;
      return;
    }
    const entry = attributionData[selectedId];
    const scores = entry.methods[currentMethod];
    snippetBody.innerHTML = "";
    snippetBody.appendChild(renderTokens(entry.tokens, scores));
    snippetTitle.textContent = `Attributed context (${currentMethod.replace("_", " ")})`;
  }

  boxes.forEach((box) => {
    box.addEventListener("click", (event) => {
      // Don't hijack the vote form's own clicks.
      if (event.target.closest("form")) return;
      const id = box.dataset.extractionId;
      const wasSelected = selectedId === id;
      boxes.forEach((b) => b.classList.remove("selected"));
      selectedId = wasSelected ? null : id;
      if (!wasSelected) box.classList.add("selected");
      render();
    });
  });

  toggleButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      currentMethod = btn.dataset.method;
      toggleButtons.forEach((b) => b.classList.toggle("active", b === btn));
      render();
    });
  });
})();
