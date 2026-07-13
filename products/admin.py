from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import (
    AutoOrderScheduleSetting,
    AutoReorderRequest,
    BranchSupplyRequest,
    Category,
    Product,
    Purchase,
    PurchaseItem,
    Stock,
    StockMovement,
    Supplier,
    SupplierReorderRequest,
    Transfer,
    TransferItem,
)
from .scheduling import sync_auto_order_periodic_task


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "contact_person", "phone_number", "email", "created_at")
    list_filter = ("branch", "created_at")
    search_fields = ("name", "contact_person", "phone_number", "email", "branch__name")
    ordering = ("name",)


@admin.register(AutoOrderScheduleSetting)
class AutoOrderScheduleSettingAdmin(admin.ModelAdmin):
    list_display = ("daily_run_time", "sunday_run_time", "updated_at")
    fields = ("daily_run_time", "sunday_run_time", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not AutoOrderScheduleSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        existing = AutoOrderScheduleSetting.objects.order_by("id").first()
        if existing:
            return redirect(reverse("admin:products_autoorderschedulesetting_change", args=[existing.pk]))
        return redirect(reverse("admin:products_autoorderschedulesetting_add"))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        sync_auto_order_periodic_task(obj)



@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "barcode",
        "unit_price",
        "cost_price",
        "reorder_level",
        "max_stock",
        "pack_quantity",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "barcode")
    ordering = ("name",)





@admin.register(AutoReorderRequest)
class AutoReorderRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product",
        "origin",
        "approval_status",
        "requested_quantity",
        "remaining_quantity",
        "target_stock_level",
        "status",
        "created_at",
        "completed_at",
    )
    list_filter = ("origin", "approval_status", "status", "created_at")
    search_fields = ("product__name", "product__barcode", "created_by__username")
    ordering = ("-created_at",)


@admin.register(SupplierReorderRequest)
class SupplierReorderRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reorder_request",
        "supplier",
        "priority",
        "requested_quantity",
        "fulfilled_quantity",
        "status",
        "expires_at",
        "responded_at",
    )
    list_filter = ("status", "priority", "created_at")
    search_fields = ("supplier__name", "reorder_request__product__name")
    ordering = ("-created_at",)


@admin.register(BranchSupplyRequest)
class BranchSupplyRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product",
        "source_branch",
        "destination_branch",
        "requested_quantity",
        "fulfilled_quantity",
        "status",
        "created_at",
    )
    list_filter = ("status", "source_branch", "destination_branch", "created_at")
    search_fields = ("product__name", "source_branch__name", "destination_branch__name")
    ordering = ("-created_at",)


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity", "updated_at")
    list_filter = ("branch",)
    search_fields = ("product__name", "product__barcode", "branch__name")
    ordering = ("product__name",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "movement_type", "quantity", "reference", "created_by", "created_at")
    list_filter = ("movement_type", "branch", "created_at")
    search_fields = ("product__name", "reference", "created_by__username")
    ordering = ("-created_at",)


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "supplier", "branch", "total_amount", "created_by", "created_at")
    list_filter = ("branch", "supplier", "created_at")
    search_fields = ("invoice_number", "supplier__name", "created_by__username")
    ordering = ("-created_at",)
    inlines = [PurchaseItemInline]


class TransferItemInline(admin.TabularInline):
    model = TransferItem
    extra = 0


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("id", "from_branch", "to_branch", "created_by", "created_at")
    list_filter = ("from_branch", "to_branch", "created_at")
    search_fields = ("created_by__username", "notes")
    ordering = ("-created_at",)
    inlines = [TransferItemInline]
