from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from academics.models import AcademicYear, LeaveType
from accounts.models import Department, StaffProfile
from core.permissions import administrator_required
from core.validation import DomainError

from . import services
from .models import StaffLeaveBalance

BALANCE_FORM = "leaves/balance_form.html"
PAGE_SIZE = 25


def _name(profile):
    return profile.user.get_full_name() or profile.user.get_username()


def _days(value):
    """10.0 -> '10', 7.5 -> '7.5'."""
    return f"{value:f}".rstrip("0").rstrip(".") or "0"


def _staff_options():
    profiles = (
        StaffProfile.objects.select_related("user", "department")
        .filter(is_active=True)
        .order_by("user__first_name", "user__last_name", "employee_id")
    )
    return [(p.pk, f"{_name(p)} ({p.employee_id}, {p.department.name})") for p in profiles]


def _leave_type_options():
    return [(t.pk, f"{t.name} ({t.code})") for t in LeaveType.objects.filter(is_active=True).order_by("name")]


def _year_options():
    return [(y.pk, y.name) for y in AcademicYear.objects.order_by("-start_date")]


def _to_list(balance):
    return redirect(f"{reverse('balance_list')}?year={balance.academic_year_id}")


@administrator_required
@require_http_methods(["GET"])
def balance_list(request):
    years = list(AcademicYear.objects.order_by("-start_date"))
    wanted = request.GET.get("year", "")
    selected = (
        next((y for y in years if str(y.pk) == wanted), None)
        or next((y for y in years if y.is_active), None)
        or (years[0] if years else None)
    )
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "").strip()
    leave_type = request.GET.get("leave_type", "").strip()

    page_obj = None
    if selected is not None:
        balances = (
            StaffLeaveBalance.objects.select_related("staff__user", "staff__department", "leave_type", "academic_year")
            .filter(academic_year=selected)
            .order_by("staff__user__first_name", "staff__user__last_name", "leave_type__code")
        )
        if q:
            balances = balances.filter(
                Q(staff__user__first_name__icontains=q) | Q(staff__user__last_name__icontains=q)
                | Q(staff__user__username__icontains=q) | Q(staff__employee_id__icontains=q)
            )
        if department.isdigit():
            balances = balances.filter(staff__department_id=int(department))
        if leave_type.isdigit():
            balances = balances.filter(leave_type_id=int(leave_type))
        page_obj = Paginator(balances, PAGE_SIZE).get_page(request.GET.get("page"))

    base = request.GET.copy()
    base.pop("page", None)
    return render(request, "leaves/balance_list.html", {
        "selected": selected,
        "page_obj": page_obj,
        "qs_base": base.urlencode(),
        "filters": {
            "year": str(selected.pk) if selected else "",
            "q": q, "department": department, "leave_type": leave_type,
        },
        "year_options": [(y.pk, y.name) for y in years],
        "department_options": [(d.pk, d.name) for d in Department.objects.order_by("name")],
        "leave_type_options": [(t.pk, f"{t.name} ({t.code})") for t in LeaveType.objects.order_by("name")],
        "has_filters": bool(q or department or leave_type),
    })


@administrator_required
@require_http_methods(["GET", "POST"])
def balance_create(request):
    context = {
        "mode": "create",
        "staff_options": _staff_options(),
        "leave_type_options": _leave_type_options(),
        "year_options": _year_options(),
    }
    if request.method == "POST":
        try:
            balance = services.create_balance(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, BALANCE_FORM, {**context, "errors": exc.errors, "values": request.POST})
        messages.success(
            request,
            f"{_name(balance.staff)} now has {_days(balance.total)} days of {balance.leave_type.code} "
            f"for {balance.academic_year.name}.",
        )
        return _to_list(balance)
    active = AcademicYear.objects.filter(is_active=True).first()
    values = {"academic_year": str(active.pk)} if active else {}
    return render(request, BALANCE_FORM, {**context, "errors": {}, "values": values})


@administrator_required
@require_http_methods(["GET", "POST"])
def balance_edit(request, pk):
    balance = get_object_or_404(
        StaffLeaveBalance.objects.select_related("staff__user", "staff__department", "leave_type", "academic_year"),
        pk=pk,
    )
    context = {"mode": "edit", "balance": balance}
    if request.method == "POST":
        try:
            balance = services.update_balance(balance=balance, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, BALANCE_FORM, {**context, "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Total changed to {_days(balance.total)} days.")
        return _to_list(balance)
    return render(request, BALANCE_FORM, {**context, "errors": {}, "values": {"total": _days(balance.total)}})
