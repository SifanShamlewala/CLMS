from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from core.permissions import administrator_required
from core.validation import DomainError

from . import services
from .models import AcademicYear, Holiday, LeaveType, PolicyConfig


# ------------------------------------------------------------------ leave types
LEAVE_TYPE_FORM = "academics/leavetype_form.html"


@administrator_required
@require_http_methods(["GET"])
def leave_type_list(request):
    leave_types = LeaveType.objects.order_by("name")
    return render(request, "academics/leavetype_list.html", {"leave_types": leave_types})


@administrator_required
@require_http_methods(["GET", "POST"])
def leave_type_create(request):
    if request.method == "POST":
        try:
            leave_type = services.create_leave_type(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, LEAVE_TYPE_FORM, {"mode": "create", "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Leave type “{leave_type.name}” created.")
        return redirect("leave_type_list")
    return render(request, LEAVE_TYPE_FORM, {"mode": "create", "errors": {}, "values": {}})


@administrator_required
@require_http_methods(["GET", "POST"])
def leave_type_edit(request, pk):
    leave_type = get_object_or_404(LeaveType, pk=pk)
    if request.method == "POST":
        try:
            services.update_leave_type(leave_type=leave_type, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, LEAVE_TYPE_FORM, {
                "mode": "edit", "leave_type": leave_type, "errors": exc.errors, "values": request.POST,
            })
        messages.success(request, f"Leave type “{leave_type.name}” updated.")
        return redirect("leave_type_list")
    return render(request, LEAVE_TYPE_FORM, {
        "mode": "edit", "leave_type": leave_type, "errors": {},
        "values": {"name": leave_type.name, "code": leave_type.code, "description": leave_type.description},
    })


@administrator_required
@require_POST
def leave_type_toggle(request, pk):
    leave_type = get_object_or_404(LeaveType, pk=pk)
    active = request.POST.get("active") == "1"
    services.set_leave_type_active(leave_type=leave_type, active=active, actor=request.user)
    messages.success(
        request, f"Leave type “{leave_type.name}” {'activated' if active else 'deactivated'}."
    )
    return redirect("leave_type_list")


# ---------------------------------------------------------------- academic years
YEAR_FORM = "academics/year_form.html"


@administrator_required
@require_http_methods(["GET"])
def year_list(request):
    years = AcademicYear.objects.annotate(
        holiday_count=Count("holidays", filter=Q(holidays__is_deleted=False))
    ).order_by("-start_date")
    no_active = not AcademicYear.objects.filter(is_active=True).exists()
    return render(request, "academics/year_list.html", {"years": years, "no_active": no_active})


@administrator_required
@require_http_methods(["GET", "POST"])
def year_create(request):
    if request.method == "POST":
        try:
            year = services.create_academic_year(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, YEAR_FORM, {"mode": "create", "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Academic year “{year.name}” created. Activate it when it is ready to use.")
        return redirect("year_list")
    return render(request, YEAR_FORM, {"mode": "create", "errors": {}, "values": {}})


@administrator_required
@require_http_methods(["GET", "POST"])
def year_edit(request, pk):
    year = get_object_or_404(AcademicYear, pk=pk)
    if request.method == "POST":
        try:
            services.update_academic_year(year=year, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, YEAR_FORM, {
                "mode": "edit", "year": year, "errors": exc.errors, "values": request.POST,
            })
        messages.success(request, f"Academic year “{year.name}” updated.")
        return redirect("year_list")
    return render(request, YEAR_FORM, {
        "mode": "edit", "year": year, "errors": {},
        "values": {
            "name": year.name,
            "start_date": year.start_date.isoformat(),
            "end_date": year.end_date.isoformat(),
        },
    })


@administrator_required
@require_POST
def year_activate(request, pk):
    year = get_object_or_404(AcademicYear, pk=pk)
    try:
        services.activate_academic_year(year=year, actor=request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"Academic year “{year.name}” is now the active year.")
    return redirect("year_list")


# ---------------------------------------------------------------------- holidays
HOLIDAY_FORM = "academics/holiday_form.html"


def _selected_year(request):
    """The year to show: ?year=<id>, else the active year, else the latest one."""
    years = list(AcademicYear.objects.order_by("-start_date"))
    wanted = request.GET.get("year", "")
    selected = (
        next((y for y in years if str(y.pk) == wanted), None)
        or next((y for y in years if y.is_active), None)
        or (years[0] if years else None)
    )
    return years, selected


def _year_options():
    return [(y.pk, y.name) for y in AcademicYear.objects.order_by("-start_date")]


def _to_calendar(year):
    return redirect(f"{reverse('holiday_list')}?year={year.pk}")


@administrator_required
@require_http_methods(["GET"])
def holiday_list(request):
    years, selected = _selected_year(request)
    holidays = (
        selected.holidays.filter(is_deleted=False).order_by("date") if selected else []
    )
    return render(request, "academics/holiday_list.html", {
        "years": years, "selected": selected, "holidays": holidays,
    })


@administrator_required
@require_http_methods(["GET", "POST"])
def holiday_create(request):
    context = {"mode": "create", "year_options": _year_options()}
    if request.method == "POST":
        try:
            holiday = services.create_holiday(data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, HOLIDAY_FORM, {**context, "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Holiday “{holiday.name}” added.")
        return _to_calendar(holiday.academic_year)
    _, selected = _selected_year(request)
    values = {"academic_year": str(selected.pk)} if selected else {}
    return render(request, HOLIDAY_FORM, {**context, "errors": {}, "values": values})


@administrator_required
@require_http_methods(["GET", "POST"])
def holiday_edit(request, pk):
    holiday = get_object_or_404(Holiday.objects.select_related("academic_year"), pk=pk, is_deleted=False)
    context = {"mode": "edit", "holiday": holiday}
    if request.method == "POST":
        try:
            services.update_holiday(holiday=holiday, data=request.POST, actor=request.user)
        except DomainError as exc:
            return render(request, HOLIDAY_FORM, {**context, "errors": exc.errors, "values": request.POST})
        messages.success(request, f"Holiday “{holiday.name}” updated.")
        return _to_calendar(holiday.academic_year)
    values = {"name": holiday.name, "date": holiday.date.isoformat()}
    return render(request, HOLIDAY_FORM, {**context, "errors": {}, "values": values})


@administrator_required
@require_POST
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday.objects.select_related("academic_year"), pk=pk, is_deleted=False)
    services.delete_holiday(holiday=holiday, actor=request.user)
    messages.success(request, f"Holiday “{holiday.name}” removed.")
    return _to_calendar(holiday.academic_year)


# ------------------------------------------------------------------------ policy
POLICY_FORM = "academics/policy_form.html"
WEEKDAY_OPTIONS = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"),
    ("3", "Thursday"), ("4", "Friday"), ("5", "Saturday"),
]


def _policy_form(source):
    """The values the form shows, from the saved policy or from a failed POST."""
    if isinstance(source, PolicyConfig):
        return {
            "weekly_off_days": sorted(str(day) for day in source.weekly_off_days_set),
            "hod_own_leave_route": source.hod_own_leave_route,
            **{flag: getattr(source, flag) for flag in services.POLICY_FLAGS},
        }
    return {
        "weekly_off_days": source.getlist("weekly_off_days"),
        "hod_own_leave_route": source.get("hod_own_leave_route", ""),
        **{flag: source.get(flag) == "on" for flag in services.POLICY_FLAGS},
    }


def _render_policy(request, form, errors):
    selected = set(form["weekly_off_days"])
    return render(request, POLICY_FORM, {
        "form": form,
        "errors": errors,
        "weekday_options": [(value, text, value in selected) for value, text in WEEKDAY_OPTIONS],
        "route_options": PolicyConfig.HodOwnLeaveRoute.choices,
    })


@administrator_required
@require_http_methods(["GET", "POST"])
def policy_edit(request):
    if request.method == "POST":
        try:
            services.update_policy(data=request.POST, actor=request.user)
        except DomainError as exc:
            return _render_policy(request, _policy_form(request.POST), exc.errors)
        messages.success(request, "Policy settings saved. They apply to new applications.")
        return redirect("policy_edit")
    return _render_policy(request, _policy_form(PolicyConfig.get()), {})
