from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from academics.models import AcademicYear, LeaveType
from accounts.models import StaffProfile
from core.permissions import get_profile, role_required
from core.validation import DomainError

from . import applications
from .access import can_view_application
from .applications import APPLICANT_ROLES, days_text
from .models import LeaveApplication, LeaveDay, StaffLeaveBalance
from .workflow import next_step_text

Role = StaffProfile.Role
APPLICANT = role_required(*APPLICANT_ROLES)
PAGE_SIZE = 20
PART_OPTIONS = [(value, label) for value, label in LeaveDay.DayPart.choices]
ZERO_DAYS = Value(0, output_field=DecimalField(max_digits=6, decimal_places=1))


def _leave_type_options(profile, year):
    """Active leave types, each labelled with the days this person has left."""
    balances = {}
    if year is not None:
        balances = {
            b.leave_type_id: b
            for b in StaffLeaveBalance.objects.filter(staff=profile, academic_year=year)
        }
    options = []
    for leave_type in LeaveType.objects.filter(is_active=True).order_by("name"):
        balance = balances.get(leave_type.pk)
        if year is None:
            note = ""
        elif balance is None:
            note = ", no balance (leave without pay)"
        else:
            note = f", {days_text(balance.remaining)} days left"
        options.append((leave_type.pk, f"{leave_type.name} ({leave_type.code}){note}"))
    return options


def _render_form(request, profile, values, errors):
    year = applications.active_year()
    return render(request, "leaves/apply_form.html", {
        "year": year,
        "blocker": applications.application_blocker(profile),
        "leave_type_options": _leave_type_options(profile, year),
        "is_principal": profile.role == Role.PRINCIPAL,
        "values": values,
        "errors": errors,
    })


@APPLICANT
@require_http_methods(["GET", "POST"])
def leave_apply(request):
    profile = request.user.profile
    if request.method == "GET":
        return _render_form(request, profile, {"requires_workload": "on"}, {})
    step = request.POST.get("step", "preview")
    if step == "edit":
        return _render_form(request, profile, request.POST, {})
    try:
        if step == "submit":
            application = applications.submit_application(user=request.user, data=request.POST)
            messages.success(
                request,
                f"Leave application #{application.pk} submitted. {next_step_text(application.status)}",
            )
            return redirect("leave_detail", pk=application.pk)
        preview = applications.build_preview(user=request.user, data=request.POST)
    except DomainError as exc:
        return _render_form(request, profile, request.POST, exc.errors)
    return render(request, "leaves/apply_preview.html", {"preview": preview, "part_options": PART_OPTIONS})


@APPLICANT
@require_http_methods(["GET"])
def leave_mine(request):
    profile = request.user.profile
    status = request.GET.get("status", "").strip()
    year = request.GET.get("year", "").strip()
    # Day totals come from the LeaveDay rows, never from the snapshot fields.
    mine = (
        LeaveApplication.objects.filter(applicant=profile, is_deleted=False)
        .select_related("leave_type", "academic_year")
        .annotate(
            counted=Coalesce(Sum("days__fraction", filter=Q(days__is_counted=True)), ZERO_DAYS),
            lwp=Coalesce(Sum("days__fraction", filter=Q(days__is_counted=True, days__is_lwp=True)), ZERO_DAYS),
        )
        .order_by("-start_date", "-id")
    )
    if status in LeaveApplication.Status.values:
        mine = mine.filter(status=status)
    if year.isdigit():
        mine = mine.filter(academic_year_id=int(year))
    page_obj = Paginator(mine, PAGE_SIZE).get_page(request.GET.get("page"))
    for application in page_obj:
        application.paid = application.counted - application.lwp
    base = request.GET.copy()
    base.pop("page", None)

    active = applications.active_year()
    balances = (
        StaffLeaveBalance.objects.filter(staff=profile, academic_year=active).select_related("leave_type")
        .order_by("leave_type__name") if active else []
    )
    return render(request, "leaves/my_leaves.html", {
        "page_obj": page_obj,
        "qs_base": base.urlencode(),
        "active_year": active,
        "balances": balances,
        "filters": {"status": status, "year": year},
        "status_options": LeaveApplication.Status.choices,
        "year_options": [(y.pk, y.name) for y in AcademicYear.objects.order_by("-start_date")],
        "has_filters": bool(status or year),
    })


@login_required
@require_http_methods(["GET"])
def leave_detail(request, pk):
    application = get_object_or_404(
        LeaveApplication.objects.select_related(
            "applicant__user", "applicant__department", "leave_type", "academic_year"
        ),
        pk=pk,
    )
    # Anyone who may not see it gets the same 404 as for a missing record,
    # so the page never confirms that an application exists.
    if not can_view_application(request.user, application):
        raise Http404
    days = list(application.days.select_related("holiday").order_by("date"))
    counted = sum((d.fraction for d in days if d.is_counted), 0)
    lwp = sum((d.fraction for d in days if d.is_counted and d.is_lwp), 0)
    return render(request, "leaves/leave_detail.html", {
        "application": application,
        "days": days,
        "totals": {"counted": counted, "lwp": lwp, "paid": counted - lwp},
        "next_step": next_step_text(application.status),
        "is_owner": get_profile(request.user) == application.applicant,
    })
