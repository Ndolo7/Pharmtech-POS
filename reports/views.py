from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from django.views.decorators.http import require_GET
from branches.models import Branch
from sales.models import Sale, Shift, ShiftExpense
from products.models import (
    AutoReorderRequest,
    Product,
    Purchase,
    StockMovement,
    Supplier,
    SupplierReorderRequest,
    Transfer,
)


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


def _normalized_branch_requirements(branch_requirements):
    if not isinstance(branch_requirements, dict):
        return []

    payload = []
    for branch_name, qty in branch_requirements.items():
        try:
            normalized_qty = max(int(qty), 0)
        except (TypeError, ValueError):
            normalized_qty = 0
        if normalized_qty > 0:
            payload.append((str(branch_name), normalized_qty))

    payload.sort(key=lambda item: item[0].lower())
    return payload


def _order_matches_branch(order, branch):
    if not branch:
        return True

    branch_requirements = order.branch_requirements or {}
    if not isinstance(branch_requirements, dict):
        return False

    try:
        branch_qty = int(branch_requirements.get(branch.name, 0))
    except (TypeError, ValueError):
        branch_qty = 0
    return branch_qty > 0


def _order_is_failed(order, supplier_requests, remaining_packets: int) -> bool:
    has_rejected_supplier = any(
        request.status == SupplierReorderRequest.STATUS_REJECTED for request in supplier_requests
    )
    if order.status in {AutoReorderRequest.STATUS_EXHAUSTED, AutoReorderRequest.STATUS_CANCELLED}:
        return True
    return has_rejected_supplier and remaining_packets > 0


def _orders_summary(orders, rows):
    return {
        "total_orders": len(rows),
        "requested_packets": sum(row["requested_packets"] for row in rows),
        "remaining_packets": sum(row["remaining_packets"] for row in rows),
        "open_count": sum(1 for order in orders if order.status == AutoReorderRequest.STATUS_OPEN),
        "fulfilled_count": sum(1 for order in orders if order.status == AutoReorderRequest.STATUS_FULFILLED),
        "exhausted_count": sum(1 for order in orders if order.status == AutoReorderRequest.STATUS_EXHAUSTED),
        "cancelled_count": sum(1 for order in orders if order.status == AutoReorderRequest.STATUS_CANCELLED),
    }


