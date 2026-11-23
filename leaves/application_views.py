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
from . import approvals, workload
from .access import can_see_balance_details, can_view_application
from .applications import APPLICANT_ROLES, days_text
from .models import LeaveApplication, LeaveDay, LeaveWorkload, StaffLeaveBalance
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


def _detail_context(request, application, errors=None, values=None):
    days = list(application.days.select_related("holiday").order_by("date"))
    counted = sum((d.fraction for d in days if d.is_counted), 0)
    lwp = sum((d.fraction for d in days if d.is_counted and d.is_lwp), 0)
    profile = get_profile(request.user)
    is_owner = profile is not None and profile == application.applicant
    sees_balance = can_see_balance_details(request.user, application)
    cover = None
    if application.requires_workload:
        cover = _cover_context(application, days, is_owner, values or {})
    stage = approvals.stage_for(profile, application) if profile is not None else None
    return {
        "application": application,
        "days": days,
        # The same people who may see the balance may read the reviewers' remarks.
        "sees_review": sees_balance,
        "approval_logs": list(application.approval_logs.select_related("action_by")) if sees_balance else [],
        "decision_stage": stage,
        "can_approve_now": stage == approvals.HOD_STAGE,
        "effect": approvals.final_effect(application) if stage == approvals.PRINCIPAL_STAGE else None,
        "totals": {"counted": counted, "lwp": lwp, "paid": counted - lwp},
        "next_step": next_step_text(application.status),
        "is_owner": is_owner,
        "sees_balance": sees_balance,
        "cover": cover,
        "errors": errors or {},
        "values": values or {},
    }


def _cover_context(application, days, is_owner, values):
    """Dates that need cover, each with its assignments, plus the assign form's choices."""
    rows = (
        LeaveWorkload.objects.filter(leave_day__leave=application, is_deleted=False)
        .select_related("assigned_staff__user", "assigned_staff__department")
        .order_by("date", "id")
    )
    by_day = {}
    for row in rows:
        by_day.setdefault(row.leave_day_id, []).append(row)
    needed = [d for d in days if d.workload_required and d.is_counted]
    required_count, covered_count = workload.cover_summary(application)
    can_arrange = is_owner and application.status == LeaveApplication.Status.WORKLOAD_PENDING
    checked = set(values.getlist("dates")) if hasattr(values, "getlist") else set()
    return {
        "dates": [
            {"day": d, "assignments": by_day.get(d.pk, []), "checked": d.date.isoformat() in checked}
            for d in needed
        ],
        "required": required_count,
        "covered": covered_count,
        "can_arrange": can_arrange,
        "assignee_options": [
            (p.pk, f"{workload.person_name(p)} ({p.department.name})")
            for p in workload.eligible_assignees(application.applicant)
        ] if can_arrange else [],
    }


@login_required
@require_http_methods(["GET"])
def leave_detail(request, pk):
    application = _visible_application(request, pk)
    return render(request, "leaves/leave_detail.html", _detail_context(request, application))


def _visible_application(request, pk):
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
    return application


@login_required
@require_http_methods(["POST"])
def workload_assign(request, pk):
    application = _visible_application(request, pk)
    try:
        workload.assign_workload(application=application, data=request.POST, actor=request.user)
    except DomainError as exc:
        context = _detail_context(request, application, errors=exc.errors, values=request.POST)
        return render(request, "leaves/leave_detail.html", context)
    messages.success(request, "Cover requested. The colleague can now accept or reject it from their inbox.")
    return redirect("leave_detail", pk=application.pk)


@login_required
@require_http_methods(["POST"])
def workload_remove(request, pk):
    row = get_object_or_404(
        LeaveWorkload.objects.select_related("leave_day__leave"), pk=pk, is_deleted=False
    )
    application = _visible_application(request, row.leave_day.leave_id)
    try:
        leave = workload.remove_workload(workload=row, actor=request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
        return redirect("leave_detail", pk=application.pk)
    messages.success(request, "Request withdrawn." + (
        f" {next_step_text(leave.status)}" if leave.status != LeaveApplication.Status.WORKLOAD_PENDING else ""
    ))
    return redirect("leave_detail", pk=application.pk)
