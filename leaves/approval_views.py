from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import StaffProfile
from core.permissions import role_required
from core.validation import DomainError

from . import approvals
from .access import can_view_application
from .models import LeaveApplication

Role = StaffProfile.Role
Status = LeaveApplication.Status
PAGE_SIZE = 20

HOD_FILTERS = [(s, Status(s).label) for s in approvals.HOD_STATUSES]
PRINCIPAL_FILTERS = [
    (approvals.WAITING, "Waiting for me"),
    (Status.APPROVED, "Approved"),
    (Status.REJECTED, "Rejected"),
]


def _paginate(request, rows):
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    base = request.GET.copy()
    base.pop("page", None)
    return page_obj, base.urlencode()


@role_required(Role.HOD)
@require_http_methods(["GET"])
def hod_queue(request):
    profile = request.user.profile
    # Nothing chosen means "waiting for me"; an empty choice means every status.
    status = request.GET.get("status", Status.HOD_PENDING).strip()
    page_obj, qs_base = _paginate(request, approvals.hod_queue(profile, status))
    for row in page_obj:
        row.stage = approvals.stage_for(profile, row)
    waiting = approvals.hod_queue(profile, Status.HOD_PENDING).count()
    return render(request, "leaves/hod_queue.html", {
        "page_obj": page_obj, "qs_base": qs_base, "waiting": waiting,
        "filters": {"status": status}, "status_options": HOD_FILTERS,
    })


@role_required(Role.PRINCIPAL)
@require_http_methods(["GET"])
def principal_queue(request):
    profile = request.user.profile
    status = request.GET.get("status", approvals.WAITING).strip()
    page_obj, qs_base = _paginate(request, approvals.principal_queue(profile, status))
    for row in page_obj:
        row.stage = approvals.stage_for(profile, row)
        row.effect = approvals.final_effect(row) if row.stage == approvals.PRINCIPAL_STAGE else None
    waiting = approvals.principal_queue(profile, approvals.WAITING).count()
    return render(request, "leaves/principal_queue.html", {
        "page_obj": page_obj, "qs_base": qs_base, "waiting": waiting,
        "filters": {"status": status}, "status_options": PRINCIPAL_FILTERS,
    })


@role_required(Role.HOD, Role.PRINCIPAL)
@require_http_methods(["POST"])
def leave_decide(request, pk):
    application = get_object_or_404(
        LeaveApplication.objects.select_related("applicant__user", "applicant__department"), pk=pk
    )
    # Someone who may not see the application gets the same 404 as for a missing one.
    if not can_view_application(request.user, application):
        raise Http404
    back = _back_url(request, application)
    try:
        leave = approvals.decide_leave(
            application=application, decision=request.POST.get("decision"),
            remarks=request.POST.get("remarks"), actor=request.user,
        )
    except DomainError as exc:
        messages.error(request, exc.message)
        return redirect(*back)
    if leave.status == Status.REJECTED:
        messages.success(request, f"Leave #{leave.pk} rejected. The applicant can read your remarks.")
    else:
        messages.success(request, f"Leave #{leave.pk} approved by you. It now waits for the Principal.")
    return redirect(*back)


def _back_url(request, application):
    # Only two known places; never an address taken from the browser.
    if request.POST.get("from") == "detail":
        return "leave_detail", application.pk
    return ("hod_queue" if request.user.profile.role == Role.HOD else "principal_queue",)
