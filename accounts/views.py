from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_POST
from .models import User
from .forms import LoginForm, UserCreateForm, UserEditForm


def _can_manage_users(user):
    return user.is_superuser or user.is_staff or user.can_manage_users()


def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(request.GET.get("next", "/"))
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = LoginForm(request)
    
    return render(request, "accounts/login.html", {"form": form})


@require_POST
@login_required
def logout_view(request):
    logout(request)
    response = redirect("/accounts/login/")
    response.delete_cookie(
        settings.SESSION_COOKIE_NAME,
        path=settings.SESSION_COOKIE_PATH,
        domain=settings.SESSION_COOKIE_DOMAIN,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@login_required
def profile_view(request):
    return render(request, "accounts/profile.html", {"user": request.user})


@login_required
def user_list_view(request):
    if not _can_manage_users(request.user):
        messages.error(request, "Permission denied.")
        return redirect("/")
    
    users = User.objects.select_related("branch").order_by("username")
    form = UserCreateForm()
    
    if request.htmx:
        return render(request, "accounts/partials/_user_table.html", {"users": users})
    
    return render(request, "accounts/user_list.html", {"users": users, "form": form})


@login_required
def user_create_view(request):
    if not _can_manage_users(request.user):
        messages.error(request, "Permission denied.")
        return redirect("/")
    
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "User created successfully.")
            users = User.objects.select_related("branch").order_by("username")
            return render(request, "accounts/partials/_user_table.html", {"users": users})
        return render(request, "accounts/partials/_user_form.html", {"form": form})
    
    form = UserCreateForm()
    return render(request, "accounts/partials/_user_form.html", {"form": form})


@login_required
def user_edit_view(request, pk):
    if not _can_manage_users(request.user):
        messages.error(request, "Permission denied.")
        return redirect("/")
    
    user = get_object_or_404(User, pk=pk)
    
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "User updated successfully.")
            users = User.objects.select_related("branch").order_by("username")
            return render(request, "accounts/partials/_user_table.html", {"users": users})
        return render(request, "accounts/partials/_user_form.html", {"form": form, "editing": user})
    
    form = UserEditForm(instance=user)
    return render(request, "accounts/partials/_user_form.html", {"form": form, "editing": user})
