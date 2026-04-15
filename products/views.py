import json
from decimal import Decimal, InvalidOperation
from io import BytesIO
from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from branches.models import Branch
from .forms import (
    ProductForm,
    CategoryForm,
    AdjustStockForm,
    TransferStockForm,
    ProductBulkUploadForm,
    SupplierForm,
    SupplierReorderResponseForm,
)
from .models import (
    AutoReorderRequest,
    Category,
    Supplier,
    SupplierReorderRequest,
    Product,
    Stock,
    StockMovement,
    Purchase,
    PurchaseItem,
    Transfer,
    TransferItem,
)
from .tasks import notify_next_supplier


def _to_non_negative_int(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _branch_pending_packets(supplier_request, branch):
    if not branch:
        return supplier_request.pending_quantity

    branch_requirements = supplier_request.reorder_request.branch_requirements or {}
    if not isinstance(branch_requirements, dict) or not branch_requirements:
        return supplier_request.pending_quantity

    return _to_non_negative_int(branch_requirements.get(branch.name, 0))


def _normalized_branch_requirements(branch_requirements):
    if not isinstance(branch_requirements, dict):
        return []

    payload = []
    for branch_name, qty in branch_requirements.items():
        normalized_qty = _to_non_negative_int(qty)
        if normalized_qty > 0:
            payload.append((str(branch_name), normalized_qty))

    payload.sort(key=lambda item: item[0].lower())
    return payload


@login_required
@require_GET
def supplier_pending_orders_view(request):
    supplier_id = request.GET.get("supplier_id")
    if not supplier_id:
        return HttpResponse("")

    supplier = get_object_or_404(Supplier, pk=supplier_id)
    active_branch = _resolve_products_branch(request)
    pending_requests = []

    orders = SupplierReorderRequest.objects.filter(
        supplier=supplier,
        status__in=[SupplierReorderRequest.STATUS_ACCEPTED, SupplierReorderRequest.STATUS_PARTIAL]
    ).select_related("reorder_request", "reorder_request__product")

    for order in orders:
        if order.pending_quantity > 0:
            branch_pending_quantity = _branch_pending_packets(order, active_branch)
            if branch_pending_quantity <= 0:
                continue
            order.branch_pending_quantity = min(order.pending_quantity, branch_pending_quantity)
            pending_requests.append(order)

    return render(
        request,
        "products/partials/_pending_order_rows.html",
        {"pending_requests": pending_requests},
    )

@login_required
def manual_order_create_view(request):
    if not _can_manage_catalog(request.user):
        return HttpResponseForbidden("Permission denied.")

    Branch = apps.get_model("branches", "Branch")
    can_select_branch = request.user.can_manage_users()
    all_branches = Branch.objects.filter(is_active=True) if can_select_branch else None
    
    active_branch_id = request.GET.get("branch") or request.POST.get("branch_id")
    if not can_select_branch:
        active_branch = request.user.branch
        active_branch_id = active_branch.id if active_branch else None
    else:
        active_branch = Branch.objects.filter(pk=active_branch_id).first() if active_branch_id else None

    if request.method == "POST":
        product_id = request.POST.get("product_id")
        packets = int(request.POST.get("packets", 0))
        if not product_id or packets <= 0 or not active_branch:
            return HttpResponseBadRequest("Invalid inputs.")

        product = get_object_or_404(Product, pk=product_id)

        with transaction.atomic():
            reorder = AutoReorderRequest.objects.create(
                product=product,
                target_stock_level=product.max_stock,
                current_stock_snapshot=product.current_stock(),
                requested_quantity=packets,
                remaining_quantity=packets,
                branch_requirements={active_branch.name: packets},
                status=AutoReorderRequest.STATUS_OPEN,
            )
        
        notify_next_supplier.delay(reorder.id)
        if request.htmx:
            return _with_hx_trigger(
                HttpResponse(""),
                "stock-action-success",
                {"message": f"Manual order for {packets} packet(s) of {product.name} created successfully."},
            )
        messages.success(request, f"Manual order for {packets} packet(s) of {product.name} created successfully.")
        return HttpResponse("")

    products = Product.objects.filter(is_active=True).order_by("name")
    return render(request, "products/partials/_create_order_form.html", {
        "products": products,
        "all_branches": all_branches,
        "active_branch": active_branch,
        "active_branch_id": int(active_branch_id) if active_branch_id else None,
    })


def _can_manage_catalog(user):
    return user.is_superuser or user.is_staff or user.can_manage_users()


def _can_select_products_branch(user):
    return user.is_superuser or getattr(user, "role", "") == "super_admin"


def _resolve_products_branch(request):
    selected_branch_id = (
        request.GET.get("branch")
        or request.GET.get("branch_id")
        or request.POST.get("branch_id")
        or request.POST.get("branch")
        or ""
    ).strip()
    can_select_branch = _can_select_products_branch(request.user)
    active_branches = Branch.objects.filter(is_active=True).order_by("name")

    if can_select_branch and selected_branch_id:
        chosen_branch = active_branches.filter(pk=selected_branch_id).first()
        if chosen_branch:
            return chosen_branch

    if request.user.branch_id:
        if can_select_branch:
            active_user_branch = active_branches.filter(pk=request.user.branch_id).first()
            if active_user_branch:
                return active_user_branch
        else:
            user_branch = Branch.objects.filter(pk=request.user.branch_id).first()
            if user_branch:
                return user_branch

    if can_select_branch:
        return active_branches.first()

    return None


def _products_page_context(request, search="", branch=None):
    active_branch = branch if branch is not None else _resolve_products_branch(request)
    can_select_branch = _can_select_products_branch(request.user)

    return {
        "product_data": _build_product_data(active_branch, search=search),
        "suppliers": Supplier.objects.all(),
        "search": search,
        "active_branch": active_branch,
        "active_branch_id": active_branch.pk if active_branch else "",
        "can_select_branch": can_select_branch,
        "branch_options": Branch.objects.filter(is_active=True).order_by("name") if can_select_branch else [],
    }


def _redirect_with_branch(route_name, branch):
    url = reverse(route_name)
    if branch:
        url = f"{url}?branch={branch.pk}"
    return redirect(url)


def _build_product_data(branch, search=""):
    products = Product.objects.filter(is_active=True)
    if search:
        products = products.filter(Q(name__icontains=search) | Q(barcode__icontains=search))

    payload = []
    for product in products.order_by("name"):
        stock_qty = product.current_stock(branch) if branch else product.current_stock()
        payload.append({"product": product, "stock": stock_qty})
    return payload


def _render_product_table(request, search="", branch=None):
    ctx = _products_page_context(request, search=search, branch=branch)
    return render(request, "products/partials/_product_table.html", ctx)


def _rows_oob_response(request, products, branch=None):
    unique_products = []
    seen_ids = set()
    for product in products:
        if not product or product.pk in seen_ids:
            continue
        seen_ids.add(product.pk)
        unique_products.append(product)
    payload = render_to_string("partials/_messages.html", {"oob": True}, request=request)

    for product in unique_products:
        stock_qty = product.current_stock(branch) if branch else product.current_stock()
        payload += render_to_string(
            "products/partials/_product_row.html",
            {"p": product, "stock": stock_qty, "oob": True, "active_branch_id": branch.pk if branch else ""},
            request=request,
        )

    return HttpResponse(payload)


def _with_hx_trigger(response, event_name, detail):
    if not response:
        return response

    triggers = {}
    existing = response.headers.get("HX-Trigger")
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict):
                triggers.update(parsed)
        except json.JSONDecodeError:
            pass

    triggers[event_name] = detail
    response["HX-Trigger"] = json.dumps(triggers)
    return response


