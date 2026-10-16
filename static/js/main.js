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
})();
