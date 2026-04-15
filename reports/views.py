from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from django.views.decorators.http import require_GET
from branches.models import Branch
from sales.models import Sale, Shift
from products.models import Purchase, Supplier


def _can_select_report_branch(user):
    return user.is_superuser or user.is_staff or getattr(user, "role", "") == "super_admin"


def _report_branch_context(request):
    can_select_branch = _can_select_report_branch(request.user)
    branch_options = Branch.objects.filter(is_active=True).order_by("name") if can_select_branch else []
    selected_branch_id = (request.GET.get("branch_id") or "all").strip()
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


def _report_date_inputs(request):
    today_iso = timezone.localdate().isoformat()
    start_date = (request.GET.get("start_date") or today_iso).strip()
    end_date = (request.GET.get("end_date") or today_iso).strip()
    return start_date, end_date


@login_required
def dashboard_view(request):
    """Main dashboard — stats loaded inline (polled via HTMX)"""
    if request.htmx and request.GET.get("partial") == "stats":
        return _dashboard_stats_partial(request)
    ctx = _build_dashboard_ctx(request)
    return render(request, "reports/dashboard.html", ctx)


def _build_dashboard_ctx(request):
    today = timezone.now().date()
    branch_ctx = _report_branch_context(request)
    branch = branch_ctx["active_branch"]

    qs = Sale.objects.filter(created_at__date=today)
    if branch:
        qs = qs.filter(branch=branch)

    today_sales = qs.aggregate(
        total_amount=Sum("total_amount"),
        total_cash=Sum("cash_amount"),
        total_mpesa=Sum("mpesa_amount"),
        count=Count("id"),
    )

    month_start = today.replace(day=1)
    month_qs = Sale.objects.filter(created_at__date__gte=month_start)
    if branch:
        month_qs = month_qs.filter(branch=branch)
    month_sales = month_qs.aggregate(
        total_amount=Sum("total_amount"),
        count=Count("id"),
    )

    active_shift = None
    shift_branch = request.user.branch if request.user.branch_id else branch
    try:
        if shift_branch:
            shift = Shift.objects.get(cashier=request.user, branch=shift_branch, is_closed=False)
            active_shift = {
                "start_time": shift.start_time,
                "opening_cash": shift.opening_cash,
                "sales_count": shift.sale_set.count(),
            }
    except Shift.DoesNotExist:
        pass

    return {
        "today_total": today_sales["total_amount"] or 0,
        "today_cash": today_sales["total_cash"] or 0,
        "today_mpesa": today_sales["total_mpesa"] or 0,
        "today_count": today_sales["count"] or 0,
        "month_total": month_sales["total_amount"] or 0,
        "month_count": month_sales["count"] or 0,
        "active_shift": active_shift,
        **branch_ctx,
    }


def _dashboard_stats_partial(request):
    ctx = _build_dashboard_ctx(request)
    return render(request, "reports/partials/_dashboard_stats.html", ctx)


@login_required
def sales_report_view(request):
    data = None
    errors = None
    start_date, end_date = _report_date_inputs(request)
    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        qs = Sale.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
        if active_branch:
            qs = qs.filter(branch=active_branch)

        summary = qs.aggregate(
            total_amount=Sum("total_amount"),
            total_cash=Sum("cash_amount"),
            total_mpesa=Sum("mpesa_amount"),
            count=Count("id"),
        )

        daily = []
        cur = sd
        while cur <= ed:
            day = qs.filter(created_at__date=cur).aggregate(
                total_amount=Sum("total_amount"),
                total_cash=Sum("cash_amount"),
                total_mpesa=Sum("mpesa_amount"),
                count=Count("id"),
            )
            daily.append({
                "date": cur.strftime("%d %b %Y"),
                "date_iso": cur.isoformat(),
                "total_amount": day["total_amount"] or 0,
                "total_cash": day["total_cash"] or 0,
                "total_mpesa": day["total_mpesa"] or 0,
                "count": day["count"] or 0,
            })
            cur += timedelta(days=1)

        data = {"summary": summary, "daily": daily}
    except ValueError:
        errors = "Invalid date format."

    ctx = {
        "data": data,
        "errors": errors,
        "start_date": start_date,
        "end_date": end_date,
        **branch_ctx,
    }
    if request.htmx:
        return render(request, "reports/partials/_sales_table.html", ctx)
    return render(request, "reports/sales_report.html", ctx)