@login_required
def product_list_view(request):
    search = request.GET.get("q", "").strip()
    active_branch = _resolve_products_branch(request)
    ctx = _products_page_context(request, search=search, branch=active_branch)

    if request.htmx:
        return render(request, "products/partials/_product_table.html", ctx)

    return render(request, "products/product_list.html", ctx)


@login_required
def stock_list_view(request):
    search = request.GET.get("q", "").strip()
    active_branch = _resolve_products_branch(request)
    ctx = _products_page_context(request, search=search, branch=active_branch)

    if request.htmx:
        return render(request, "products/partials/_product_table.html", ctx)
    return render(request, "products/stock_list.html", ctx)


@login_required
def product_create_view(request):
    active_branch = _resolve_products_branch(request)

    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Product created successfully.")
            return _render_product_table(request, branch=active_branch)
        return render(
            request,
            "products/partials/_product_form.html",
            {"form": form, "active_branch_id": active_branch.pk if active_branch else ""},
        )

    form = ProductForm()
    return render(
        request,
        "products/partials/_product_form.html",
        {"form": form, "active_branch_id": active_branch.pk if active_branch else ""},
    )


@login_required
def product_edit_view(request, pk):
    active_branch = _resolve_products_branch(request)
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "Product updated.")
            return _render_product_table(request, branch=active_branch)
        return render(
            request,
            "products/partials/_product_form.html",
            {"form": form, "product": product, "active_branch_id": active_branch.pk if active_branch else ""},
        )
    form = ProductForm(instance=product)
    return render(
        request,
        "products/partials/_product_form.html",
        {"form": form, "product": product, "active_branch_id": active_branch.pk if active_branch else ""},
    )


