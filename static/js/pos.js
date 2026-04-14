function posTerminal() {
  return {
    cart: [],
    paymentMethod: "cash",
    cashAmount: "",
    mpesaAmount: "",
    customerName: "",
    customerPhone: "",
    processing: false,
    shift_active: false,
    showCloseShift: false,
    showSessionSalesModal: false,
    uiError: "",
    saleSuccessVisible: false,
    saleSuccessMessage: "",
    saleSuccessTimer: null,
    lastSaleHandledAt: 0,

    init() {
      window.__posTerminal = this;
      this.syncShiftState();

      // Prevent duplicate bindings if init() runs more than once on this page.
      if (window.__posProductClickHandler) {
        document.removeEventListener("click", window.__posProductClickHandler);
      }

      this._productClickHandler = (event) => {
        const card = event.target.closest("[data-product-id]");
        if (!card) return;

        const id = parseInt(card.dataset.productId, 10);
        const product = (window.POS_PRODUCTS || []).find((item) => item.id === id);
        if (product) this.addItem(product);
      };
      document.addEventListener("click", this._productClickHandler);
      window.__posProductClickHandler = this._productClickHandler;

      document.body.addEventListener("htmx:afterSwap", (event) => {
        this.syncShiftState();
        this.consumeCartResetSignal();
      });

      document.body.addEventListener("htmx:oobAfterSwap", () => {
        this.syncShiftState();
        this.consumeCartResetSignal();
      });

      if (window.__posSaleProcessedHandler) {
        document.body.removeEventListener("sale-processed", window.__posSaleProcessedHandler);
      }
      this._saleProcessedHandler = (event) => {
        this.handleSaleProcessed(event?.detail || {});
      };
      document.body.addEventListener("sale-processed", this._saleProcessedHandler);
      window.__posSaleProcessedHandler = this._saleProcessedHandler;

      this.consumeCartResetSignal();
    },

    syncShiftState() {
      const panel = document.getElementById("current-shift-panel");
      this.shift_active = panel ? panel.dataset.shiftActive === "1" : false;
      if (!this.shift_active) {
        this.showCloseShift = false;
        this.showSessionSalesModal = false;
        this.clearUiError();
      }
    },

    consumeCartResetSignal() {
      const hook = document.getElementById("cart-reset-hook");
      if (!hook) return;
      const signal = hook.querySelector("[data-reset-cart]");
      if (!signal) return;
      this.resetCart();
      hook.innerHTML = "";
    },

    addItem(product) {
      if (product.stock <= 0) {
        this.setUiError(`Product ${product.name} is out of stock.`);
        return;
      }

      const existing = this.cart.find((item) => item.id === product.id);
      if (existing) {
        if (existing.quantity >= product.stock) {
          this.raiseStockException(existing, existing.quantity + 1);
          return;
        }
        existing.quantity += 1;
        existing.qtyInput = String(existing.quantity);
        this.clearUiError();
        return;
      }

      this.cart.push({
        id: product.id,
        name: product.name,
        price: product.price,
        quantity: 1,
        qtyInput: "1",
        stock: product.stock,
      });
      this.clearUiError();
    },

    setUiError(message) {
      this.uiError = message;
    },

    clearUiError() {
      this.uiError = "";
    },

    parseQtyInput(value) {
      if (value === null || value === undefined || value === "") return null;
      const parsed = Number(value);
      if (!Number.isInteger(parsed)) return null;
      return parsed;
    },

    isQtyInputInvalid(item) {
      const qty = this.parseQtyInput(item.qtyInput);
      return qty === null || qty <= 0 || qty > item.stock;
    },

    isCartStockAvailable() {
      return this.cart.every((item) => !this.isQtyInputInvalid(item));
    },

    raiseStockException(item, attemptedQty) {
      this.setUiError(
        `Requested quantity (${attemptedQty}) for ${item.name} exceeds available stock (${item.stock}).`,
      );
    },

    commitManualQty(id) {
      const item = this.cart.find((entry) => entry.id === id);
      if (!item) return;

      const parsedQty = this.parseQtyInput(item.qtyInput);
      if (parsedQty === null || parsedQty <= 0) return;
      if (parsedQty > item.stock) {
        this.raiseStockException(item, parsedQty);
        return;
      }

      item.quantity = parsedQty;
      item.qtyInput = String(parsedQty);
      this.clearUiError();
    },

    updateQty(id, newQty) {
      if (newQty <= 0) {
        this.removeItem(id);
        return;
      }

      const item = this.cart.find((entry) => entry.id === id);
      if (!item) return;

      if (newQty > item.stock) {
        this.raiseStockException(item, newQty);
        return;
      }

      item.quantity = newQty;
      item.qtyInput = String(newQty);
      this.clearUiError();
    },

    removeItem(id) {
      this.cart = this.cart.filter((item) => item.id !== id);
      if (this.isCartStockAvailable()) this.clearUiError();
    },

    getTotal() {
      return this.cart.reduce((sum, item) => sum + item.quantity * item.price, 0);
    },

    submitSale(event) {
      if (this.processing) {
        event.preventDefault();
        return;
      }
      if (!this.shift_active) {
        event.preventDefault();
        alert("Start a shift before processing sales.");
        return;
      }
      if (this.cart.length === 0) {
        event.preventDefault();
        return;
      }
      if (!this.isCartStockAvailable()) {
        event.preventDefault();
        this.setUiError("Please correct cart quantities to match available stock before submitting.");
        return;
      }
      this.clearUiError();
      this.processing = true;
    },

    openSessionSalesModal() {
      this.showSessionSalesModal = true;
      const content = document.getElementById("session-sales-modal-content");
      if (!content) return;
      content.innerHTML = '<div class="text-sm text-muted">Loading session sales...</div>';
    },

    onSaleComplete(event) {
      this.processing = false;
      if (!event.detail.successful) return;
      const xhr = event?.detail?.xhr;
      const responseText = event?.detail?.xhr?.responseText || "";
      const triggerHeader = xhr?.getResponseHeader("HX-Trigger") || "";
      const saleCompleted = responseText.includes("data-reset-cart") || triggerHeader.includes("sale-processed");
      if (!saleCompleted) return;

      this.handleSaleProcessed({});
    },

    handleSaleProcessed(detail) {
      const now = Date.now();
      if (now - this.lastSaleHandledAt < 1000) return;
      this.lastSaleHandledAt = now;

      this.consumeCartResetSignal();
      this.showSaleSuccess(detail?.message || "Sale processed successfully.");
      window.setTimeout(() => {
        window.location.reload();
      }, 4300);
    },

    showSaleSuccess(message) {
      if (this.saleSuccessTimer) {
        window.clearTimeout(this.saleSuccessTimer);
      }

      this.saleSuccessMessage = message || "Sale processed successfully.";
      this.saleSuccessVisible = true;
      this.saleSuccessTimer = window.setTimeout(() => {
        this.saleSuccessVisible = false;
      }, 4000);
    },

    resetCart() {
      this.cart = [];
      this.customerName = "";
      this.customerPhone = "";
      this.cashAmount = "";
      this.mpesaAmount = "";
      this.paymentMethod = "cash";
      this.clearUiError();
    },
  };
}
