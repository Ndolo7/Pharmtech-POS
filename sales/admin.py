from django.contrib import admin

from .models import Sale, SaleItem, Shift


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "receipt_number",
        "branch",
        "cashier",
        "payment_method",
        "cash_amount",
        "mpesa_amount",
        "total_amount",
        "created_at",
    )
    list_filter = ("payment_method", "branch", "cashier", "created_at")
    search_fields = ("receipt_number", "cashier__username", "customer_name", "customer_phone")
    ordering = ("-created_at",)
    inlines = [SaleItemInline]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cashier",
        "branch",
        "start_time",
        "end_time",
        "opening_cash",
        "is_closed",
        "cash_variance",
        "mpesa_variance",
    )
    list_filter = ("is_closed", "branch", "cashier", "start_time")
    search_fields = ("cashier__username", "branch__name")
    ordering = ("-start_time",)

