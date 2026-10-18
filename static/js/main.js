/**
 * DeenyaMart Storefront Core (main.js)
 * Accessible drawers (<dialog>), mobile navigation, toast notifications,
 * wishlist toggle, and newsletter progressive enhancement.
 */
(function () {
  "use strict";

  const liveRegion = document.getElementById("dm-live-region");
  const toastRegion = document.getElementById("dm-toast-region");

  function announce(message) {
    if (!liveRegion) return;
    liveRegion.textContent = "";
    window.setTimeout(() => {
      liveRegion.textContent = message;
    }, 30);
  }

  function showToast(message, options = {}) {
    announce(message);
    if (!toastRegion) return;

    const toast = document.createElement("div");
    toast.className = "dm-toast";
    toast.setAttribute("role", "status");

    const textSpan = document.createElement("span");
    textSpan.textContent = message;
    toast.appendChild(textSpan);

    if (options.actionLabel && typeof options.onAction === "function") {
      const actionBtn = document.createElement("button");
      actionBtn.type = "button";
      actionBtn.className = "dm-toast__action";
      actionBtn.textContent = options.actionLabel;
      actionBtn.addEventListener("click", () => {
        options.onAction();
        toast.remove();
      });
      toast.appendChild(actionBtn);
    }

    toastRegion.appendChild(toast);
    window.setTimeout(() => {
      if (toast.parentNode) {
        toast.remove();
      }
    }, options.duration || 4200);
  }

  window.DeenyaUI = {
    announce,
    showToast,
    openDialog,
    closeDialog,
  };

  /* Accessible <dialog> Drawer Controller */
  let lastTrigger = null;

  function openDialog(dialogId, triggerEl) {
    const dialog = document.getElementById(dialogId);
    if (!dialog) return;
    lastTrigger = triggerEl || document.activeElement;

    if (typeof dialog.showModal === "function") {
      if (!dialog.open) {
        dialog.showModal();
      }
    } else {
      dialog.setAttribute("open", "");
    }

    if (triggerEl) {
      triggerEl.setAttribute("aria-expanded", "true");
    }
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function" && dialog.open) {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
    if (lastTrigger && typeof lastTrigger.focus === "function") {
      lastTrigger.setAttribute("aria-expanded", "false");
      lastTrigger.focus();
    }
  }

  document.addEventListener("click", (event) => {
    const openBtn = event.target.closest("[data-open-dialog]");
    if (openBtn) {
      event.preventDefault();
      const targetId = openBtn.getAttribute("data-open-dialog");
      openDialog(targetId, openBtn);
      return;
    }

    const closeBtn = event.target.closest("[data-close-dialog]");
    if (closeBtn) {
      event.preventDefault();
      const dialog = closeBtn.closest("dialog");
      closeDialog(dialog);
      return;
    }

    /* Close native <dialog> when clicking on backdrop outside .dm-drawer__layout */
    if (event.target.tagName === "DIALOG" && event.target.open) {
      const rect = event.target.getBoundingClientRect();
      const clickedInDialog =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!clickedInDialog) {
        closeDialog(event.target);
      }
    }

    /* Mobile Subcategory Accordion Toggle */
    const subToggle = event.target.closest("[data-mobile-sub-toggle]");
    if (subToggle) {
      const targetId = subToggle.getAttribute("aria-controls");
      const panel = document.getElementById(targetId);
      if (panel) {
        const isExpanded = subToggle.getAttribute("aria-expanded") === "true";
        subToggle.setAttribute("aria-expanded", String(!isExpanded));
        panel.hidden = isExpanded;
      }
    }
  });

  /* Mobile Bottom-Nav "Search" — focus header search input (falls back to search page link) */
  document.addEventListener("click", (event) => {
    const focusTrigger = event.target.closest("[data-focus-search]");
    if (!focusTrigger) return;
    const input = document.getElementById("dm-search-input");
    if (!input) return;
    event.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
    input.focus({ preventScroll: true });
  });

  /* Wishlist Async Toggle */
  document.addEventListener("submit", async (event) => {
    const wishlistForm = event.target.closest(".js-wishlist-form");
    if (wishlistForm) {
      event.preventDefault();
      const btn = wishlistForm.querySelector(".dm-wishlist-btn");
      try {
        const response = await fetch(wishlistForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
          body: new FormData(wishlistForm),
        });
        const data = await response.json();
        if (data.ok) {
          document.querySelectorAll("[data-wishlist-count]").forEach((el) => {
            el.textContent = data.wishlist_count;
          });
          if (btn) {
            btn.classList.toggle("is-active", Boolean(data.added));
            btn.setAttribute("aria-pressed", String(Boolean(data.added)));
          }
          showToast(data.message);
        }
      } catch (err) {
        wishlistForm.submit();
      }
      return;
    }

    /* Newsletter Progressive Enhancement */
    const newsletterForm = event.target.closest(".js-newsletter-form");
    if (newsletterForm) {
      event.preventDefault();
      const feedbackEl = newsletterForm.querySelector(".js-newsletter-feedback");
      const submitBtn = newsletterForm.querySelector("button[type='submit']");
      if (submitBtn) submitBtn.disabled = true;

      try {
        const response = await fetch(newsletterForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
          body: new FormData(newsletterForm),
        });
        const data = await response.json();
        if (feedbackEl) {
          feedbackEl.hidden = false;
          feedbackEl.textContent = data.message;
          feedbackEl.style.color = data.ok ? "var(--dm-status-success)" : "var(--dm-status-danger)";
        }
        if (data.ok) {
          newsletterForm.reset();
          showToast(data.message);
        }
      } catch (err) {
        newsletterForm.submit();
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    }
  });

  /* Homepage Hero Carousel Controller (progressive enhancement) */
  document.querySelectorAll("[data-hero-carousel]").forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll("[data-hero-slide]"));
    const dots = Array.from(carousel.querySelectorAll("[data-hero-dot]"));
    const prevBtn = carousel.querySelector("[data-hero-prev]");
    const nextBtn = carousel.querySelector("[data-hero-next]");
    if (slides.length < 2) return;

    let currentIndex = 0;
    let timerId = null;
    const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function goToSlide(index) {
      currentIndex = (index + slides.length) % slides.length;
      slides.forEach((slide, idx) => {
        const active = idx === currentIndex;
        slide.classList.toggle("is-active", active);
        slide.hidden = !active;
      });
      dots.forEach((dot, idx) => {
        const active = idx === currentIndex;
        dot.classList.toggle("is-active", active);
        dot.setAttribute("aria-selected", String(active));
      });
    }

    function startAuto() {
      if (prefersReducedMotion) return;
      stopAuto();
      timerId = window.setInterval(() => {
        goToSlide(currentIndex + 1);
      }, 6500);
    }

    function stopAuto() {
      if (timerId) {
        window.clearInterval(timerId);
        timerId = null;
      }
    }

    if (prevBtn) {
      prevBtn.addEventListener("click", () => {
        goToSlide(currentIndex - 1);
        startAuto();
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", () => {
        goToSlide(currentIndex + 1);
        startAuto();
      });
    }

    dots.forEach((dot, idx) => {
      dot.addEventListener("click", () => {
        goToSlide(idx);
        startAuto();
      });
    });

    carousel.addEventListener("mouseenter", stopAuto);
    carousel.addEventListener("mouseleave", startAuto);
    carousel.addEventListener("focusin", stopAuto);
    carousel.addEventListener("focusout", startAuto);
    startAuto();
  });

  /* Homepage Featured Collections Tab Switcher ("New In", "Trending Now", "Best Sellers") */
  document.querySelectorAll("[data-collection-tabs]").forEach((section) => {
    const tabs = Array.from(section.querySelectorAll("[data-collection-tab]"));
    const panels = Array.from(section.querySelectorAll("[data-collection-panel]"));
    if (tabs.length === 0 || panels.length === 0) return;

    function activateTab(targetKey) {
      tabs.forEach((tab) => {
        const active = tab.getAttribute("data-collection-tab") === targetKey;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      panels.forEach((panel) => {
        const active = panel.getAttribute("data-collection-panel") === targetKey;
        panel.classList.toggle("is-active", active);
        panel.hidden = !active;
      });
    }

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        activateTab(tab.getAttribute("data-collection-tab"));
      });
    });
  });

  /* Product Detail Page (PDP) Controller: Gallery Thumbs, Variant Swatches/Pills & Quantity Stepper */
  document.querySelectorAll("[data-pdp-root]").forEach((pdpRoot) => {
    const mainImg = document.getElementById("pdp-main-image");
    const lightboxImg = document.getElementById("pdp-lightbox-image");
    const thumbs = Array.from(pdpRoot.querySelectorAll("[data-pdp-thumb]"));

    thumbs.forEach((thumb) => {
      thumb.addEventListener("click", () => {
        const src = thumb.getAttribute("data-img-src");
        const alt = thumb.getAttribute("data-img-alt");
        if (mainImg && src) {
          mainImg.src = src;
          if (alt) mainImg.alt = alt;
        }
        if (lightboxImg && src) {
          lightboxImg.src = src;
          if (alt) lightboxImg.alt = alt;
        }
        thumbs.forEach((t) => {
          const active = t === thumb;
          t.classList.toggle("is-active", active);
          t.setAttribute("aria-selected", String(active));
        });
      });
    });

    const variantSelect = pdpRoot.querySelector("[data-pdp-variant-select]");
    const variantPills = Array.from(pdpRoot.querySelectorAll("[data-pdp-variant-pill]"));
    const colorBtns = Array.from(pdpRoot.querySelectorAll("[data-pdp-color-btn]"));
    const selectedColorEl = pdpRoot.querySelector("[data-pdp-selected-color]");
    const selectedSizeEl = pdpRoot.querySelector("[data-pdp-selected-size]");
    const skuEl = pdpRoot.querySelector("[data-pdp-sku]");
    const priceEl = pdpRoot.querySelector("[data-pdp-price]");
    const qtyInput = pdpRoot.querySelector("[data-qty-input]");
    const qtyDec = pdpRoot.querySelector("[data-qty-dec]");
    const qtyInc = pdpRoot.querySelector("[data-qty-inc]");

    function clampQuantity() {
      if (!qtyInput) return;
      const min = parseInt(qtyInput.getAttribute("min") || "1", 10) || 1;
      const max = parseInt(qtyInput.getAttribute("max") || "99", 10) || 99;
      let val = parseInt(qtyInput.value || "1", 10);
      if (Number.isNaN(val) || val < min) val = min;
      if (val > max) val = max;
      qtyInput.value = String(val);
    }

    function syncVariantState(variantId) {
      if (variantSelect && String(variantSelect.value) !== String(variantId)) {
        variantSelect.value = String(variantId);
      }

      let activePill = null;
      variantPills.forEach((pill) => {
        const isMatch = pill.getAttribute("data-pdp-variant-pill") === String(variantId);
        pill.classList.toggle("is-active", isMatch);
        pill.classList.toggle("is-selected", isMatch);
        pill.setAttribute("aria-checked", String(isMatch));
        if (isMatch) activePill = pill;
      });

      if (!activePill) return;

      const color = activePill.getAttribute("data-variant-color") || "";
      const size = activePill.getAttribute("data-variant-size") || "";
      const sku = activePill.getAttribute("data-variant-sku") || "";
      const price = activePill.getAttribute("data-variant-price") || "";
      const stock = parseInt(activePill.getAttribute("data-variant-stock") || "99", 10) || 99;

      if (selectedColorEl && color) selectedColorEl.textContent = color;
      if (selectedSizeEl && size) selectedSizeEl.textContent = size;
      if (skuEl && sku) skuEl.textContent = `SKU: ${sku}`;
      if (priceEl && price) {
        const symbol = priceEl.getAttribute("data-currency") || "৳";
        priceEl.textContent = `${symbol}${price}`;
      }

      colorBtns.forEach((btn) => {
        const isMatch = btn.getAttribute("data-pdp-color-btn") === color;
        btn.classList.toggle("is-selected", isMatch);
        btn.setAttribute("aria-checked", String(isMatch));
      });

      if (qtyInput && stock > 0) {
        qtyInput.setAttribute("max", String(stock));
        clampQuantity();
      }
    }

    variantPills.forEach((pill) => {
      pill.addEventListener("click", () => {
        if (pill.disabled) return;
        syncVariantState(pill.getAttribute("data-pdp-variant-pill"));
      });
    });

    colorBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetColor = btn.getAttribute("data-pdp-color-btn");
        const matchingPill = variantPills.find(
          (p) => !p.disabled && p.getAttribute("data-variant-color") === targetColor
        );
        if (matchingPill) {
          syncVariantState(matchingPill.getAttribute("data-pdp-variant-pill"));
        }
      });
    });

    if (variantSelect) {
      variantSelect.addEventListener("change", () => {
        syncVariantState(variantSelect.value);
      });
    }

    if (qtyDec && qtyInput) {
      qtyDec.addEventListener("click", () => {
        qtyInput.value = String((parseInt(qtyInput.value || "1", 10) || 1) - 1);
        clampQuantity();
      });
    }

    if (qtyInc && qtyInput) {
      qtyInc.addEventListener("click", () => {
        qtyInput.value = String((parseInt(qtyInput.value || "1", 10) || 1) + 1);
        clampQuantity();
      });
    }

    if (qtyInput) {
      qtyInput.addEventListener("change", clampQuantity);
    }

    /* --- 9. Cart Drawer Free Shipping Progress Bar Sync --- */
    function syncCartShippingProgress() {
      const bar = document.querySelector("[data-shipping-progress]");
      if (!bar) return;
      const subtotal = parseFloat(bar.getAttribute("data-subtotal") || "0") || 0;
      const threshold = parseFloat(bar.getAttribute("data-threshold") || "2500") || 2500;
      const currency = bar.getAttribute("data-currency") || "৳";
      const remainingEl = bar.querySelector("[data-shipping-remaining]");
      if (remainingEl && subtotal < threshold) {
        const remaining = Math.max(0, Math.round(threshold - subtotal));
        remainingEl.textContent = `${currency}${remaining}`;
      }
    }

    syncCartShippingProgress();

    const cartDrawerBody = document.getElementById("dm-cart-drawer-body");
    if (cartDrawerBody && typeof MutationObserver !== "undefined") {
      const observer = new MutationObserver(() => {
        syncCartShippingProgress();
      });
      observer.observe(cartDrawerBody, { childList: true });
    }
  });
})();
