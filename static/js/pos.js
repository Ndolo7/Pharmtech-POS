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

    init() {
      window.__posTerminal = this;
      this.syncShiftState();

      document.addEventListener("click", (event) => {
        const card = event.target.closest("[data-product-id]");
        if (!card) return;

        const id = parseInt(card.dataset.productId, 10);
        const product = (window.POS_PRODUCTS || []).find((item) => item.id === id);
        if (product) this.addItem(product);
      });

      document.body.addEventListener("htmx:afterSwap", (event) => {
        this.syncShiftState();
        this.consumeCartResetSignal();
      });

      document.body.addEventListener("htmx:oobAfterSwap", () => {
        this.syncShiftState();
        this.consumeCartResetSignal();
      });

      this.consumeCartResetSignal();
    },

    syncShiftState() {
      const panel = document.getElementById("current-shift-panel");
      this.shift_active = panel ? panel.dataset.shiftActive === "1" : false;
      if (!this.shift_active) {
        this.showCloseShift = false;
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
        alert("Product out of stock");
        return;
      }

      const existing = this.cart.find((item) => item.id === product.id);
      if (existing) {
        if (existing.qty >= product.stock) {
          alert("Insufficient stock");
          return;
        }
        existing.qty += 1;
        return;
      }

      this.cart.push({
        id: product.id,
        name: product.name,
        price: product.price,
        qty: 1,
        stock: product.stock,
      });
    },

    updateQty(id, newQty) {
      if (newQty <= 0) {
        this.removeItem(id);
        return;
      }

      const item = this.cart.find((entry) => entry.id === id);
      if (!item) return;

      if (newQty > item.stock) {
        alert("Insufficient stock");
        return;
      }

      item.qty = newQty;
    },

    removeItem(id) {
      this.cart = this.cart.filter((item) => item.id !== id);
    },

    getTotal() {
      return this.cart.reduce((sum, item) => sum + item.qty * item.price, 0);
    },

    submitSale() {
      if (!this.shift_active) {
        alert("Start a shift before processing sales.");
        return;
      }
      if (this.cart.length === 0) return;
      this.processing = true;
      htmx.trigger(document.getElementById("sale-form"), "submit");
    },

    onSaleComplete(event) {
      this.processing = false;
      if (!event.detail.successful) return;
      this.consumeCartResetSignal();
    },

    resetCart() {
      this.cart = [];
      this.customerName = "";
      this.customerPhone = "";
      this.cashAmount = "";
      this.mpesaAmount = "";
      this.paymentMethod = "cash";
    },
  };
}
