from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.permissions import role_required
from core.validation import DomainError

from . import workload
from .models import LeaveApplication, LeaveWorkload
from .workflow import next_step_text

COVER = role_required(*workload.ANY_ROLE)
PAGE_SIZE = 20
STATUS_OPTIONS = [(value, label) for value, label in LeaveWorkload.Status.choices if value != "TRANSFERRED"]


@COVER
@require_http_methods(["GET"])
def workload_inbox(request):
    profile = request.user.profile
    # No filter chosen at all means "what needs my answer"; an empty choice means everything.
    status = request.GET.get("status", LeaveWorkload.Status.PENDING).strip()
    rows = (
        LeaveWorkload.objects.filter(assigned_staff=profile, is_deleted=False)
        .select_related("leave_day__leave__applicant__user", "leave_day__leave__applicant__department",
                        "leave_day__leave__leave_type")
        .order_by("date", "id")
    )
    if status in LeaveWorkload.Status.values:
        rows = rows.filter(status=status)
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    for row in page_obj:
        row.leave = row.leave_day.leave
        row.can_answer = (
            row.status == LeaveWorkload.Status.PENDING
            and row.leave.status == LeaveApplication.Status.WORKLOAD_PENDING
            and not row.leave.is_deleted
        )
    base = request.GET.copy()
    base.pop("page", None)
    waiting = LeaveWorkload.objects.filter(
        assigned_staff=profile, is_deleted=False, status=LeaveWorkload.Status.PENDING
    ).count()
    return render(request, "leaves/workload_inbox.html", {
        "page_obj": page_obj,
        "qs_base": base.urlencode(),
        "filters": {"status": status},
        "status_options": STATUS_OPTIONS,
        "waiting": waiting,
    })


@COVER
@require_http_methods(["POST"])
def workload_decide(request, pk):
    # Someone else's request looks exactly like a missing one.
    row = get_object_or_404(
        LeaveWorkload, pk=pk, assigned_staff=request.user.profile, is_deleted=False
    )
    try:
        leave = workload.decide_workload(
            workload=row, decision=request.POST.get("decision"),
            remarks=request.POST.get("remarks"), actor=request.user,
        )
    except DomainError as exc:
        messages.error(request, exc.message)
        return redirect("workload_inbox")
    if leave.status == LeaveApplication.Status.REJECTED:
        messages.success(request, f"You rejected the request. Leave #{leave.pk} is rejected as a result.")
    elif leave.status != LeaveApplication.Status.WORKLOAD_PENDING:
        messages.success(
            request, f"Accepted. All cover for leave #{leave.pk} is now arranged. {next_step_text(leave.status)}"
        )
    else:
        messages.success(request, "Accepted. Thank you.")
    return redirect("workload_inbox")
