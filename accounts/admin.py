from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "branch",
        "is_active_shift",
        "is_staff",
        "is_active",
    )
    list_filter = ("role", "branch", "is_active_shift", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name", "phone_number")
    ordering = ("username",)

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "POS Details",
            {"fields": ("role", "phone_number", "branch", "is_active_shift")},
        ),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "POS Details",
            {"fields": ("role", "phone_number", "branch", "is_active_shift")},
        ),
    )