@login_required
def receive_stock_view(request):
    if request.user.get_role_display() == "Cashier":
        messages.error(request, "Permission denied.")
        return redirect("/")

    can_select_branch = _can_select_products_branch(request.user)
    active_branch = _resolve_products_branch(request)

    if request.method == "POST":
        if request.POST.get("branch_id") and can_select_branch:
            active_branch = get_object_or_404(Branch, pk=request.POST["branch_id"])

        changed_products = []

        if not active_branch:
            messages.error(request, "You are not assigned to a branch.")
            return _rows_oob_response(request, changed_products, branch=active_branch) if request.htmx else _redirect_with_branch("stock-list", active_branch)

        try:
            with transaction.atomic():
                supplier = get_object_or_404(Supplier, pk=request.POST.get("supplier_id"))
                invoice_number = request.POST.get("invoice_number", "").strip()
                request_ids = request.POST.getlist("reorder_request_id[]")
                quantities = request.POST.getlist("quantity[]")
                cost_prices = request.POST.getlist("cost_price[]")
                selling_prices = request.POST.getlist("selling_price[]")

                if not invoice_number:
                    raise ValueError("Invoice number is required.")
                if not request_ids:
                    raise ValueError("No pending items found or selected to receive.")
                if len(request_ids) != len(quantities):
                    raise ValueError("Incomplete stock lines were submitted.")
                if len(request_ids) != len(cost_prices) or len(request_ids) != len(selling_prices):
                    raise ValueError("Incomplete pricing lines were submitted.")

                total_amount = Decimal("0.00")
                cleaned_lines = []
                for req_id, qty, cost_raw, selling_raw in zip(request_ids, quantities, cost_prices, selling_prices):
                    try:
                        qty_int = int(qty)
                    except (TypeError, ValueError):
                        raise ValueError("Quantity must be a valid whole number.")
                    if qty_int < 0:
                        raise ValueError("Quantity cannot be negative.")
                    if qty_int == 0:
                        continue

                    sup_req = SupplierReorderRequest.objects.select_for_update().get(pk=req_id)
                    product = sup_req.reorder_request.product

                    try:
                        cost_dec = Decimal(str(cost_raw).strip())
                    except (TypeError, ValueError, InvalidOperation):
                        raise ValueError(f"Invalid cost price for {product.name}.")

                    try:
                        selling_dec = Decimal(str(selling_raw).strip())
                    except (TypeError, ValueError, InvalidOperation):
                        raise ValueError(f"Invalid selling price for {product.name}.")

                    if cost_dec <= 0:
                        raise ValueError(f"Cost price must be greater than 0 for {product.name}.")

                    pack_quantity = max(int(product.pack_quantity or 1), 1)
                    cost_per_unit = cost_dec / Decimal(pack_quantity)
                    min_selling_price = cost_per_unit * Decimal("1.33")
                    if selling_dec < min_selling_price:
                        raise ValueError(
                            f"Selling price for {product.name} must be at least {min_selling_price.quantize(Decimal('0.01'))} (33% above unit cost)."
                        )

                    branch_pending_packets = _branch_pending_packets(sup_req, active_branch)
                    max_allowed_packets = min(sup_req.pending_quantity, branch_pending_packets)
                    if max_allowed_packets <= 0:
                        raise ValueError(
                            f"{sup_req.reorder_request.product.name} is not pending for {active_branch.name}."
                        )
                    if qty_int > max_allowed_packets:
                        raise ValueError(
                            f"Cannot receive more than {max_allowed_packets} packet(s) for {sup_req.reorder_request.product.name} in {active_branch.name}."
                        )

                    actual_units_received = qty_int * product.pack_quantity
                    total_amount += qty_int * cost_dec
                    cleaned_lines.append((sup_req, product, qty_int, actual_units_received, cost_dec, cost_per_unit, selling_dec))

                if not cleaned_lines:
                    raise ValueError("List must contain at least one positive quantity item.")

                purchase = Purchase.objects.create(
                    supplier=supplier,
                    branch=active_branch,
                    invoice_number=invoice_number,
                    total_amount=total_amount,
                    created_by=request.user,
                )

                for sup_req, product, qty_int, actual_units_received, cost_per_packet_dec, cost_per_unit_dec, selling_dec in cleaned_lines:
                    changed_products.append(product)

                    sup_req.received_quantity += qty_int
                    sup_req.save(update_fields=["received_quantity", "updated_at"])

                    reorder_request = sup_req.reorder_request
                    branch_requirements = reorder_request.branch_requirements or {}
                    if (
                        active_branch
                        and isinstance(branch_requirements, dict)
                        and active_branch.name in branch_requirements
                    ):
                        remaining_branch_packets = _to_non_negative_int(branch_requirements.get(active_branch.name)) - qty_int
                        branch_requirements[active_branch.name] = max(remaining_branch_packets, 0)
                        reorder_request.branch_requirements = branch_requirements
                        reorder_request.save(update_fields=["branch_requirements", "updated_at"])

                    if product.cost_price != cost_per_packet_dec or product.unit_price != selling_dec:
                        product.cost_price = cost_per_packet_dec
                        product.unit_price = selling_dec
                        product.save(update_fields=["cost_price", "unit_price", "updated_at"])

                    PurchaseItem.objects.create(
                        purchase=purchase,
                        product=product,
                        quantity=actual_units_received,
                        unit_cost=cost_per_unit_dec,
                    )

                    stock, _ = Stock.objects.get_or_create(
                        product=product,
                        branch=active_branch,
                        defaults={"quantity": 0},
                    )
                    stock.quantity += actual_units_received
                    stock.save(update_fields=["quantity", "updated_at"])

                    StockMovement.objects.create(
                        product=product,
                        branch=active_branch,
                        movement_type="purchase",
                        quantity=actual_units_received,
                        reference=invoice_number,
                        created_by=request.user,
                    )

            if request.htmx:
                response = _rows_oob_response(request, changed_products, branch=active_branch)
                return _with_hx_trigger(
                    response,
                    "stock-action-success",
                    {"message": "Stock received successfully."},
                )
            messages.success(request, "Stock received successfully.")
            return _redirect_with_branch("stock-list", active_branch)

        except Exception as exc:
            messages.error(request, f"Error: {exc}")
            return _rows_oob_response(request, changed_products, branch=active_branch) if request.htmx else _redirect_with_branch("stock-list", active_branch)

    suppliers = Supplier.objects.all()
    products = Product.objects.filter(is_active=True)
    all_branches = Branch.objects.filter(is_active=True).order_by("name") if can_select_branch else None
    template_name = "products/partials/_receive_form.html" if request.htmx else "products/receive_stock.html"
    return render(
        request,
        template_name,
        {
            "suppliers": suppliers,
            "products": products,
            "all_branches": all_branches,
            "active_branch": active_branch,
            "active_branch_id": active_branch.pk if active_branch else "",
        },
    )


