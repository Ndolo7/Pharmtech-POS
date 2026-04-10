from django.contrib import admin

from .models import (
    AutoReorderRequest,
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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone_number", "email", "created_at")
    search_fields = ("name", "contact_person", "phone_number", "email")
    ordering = ("name",)



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
        "requested_quantity",
        "remaining_quantity",
        "target_stock_level",
        "status",
        "created_at",
        "completed_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("product__name", "product__barcode")
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
