import json
import uuid
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction
from django.template.loader import render_to_string
from .models import Sale, SaleItem, Shift, ShiftExpense
from .forms import ShiftForm, CloseShiftForm, SaleForm
from products.models import Product, Stock, StockMovement


def _active_shift_for_user(user):
    return Shift.objects.filter(
        cashier=user,
        branch=user.branch,
        is_closed=False,
    ).first()


def _build_product_data(branch, q=""):
    products = Product.objects.filter(is_active=True)
    if q:
        products = products.filter(name__icontains=q) | products.filter(barcode__icontains=q)

    data = []
    for product in products.order_by("name"):
        stock_qty = product.current_stock(branch) if branch else product.current_stock()
        data.append({"product": product, "stock": stock_qty})
    return data


def _error_fragment(message, status=200):
    return HttpResponse(f'<div class="message message-error">{message}</div>', status=status)


def _parse_shift_expenses(request):
    amount_rows = request.POST.getlist("expense_amount[]")
    description_rows = request.POST.getlist("expense_description[]")
    row_count = max(len(amount_rows), len(description_rows))
    expenses = []

    for idx in range(row_count):
        amount_raw = (amount_rows[idx] if idx < len(amount_rows) else "").strip()
        description = (description_rows[idx] if idx < len(description_rows) else "").strip()

        if not amount_raw and not description:
            continue
        if not amount_raw or not description:
            return [], f"Expense row {idx + 1} requires both amount and description."

        try:
            amount = Decimal(amount_raw)
        except InvalidOperation:
            return [], f"Expense row {idx + 1} has an invalid amount."

        if amount <= 0:
            return [], f"Expense row {idx + 1} amount must be greater than zero."

        expenses.append({"amount": amount, "description": description})

    return expenses, None


def _shift_action_response(request):
    shift = _active_shift_for_user(request.user)
    ctx = {
        "shift": shift,
        "start_form": ShiftForm(),
        "close_form": CloseShiftForm(),
        "shift_active": bool(shift),
    }

    payload = render_to_string("sales/partials/_current_shift.html", ctx, request=request)
    payload += render_to_string("sales/partials/_shift_modal.html", {**ctx, "oob": True}, request=request)
    payload += render_to_string("partials/_shift_status_badge.html", {**ctx, "oob": True}, request=request)
    payload += render_to_string("partials/_messages.html", {"oob": True}, request=request)
    return HttpResponse(payload)


@login_required
def pos_view(request):
    """POS terminal page"""
    shift = _active_shift_for_user(request.user)
    branch = request.user.branch

    products = Product.objects.filter(is_active=True).order_by("name")
    product_data = []
    for p in products:
        stock_qty = p.current_stock(branch) if branch else p.current_stock()
        product_data.append({"id": p.id, "name": p.name, "price": float(p.unit_price), "stock": stock_qty})

    ctx = {
        "shift": shift,
        "shift_active": bool(shift),
        "products_json": json.dumps(product_data),
        "product_data_for_grid": _build_product_data(branch),
        "start_form": ShiftForm(),
        "close_form": CloseShiftForm(),
    }
    return render(request, "sales/pos.html", ctx)


@require_GET
@login_required
def product_search_view(request):
    """HTMX: search products for POS grid"""
    q = request.GET.get("q", "").strip()
    branch = request.user.branch
    product_data = _build_product_data(branch, q=q)
    return render(request, "sales/partials/_product_grid.html", {"product_data": product_data})


@require_POST
@login_required
def process_sale_view(request):
    """Process a sale submitted from Alpine.js cart via HTMX"""
    try:
        with transaction.atomic():
            form = SaleForm(request.POST)
            if not form.is_valid():
                return _error_fragment("Please check your payment details and try again.")

            cart = json.loads(form.cleaned_data.get("cart_json") or "[]")
            if not cart:
                return _error_fragment("Cart is empty.")

            branch = request.user.branch
            shift = _active_shift_for_user(request.user)
            if not shift:
                return _error_fragment("No active shift. Please start a shift first.")

            payment_method = form.cleaned_data["payment_method"]
            cash_amount = float(form.cleaned_data.get("cash_amount") or 0)
            mpesa_amount = float(form.cleaned_data.get("mpesa_amount") or 0)
            customer_name = form.cleaned_data.get("customer_name", "")
            customer_phone = form.cleaned_data.get("customer_phone", "")

            normalized_cart = []
            for item in cart:
                quantity = item.get("quantity", item.get("qty"))
                if quantity is None:
                    return _error_fragment("Invalid cart payload: missing quantity.")
                product_id = item.get("id")
                if not product_id:
                    return _error_fragment("Invalid cart payload: missing product id.")
                qty_int = int(quantity)
                if qty_int <= 0:
                    return _error_fragment("Invalid cart payload: quantity must be greater than zero.")
                normalized_cart.append(
                    {
                        "id": int(product_id),
                        "name": item.get("name", ""),
                        "price": float(item.get("price", 0)),
                        "quantity": qty_int,
                    }
                )

            total_amount = sum(item["quantity"] * item["price"] for item in normalized_cart)

            # Auto-fill single payment
            if payment_method == "cash":
                cash_amount = total_amount
                mpesa_amount = 0
            elif payment_method == "mpesa":
                mpesa_amount = total_amount
                cash_amount = 0
            elif round(cash_amount + mpesa_amount, 2) != round(total_amount, 2):
                return _error_fragment("For mixed payments, cash plus M-Pesa must equal the sale total.")

            receipt_number = f"RCP-{uuid.uuid4().hex[:8].upper()}"

            sale = Sale.objects.create(
                receipt_number=receipt_number,
                branch=branch,
                cashier=request.user,
                payment_method=payment_method,
                cash_amount=cash_amount,
                mpesa_amount=mpesa_amount,
                total_amount=total_amount,
                customer_name=customer_name,
                customer_phone=customer_phone,
                shift=shift,
            )

            for item in normalized_cart:
                product = get_object_or_404(Product, pk=item["id"])
                qty = int(item["quantity"])

                stock, _ = Stock.objects.get_or_create(product=product, branch=branch, defaults={"quantity": 0})
                if stock.quantity < qty:
                    raise Exception(f"Insufficient stock for {product.name}")

                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=qty,
                    unit_price=item["price"],
                )
                stock.quantity -= qty
                stock.save()

                StockMovement.objects.create(
                    product=product,
                    branch=branch,
                    movement_type="sale",
                    quantity=-qty,
                    reference=receipt_number,
                    created_by=request.user,
                )

        receipt_items = [
            {**item, "line_total": item["quantity"] * item["price"]}
            for item in normalized_cart
        ]

        response = render(request, "sales/partials/_receipt.html", {
            "sale": sale,
            "receipt_number": receipt_number,
            "cart": receipt_items,
            "total_amount": total_amount,
        })
        # POS handles delayed refresh in frontend so the success popup remains visible.
        response["X-Skip-HX-Refresh"] = "true"
        response["HX-Trigger"] = json.dumps(
            {"sale-processed": {"message": f"Sale {receipt_number} successful."}}
        )
        return response

    except Exception as e:
        return _error_fragment(f"Error: {e}")