@login_required
def adjust_stock_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    branch = _resolve_products_branch(request)

    if not request.user.can_adjust_stock():
        messages.error(request, "Permission denied.")
        return _rows_oob_response(request, [product], branch=branch) if request.htmx else _redirect_with_branch("stock-list", branch)

    if not branch:
        messages.error(request, "Select or assign a branch first.")
        return _rows_oob_response(request, [product], branch=branch) if request.htmx else _redirect_with_branch("stock-list", branch)

    if request.method == "POST":
        form = AdjustStockForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    new_qty = form.cleaned_data["new_quantity"]
                    reason = form.cleaned_data["reason"]

                    stock, _ = Stock.objects.get_or_create(product=product, branch=branch, defaults={"quantity": 0})
                    adjustment = new_qty - stock.quantity
                    stock.quantity = new_qty
                    stock.save(update_fields=["quantity", "updated_at"])

                    StockMovement.objects.create(
                        product=product,
                        branch=branch,
                        movement_type="adjustment",
                        quantity=adjustment,
                        notes=reason,
                        created_by=request.user,
                    )

                messages.success(request, f"Stock adjusted for {product.name}.")
                return _rows_oob_response(request, [product], branch=branch) if request.htmx else _redirect_with_branch("stock-list", branch)
            except Exception as exc:
                messages.error(request, f"Error: {exc}")
                return _rows_oob_response(request, [product], branch=branch) if request.htmx else _redirect_with_branch("stock-list", branch)

        template_name = "products/partials/_adjust_form.html" if request.htmx else "products/adjust_stock.html"
        return render(
            request,
            template_name,
            {
                "form": form,
                "product": product,
                "current_stock": product.current_stock(branch),
                "active_branch": branch,
                "active_branch_id": branch.pk if branch else "",
            },
        )

    form = AdjustStockForm()
    template_name = "products/partials/_adjust_form.html" if request.htmx else "products/adjust_stock.html"
    return render(
        request,
        template_name,
        {
            "form": form,
            "product": product,
            "current_stock": product.current_stock(branch),
            "active_branch": branch,
            "active_branch_id": branch.pk if branch else "",
        },
    )


