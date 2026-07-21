(function () {
  function parseIntSafe(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function getRootElement(root, selector) {
    if (!root) return null;
    if (root === document) return document.querySelector(selector);
    return root.querySelector(selector);
  }

  function initManualOrderModal(root) {
    const scope = root || document;
    const form = getRootElement(scope, "#manual-order-form");
    if (!form || form.dataset.manualOrderBound === "true") return;
    form.dataset.manualOrderBound = "true";

    const groupsContainer = getRootElement(scope, "#manual-order-group-list");
    const addGroupButton = getRootElement(scope, "#manual-order-add-group");
    const submitButton = getRootElement(scope, "#manual-order-submit");
    const capacityDataNode = getRootElement(scope, "#manual-order-capacity-data");
    const productOptionsNode = getRootElement(scope, "#manual-order-product-options");
    if (!groupsContainer || !submitButton) return;

    const canSelectBranch = groupsContainer?.dataset.canSelectBranch === "1";
    let capacityData = {};

    try {
      capacityData = capacityDataNode ? JSON.parse(capacityDataNode.textContent || "{}") : {};
    } catch (error) {
      capacityData = {};
    }

    function normalizeSearchSelects(searchRoot) {
      if (!searchRoot) return;

      searchRoot.querySelectorAll(".ts-wrapper").forEach((wrapper) => {
        const underlyingSelect = wrapper.querySelector("select");
        if (underlyingSelect) {
          wrapper.replaceWith(underlyingSelect);
        } else {
          wrapper.remove();
        }
      });

      searchRoot.querySelectorAll("select.search-select").forEach((selectEl) => {
        selectEl.classList.remove("tomselected", "ts-hidden-accessible");
        selectEl.removeAttribute("tabindex");
        selectEl.removeAttribute("style");
      });
    }

    function getGroupBranchId(group) {
      if (canSelectBranch) {
        return group.querySelector(".manual-order-group-branch")?.value || "";
      }
      return group.querySelector(".manual-order-group-branch-hidden")?.value || "";
    }

    function syncGroupRows(group) {
      const branchId = getGroupBranchId(group);
      group.querySelectorAll(".manual-order-row-branch-id").forEach((input) => {
        input.value = branchId;
      });
    }

    function buildRow(group) {
      const productOptionsHtml = productOptionsNode ? productOptionsNode.innerHTML : "";
      const row = document.createElement("div");
      row.className = "manual-order-row";
      row.style.cssText = "border:1px solid var(--gray-200);border-radius:12px;padding:12px;";
      row.innerHTML = `
        <div style="display:grid;grid-template-columns:2fr 1fr auto;gap:8px;align-items:end;">
          <input type="hidden" name="branch_id[]" class="manual-order-row-branch-id" value="${getGroupBranchId(group)}">
          <div class="form-group" style="margin-bottom:0;">
            <label>Product *</label>
            <select name="product_id[]" class="form-input manual-order-product search-select" required>
              <option value="">Select product to order</option>
              ${productOptionsHtml}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label>Packets *</label>
            <input type="number" name="packets[]" class="form-input manual-order-packets" required min="1" step="1" placeholder="e.g. 5">
          </div>
          <div style="display:flex;align-items:flex-end;justify-content:flex-end;">
            <button type="button" class="btn btn-outline manual-order-remove-row" style="white-space:nowrap;touch-action:manipulation;">Remove</button>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap;">
          <label style="display:inline-flex;align-items:center;gap:8px;margin:0;cursor:pointer;" class="text-xs text-muted">
            <input type="checkbox" class="manual-order-exempt-checkbox" style="width:16px;height:16px;border:1px solid var(--gray-300);border-radius:4px;cursor:pointer;flex-shrink:0;">
            <input type="hidden" name="exempt_from_auto_reorder[]" class="manual-order-exempt-hidden" value="0">
            Exempt from auto reorder
          </label>
        </div>
        <div class="manual-order-helper text-xs text-muted" style="margin-top:6px;"></div>
        <div class="manual-order-error text-xs text-danger" style="margin-top:4px;display:none;"></div>
      `;
      return row;
    }

    function addRow(group) {
      const rowsContainer = group.querySelector(".manual-order-rows");
      const row = buildRow(group);
      rowsContainer.appendChild(row);
      if (typeof initTomSelect === "function") initTomSelect(row);
      updateGroupRemoveButtons();
      validateRows();
    }

    function addGroup() {
      const template = groupsContainer.querySelector(".manual-order-group");
      if (!template) return;
      const clone = template.cloneNode(true);
      normalizeSearchSelects(clone);
      clone.querySelectorAll("input, select, textarea").forEach((field) => {
        if (field.matches(".manual-order-group-branch-hidden")) return;
        if (field.tagName === "SELECT") {
          field.selectedIndex = 0;
        } else if (field.type !== "hidden") {
          field.value = "";
        }
      });
      const rowsContainer = clone.querySelector(".manual-order-rows");
      rowsContainer.innerHTML = "";
      addRow(clone);
      groupsContainer.appendChild(clone);
      if (typeof initTomSelect === "function") initTomSelect(clone);
      updateGroupRemoveButtons();
      syncGroupRows(clone);
      validateRows();
    }

    function updateGroupRemoveButtons() {
      const groups = groupsContainer.querySelectorAll(".manual-order-group");
      groups.forEach((group) => {
        const removeBtn = group.querySelector(".manual-order-remove-group");
        if (!removeBtn) return;
        const shouldShow = groups.length > 1;
        removeBtn.style.display = shouldShow ? "" : "none";
        removeBtn.disabled = !shouldShow;
      });
    }


    function validateRows() {
      const rows = groupsContainer.querySelectorAll(".manual-order-row");
      if (!submitButton || !rows.length) return;

      let allValid = true;

      rows.forEach((row) => {
        const group = row.closest(".manual-order-group");
        const productSelect = row.querySelector(".manual-order-product");
        const packetsInput = row.querySelector(".manual-order-packets");
        const helper = row.querySelector(".manual-order-helper");
        const error = row.querySelector(".manual-order-error");
        const branchId = row.querySelector(".manual-order-row-branch-id")?.value || "";
        const branchSelect = group?.querySelector(".manual-order-group-branch");
        const selectedOption = productSelect?.selectedOptions?.[0] || null;
        const productId = productSelect?.value || "";
        const branchCapacity = (capacityData[productId] && capacityData[productId][branchId]) ? capacityData[productId][branchId] : null;
        const absoluteMaxPackets = selectedOption ? parseIntSafe(selectedOption.getAttribute("data-max-packets")) : null;
        const packQty = selectedOption ? parseIntSafe(selectedOption.getAttribute("data-pack-qty")) : null;
        const maxStock = selectedOption ? parseIntSafe(selectedOption.getAttribute("data-max-stock")) : null;
        const availableUnits = branchCapacity ? parseIntSafe(branchCapacity.available_units) : null;
        const currentUnits = branchCapacity ? parseIntSafe(branchCapacity.current_units) : null;
        const pendingUnits = branchCapacity ? parseIntSafe(branchCapacity.pending_units) : null;

        let rowValid = true;
        let errorText = "";
        let helperText = "Amount is in supplier packet sizes, not individual units.";

        if (canSelectBranch && !branchSelect?.value) {
          rowValid = false;
          errorText = "Select a branch for this group.";
        } else if (!productSelect?.value) {
          rowValid = false;
          errorText = "Select a product.";
        } else if (!packetsInput?.value) {
          rowValid = false;
          errorText = "Enter packets to order.";
        } else {
          const packets = parseIntSafe(packetsInput.value);
          if (!packets || packets <= 0) {
            rowValid = false;
            errorText = "Packets must be a positive whole number.";
          } else if (absoluteMaxPackets !== null && packets > absoluteMaxPackets) {
            rowValid = false;
            errorText = `Max ${absoluteMaxPackets} packet(s) allowed.`;
          }
        }

        if (packetsInput && absoluteMaxPackets !== null) {
          packetsInput.max = String(absoluteMaxPackets);
        }

        if (branchCapacity && packQty && maxStock !== null && availableUnits !== null && currentUnits !== null && pendingUnits !== null) {
          helperText = `Needed: ${branchCapacity.max_packets} packets (${availableUnits} units) | Available: ${currentUnits} units | Pending orders: ${pendingUnits} units | Max stock: ${maxStock} | Pack size: ${packQty}.`;
        } else if (absoluteMaxPackets !== null && packQty && maxStock) {
          helperText = `Max ${absoluteMaxPackets} packet(s) allowed.`;
        }

        if (!rowValid) {
          allValid = false;
          if (error) {
            error.textContent = errorText;
            error.style.display = "block";
          }
          if (packetsInput) {
            packetsInput.style.borderColor = "var(--danger)";
          }
        } else {
          if (error) {
            error.textContent = "";
            error.style.display = "none";
          }
          if (packetsInput) {
            packetsInput.style.borderColor = "";
          }
        }

        if (helper) {
          helper.textContent = helperText;
        }
      });

      submitButton.disabled = false;
      submitButton.dataset.manualOrderValid = allValid ? "1" : "0";
    }

    function updateSupplierStrategyUI() {
      const strategy = form.querySelector('input[name="supplier_strategy"]:checked')?.value || "cascade";
      const selectedWrap = getRootElement(scope, "#manual-order-selected-suppliers");
      const unregisteredWrap = getRootElement(scope, "#manual-order-unregistered-supplier");
      if (!selectedWrap || !unregisteredWrap) return;
      selectedWrap.style.display = strategy === "selected" ? "" : "none";
      unregisteredWrap.style.display = strategy === "unregistered" ? "" : "none";
    }

    function refreshBranchIds() {
      groupsContainer.querySelectorAll(".manual-order-group").forEach((group) => syncGroupRows(group));
    }

    if (canSelectBranch) {
      groupsContainer.querySelectorAll(".manual-order-group-branch").forEach((selectEl) => {
        selectEl.addEventListener("change", () => {
          syncGroupRows(selectEl.closest(".manual-order-group"));
          validateRows();
        });
      });
    }

    groupsContainer.querySelectorAll(".manual-order-group").forEach((group) => {
      if (group.querySelectorAll(".manual-order-row").length === 0) {
        addRow(group);
      }
      syncGroupRows(group);
    });

    function handleActionEvent(event) {
      const addRowButton = event.target.closest(".manual-order-add-row");
      if (addRowButton) {
        event.preventDefault();
        const group = addRowButton.closest(".manual-order-group");
        if (group) addRow(group);
        return;
      }

      const removeRowButton = event.target.closest(".manual-order-remove-row");
      if (removeRowButton) {
        event.preventDefault();
        const row = removeRowButton.closest(".manual-order-row");
        const group = removeRowButton.closest(".manual-order-group");
        if (!row || !group) return;
        const rowCount = group.querySelectorAll(".manual-order-row").length;
        if (rowCount <= 1) return;
        row.remove();
        updateGroupRemoveButtons();
        validateRows();
        return;
      }

      const removeGroupButton = event.target.closest(".manual-order-remove-group");
      if (removeGroupButton) {
        event.preventDefault();
        const group = removeGroupButton.closest(".manual-order-group");
        const groupCount = groupsContainer.querySelectorAll(".manual-order-group").length;
        if (!group || groupCount <= 1) return;
        group.remove();
        updateGroupRemoveButtons();
        validateRows();
      }
    }

    // Use click only so a single user action creates a single row on all devices.
    groupsContainer.addEventListener("click", handleActionEvent);

    addGroupButton?.addEventListener("click", (event) => {
      event.preventDefault();
      addGroup();
    });

    form.addEventListener("input", validateRows);
    form.addEventListener("change", (event) => {
      validateRows();
      if (event.target && event.target.name === "supplier_strategy") {
        updateSupplierStrategyUI();
      }
      if (event.target && event.target.classList.contains("manual-order-product")) {
        const row = event.target.closest(".manual-order-row");
        if (row) {
          const selectedOption = event.target.selectedOptions?.[0] || null;
          const checkbox = row.querySelector(".manual-order-exempt-checkbox");
          const hiddenInput = row.querySelector(".manual-order-exempt-hidden");
          if (checkbox && hiddenInput) {
            const isExempt = selectedOption?.dataset.exempt === "1";
            checkbox.checked = isExempt;
            hiddenInput.value = isExempt ? "1" : "0";
          }
        }
      }
      if (event.target && event.target.classList.contains("manual-order-exempt-checkbox")) {
        const row = event.target.closest(".manual-order-row");
        if (row) {
          const hiddenInput = row.querySelector(".manual-order-exempt-hidden");
          if (hiddenInput) {
            hiddenInput.value = event.target.checked ? "1" : "0";
          }
        }
      }
    });

    const closeButtons = form.querySelectorAll("[data-manual-order-close]");
    closeButtons.forEach((button) => {
      if (button.dataset.manualOrderCloseBound === "true") return;
      button.dataset.manualOrderCloseBound = "true";
      button.addEventListener("click", () => {
        const modalContainer = document.getElementById("modal-container");
        if (modalContainer) modalContainer.innerHTML = "";
      });
    });

    const backdrop = form.closest("[data-manual-order-backdrop]");
    if (backdrop && backdrop.dataset.manualOrderBackdropBound !== "true") {
      backdrop.dataset.manualOrderBackdropBound = "true";
      backdrop.addEventListener("click", (event) => {
        if (event.target !== backdrop) return;
        const modalContainer = document.getElementById("modal-container");
        if (modalContainer) modalContainer.innerHTML = "";
      });
    }

    form.addEventListener("htmx:afterRequest", (event) => {
      if (!event.detail || !event.detail.successful) return;
      const modalContainer = document.getElementById("modal-container");
      if (modalContainer) modalContainer.innerHTML = "";
    });

    if (typeof initTomSelect === "function") initTomSelect(groupsContainer);
    updateGroupRemoveButtons();
    refreshBranchIds();
    validateRows();
    updateSupplierStrategyUI();
  }

  function bootstrap(event) {
    const root = event && event.target ? event.target : document;
    initManualOrderModal(root);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initManualOrderModal(document));
  } else {
    initManualOrderModal(document);
  }

  document.body.addEventListener("htmx:load", bootstrap);
  document.body.addEventListener("htmx:afterSwap", bootstrap);
  window.initManualOrderModal = initManualOrderModal;
})();
