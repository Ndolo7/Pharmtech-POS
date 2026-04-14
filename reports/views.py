from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import datetime, timedelta
from sales.models import Sale, Shift
from products.models import Purchase, Supplier


@login_required
def dashboard_view(request):
    """Main dashboard — stats loaded inline (polled via HTMX)"""
    if request.htmx and request.GET.get("partial") == "stats":
        return _dashboard_stats_partial(request)
    ctx = _build_dashboard_ctx(request)
    return render(request, "reports/dashboard.html", ctx)


def _build_dashboard_ctx(request):
    today = timezone.now().date()
    branch = request.user.branch

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
    try:
        shift = Shift.objects.get(cashier=request.user, branch=branch, is_closed=False)
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
    }


def _dashboard_stats_partial(request):
    ctx = _build_dashboard_ctx(request)
    return render(request, "reports/partials/_dashboard_stats.html", ctx)


@login_required
def sales_report_view(request):
    data = None
    errors = None
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if start_date and end_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
            qs = Sale.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
            if request.user.branch:
                qs = qs.filter(branch=request.user.branch)

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
                    "total_amount": day["total_amount"] or 0,
                    "total_cash": day["total_cash"] or 0,
                    "total_mpesa": day["total_mpesa"] or 0,
                    "count": day["count"] or 0,
                })
                cur += timedelta(days=1)

            data = {"summary": summary, "daily": daily}
        except ValueError:
            errors = "Invalid date format."

    ctx = {"data": data, "errors": errors, "start_date": start_date, "end_date": end_date}
    if request.htmx:
        return render(request, "reports/partials/_sales_table.html", ctx)
    return render(request, "reports/sales_report.html", ctx)


@login_required
def supplier_report_view(request):
    data = None
    errors = None
    supplier_id = request.GET.get("supplier_id")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    suppliers = Supplier.objects.all().order_by("name")

    if start_date and end_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
            qs = Purchase.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
            
            if supplier_id and supplier_id != "all":
                qs = qs.filter(supplier_id=supplier_id)
                
            if request.user.branch:
                qs = qs.filter(branch=request.user.branch)

            supplier_data = {}
            for p in qs.select_related("supplier"):
                name = p.supplier.name
                if name not in supplier_data:
                    supplier_data[name] = {"name": name, "total": 0, "count": 0, "invoices": []}
                supplier_data[name]["total"] += float(p.total_amount)
                supplier_data[name]["count"] += 1
                supplier_data[name]["invoices"].append({
                    "number": p.invoice_number,
                    "amount": p.total_amount,
                    "date": p.created_at.strftime("%d %b %Y"),
                })
            data = list(supplier_data.values())
        except ValueError:
            errors = "Invalid date format."

    ctx = {
        "data": data, 
        "errors": errors, 
        "start_date": start_date, 
        "end_date": end_date,
        "suppliers": suppliers,
        "supplier_id": supplier_id
    }
    if request.htmx:
        return render(request, "reports/partials/_supplier_table.html", ctx)
    return render(request, "reports/supplier_report.html", ctx)


@login_required
def shift_report_view(request):
    data = None
    errors = None
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if start_date and end_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
            qs = Shift.objects.filter(
                start_time__date__gte=sd,
                start_time__date__lte=ed,
                is_closed=True,
            ).select_related("cashier")
            if request.user.branch:
                qs = qs.filter(branch=request.user.branch)

            data = []
            for shift in qs:
                data.append({
                    "cashier": shift.cashier.get_full_name() or shift.cashier.username,
                    "date": shift.start_time.strftime("%d %b %Y"),
                    "expected_cash": shift.calculate_expected_cash(),
                    "declared_cash": shift.closing_cash_declared or 0,
                    "cash_variance": shift.cash_variance,
                    "expected_mpesa": shift.calculate_expected_mpesa(),
                    "declared_mpesa": shift.closing_mpesa_declared or 0,
                    "mpesa_variance": shift.mpesa_variance,
                })
        except ValueError:
            errors = "Invalid date format."

    ctx = {"data": data, "errors": errors, "start_date": start_date, "end_date": end_date}
    if request.htmx:
        return render(request, "reports/partials/_shift_table.html", ctx)
    return render(request, "reports/shift_report.html", ctx)