@login_required
def transfer_stock_view(request):
    if request.user.get_role_display() == "Cashier":
        messages.error(request, "Permission denied.")
        return redirect("/")

    from_branch = _resolve_products_branch(request)
    template_name = "products/partials/_transfer_form.html" if request.htmx else "products/transfer_stock.html"
    
    if not from_branch:
        messages.error(request, "You are not assigned to any branch.")
        if request.method == "GET":
            return render(request, template_name, {"blocked": True})
        return _rows_oob_response(request, [], branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)

    destination_branches = Branch.objects.filter(is_active=True).exclude(pk=from_branch.pk).order_by("name")

    if request.method == "POST":
        if request.POST.get("from_branch_id"):
            from_branch = get_object_or_404(Branch, pk=request.POST["from_branch_id"])

        destination_branches = Branch.objects.filter(is_active=True).exclude(pk=from_branch.pk).order_by("name")
        form = TransferStockForm(request.POST)
        form.fields["to_branch"].queryset = Branch.objects.filter(is_active=True).order_by("name")
        changed_products = []

        if form.is_valid():
            to_branch = form.cleaned_data["to_branch"]
            if from_branch and to_branch and from_branch.pk == to_branch.pk:
                form.add_error("to_branch", "Destination branch must be different from source branch.")
                all_branches = Branch.objects.filter(is_active=True).order_by("name")
                products = Product.objects.filter(is_active=True).order_by("name")
                return render(
                    request,
                    template_name,
                    {
                        "form": form,
                        "products": products,
                        "all_branches": all_branches,
                        "branches": destination_branches,
                        "destination_branches": destination_branches,
                        "selected_to_branch_id": request.POST.get("to_branch", ""),
                        "active_branch": from_branch,
                        "active_branch_id": from_branch.pk if from_branch else "",
                    },
                )
            notes = form.cleaned_data.get("notes", "")
            product_ids = request.POST.getlist("product_id[]")
            quantities = request.POST.getlist("quantity[]")
            if not product_ids:
                messages.error(request, "Add at least one product line.")
                return _rows_oob_response(request, changed_products, branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)
            if len(product_ids) != len(quantities):
                messages.error(request, "Incomplete transfer lines were submitted.")
                return _rows_oob_response(request, changed_products, branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)

            try:
                with transaction.atomic():
                    transfer = Transfer.objects.create(
                        from_branch=from_branch,
                        to_branch=to_branch,
                        created_by=request.user,
                        notes=notes,
                    )

                    for pid, qty in zip(product_ids, quantities):
                        qty_int = int(qty)
                        if qty_int <= 0:
                            raise ValueError("Transfer quantity must be greater than zero.")

                        product = get_object_or_404(Product, pk=pid)
                        from_stock, _ = Stock.objects.get_or_create(
                            product=product,
                            branch=from_branch,
                            defaults={"quantity": 0},
                        )
                        if from_stock.quantity < qty_int:
                            raise ValueError(f"Insufficient stock for {product.name}.")

                        to_stock, _ = Stock.objects.get_or_create(
                            product=product,
                            branch=to_branch,
                            defaults={"quantity": 0},
                        )

                        TransferItem.objects.create(
                            transfer=transfer,
                            product=product,
                            quantity=qty_int,
                        )

                        from_stock.quantity -= qty_int
                        from_stock.save(update_fields=["quantity", "updated_at"])
                        to_stock.quantity += qty_int
                        to_stock.save(update_fields=["quantity", "updated_at"])

                        StockMovement.objects.create(
                            product=product,
                            branch=from_branch,
                            movement_type="transfer_out",
                            quantity=-qty_int,
                            reference=f"TRF-{transfer.id}",
                            notes=notes,
                            created_by=request.user,
                        )
                        StockMovement.objects.create(
                            product=product,
                            branch=to_branch,
                            movement_type="transfer_in",
                            quantity=qty_int,
                            reference=f"TRF-{transfer.id}",
                            notes=notes,
                            created_by=request.user,
                        )

                        changed_products.append(product)

                if request.htmx:
                    response = _rows_oob_response(request, changed_products, branch=from_branch)
                    return _with_hx_trigger(
                        response,
                        "stock-action-success",
                        {"message": f"Transfer to {to_branch.name} completed."},
                    )
                messages.success(request, f"Transfer to {to_branch.name} completed.")
                return _redirect_with_branch("stock-list", from_branch)
            except Exception as exc:
                messages.error(request, f"Error: {exc}")
                return _rows_oob_response(request, changed_products, branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)

        all_branches = Branch.objects.filter(is_active=True).order_by("name")
        products = Product.objects.filter(is_active=True).order_by("name")
        return render(
            request,
            template_name,
            {
                "form": form,
                "products": products,
                "all_branches": all_branches,
                "branches": destination_branches,
                "destination_branches": destination_branches,
                "selected_to_branch_id": request.POST.get("to_branch", ""),
                "active_branch": from_branch,
                "active_branch_id": from_branch.pk if from_branch else "",
            },
        )

    form = TransferStockForm()
    form.fields["to_branch"].queryset = destination_branches
    all_branches = Branch.objects.filter(is_active=True).order_by("name")
    products = Product.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        template_name,
        {
            "form": form,
            "products": products,
            "all_branches": all_branches,
            "branches": destination_branches,
            "destination_branches": destination_branches,
            "selected_to_branch_id": "",
            "active_branch": from_branch,
            "active_branch_id": from_branch.pk if from_branch else "",
        },
    )


