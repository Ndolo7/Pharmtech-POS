from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse

from branches.models import Branch
from .forms import (
    ProductForm,
    CategoryForm,
    AdjustStockForm,
    TransferStockForm,
    ProductBulkUploadForm,
    SupplierForm,
)
from .models import (
    Category,
    Supplier,
    Product,
    Stock,
    StockMovement,
    Purchase,
    PurchaseItem,
    Transfer,
    TransferItem,
)


def _can_manage_catalog(user):
    return user.is_superuser or user.is_staff or user.can_manage_users()


def _can_select_products_branch(user):
    return user.is_superuser or getattr(user, "role", "") == "super_admin"


def _resolve_products_branch(request):
    selected_branch_id = (request.GET.get("branch") or request.POST.get("branch_id") or "").strip()
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
        "categories": Category.objects.all(),
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
    products = Product.objects.filter(is_active=True).select_related("category")
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
    active_branch = _resolve_products_branch(request)

    if request.method == "POST":
        changed_products = []

        if not active_branch:
            messages.error(request, "You are not assigned to a branch.")
            return _rows_oob_response(request, changed_products, branch=active_branch) if request.htmx else _redirect_with_branch("stock-list", active_branch)

        try:
            with transaction.atomic():
                supplier = get_object_or_404(Supplier, pk=request.POST.get("supplier_id"))
                invoice_number = request.POST.get("invoice_number", "").strip()
                product_ids = request.POST.getlist("product_id[]")
                quantities = request.POST.getlist("quantity[]")
                unit_costs = request.POST.getlist("unit_cost[]")

                if not invoice_number:
                    raise ValueError("Invoice number is required.")
                if not product_ids:
                    raise ValueError("Add at least one product line.")
                if len(product_ids) != len(quantities) or len(product_ids) != len(unit_costs):
                    raise ValueError("Incomplete stock lines were submitted.")

                total_amount = 0
                cleaned_lines = []
                for pid, qty, cost in zip(product_ids, quantities, unit_costs):
                    qty_int = int(qty)
                    cost_float = float(cost)
                    if qty_int <= 0:
                        raise ValueError("Quantity must be greater than zero.")
                    if cost_float < 0:
                        raise ValueError("Unit cost cannot be negative.")
                    total_amount += qty_int * cost_float
                    cleaned_lines.append((pid, qty_int, cost_float))

                purchase = Purchase.objects.create(
                    supplier=supplier,
                    branch=active_branch,
                    invoice_number=invoice_number,
                    total_amount=total_amount,
                    created_by=request.user,
                )

                for pid, qty_int, cost_float in cleaned_lines:
                    product = get_object_or_404(Product, pk=pid)
                    changed_products.append(product)

                    PurchaseItem.objects.create(
                        purchase=purchase,
                        product=product,
                        quantity=qty_int,
                        unit_cost=cost_float,
                    )

                    stock, _ = Stock.objects.get_or_create(
                        product=product,
                        branch=active_branch,
                        defaults={"quantity": 0},
                    )
                    stock.quantity += qty_int
                    stock.save(update_fields=["quantity", "updated_at"])

                    StockMovement.objects.create(
                        product=product,
                        branch=active_branch,
                        movement_type="purchase",
                        quantity=qty_int,
                        reference=invoice_number,
                        created_by=request.user,
                    )

            messages.success(request, "Stock received successfully.")
            return _rows_oob_response(request, changed_products, branch=active_branch) if request.htmx else _redirect_with_branch("stock-list", active_branch)

        except Exception as exc:
            messages.error(request, f"Error: {exc}")
            return _rows_oob_response(request, changed_products, branch=active_branch) if request.htmx else _redirect_with_branch("stock-list", active_branch)

    suppliers = Supplier.objects.all()
    products = Product.objects.filter(is_active=True)
    return render(
        request,
        "products/partials/_receive_form.html",
        {
            "suppliers": suppliers,
            "products": products,
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

        return render(
            request,
            "products/partials/_adjust_form.html",
            {
                "form": form,
                "product": product,
                "current_stock": product.current_stock(branch),
                "active_branch": branch,
                "active_branch_id": branch.pk if branch else "",
            },
        )

    form = AdjustStockForm()
    return render(
        request,
        "products/partials/_adjust_form.html",
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
    from_branch = _resolve_products_branch(request)
    if not from_branch:
        messages.error(request, "You are not assigned to any branch.")
        if request.method == "GET":
            return render(request, "products/partials/_transfer_form.html", {"blocked": True})
        return _rows_oob_response(request, [], branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)

    active_branches = Branch.objects.filter(is_active=True).exclude(pk=from_branch.pk).order_by("name")

    if request.method == "POST":
        form = TransferStockForm(request.POST)
        form.fields["to_branch"].queryset = active_branches
        changed_products = []

        if form.is_valid():
            to_branch = form.cleaned_data["to_branch"]
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

                messages.success(request, f"Transfer to {to_branch.name} completed.")
                return _rows_oob_response(request, changed_products, branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)
            except Exception as exc:
                messages.error(request, f"Error: {exc}")
                return _rows_oob_response(request, changed_products, branch=from_branch) if request.htmx else _redirect_with_branch("stock-list", from_branch)

        products = Product.objects.filter(is_active=True).order_by("name")
        return render(
            request,
            "products/partials/_transfer_form.html",
            {
                "form": form,
                "products": products,
                "branches": active_branches,
                "active_branch": from_branch,
                "active_branch_id": from_branch.pk if from_branch else "",
            },
        )

    form = TransferStockForm()
    form.fields["to_branch"].queryset = active_branches
    products = Product.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "products/partials/_transfer_form.html",
        {
            "form": form,
            "products": products,
            "branches": active_branches,
            "active_branch": from_branch,
            "active_branch_id": from_branch.pk if from_branch else "",
        },
    )


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

        headers = [str(col).strip().lower() if col is not None else "" for col in rows[0]]
        required_headers = {"name", "category", "unit_price", "cost_price"}
        if not required_headers.issubset(set(headers)):
            messages.error(
                request,
                "Missing required columns. Required: name, category, unit_price, cost_price.",
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
                name = str(row[h["name"]]).strip()
                category_name = str(row[h["category"]]).strip()
                unit_price = Decimal(str(row[h["unit_price"]]).strip())
                cost_price = Decimal(str(row[h["cost_price"]]).strip())
                if not name or not category_name:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue

            barcode = ""
            if "barcode" in h and row[h["barcode"]] not in (None, ""):
                barcode = str(row[h["barcode"]]).strip()

            description = ""
            if "description" in h and row[h["description"]] not in (None, ""):
                description = str(row[h["description"]]).strip()

            reorder_level = 10
            if "reorder_level" in h and row[h["reorder_level"]] not in (None, ""):
                try:
                    reorder_level = int(row[h["reorder_level"]])
                except Exception:
                    reorder_level = 10

            category, _ = Category.objects.get_or_create(name=category_name, defaults={"description": ""})

            defaults = {
                "name": name,
                "category": category,
                "description": description,
                "unit_price": unit_price,
                "cost_price": cost_price,
                "reorder_level": reorder_level,
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
