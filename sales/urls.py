from django.urls import path
from . import views

urlpatterns = [
    path("", views.pos_view, name="pos"),
    path("credit/", views.credit_ledger_view, name="credit-ledger"),
    path("credit/<int:account_id>/repay/", views.record_credit_repayment_view, name="credit-repayment"),
    path("search/", views.product_search_view, name="product-search"),
    path("process/", views.process_sale_view, name="process-sale"),
    path("shifts/current/", views.current_shift_view, name="current-shift"),
    path("shifts/current/sales/", views.current_shift_sales_view, name="current-shift-sales"),
    path("shifts/start/", views.start_shift_view, name="start-shift"),
    path("shifts/close/", views.close_shift_view, name="close-shift"),
]