@login_required
@require_GET
def transfer_destination_branches_view(request):
    from_branch_id = (request.GET.get("from_branch_id") or "").strip()
    selected_to_branch_id = (
        request.GET.get("selected_to_branch_id")
        or request.GET.get("to_branch")
        or ""
    ).strip()

    destination_branches = Branch.objects.filter(is_active=True).order_by("name")
    if from_branch_id:
        destination_branches = destination_branches.exclude(pk=from_branch_id)

    return render(
        request,
        "products/partials/_transfer_destination_field.html",
        {
            "destination_branches": destination_branches,
            "selected_to_branch_id": selected_to_branch_id,
            "to_branch_errors": "",
        },
    )


def _normalized_header_name(value):
    return str(value or "").strip().lower().replace(" ", "_")


def _to_cell_text(value):
    if value in (None, ""):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


@login_required
def product_bulk_template_download_view(request):
    try:
        from openpyxl import Workbook
    except ImportError:
        messages.error(request, "openpyxl is not installed. Please install dependencies and retry.")
        return redirect("product-list")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Inventory Template"
    sheet.append(["code", "DESCRIPTION", "PACK_QTY", "INV_TRADEPRICE", "selling_Price", "minimum", "max"])
    sheet.append(["1000001", "Paracetamol 500mg", 10, 50.00, 80.00, 20, 200])

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="inventory_upload_template.xlsx"'
    return response


@login_required
def product_bulk_upload_view(request):
    active_branch = _resolve_products_branch(request)

    if request.method == "POST":
        form = ProductBulkUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request,
                "products/partials/_bulk_upload_form.html",
                {"form": form, "active_branch_id": active_branch.pk if active_branch else ""},
            )

        try:
            from openpyxl import load_workbook
        except ImportError:
            messages.error(request, "openpyxl is not installed. Please install dependencies and retry.")
            return _render_product_table(request, branch=active_branch)

        upload = form.cleaned_data["file"]
        workbook = load_workbook(upload, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            messages.error(request, "The uploaded sheet is empty.")
            return _render_product_table(request, branch=active_branch)

        headers = [_normalized_header_name(col) for col in rows[0]]
        required_headers = {"code", "description", "pack_qty", "inv_tradeprice", "selling_price", "minimum", "max"}
        if not required_headers.issubset(set(headers)):
            messages.error(
                request,
                "Missing required columns. Required: code, DESCRIPTION, PACK_QTY, INV_TRADEPRICE, "
                "selling_Price, minimum, max.",
            )
            return _render_product_table(request, branch=active_branch)

        h = {header: idx for idx, header in enumerate(headers)}
        created = 0
        updated = 0
        skipped = 0

        for row in rows[1:]:
            if row is None or all(cell in (None, "") for cell in row):
                continue

            try:
                barcode = _to_cell_text(row[h["code"]])
                description = _to_cell_text(row[h["description"]])
                pack_quantity = int(Decimal(str(row[h["pack_qty"]]).strip()))
                cost_price = Decimal(str(row[h["inv_tradeprice"]]).strip())
                unit_price = Decimal(str(row[h["selling_price"]]).strip())
                reorder_level = int(Decimal(str(row[h["minimum"]]).strip()))
                max_stock = int(Decimal(str(row[h["max"]]).strip()))
                if not description:
                    skipped += 1
                    continue
                if pack_quantity < 1 or max_stock < 1 or reorder_level < 0:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue

            name = description
            defaults = {
                "name": name,
                "description": description,
                "unit_price": unit_price,
                "cost_price": cost_price,
                "reorder_level": reorder_level,
                "max_stock": max_stock,
                "pack_quantity": pack_quantity,
                "is_active": True,
            }

            if barcode:
                product, was_created = Product.objects.update_or_create(barcode=barcode, defaults=defaults)
            else:
                product_qs = Product.objects.filter(name__iexact=name)
                if product_qs.exists():
                    product = product_qs.first()
                    for field, value in defaults.items():
                        setattr(product, field, value)
                    product.save()
                    was_created = False
                else:
                    product = Product.objects.create(barcode="", **defaults)
                    was_created = True

            if was_created:
                created += 1
            else:
                updated += 1

        messages.success(
            request,
            f"Bulk upload complete. Created: {created}, Updated: {updated}, Skipped: {skipped}.",
        )
        return _render_product_table(request, branch=active_branch)

    return render(
        request,
        "products/partials/_bulk_upload_form.html",
        {"form": ProductBulkUploadForm(), "active_branch_id": active_branch.pk if active_branch else ""},
    )


@login_required
def supplier_list_view(request):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    suppliers = Supplier.objects.order_by("name")
    ctx = {"suppliers": suppliers, "form": SupplierForm()}

    if request.htmx:
        return render(request, "products/partials/_supplier_table.html", ctx)
    return render(request, "products/supplier_list.html", ctx)


@login_required
def supplier_create_view(request):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Supplier created successfully.")
            suppliers = Supplier.objects.order_by("name")
            return render(request, "products/partials/_supplier_table.html", {"suppliers": suppliers})
        return render(request, "products/partials/_supplier_form.html", {"form": form})

    return render(request, "products/partials/_supplier_form.html", {"form": SupplierForm()})


@login_required
def supplier_edit_view(request, pk):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "Supplier updated.")
            suppliers = Supplier.objects.order_by("name")
            return render(request, "products/partials/_supplier_table.html", {"suppliers": suppliers})
        return render(request, "products/partials/_supplier_form.html", {"form": form, "supplier": supplier})

    return render(request, "products/partials/_supplier_form.html", {"form": SupplierForm(instance=supplier), "supplier": supplier})


