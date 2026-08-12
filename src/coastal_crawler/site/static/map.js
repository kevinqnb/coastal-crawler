// Leaflet map on the main papers list (list.html). MAP_LOCATIONS and the
// #map element's data-filter-qs attribute are set by that template's inline
// script — this file only reads them, same "vanilla JS reads server-embedded
// data" pattern as search.js reads /search.json.
(() => {
  const DEFAULT_CENTER = [20, 0]; // world view when there's nothing to fit bounds to
  const DEFAULT_ZOOM = 2;

  const mapEl = document.getElementById("map");
  if (!mapEl || typeof MAP_LOCATIONS === "undefined" || MAP_LOCATIONS.length === 0) {
    if (mapEl) mapEl.hidden = true;
    return;
  }

  const filterQs = mapEl.dataset.filterQs || "";

  const map = L.map(mapEl).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  const clusterGroup = L.markerClusterGroup();
  const bounds = [];

  for (const loc of MAP_LOCATIONS) {
    const marker = L.marker([loc.latitude, loc.longitude]);
    const papersUrl = `/locations/${loc.location_id}/papers${filterQs ? `?${filterQs}` : ""}`;

    // Built via DOM nodes, not an HTML string passed to bindPopup —
    // loc.location_name is LLM output read off third-party PDFs (same
    // untrusted-input treatment as paper titles get from _render_title/nh3
    // server-side), so it must never be interpreted as HTML.
    const popup = document.createElement("div");
    const nameEl = document.createElement("strong");
    nameEl.textContent = loc.location_name || "(unnamed location)";
    const countEl = document.createElement("div");
    countEl.textContent = `${loc.paper_count} paper${loc.paper_count === 1 ? "" : "s"}`;
    const link = document.createElement("a");
    link.href = papersUrl;
    link.textContent = "view papers →";
    popup.append(nameEl, countEl, link);

    marker.bindPopup(popup);
    marker.on("mouseover", () => marker.openPopup());
    marker.on("mouseout", () => marker.closePopup());
    marker.on("click", () => {
      window.location.href = papersUrl;
    });

    clusterGroup.addLayer(marker);
    bounds.push([loc.latitude, loc.longitude]);
  }

  map.addLayer(clusterGroup);
  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [20, 20], maxZoom: 10 });
  }
})();