@login_required
def supplier_report_view(request):
    data = None
    errors = None
    supplier_id = (request.GET.get("supplier_id") or "all").strip()
    start_date, end_date = _report_date_inputs(request)
    suppliers = Supplier.objects.all().order_by("name")
    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        qs = Purchase.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)

        if supplier_id and supplier_id != "all":
            qs = qs.filter(supplier_id=supplier_id)

        if active_branch:
            qs = qs.filter(branch=active_branch)

        supplier_data = {}
        for p in qs.select_related("supplier"):
            name = p.supplier.name
            if name not in supplier_data:
                supplier_data[name] = {"name": name, "total": 0, "count": 0, "invoices": []}
            supplier_data[name]["total"] += float(p.total_amount)
            supplier_data[name]["count"] += 1
            supplier_data[name]["invoices"].append(
                {
                    "id": p.id,
                    "number": p.invoice_number,
                    "amount": p.total_amount,
                    "date": p.created_at.strftime("%d %b %Y"),
                }
            )
        data = list(supplier_data.values())
    except ValueError:
        errors = "Invalid date format."

    ctx = {
        "data": data, 
        "errors": errors, 
        "start_date": start_date, 
        "end_date": end_date,
        "suppliers": suppliers,
        "supplier_id": supplier_id,
        **branch_ctx,
    }
    if request.htmx:
        return render(request, "reports/partials/_supplier_table.html", ctx)
    return render(request, "reports/supplier_report.html", ctx)


@login_required
def shift_report_view(request):
    data = None
    errors = None
    start_date, end_date = _report_date_inputs(request)
    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        qs = Shift.objects.filter(
            start_time__date__gte=sd,
            start_time__date__lte=ed,
            is_closed=True,
        ).select_related("cashier").prefetch_related("expenses")
        if active_branch:
            qs = qs.filter(branch=active_branch)

        data = []
        for shift in qs:
            shift_expenses = list(shift.expenses.all())
            total_expenses = sum((expense.amount for expense in shift_expenses), Decimal("0.00"))
            data.append({
                "cashier": shift.cashier.get_full_name() or shift.cashier.username,
                "date": shift.start_time.strftime("%d %b %Y"),
                "expected_cash": shift.calculate_expected_cash(),
                "declared_cash": shift.closing_cash_declared or 0,
                "cash_variance": shift.cash_variance,
                "total_expenses": total_expenses,
                "expense_details": "; ".join(
                    [f"{expense.description} (KES {expense.amount:.2f})" for expense in shift_expenses]
                ),
                "expected_mpesa": shift.calculate_expected_mpesa(),
                "declared_mpesa": shift.closing_mpesa_declared or 0,
                "mpesa_variance": shift.mpesa_variance,
            })
    except ValueError:
        errors = "Invalid date format."

    ctx = {"data": data, "errors": errors, "start_date": start_date, "end_date": end_date, **branch_ctx}
    if request.htmx:
        return render(request, "reports/partials/_shift_table.html", ctx)
    return render(request, "reports/shift_report.html", ctx)


@login_required
@require_GET
def sales_day_breakdown_modal_view(request):
    report_date = request.GET.get("date", "").strip()
    if not report_date:
        return render(
            request,
            "reports/partials/_daily_sales_modal_content.html",
            {"error": "Missing date."},
        )

    try:
        day = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        return render(
            request,
            "reports/partials/_daily_sales_modal_content.html",
            {"error": "Invalid date format."},
        )

    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    sales_qs = Sale.objects.filter(created_at__date=day)
    if active_branch:
        sales_qs = sales_qs.filter(branch=active_branch)

    sales = list(sales_qs.select_related("cashier", "branch").prefetch_related("items__product").order_by("-created_at"))
    totals = sales_qs.aggregate(
        total_amount=Sum("total_amount"),
        total_cash=Sum("cash_amount"),
        total_mpesa=Sum("mpesa_amount"),
        count=Count("id"),
    )

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
    for row in summary_by_product.values():
        price_values = sorted(row["prices"])
        sales_summary.append(
            {
                "name": row["name"],
                "quantity": row["quantity"],
                "price_display": ", ".join([f"KES {price:.2f}" for price in price_values]),
                "total": row["total"],
            }
        )
    sales_summary.sort(key=lambda row: row["name"].lower())

    return render(
        request,
        "reports/partials/_daily_sales_modal_content.html",
        {
            "report_date": day,
            "sales": sales,
            "sales_summary": sales_summary,
            "totals": totals,
            "active_branch": active_branch,
        },
    )


@login_required
@require_GET
def supplier_invoice_items_modal_view(request):
    raw_invoice_id = (request.GET.get("invoice_id") or "").strip()
    if not raw_invoice_id:
        return render(
            request,
            "reports/partials/_supplier_invoice_modal_content.html",
            {"error": "Missing invoice id."},
        )

    try:
        invoice_id = int(raw_invoice_id)
    except ValueError:
        return render(
            request,
            "reports/partials/_supplier_invoice_modal_content.html",
            {"error": "Invalid invoice id."},
        )

    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    purchase_qs = Purchase.objects.select_related("supplier", "branch", "created_by").prefetch_related("items__product")
    if active_branch:
        purchase_qs = purchase_qs.filter(branch=active_branch)

    purchase = get_object_or_404(purchase_qs, pk=invoice_id)
    items = list(purchase.items.select_related("product").all())

    return render(
        request,
        "reports/partials/_supplier_invoice_modal_content.html",
        {
            "purchase": purchase,
            "items": items,
        },
    )
