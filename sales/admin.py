from django.contrib import admin

from .models import CreditAccount, CreditTransaction, Sale, SaleItem, Shift, ShiftExpense


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


class ShiftExpenseInline(admin.TabularInline):
    model = ShiftExpense
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
        "credit_amount",
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
    inlines = [ShiftExpenseInline]


@admin.register(ShiftExpense)
class ShiftExpenseAdmin(admin.ModelAdmin):
    list_display = ("id", "shift", "amount", "description", "created_at")
    list_filter = ("shift__branch", "shift__cashier", "created_at")
    search_fields = ("description", "shift__cashier__username", "shift__branch__name")
    ordering = ("-created_at",)


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    list_display = ("customer_name", "customer_phone", "branch", "outstanding_balance", "updated_at")
    list_filter = ("branch", "updated_at")
    search_fields = ("customer_name", "customer_phone", "branch__name")
    ordering = ("-outstanding_balance", "customer_name")


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("account", "transaction_type", "amount", "payment_method", "branch", "created_by", "created_at")
    list_filter = ("transaction_type", "payment_method", "branch", "created_at")
    search_fields = ("account__customer_name", "account__customer_phone", "sale__receipt_number")
    ordering = ("-created_at",)
