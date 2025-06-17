from django.urls import path
from . import views

urlpatterns = [
    path('sales/', views.sales_report, name='sales-report'),
    path('suppliers/', views.supplier_report, name='supplier-report'),
    path('shifts/', views.shift_variance_report, name='shift-variance-report'),
    path('dashboard/', views.dashboard_stats, name='dashboard-stats'),
]
