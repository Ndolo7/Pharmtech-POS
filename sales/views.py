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
from django.db.models import Count, Q, Sum
from django.template.loader import render_to_string
from django.urls import reverse
from branches.models import Branch
from products.sms import normalize_phone_number
from .models import CreditAccount, CreditTransaction, Sale, SaleItem, Shift, ShiftExpense
from .forms import CloseShiftForm, CreditRepaymentForm, SaleForm, ShiftForm
from products.models import Product, Stock, StockMovement


def _active_shift_for_user(user):
    return Shift.objects.filter(
        cashier=user,
        branch=user.branch,
        is_closed=False,
    ).first()


def _can_select_credit_branch(user):
    return user.is_superuser or user.is_staff or getattr(user, "role", "") == "super_admin"


def _credit_branch_context(request):
    can_select_branch = _can_select_credit_branch(request.user)
    branch_options = Branch.objects.filter(is_active=True).order_by("name") if can_select_branch else []
    selected_branch_id = (request.GET.get("branch_id") or request.POST.get("branch_id") or "all").strip()
    active_branch = None

    if can_select_branch:
        if selected_branch_id != "all":
            active_branch = branch_options.filter(pk=selected_branch_id).first()
            if not active_branch:
                selected_branch_id = "all"
    else:
        if request.user.branch_id:
            active_branch = Branch.objects.filter(pk=request.user.branch_id).first()
            selected_branch_id = str(active_branch.pk) if active_branch else "all"
        else:
            selected_branch_id = "all"

    return {
        "can_select_branch": can_select_branch,
        "branch_options": branch_options,
        "active_branch": active_branch,
        "active_branch_id": selected_branch_id,
    }


def _credit_ledger_redirect(branch_id):
    url = reverse("credit-ledger")
    normalized_branch_id = (branch_id or "").strip()
    if normalized_branch_id:
        url = f"{url}?branch_id={normalized_branch_id}"
    return redirect(url)


