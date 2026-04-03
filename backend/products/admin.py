from django.contrib import admin

from .models import (
    Category,
    Product,
    Purchase,
    PurchaseItem,
    Stock,
    StockMovement,
    Supplier,
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
    list_display = ("name", "barcode", "category", "unit_price", "cost_price", "reorder_level", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name", "barcode")
    ordering = ("name",)


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

