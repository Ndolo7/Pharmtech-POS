import json
import uuid
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction
from django.template.loader import render_to_string
from .models import Sale, SaleItem, Shift
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

            total_amount = sum(item["quantity"] * item["price"] for item in cart)

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

            for item in cart:
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
            for item in cart
        ]

        return render(request, "sales/partials/_receipt.html", {
            "sale": sale,
            "receipt_number": receipt_number,
            "cart": receipt_items,
            "total_amount": total_amount,
        })

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
        try:
            shift = Shift.objects.get(cashier=request.user, branch=request.user.branch, is_closed=False)
            shift.closing_cash_declared = form.cleaned_data["closing_cash"]
            shift.closing_mpesa_declared = form.cleaned_data.get("closing_mpesa") or 0
            shift.notes = form.cleaned_data.get("notes", "")
            shift.end_time = timezone.now()
            shift.is_closed = True
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