def _resolve_credit_account(branch, customer_name, customer_phone):
    normalized_name = (customer_name or "").strip()
    normalized_phone = normalize_phone_number(customer_phone) or ""

    if not normalized_name and not normalized_phone:
        raise ValueError("Credit sales require customer name or phone number.")

    account_qs = CreditAccount.objects.select_for_update().filter(branch=branch)
    account = None
    if normalized_phone:
        account = account_qs.filter(customer_phone=normalized_phone).first()
    if not account and normalized_name:
        account = account_qs.filter(customer_name__iexact=normalized_name, customer_phone=normalized_phone).first()

    if account:
        changed_fields = []
        if normalized_name and account.customer_name != normalized_name:
            account.customer_name = normalized_name
            changed_fields.append("customer_name")
        if normalized_phone and account.customer_phone != normalized_phone:
            account.customer_phone = normalized_phone
            changed_fields.append("customer_phone")
        if changed_fields:
            account.save(update_fields=[*changed_fields, "updated_at"])
        return account

    return CreditAccount.objects.create(
        branch=branch,
        customer_name=normalized_name or normalized_phone,
        customer_phone=normalized_phone,
    )


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
            cash_amount = Decimal(str(form.cleaned_data.get("cash_amount") or 0))
            mpesa_amount = Decimal(str(form.cleaned_data.get("mpesa_amount") or 0))
            credit_amount = Decimal(str(form.cleaned_data.get("credit_amount") or 0))
            customer_name = (form.cleaned_data.get("customer_name", "") or "").strip()
            customer_phone = (form.cleaned_data.get("customer_phone", "") or "").strip()

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
                        "price": Decimal(str(item.get("price", 0))),
                        "quantity": qty_int,
                    }
                )

            total_amount = sum((item["quantity"] * item["price"] for item in normalized_cart), Decimal("0.00"))

            # Auto-fill single payment
            if payment_method == "cash":
                cash_amount = total_amount
                mpesa_amount = Decimal("0.00")
                credit_amount = Decimal("0.00")
            elif payment_method == "mpesa":
                mpesa_amount = total_amount
                cash_amount = Decimal("0.00")
                credit_amount = Decimal("0.00")
            elif payment_method == "credit":
                credit_amount = total_amount
                cash_amount = Decimal("0.00")
                mpesa_amount = Decimal("0.00")
            elif payment_method == "mixed":
                if (cash_amount + mpesa_amount + credit_amount).quantize(Decimal("0.01")) != total_amount.quantize(Decimal("0.01")):
                    return _error_fragment("For mixed payments, cash + M-Pesa + credit must equal the sale total.")
            else:
                return _error_fragment("Invalid payment method selected.")

            if credit_amount > Decimal("0.00") and not (customer_name or customer_phone):
                return _error_fragment("Customer name or phone is required for credit sales.")

            receipt_number = f"RCP-{uuid.uuid4().hex[:8].upper()}"

            sale = Sale.objects.create(
                receipt_number=receipt_number,
                branch=branch,
                cashier=request.user,
                payment_method=payment_method,
                cash_amount=cash_amount,
                mpesa_amount=mpesa_amount,
                credit_amount=credit_amount,
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

            if credit_amount > Decimal("0.00"):
                credit_account = _resolve_credit_account(branch, customer_name, customer_phone)
                CreditTransaction.objects.create(
                    account=credit_account,
                    sale=sale,
                    branch=branch,
                    transaction_type=CreditTransaction.TYPE_CHARGE,
                    amount=credit_amount,
                    notes=f"Credit sale {receipt_number}",
                    created_by=request.user,
                )
                credit_account.outstanding_balance += credit_amount
                credit_account.save(update_fields=["outstanding_balance", "updated_at"])

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


@require_GET
@login_required
def credit_ledger_view(request):
    branch_ctx = _credit_branch_context(request)
    active_branch = branch_ctx["active_branch"]
    search = (request.GET.get("q") or "").strip()

    accounts = CreditAccount.objects.all()
    transactions = CreditTransaction.objects.select_related("account", "sale", "branch", "created_by")
    if active_branch:
        accounts = accounts.filter(branch=active_branch)
        transactions = transactions.filter(branch=active_branch)

    if search:
        accounts = accounts.filter(Q(customer_name__icontains=search) | Q(customer_phone__icontains=search))

    outstanding_accounts = accounts.filter(outstanding_balance__gt=0).order_by("-outstanding_balance", "customer_name")
    recent_transactions = transactions.order_by("-created_at", "-id")[:80]

    today = timezone.localdate()
    collected_today = transactions.filter(
        transaction_type=CreditTransaction.TYPE_REPAYMENT,
        created_at__date=today,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    summary = {
        "total_outstanding": outstanding_accounts.aggregate(total=Sum("outstanding_balance"))["total"] or Decimal("0.00"),
        "customers_with_balance": outstanding_accounts.count(),
        "tracked_customers": accounts.aggregate(total=Count("id"))["total"] or 0,
        "collected_today": collected_today,
    }

    return render(
        request,
        "sales/credit_ledger.html",
        {
            "outstanding_accounts": outstanding_accounts,
            "recent_transactions": recent_transactions,
            "summary": summary,
            "search": search,
            "repayment_form": CreditRepaymentForm(),
            **branch_ctx,
        },
    )


@require_POST
@login_required
def record_credit_repayment_view(request, account_id: int):
    branch_ctx = _credit_branch_context(request)
    branch_id = request.POST.get("branch_id") or branch_ctx["active_branch_id"]

    form = CreditRepaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please provide a valid repayment amount and payment method.")
        return _credit_ledger_redirect(branch_id)

    posted_account_id = form.cleaned_data["account_id"]
    if int(posted_account_id) != int(account_id):
        messages.error(request, "Invalid repayment account details.")
        return _credit_ledger_redirect(branch_id)

    account_qs = CreditAccount.objects.select_related("branch")
    if branch_ctx["active_branch"]:
        account_qs = account_qs.filter(branch=branch_ctx["active_branch"])
    elif not branch_ctx["can_select_branch"]:
        messages.error(request, "You are not assigned to a valid branch.")
        return _credit_ledger_redirect(branch_id)

    account = get_object_or_404(account_qs, pk=account_id)
    amount = form.cleaned_data["amount"]
    payment_method = form.cleaned_data["payment_method"]
    notes = (form.cleaned_data.get("notes") or "").strip()

    try:
        with transaction.atomic():
            account = CreditAccount.objects.select_for_update().get(pk=account.pk)
            if amount > account.outstanding_balance:
                messages.error(
                    request,
                    f"Repayment cannot exceed outstanding balance (KES {account.outstanding_balance:.2f}).",
                )
                return _credit_ledger_redirect(branch_id)

            account.outstanding_balance -= amount
            account.save(update_fields=["outstanding_balance", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                branch=account.branch,
                transaction_type=CreditTransaction.TYPE_REPAYMENT,
                amount=amount,
                payment_method=payment_method,
                notes=notes,
                created_by=request.user,
            )
    except Exception as exc:
        messages.error(request, f"Could not record repayment: {exc}")
        return _credit_ledger_redirect(branch_id)

    messages.success(
        request,
        f"Repayment recorded for {account.customer_name} (KES {amount:.2f}).",
    )
    return _credit_ledger_redirect(branch_id)


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