@require_POST
@login_required
def start_shift_view(request):
    form = ShiftForm(request.POST)
    if not request.user.branch:
        messages.error(request, "You are not assigned to a branch yet.")
        return _shift_action_response(request) if request.htmx else redirect("/sales/")

    if form.is_valid():
        branch = request.user.branch
        existing = _active_shift_for_user(request.user)
        if existing:
            messages.warning(request, "You already have an active shift.")
        else:
            Shift.objects.create(
                cashier=request.user,
                branch=branch,
                opening_cash=form.cleaned_data["opening_cash"],
            )
            request.user.is_active_shift = True
            request.user.save(update_fields=["is_active_shift"])
            messages.success(request, "Shift started successfully.")
    else:
        messages.error(request, "Opening cash is required.")

    return _shift_action_response(request) if request.htmx else redirect("/sales/")


@require_POST
@login_required
def close_shift_view(request):
    form = CloseShiftForm(request.POST)
    if form.is_valid():
        expenses, expense_error = _parse_shift_expenses(request)
        if expense_error:
            messages.error(request, expense_error)
            return _shift_action_response(request) if request.htmx else redirect("/sales/")
        try:
            with transaction.atomic():
                shift = Shift.objects.select_for_update().get(cashier=request.user, branch=request.user.branch, is_closed=False)
                shift.closing_cash_declared = form.cleaned_data["closing_cash"]
                shift.closing_mpesa_declared = form.cleaned_data.get("closing_mpesa") or 0
                shift.notes = form.cleaned_data.get("notes", "")
                shift.end_time = timezone.now()
                shift.is_closed = True

                if expenses:
                    ShiftExpense.objects.bulk_create(
                        [
                            ShiftExpense(
                                shift=shift,
                                amount=expense["amount"],
                                description=expense["description"],
                            )
                            for expense in expenses
                        ]
                    )

                shift.calculate_variances()
                shift.save()

            request.user.is_active_shift = False
            request.user.save(update_fields=["is_active_shift"])
            messages.success(request, f"Shift closed. Cash variance: KES {shift.cash_variance:.2f}")
        except Shift.DoesNotExist:
            messages.error(request, "No active shift found.")
    else:
        messages.error(request, "Please provide valid closing figures.")
    return _shift_action_response(request) if request.htmx else redirect("/sales/")


@require_GET
@login_required
def current_shift_view(request):
    shift = _active_shift_for_user(request.user)
    ctx = {"shift": shift, "close_form": CloseShiftForm(), "shift_active": bool(shift)}
    return render(request, "sales/partials/_current_shift.html", ctx)


@require_GET
@login_required
def current_shift_sales_view(request):
    shift = _active_shift_for_user(request.user)
    if not shift:
        return render(
            request,
            "sales/partials/_session_sales_modal_content.html",
            {"shift": None, "sales": [], "sales_summary": []},
        )

    sales = shift.sale_set.select_related("cashier").prefetch_related("items__product").order_by("-created_at")
    summary_by_product = {}
    for sale in sales:
        for item in sale.items.all():
            product_id = item.product_id
            if product_id not in summary_by_product:
                summary_by_product[product_id] = {
                    "name": item.product.name,
                    "quantity": 0,
                    "prices": set(),
                    "total": Decimal("0.00"),
                }

            summary_by_product[product_id]["quantity"] += item.quantity
            summary_by_product[product_id]["prices"].add(item.unit_price)
            summary_by_product[product_id]["total"] += item.total_price

    sales_summary = []
    for item in summary_by_product.values():
        price_values = sorted(item["prices"])
        sales_summary.append(
            {
                "name": item["name"],
                "quantity": item["quantity"],
                "price_display": ", ".join([f"KES {price:.2f}" for price in price_values]),
                "total": item["total"],
            }
        )
    sales_summary.sort(key=lambda row: row["name"].lower())

    return render(
        request,
        "sales/partials/_session_sales_modal_content.html",
        {"shift": shift, "sales": sales, "sales_summary": sales_summary},
    )
