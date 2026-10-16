/**
 * DeenyaMart Live Search Combobox (search.js)
 * Debounced autocomplete suggestions for products and categories.
 */
(function () {
  "use strict";

  const searchInput = document.getElementById("dm-search-input");
  const dropdown = document.getElementById("dm-search-dropdown");
  if (!searchInput || !dropdown) return;

  let debounceTimer = null;
  let activeController = null;

  function closeDropdown() {
    dropdown.hidden = true;
    searchInput.setAttribute("aria-expanded", "false");
  }

  function renderSuggestions(data) {
    const products = data.products || [];
    const categories = data.categories || [];

    if (products.length === 0 && categories.length === 0) {
      dropdown.innerHTML = `
        <div style="padding: 0.75rem; font-size: 0.85rem; color: var(--dm-text-secondary);">
          No direct matches for "<strong></strong>". Press Enter to search full catalog.
        </div>
      `;
      const strong = dropdown.querySelector("strong");
      if (strong) strong.textContent = data.query;
      dropdown.hidden = false;
      searchInput.setAttribute("aria-expanded", "true");
      return;
    }

    let html = "";
    if (categories.length > 0) {
      html += `<div style="padding: 0.25rem 0.5rem; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--dm-text-muted);">Categories</div>`;
      categories.forEach((cat) => {
        html += `
          <a href="${cat.url}" class="dm-search__item" role="option">
            <span style="font-size: 0.85rem; font-weight: 600;">${cat.name}</span>
            <span style="font-size: 0.75rem; color: var(--dm-brand-primary);">Browse →</span>
          </a>
        `;
      });
    }

    if (products.length > 0) {
      html += `<div style="padding: 0.45rem 0.5rem 0.25rem; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--dm-text-muted);">Matching Products</div>`;
      products.forEach((p) => {
        html += `
          <a href="${p.url}" class="dm-search__item" role="option">
            <div style="display: flex; align-items: center; gap: 0.65rem; min-width: 0;">
              <img src="${p.image_url}" alt="" class="dm-search__item-thumb" width="40" height="48" loading="lazy">
              <div style="min-width: 0;">
                <div style="font-size: 0.85rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${p.name}</div>
                <div style="font-size: 0.72rem; color: var(--dm-text-muted);">${p.category} · ${p.sku}</div>
              </div>
            </div>
            <div style="font-family: var(--dm-font-mono); font-size: 0.85rem; font-weight: 700; flex-shrink: 0;">
              ${p.price}
            </div>
          </a>
        `;
      });
    }

    dropdown.innerHTML = html;
    dropdown.hidden = false;
    searchInput.setAttribute("aria-expanded", "true");
  }

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim();
    window.clearTimeout(debounceTimer);

    if (query.length < 2) {
      closeDropdown();
      return;
    }

    debounceTimer = window.setTimeout(async () => {
      if (activeController) activeController.abort();
      activeController = new AbortController();

      try {
        const url = `/search/suggestions/?q=${encodeURIComponent(query)}`;
        const response = await fetch(url, {
          signal: activeController.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const data = await response.json();
        renderSuggestions(data);
      } catch (err) {
        if (err.name !== "AbortError") {
          closeDropdown();
        }
      }
    }, 180);
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDropdown();
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".dm-search")) {
      closeDropdown();
    }
  });
})();
