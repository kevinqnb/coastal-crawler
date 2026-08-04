// Type-ahead dropdown for the header search box (base.html). The box is a
// real <form action="/search"> so Enter/no-JS still works via a full page
// load — this only adds the live suggestion list on top.
(() => {
  const MIN_QUERY_LENGTH = 2; // keep in sync with app.py's _SEARCH_MIN_CHARS
  const DEBOUNCE_MS = 200;

  const input = document.getElementById("search-input");
  const suggestions = document.getElementById("search-suggestions");
  if (!input || !suggestions) return;

  let debounceTimer = null;
  let activeController = null;
  let activeIndex = -1;

  function closeSuggestions() {
    suggestions.hidden = true;
    suggestions.innerHTML = "";
    activeIndex = -1;
  }

  function renderSuggestions(papers) {
    suggestions.innerHTML = "";
    activeIndex = -1;

    if (papers.length === 0) {
      const empty = document.createElement("div");
      empty.className = "search-suggestion-empty";
      empty.textContent = "No matching papers";
      suggestions.appendChild(empty);
      suggestions.hidden = false;
      return;
    }

    for (const paper of papers) {
      const link = document.createElement("a");
      link.className = "search-suggestion-item";
      link.href = `/papers/${paper.id}`;

      const title = document.createElement("span");
      title.className = "search-suggestion-title";
      // paper.title is sanitized server-side (nh3, small allow-list) before
      // it reaches this JSON, same as the rest of the site's render_title.
      title.innerHTML = paper.title;
      link.appendChild(title);

      const authorsYear = [
        paper.authors && paper.authors.length ? paper.authors.join(", ") : null,
        paper.year || null,
      ]
        .filter(Boolean)
        .join(" · ");
      if (authorsYear) {
        const subtitle = document.createElement("span");
        subtitle.className = "search-suggestion-meta";
        subtitle.textContent = authorsYear;
        link.appendChild(subtitle);
      }

      suggestions.appendChild(link);
    }
    suggestions.hidden = false;
  }

  async function fetchSuggestions(query) {
    if (activeController) activeController.abort();
    activeController = new AbortController();
    const thisController = activeController;

    let response;
    try {
      response = await fetch(`/search.json?q=${encodeURIComponent(query)}`, {
        signal: thisController.signal,
      });
    } catch (err) {
      if (err.name === "AbortError") return; // superseded by a newer keystroke
      closeSuggestions();
      return;
    }
    if (thisController.signal.aborted) return; // response for a stale query
    if (!response.ok) {
      closeSuggestions();
      return;
    }
    renderSuggestions(await response.json());
  }

  input.addEventListener("input", () => {
    const query = input.value.trim();
    clearTimeout(debounceTimer);
    if (query.length < MIN_QUERY_LENGTH) {
      if (activeController) activeController.abort();
      closeSuggestions();
      return;
    }
    debounceTimer = setTimeout(() => fetchSuggestions(query), DEBOUNCE_MS);
  });

  input.addEventListener("keydown", (event) => {
    const items = suggestions.querySelectorAll(".search-suggestion-item");
    if (suggestions.hidden || items.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % items.length;
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = (activeIndex - 1 + items.length) % items.length;
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      items[activeIndex].click();
      return;
    } else if (event.key === "Escape") {
      closeSuggestions();
      return;
    } else {
      return;
    }

    items.forEach((item, i) => item.classList.toggle("active", i === activeIndex));
  });

  document.addEventListener("click", (event) => {
    if (!input.contains(event.target) && !suggestions.contains(event.target)) {
      closeSuggestions();
    }
  });
})();