@login_required
def category_list_view(request):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    categories = Category.objects.order_by("name")
    if request.htmx:
        return render(request, "products/partials/_category_table.html", {"categories": categories})
    return render(request, "products/category_list.html", {"categories": categories})


@login_required
def category_create_view(request):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category created successfully.")
            categories = Category.objects.order_by("name")
            return render(request, "products/partials/_category_table.html", {"categories": categories})
        return render(request, "products/partials/_category_form.html", {"form": form})

    return render(request, "products/partials/_category_form.html", {"form": CategoryForm()})


@login_required
def category_edit_view(request, pk):
    if not _can_manage_catalog(request.user):
        messages.error(request, "Permission denied.")
        return redirect("dashboard")

    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
            categories = Category.objects.order_by("name")
            return render(request, "products/partials/_category_table.html", {"categories": categories})
        return render(request, "products/partials/_category_form.html", {"form": form, "category": category})

    return render(request, "products/partials/_category_form.html", {"form": CategoryForm(instance=category), "category": category})


def supplier_reorder_response_view(request, token):
    primary_request = get_object_or_404(
        SupplierReorderRequest.objects.select_related("supplier"),
        token=token,
    )
    supplier = primary_request.supplier
    now = timezone.now()

    # Expire any pending expired requests for this supplier
    pending_expired = SupplierReorderRequest.objects.filter(
        supplier=supplier,
        status=SupplierReorderRequest.STATUS_PENDING,
        expires_at__lt=now
    ).select_related("reorder_request")

    for locked_request in pending_expired:
        with transaction.atomic():
            locked = SupplierReorderRequest.objects.select_for_update().filter(
                pk=locked_request.pk, status=SupplierReorderRequest.STATUS_PENDING
            ).first()
            if locked:
                locked.status = SupplierReorderRequest.STATUS_EXPIRED
                locked.responded_at = now
                locked.save(update_fields=["status", "responded_at", "updated_at"])
                reorder = locked.reorder_request
                if reorder.status == AutoReorderRequest.STATUS_OPEN and reorder.remaining_quantity > 0:
                    notify_next_supplier.delay(reorder.id)

    pending_requests_qs = SupplierReorderRequest.objects.filter(
        supplier=supplier,
        status=SupplierReorderRequest.STATUS_PENDING,
        expires_at__gte=now
    ).select_related("reorder_request__product").order_by("created_at")
    pending_requests = list(pending_requests_qs)

    branch_product_map = {}
    for pending_request in pending_requests:
        branch_breakdown = _normalized_branch_requirements(
            pending_request.reorder_request.branch_requirements or {}
        )
        pending_request.branch_breakdown = branch_breakdown
        for branch_name, qty in branch_breakdown:
            branch_product_map.setdefault(branch_name, []).append(
                {
                    "product_name": pending_request.reorder_request.product.name,
                    "quantity": qty,
                }
            )

    branch_groups = []
    for branch_name in sorted(branch_product_map.keys(), key=lambda value: value.lower()):
        branch_items = branch_product_map[branch_name]
        branch_groups.append(
            {
                "name": branch_name,
                "items": branch_items,
                "total_packets": sum(item["quantity"] for item in branch_items),
            }
        )

    if request.method == "POST":
        should_escalate_ids = []
        feedback_messages = []

        request_ids = request.POST.getlist("request_id")
        for req_id_str in request_ids:
            try:
                req_id = int(req_id_str)
            except ValueError:
                continue

            can_supply = request.POST.get(f"can_supply_{req_id}") == "yes"
            try:
                quantity = int(request.POST.get(f"quantity_{req_id}") or 0)
            except ValueError:
                quantity = 0

            with transaction.atomic():
                locked_req = (
                    SupplierReorderRequest.objects.select_for_update()
                    .select_related("reorder_request__product")
                    .filter(pk=req_id, supplier=supplier, status=SupplierReorderRequest.STATUS_PENDING)
                    .first()
                )
                if not locked_req:
                    continue

                if now > locked_req.expires_at:
                    continue

                reorder = (
                    AutoReorderRequest.objects.select_for_update()
                    .filter(pk=locked_req.reorder_request_id, status=AutoReorderRequest.STATUS_OPEN)
                    .first()
                )
                if not reorder:
                    locked_req.status = SupplierReorderRequest.STATUS_REJECTED
                    locked_req.responded_at = now
                    locked_req.save(update_fields=["status", "responded_at", "updated_at"])
                    continue

                effective_max = min(locked_req.requested_quantity, reorder.remaining_quantity)
                quantity = min(quantity, effective_max) if can_supply else 0

                if not can_supply or quantity <= 0:
                    locked_req.status = SupplierReorderRequest.STATUS_REJECTED
                    locked_req.fulfilled_quantity = 0
                    locked_req.responded_at = now
                    locked_req.save(update_fields=["status", "fulfilled_quantity", "responded_at", "updated_at"])
                    if reorder.remaining_quantity > 0:
                        should_escalate_ids.append(reorder.id)
                    feedback_messages.append(f"{locked_req.reorder_request.product.name}: We will contact the next supplier.")
                else:
                    locked_req.fulfilled_quantity = quantity
                    if quantity < locked_req.requested_quantity:
                        locked_req.status = SupplierReorderRequest.STATUS_PARTIAL
                    else:
                        locked_req.status = SupplierReorderRequest.STATUS_ACCEPTED
                    locked_req.responded_at = now
                    locked_req.save(update_fields=["status", "fulfilled_quantity", "responded_at", "updated_at"])

                    reorder.remaining_quantity = max(reorder.remaining_quantity - quantity, 0)
                    if reorder.remaining_quantity == 0:
                        reorder.status = AutoReorderRequest.STATUS_FULFILLED
                        reorder.completed_at = now
                    reorder.save(update_fields=["remaining_quantity", "status", "completed_at", "updated_at"])

                    if reorder.remaining_quantity > 0:
                        should_escalate_ids.append(reorder.id)
                        feedback_messages.append(f"{locked_req.reorder_request.product.name}: Accepted {quantity}. Will contact next supplier for {reorder.remaining_quantity}.")
                    else:
                        feedback_messages.append(f"{locked_req.reorder_request.product.name}: Accepted {quantity}. Request fulfilled.")

        for esc_id in set(should_escalate_ids):
            notify_next_supplier.delay(esc_id)

        return render(
            request,
            "products/supplier_reorder_response.html",
            {
                "supplier": supplier,
                "can_respond": False,
                "message_type": "success",
                "message": "Feedback received successfully. Thank you for your response.",
                "branch_groups": [],
                "pending_count": 0,
            },
        )

    return render(
        request,
        "products/supplier_reorder_response.html",
        {
            "supplier": supplier,
            "pending_requests": pending_requests,
            "can_respond": bool(pending_requests),
            "branch_groups": branch_groups,
            "pending_count": len(pending_requests),
        },
    )


@login_required
def supplier_prioritize_view(request):
    if not getattr(request.user, "can_manage_users", False):
        messages.error(request, "Permission denied.")
        return redirect("/")
        
    if request.method == "POST":
        supplier_ids = request.POST.getlist("supplier_id")
        try:
            with transaction.atomic():
                for idx, s_id in enumerate(supplier_ids):
                    Supplier.objects.filter(id=int(s_id)).update(priority=idx + 1)
            messages.success(request, f"Global supplier priorities updated successfully.")
        except Exception as e:
            messages.error(request, f"Error updating priorities: {e}")
        return redirect("supplier-prioritize")
        
    suppliers = Supplier.objects.all().order_by("priority", "id")
    
    return render(request, "products/supplier_prioritize.html", {
        "suppliers": suppliers,
    })
