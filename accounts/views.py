import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core.permissions import administrator_required, get_profile
from core.validation import DomainError

from . import services
from .models import Department, Designation

security_log = logging.getLogger("vsclms.security")

GENERIC_ERROR = "Invalid username or password."
REQUIRED_ERROR = "Both username and password are required."

# ------------------------------------ auth------------------------------------------------

def _safe_next(request):
    """Return the ``next`` target only when it stays on this site."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return ""


@login_required
def dashboard_view(request):
    return render(request, "accounts/dashboard.html")


@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = _safe_next(request)
    if request.user.is_authenticated:
        return redirect(next_url or "dashboard")

    def fail(message, username=""):
        return render(
            request, "accounts/login.html",
            {"error":message, "username":username, "next": next_url},
        )

    if request.method == "POST":
        username = request.POST.get("username").strip()
        password = request.POST.get("password")
        if not username or not password:
            return fail(REQUIRED_ERROR, username=username)
        user = authenticate(request, username=username, password=password)
        # One generic message for every failure, so the page never reveals
        # whether a username exists or whether an account is switched off.
        if user is None:
            security_log.warning("login failed username=%r", username)
            return fail(GENERIC_ERROR, username=username)
        profile = get_profile(user)
        if profile is not None and not profile.is_active:
            security_log.warning("login refused (inactive profile) user=%s", user.pk)
            return fail(GENERIC_ERROR, username=username)
        login(request, user)
        return redirect(next_url or "dashboard")
    return render(request, "accounts/login.html", {"next": next_url})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")

# ------------------------------- designations ----------------------------------------

DESIGNATION_FORM = "accounts/designation_form.html"

@administrator_required
@require_http_methods(["GET"])
def designation_list(request):
    designations = Designation.objects.order_by("title")
    return render(request, "accounts/designation_list.html", {"designations": designations})

@administrator_required
@require_http_methods(["GET", "POST"])
def designation_create(request):
    if request.method == "POST":
        try:
            designation = services.create_designation(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, DESIGNATION_FORM, {"mode": "create", "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Designation “{designation.title}” created.")
        return redirect("designation_list")
    return render(request, DESIGNATION_FORM, {"mode": "create", "errors": {}, "values": {}})

@administrator_required
@require_http_methods(["GET", "POST"])
def designation_edit(request, pk):
    designation = get_object_or_404(Designation, pk=pk)
    if request.method == "POST":
        try:
            services.update_designation(designation=designation, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, DESIGNATION_FORM, {
                "mode": "edit", "designation": designation, "errors": exc.errors, "values": request.POST,
            })
        messages.success(request,  f"Designation “{designation.title}” updated.")
        return redirect("designation_list")
    return render(request, DESIGNATION_FORM, {"mode": "edit", "designation": designation, "errors": {}, "values": {"title": designation.title}})

@administrator_required
@require_POST
def designation_toggle(request, pk):
    designation = get_object_or_404(Designation, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_designation_active(designation=designation, active=active, actor=request.user)
    messages.success(
        request, f"Designation “{designation.title}” {'activated' if active else 'deactivated'}."
    )
    return redirect("designation_list")

# -------------------------- departments ------------------------------------------
DEPARTMENT_FORM = "accounts/department_form.html"

@administrator_required
@require_http_methods(["GET"])
def department_list(request):
    departments = (
        Department.objects.select_related("hod__user")
        .annotate(active_staff=Count("staff", filter=Q(staff__is_active=True)))
        .order_by("name")
        )
    return render(request, "accounts/department_list.html", {"departments": departments})

@administrator_required
@require_http_methods(["GET", "POST"])
def department_create(request):
    if request.method == "POST":
        try:
            department = services.create_department(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, DEPARTMENT_FORM, {"mode": "create", "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Department “{department.name}” created.")
        return redirect("department_list")
    return render(request, DEPARTMENT_FORM, {"mode": "create", "errors": {}, "values": {}})

@administrator_required
@require_http_methods(["GET", "POST"])
def department_edit(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == "POST":
        try:
            services.update_department(department=department, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, DEPARTMENT_FORM, {"mode": "edit", "department": department, "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Department “{department.name}” updated.")
        return redirect("department_list")
    return render(request, DEPARTMENT_FORM, {"mode": "edit", "department": department, "errors": {}, "values": {"name": department.name}})

@administrator_required
@require_POST
def department_toggle(request, pk):
    department = get_object_or_404(Department, pk=pk)
    active = request.POST.get("active") == "1"
    try:
        services.set_department_active(department=department, active=active, actor=request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request,  f"Department “{department.name}” {'activated' if active else 'deactivated'}.")
    return redirect("department_list")

