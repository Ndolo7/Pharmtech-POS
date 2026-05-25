from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("reports/sales/", views.sales_report_view, name="sales-report"),
    path("reports/sales/day-breakdown/", views.sales_day_breakdown_modal_view, name="sales-day-breakdown-modal"),
    path("reports/suppliers/", views.supplier_report_view, name="supplier-report"),
    path("reports/suppliers/invoice-items/", views.supplier_invoice_items_modal_view, name="supplier-invoice-items-modal"),
    path("reports/orders/", views.orders_report_view, name="orders-report"),
    path("reports/products-trail/", views.products_trail_report_view, name="products-trail-report"),
    path("reports/shifts/", views.shift_report_view, name="shift-report"),
]
