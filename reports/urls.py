from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("reports/sales/", views.sales_report_view, name="sales-report"),
    path("reports/suppliers/", views.supplier_report_view, name="supplier-report"),
    path("reports/shifts/", views.shift_report_view, name="shift-report"),
]
