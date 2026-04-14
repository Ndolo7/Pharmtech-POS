from django.urls import path
from . import views

urlpatterns = [
    path("", views.product_list_view, name="product-list"),
    path("stock/", views.stock_list_view, name="stock-list"),
    path("create/", views.product_create_view, name="product-create"),
    path("bulk-upload/template/", views.product_bulk_template_download_view, name="product-bulk-template-download"),
    path("bulk-upload/", views.product_bulk_upload_view, name="product-bulk-upload"),
    path("<int:pk>/edit/", views.product_edit_view, name="product-edit"),
    path("receive/", views.receive_stock_view, name="receive-stock"),
    path("<int:pk>/adjust/", views.adjust_stock_view, name="adjust-stock"),
    path("transfer/", views.transfer_stock_view, name="transfer-stock"),
    path("suppliers/", views.supplier_list_view, name="supplier-list"),
    path("suppliers/create/", views.supplier_create_view, name="supplier-create"),
    path("suppliers/prioritize/", views.supplier_prioritize_view, name="supplier-prioritize"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit_view, name="supplier-edit"),
    path("categories/", views.category_list_view, name="category-list"),
    path("categories/create/", views.category_create_view, name="category-create"),
    path("categories/<int:pk>/edit/", views.category_edit_view, name="category-edit"),
    path(
        "reorder/respond/<uuid:token>/",
        views.supplier_reorder_response_view,
        name="supplier-reorder-response",
    ),
    path("supplier-pending-orders/", views.supplier_pending_orders_view, name="supplier-pending-orders"),
    path("order/create/", views.manual_order_create_view, name="create-order"),
]