def _is_super_admin(user):
    return user.is_superuser or getattr(user, "role", "") == "super_admin"


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
    month_purchase_qs = Purchase.objects.filter(created_at__date__gte=month_start)
    if branch:
        month_purchase_qs = month_purchase_qs.filter(branch=branch)
    month_supplier_costs = month_purchase_qs.aggregate(total_amount=Sum("total_amount"))
    month_expenses_qs = ShiftExpense.objects.filter(shift__start_time__date__gte=month_start)
    if branch:
        month_expenses_qs = month_expenses_qs.filter(shift__branch=branch)
    month_total_expenses = month_expenses_qs.aggregate(total_amount=Sum("amount"))["total_amount"] or Decimal("0.00")
    month_total_sales = month_sales["total_amount"] or Decimal("0.00")

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

    can_view_gross_profit = request.user.is_superuser or getattr(request.user, "role", "") == "super_admin"

    return {
        "today_total": today_sales["total_amount"] or 0,
        "today_cash": today_sales["total_cash"] or 0,
        "today_mpesa": today_sales["total_mpesa"] or 0,
        "today_count": today_sales["count"] or 0,
        "month_total": month_sales["total_amount"] or 0,
        "month_count": month_sales["count"] or 0,
        "month_supplier_costs": month_supplier_costs["total_amount"] or 0,
        "month_expenses": month_total_expenses,
        "month_gross_profit": month_total_sales - month_total_expenses,
        "can_view_gross_profit": can_view_gross_profit,
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

    tab = (request.GET.get("tab") or "daily").strip().lower()
    can_view_transactions = request.user.is_superuser or getattr(request.user, "role", "") == "super_admin"
    product_id = (request.GET.get("product_id") or "all").strip()
    product_options = Product.objects.filter(is_active=True).order_by("name")
    if tab not in {"daily", "transactions"}:
        tab = "daily"
    if tab == "transactions" and not can_view_transactions:
        tab = "daily"
    if product_id != "all" and not product_options.filter(pk=product_id).exists():
        product_id = "all"

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
            total_credit=Sum("credit_amount"),
            count=Count("id"),
        )
        purchases_qs = Purchase.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
        if active_branch:
            purchases_qs = purchases_qs.filter(branch=active_branch)
        total_supplier_purchases = purchases_qs.aggregate(total_amount=Sum("total_amount"))["total_amount"] or Decimal("0.00")
        expenses_qs = ShiftExpense.objects.filter(shift__start_time__date__gte=sd, shift__start_time__date__lte=ed)
        if active_branch:
            expenses_qs = expenses_qs.filter(shift__branch=active_branch)
        total_expenses = expenses_qs.aggregate(total_amount=Sum("amount"))["total_amount"] or Decimal("0.00")
        total_sales_amount = summary["total_amount"] or Decimal("0.00")
        summary["total_supplier_purchases"] = total_supplier_purchases
        summary["total_expenses"] = total_expenses
        summary["gross_profit"] = total_sales_amount - total_expenses

        daily = []
        cur = sd
        while cur <= ed:
            day = qs.filter(created_at__date=cur).aggregate(
                total_amount=Sum("total_amount"),
                total_cash=Sum("cash_amount"),
                total_mpesa=Sum("mpesa_amount"),
                total_credit=Sum("credit_amount"),
                count=Count("id"),
            )
            day_supplier_purchases = purchases_qs.filter(created_at__date=cur).aggregate(
                total_amount=Sum("total_amount")
            )["total_amount"] or Decimal("0.00")
            day_expenses = expenses_qs.filter(shift__start_time__date=cur).aggregate(total_amount=Sum("amount"))["total_amount"] or Decimal("0.00")
            day_sales_amount = day["total_amount"] or Decimal("0.00")
            daily.append({
                "date": cur.strftime("%d %b %Y"),
                "date_iso": cur.isoformat(),
                "total_amount": day_sales_amount,
                "total_cash": day["total_cash"] or 0,
                "total_mpesa": day["total_mpesa"] or 0,
                "total_credit": day["total_credit"] or 0,
                "supplier_purchases": day_supplier_purchases,
                "expenses": day_expenses,
                "gross_profit": day_sales_amount - day_expenses,
                "count": day["count"] or 0,
            })
            cur += timedelta(days=1)

        data = {"summary": summary, "daily": daily}

        if can_view_transactions:
            sales_qs = (
                Sale.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
                .select_related("branch", "cashier")
                .prefetch_related("items__product")
            )
            if active_branch:
                sales_qs = sales_qs.filter(branch=active_branch)

            all_rows = []
            for sale in sales_qs.order_by("-created_at"):
                for item in sale.items.all():
                    if product_id != "all" and str(item.product_id) != product_id:
                        continue
                    all_rows.append(
                        {
                            "receipt_number": sale.receipt_number,
                            "created_at": timezone.localtime(sale.created_at),
                            "date": timezone.localtime(sale.created_at).date(),
                            "branch_name": sale.branch.name,
                            "cashier_name": sale.cashier.get_full_name() or sale.cashier.username,
                            "payment_method": sale.get_payment_method_display(),
                            "product_name": item.product.name,
                            "quantity": item.quantity,
                            "unit_price": item.unit_price,
                            "line_total": item.total_price,
                            "sale_total": sale.total_amount,
                            "cash_amount": sale.cash_amount,
                            "mpesa_amount": sale.mpesa_amount,
                            "credit_amount": sale.credit_amount,
                        }
                    )

            # Group flat rows by date (rows already ordered newest-first)
            groups = []
            seen_dates = []
            rows_by_date = {}
            for row in all_rows:
                d = row["date"]
                if d not in rows_by_date:
                    rows_by_date[d] = []
                    seen_dates.append(d)
                rows_by_date[d].append(row)

            for d in seen_dates:
                day_rows = rows_by_date[d]
                groups.append(
                    {
                        "date_display": d.strftime("%d %b %Y"),
                        "count": len(day_rows),
                        "total_quantity": sum(r["quantity"] for r in day_rows),
                        "total_cash": sum((r["cash_amount"] for r in day_rows), Decimal("0.00")),
                        "total_mpesa": sum((r["mpesa_amount"] for r in day_rows), Decimal("0.00")),
                        "total_credit": sum((r["credit_amount"] for r in day_rows), Decimal("0.00")),
                        "total_amount": sum((r["line_total"] for r in day_rows), Decimal("0.00")),
                        "rows": day_rows,
                    }
                )

            data["transactions"] = {
                "summary": {
                    "total_rows": len(all_rows),
                    "total_quantity": sum((r["quantity"] for r in all_rows), 0),
                    "total_amount": sum((r["line_total"] for r in all_rows), Decimal("0.00")),
                },
                "groups": groups,
            }

    except ValueError:
        errors = "Invalid date format."

    ctx = {
        "data": data,
        "errors": errors,
        "start_date": start_date,
        "end_date": end_date,
        "tab": tab,
        "can_view_transactions": can_view_transactions,
        "product_id": product_id,
        "product_options": product_options,
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
def orders_report_view(request):
    data = None
    errors = None
    start_date, end_date = _report_date_inputs(request)
    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]

    origin = (request.GET.get("origin") or "all").strip().lower()
    status = (request.GET.get("status") or "all").strip().lower()
    tab = (request.GET.get("tab") or "main").strip().lower()

    origin_options = [
        ("all", "All Types"),
        (AutoReorderRequest.ORIGIN_MANUAL, "Manual"),
        (AutoReorderRequest.ORIGIN_AUTO, "Automatic"),
    ]
    status_options = [("all", "All Statuses"), *AutoReorderRequest.STATUS_CHOICES]

    valid_origins = {item[0] for item in origin_options}
    valid_statuses = {item[0] for item in status_options}
    if origin not in valid_origins:
        origin = "all"
    if status not in valid_statuses:
        status = "all"
    if tab not in {"main", "failed"}:
        tab = "main"

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        qs = (
            AutoReorderRequest.objects.filter(created_at__date__gte=sd, created_at__date__lte=ed)
            .select_related("product")
            .prefetch_related("supplier_requests__supplier")
            .order_by("-created_at")
        )

        if origin != "all":
            qs = qs.filter(origin=origin)
        if status != "all":
            qs = qs.filter(status=status)

        orders = list(qs)
        if active_branch:
            orders = [order for order in orders if _order_matches_branch(order, active_branch)]

        main_rows = []
        failed_rows = []
        main_orders = []
        failed_orders = []
        for order in orders:
            supplier_requests = list(order.supplier_requests.all())
            latest_supplier_request = supplier_requests[-1] if supplier_requests else None
            requested_packets = int(order.requested_quantity or 0)
            remaining_packets = int(order.remaining_quantity or 0)
            fulfilled_packets = max(requested_packets - remaining_packets, 0)
            received_packets = sum(int(item.received_quantity or 0) for item in supplier_requests)
            row = {
                "id": order.id,
                "created_at": timezone.localtime(order.created_at),
                "product_name": order.product.name,
                "origin_display": dict(AutoReorderRequest.ORIGIN_CHOICES).get(order.origin, order.origin),
                "status_display": order.get_status_display(),
                "requested_packets": requested_packets,
                "remaining_packets": remaining_packets,
                "fulfilled_packets": fulfilled_packets,
                "received_packets": received_packets,
                "branch_breakdown": _normalized_branch_requirements(order.branch_requirements or {}),
                "supplier_requests_count": len(supplier_requests),
                "latest_supplier_name": latest_supplier_request.supplier.name if latest_supplier_request else "-",
                "latest_supplier_status": latest_supplier_request.get_status_display() if latest_supplier_request else "-",
            }

            if _order_is_failed(order, supplier_requests, remaining_packets):
                failed_rows.append(row)
                failed_orders.append(order)
            else:
                main_rows.append(row)
                main_orders.append(order)

        data = {
            "main": {
                "summary": _orders_summary(main_orders, main_rows),
                "orders": main_rows,
            },
            "failed": {
                "summary": _orders_summary(failed_orders, failed_rows),
                "orders": failed_rows,
            },
        }
    except ValueError:
        errors = "Invalid date format."

    ctx = {
        "data": data,
        "errors": errors,
        "start_date": start_date,
        "end_date": end_date,
        "origin": origin,
        "status": status,
        "tab": tab,
        "active_data": (data or {}).get(tab, {"summary": {}, "orders": []}),
        "origin_options": origin_options,
        "status_options": status_options,
        **branch_ctx,
    }
    if request.htmx:
        return render(request, "reports/partials/_orders_table.html", ctx)
    return render(request, "reports/orders_report.html", ctx)


