from django.urls import path
from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("users/", views.user_list_view, name="user-list"),
    path("users/create/", views.user_create_view, name="user-create"),
    path("users/<int:pk>/edit/", views.user_edit_view, name="user-edit"),
]
