from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .forms import BranchForm
from .models import Branch


def _can_manage_branches(user):
    return user.is_superuser or user.is_staff or user.can_manage_branches()


@login_required
def branch_list_view(request):
    branches = Branch.objects.select_related("manager").order_by("name")
    ctx = {
        "branches": branches,
        "form": BranchForm(),
        "can_manage_branches": _can_manage_branches(request.user),
    }

    if request.htmx:
        return render(request, "branches/partials/_branch_table.html", ctx)

    return render(request, "branches/branch_list.html", ctx)


@login_required
def branch_create_view(request):
    if not _can_manage_branches(request.user):
        messages.error(request, "Permission denied.")
        if request.htmx:
            return render(request, "branches/partials/_branch_form.html", {"form": BranchForm(), "blocked": True})
        return redirect("dashboard")

    if request.method == "POST":
        form = BranchForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Branch created successfully.")
            branches = Branch.objects.select_related("manager").order_by("name")
            return render(request, "branches/partials/_branch_table.html", {"branches": branches})
        return render(request, "branches/partials/_branch_form.html", {"form": form})

    return render(request, "branches/partials/_branch_form.html", {"form": BranchForm()})
