from django.urls import path
from . import views

urlpatterns = [
    path("", views.branch_list_view, name="branch-list"),
    path("create/", views.branch_create_view, name="branch-create"),
]
