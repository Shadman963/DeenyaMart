/**
 * DeenyaMart Async Cart Controller (cart.js)
 * Non-obstructive Add-to-Cart feedback, badge updates, and slide-over Cart Drawer sync.
 */
(function () {
  "use strict";

  function updateCartUI(data) {
    if (typeof data.cart_count !== "undefined") {
      document.querySelectorAll("[data-cart-count]").forEach((el) => {
        el.textContent = data.cart_count;
      });
    }
    if (data.drawer_html) {
      const drawerBody = document.getElementById("dm-cart-drawer-body");
      if (drawerBody) {
        drawerBody.innerHTML = data.drawer_html;
      }
    }
  }

  document.addEventListener("submit", async (event) => {
    const addForm = event.target.closest(".js-add-to-cart-form");
    if (addForm) {
      /* If user clicked a quick-buy button that should redirect to checkout, let form submit normally */
      if (event.submitter && event.submitter.name === "buy_now") {
        return;
      }

      event.preventDefault();
      const submitBtn = addForm.querySelector("button[type='submit']");
      const originalHtml = submitBtn ? submitBtn.innerHTML : "";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Adding…";
      }

      try {
        const response = await fetch(addForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
          body: new FormData(addForm),
        });
        const data = await response.json();
        updateCartUI(data);

        if (window.DeenyaUI) {
          window.DeenyaUI.showToast(data.message, {
            actionLabel: "View Bag",
            onAction: () => {
              const cartTrigger = document.querySelector("[data-open-dialog='dm-cart-drawer']");
              window.DeenyaUI.openDialog("dm-cart-drawer", cartTrigger);
            },
          });
        }
      } catch (err) {
        addForm.submit();
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = originalHtml;
        }
      }
      return;
    }

    const updateForm = event.target.closest(".js-cart-update-form");
    if (updateForm) {
      event.preventDefault();
      const formData = new FormData(updateForm);
      if (event.submitter && event.submitter.name) {
        formData.set(event.submitter.name, event.submitter.value);
      }

      try {
        const response = await fetch(updateForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
          body: formData,
        });
        const data = await response.json();
        updateCartUI(data);
        if (window.DeenyaUI && data.message) {
          window.DeenyaUI.announce(data.message);
        }
      } catch (err) {
        updateForm.submit();
      }
    }
  });
})();
