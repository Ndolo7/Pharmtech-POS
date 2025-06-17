from django.urls import path
from . import views

urlpatterns = [
    path('categories/', views.CategoryListCreateView.as_view(), name='categories'),
    path('suppliers/', views.SupplierListCreateView.as_view(), name='suppliers'),
    path('', views.ProductListCreateView.as_view(), name='products'),
    path('<int:pk>/', views.ProductDetailView.as_view(), name='product-detail'),
    path('stock/', views.StockListView.as_view(), name='stock'),
    path('receive/', views.receive_stock, name='receive-stock'),
    path('transfer/', views.transfer_stock, name='transfer-stock'),
    path('adjust/', views.adjust_stock, name='adjust-stock'),
]
