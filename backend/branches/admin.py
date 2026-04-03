from django.contrib import admin

from .models import Branch


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "phone_number", "manager", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "code", "phone_number", "address")
    ordering = ("name",)

