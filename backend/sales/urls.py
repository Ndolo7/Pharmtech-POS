from django.urls import path
from . import views

urlpatterns = [
    path('', views.SaleListCreateView.as_view(), name='sales'),
    path('process/', views.process_sale, name='process-sale'),
    path('shifts/', views.ShiftListView.as_view(), name='shifts'),
    path('shifts/start/', views.start_shift, name='start-shift'),
    path('shifts/close/', views.close_shift, name='close-shift'),
    path('shifts/current/', views.current_shift, name='current-shift'),
]