@login_required
def products_trail_report_view(request):
    if not _is_super_admin(request.user):
        return HttpResponseForbidden("Permission denied.")

    branch_ctx = _report_branch_context(request)
    active_branch = branch_ctx["active_branch"]
    product_options = Product.objects.filter(is_active=True).order_by("name")
    product_id = (request.GET.get("product_id") or "").strip()
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    errors = None

    start_date_value = None
    end_date_value = None
    try:
        if start_date:
            start_date_value = datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date:
            end_date_value = datetime.strptime(end_date, "%Y-%m-%d").date()
        if start_date_value and end_date_value and start_date_value > end_date_value:
            errors = "Start date cannot be after end date."
    except ValueError:
        errors = "Invalid date format."

    selected_product = None
    trail_data = None
    if product_id and not errors:
        selected_product = product_options.filter(pk=product_id).first()
        if selected_product:
            movements_qs = StockMovement.objects.filter(product=selected_product).select_related(
                "branch", "created_by"
            )
            if active_branch:
                movements_qs = movements_qs.filter(branch=active_branch)
            if start_date_value:
                movements_qs = movements_qs.filter(created_at__date__gte=start_date_value)
            if end_date_value:
                movements_qs = movements_qs.filter(created_at__date__lte=end_date_value)
            movements_qs = movements_qs.order_by("created_at", "id")

            movements = list(movements_qs)
            if movements:
                sale_refs = {m.reference for m in movements if m.movement_type == "sale" and m.reference}
                sales_by_receipt = {
                    s.receipt_number: s
                    for s in Sale.objects.filter(receipt_number__in=sale_refs).select_related("cashier", "branch")
                }

                transfer_ids = set()
                for movement in movements:
                    if movement.reference.startswith("TRF-"):
                        try:
                            transfer_ids.add(int(movement.reference.split("-", 1)[1]))
                        except (TypeError, ValueError):
                            continue
                transfers_by_id = {
                    t.id: t
                    for t in Transfer.objects.filter(id__in=transfer_ids).select_related(
                        "from_branch", "to_branch", "created_by"
                    )
                }

                purchase_refs = {m.reference for m in movements if m.movement_type == "purchase" and m.reference}
                purchases_by_invoice = {
                    p.invoice_number: p
                    for p in Purchase.objects.filter(invoice_number__in=purchase_refs).select_related(
                        "supplier", "created_by", "branch"
                    )
                }

                running_stock = 0
                rows = []
                for movement in movements:
                    running_stock += movement.quantity
                    detail = ""
                    if movement.movement_type == "sale":
                        sale = sales_by_receipt.get(movement.reference)
                        if sale:
                            detail = (
                                f"Receipt {sale.receipt_number} · {sale.get_payment_method_display()} · "
                                f"Cashier: {sale.cashier.get_full_name() or sale.cashier.username}"
                            )
                        else:
                            detail = f"Receipt {movement.reference}"
                    elif movement.movement_type in {"transfer_in", "transfer_out"} and movement.reference.startswith("TRF-"):
                        try:
                            transfer_id = int(movement.reference.split("-", 1)[1])
                        except (TypeError, ValueError):
                            transfer_id = None
                        transfer = transfers_by_id.get(transfer_id) if transfer_id else None
                        if transfer:
                            detail = (
                                f"Transfer {transfer.id} · From {transfer.from_branch.name} "
                                f"to {transfer.to_branch.name}"
                            )
                        else:
                            detail = movement.reference
                    elif movement.movement_type == "purchase":
                        purchase = purchases_by_invoice.get(movement.reference)
                        if purchase:
                            detail = (
                                f"Invoice {purchase.invoice_number} · Supplier: {purchase.supplier.name}"
                            )
                        else:
                            detail = f"Invoice {movement.reference}"
                    elif movement.movement_type == "adjustment":
                        detail = movement.notes or "Manual stock adjustment"

                    rows.append(
                        {
                            "created_at": timezone.localtime(movement.created_at),
                            "movement_type_display": movement.get_movement_type_display(),
                            "branch_name": movement.branch.name,
                            "quantity": movement.quantity,
                            "running_stock": running_stock,
                            "reference": movement.reference or "-",
                            "detail": detail or "-",
                            "created_by": movement.created_by.get_full_name() or movement.created_by.username,
                        }
                    )

                trail_data = {
                    "start_date": timezone.localtime(movements[0].created_at).date(),
                    "end_date": timezone.localtime(movements[-1].created_at).date(),
                    "rows": rows,
                    "summary": {
                        "total_events": len(rows),
                        "total_in": sum((max(row["quantity"], 0) for row in rows), 0),
                        "total_out": sum((abs(min(row["quantity"], 0)) for row in rows), 0),
                        "current_balance": rows[-1]["running_stock"],
                    },
                }
            else:
                trail_data = {"start_date": None, "end_date": None, "rows": [], "summary": None}

    return render(
        request,
        "reports/product_trail_report.html",
        {
            "product_options": product_options,
            "product_id": str(selected_product.pk) if selected_product else "",
            "selected_product": selected_product,
            "trail_data": trail_data,
            "start_date": start_date,
            "end_date": end_date,
            "errors": errors,
            **branch_ctx,
        },
    )


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
        total_credit=Sum("credit_amount"),
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
