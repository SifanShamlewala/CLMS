"""Workload cover: assigning colleagues, withdrawing, accepting and rejecting (spec 8.1 steps 3-4).

Rules (decisions D11, D13, D19):
* The applicant assigns colleagues per date, only while the leave is WORKLOAD_PENDING.
* Nobody can be assigned to their own work, twice on one date, while inactive, or on a
  date they are themselves on leave.
* Every date that needs cover needs at least one active assignment, and every active
  assignment must be ACCEPTED. When that is true the leave moves on to the review stage.
* Any rejection rejects the whole leave; the other open requests are cancelled, kept as history.
* Every change locks the leave row first, so an accept, a withdrawal and a new assignment
  for the same leave happen one at a time.
"""
import logging
from collections import defaultdict

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from academics.models import PolicyConfig
from accounts.models import StaffProfile
from core.lookup import parse_object
from core.permissions import get_profile, require_role
from core.validation import (
    CONFLICT_MESSAGE, DomainError, optional_text, parse_choice, parse_date, required_text, validate,
)

from .models import LeaveApplication, LeaveDay, LeaveWorkload
from .workflow import stage_after_workload

logger = logging.getLogger("vsclms.leave")

Status = LeaveApplication.Status
WStatus = LeaveWorkload.Status
ACTIVE = (WStatus.PENDING, WStatus.ACCEPTED)
ANY_ROLE = (StaffProfile.Role.STAFF, StaffProfile.Role.HOD, StaffProfile.Role.PRINCIPAL)
MAX_REMARKS = 300
ACCEPT, REJECT = "accept", "reject"
MAX_DATES_SHOWN = 3


def _fmt(day):
    return day.strftime("%a %d %b")


def _dates_text(days):
    days = sorted(days)
    shown = ", ".join(_fmt(d) for d in days[:MAX_DATES_SHOWN])
    hidden = len(days) - MAX_DATES_SHOWN
    return shown + (f" and {hidden} more" if hidden > 0 else "")


def person_name(profile):
    return profile.user.get_full_name() or profile.user.get_username()


def _getlist(data, key):
    """Read several values for one key from request.POST or from a plain dict."""
    if hasattr(data, "getlist"):
        return data.getlist(key)
    value = data.get(key) or []
    return [value] if isinstance(value, str) else list(value)


def active_workloads():
    return LeaveWorkload.objects.filter(is_deleted=False, status__in=ACTIVE)


def assignee_clash_dates(profile, dates):
    """Dates among ``dates`` on which ``profile`` is on a live leave of their own."""
    return set(
        LeaveDay.objects.filter(staff=profile, blocks_overlap=True, date__in=list(dates))
        .values_list("date", flat=True)
    )


def cover_clash_dates(profile, start, end):
    """Dates in a range on which ``profile`` has agreed, or been asked, to cover a colleague."""
    return set(
        active_workloads().filter(assigned_staff=profile, date__range=(start, end))
        .values_list("date", flat=True)
    )


def eligible_assignees(applicant):
    """Active colleagues who may be offered the work (everyone active except the applicant)."""
    return (
        StaffProfile.objects.filter(is_active=True, user__is_active=True).exclude(pk=applicant.pk)
        .select_related("user", "department")
        .order_by("user__first_name", "user__last_name", "employee_id")
    )


def workload_complete(leave):
    """Decision D11: every date that needs cover has an active assignment, all accepted."""
    required = list(
        leave.days.filter(workload_required=True, is_counted=True).values_list("pk", flat=True)
    )
    if not required:
        return False  # nothing to prove cover for; never advance on no evidence
    by_day = defaultdict(list)
    rows = active_workloads().filter(leave_day_id__in=required).values_list("leave_day_id", "status")
    for day_id, status in rows:
        by_day[day_id].append(status)
    return all(by_day[day] and all(s == WStatus.ACCEPTED for s in by_day[day]) for day in required)


def cover_summary(leave):
    """(dates needing cover, dates fully covered and accepted) for the detail page."""
    required = list(
        leave.days.filter(workload_required=True, is_counted=True).values_list("pk", flat=True)
    )
    by_day = defaultdict(list)
    rows = active_workloads().filter(leave_day_id__in=required).values_list("leave_day_id", "status")
    for day_id, status in rows:
        by_day[day_id].append(status)
    covered = sum(1 for d in required if by_day[d] and all(s == WStatus.ACCEPTED for s in by_day[d]))
    return len(required), covered


def _lock_leave(leave_id):
    return LeaveApplication.objects.select_for_update().select_related("applicant__user").get(pk=leave_id)


def _advance_if_complete(leave):
    """Move the leave on to its review stage when the completion rule is met."""
    if leave.status != Status.WORKLOAD_PENDING or not workload_complete(leave):
        return False
    leave.status = stage_after_workload(leave.applicant.role, PolicyConfig.get())
    leave.save(update_fields=["status", "updated_at"])
    logger.info("audit action=LEAVE_UPDATED id=%s detail=workload_complete status=%s", leave.pk, leave.status)
    return True


def _check_open(leave):
    if leave.is_deleted:
        raise DomainError({"form": "This application is no longer available."})
    if leave.status != Status.WORKLOAD_PENDING or not leave.requires_workload:
        raise DomainError({"form": "This leave is no longer waiting for workload cover."})


# ------------------------------------------------------------------- assigning
def assign_workload(*, application, data, actor):
    """Ask one colleague to cover one or more dates. All dates are saved, or none."""
    require_role(actor, *ANY_ROLE)
    profile = get_profile(actor)
    try:
        with transaction.atomic():
            leave = _lock_leave(application.pk)
            if leave.applicant_id != profile.pk:
                raise PermissionDenied  # only the applicant arranges cover
            _check_open(leave)
            required = {
                d.date: d for d in leave.days.filter(workload_required=True, is_counted=True)
            }
            cleaned = validate({
                "assignee": lambda: parse_object(
                    StaffProfile.objects.filter(is_active=True, user__is_active=True).select_related("user"),
                    data.get("assignee"), "assignee", "colleague",
                ),
                "dates": lambda: _parse_dates(_getlist(data, "dates"), required),
            })
            assignee, dates = cleaned["assignee"], cleaned["dates"]
            _check_assignment(leave, assignee, dates, required)
            LeaveWorkload.objects.bulk_create([
                LeaveWorkload(
                    leave_day=required[d], assigned_staff=assignee, date=d,
                    status=WStatus.PENDING, assigned_by=actor,
                )
                for d in dates
            ])
    except IntegrityError:  # a duplicate slipped past the check at the same moment
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info(
        "audit action=WORKLOAD_ASSIGNED leave=%s assignee=%s dates=%s by=%s",
        leave.pk, assignee.pk, ",".join(d.isoformat() for d in dates), actor.pk,
    )
    return leave


def _parse_dates(values, required):
    if not [v for v in values if (v or "").strip()]:
        raise DomainError({"dates": "Choose at least one date for this colleague to cover."})
    dates = []
    for value in values:
        day = parse_date(value, "dates", "Date")
        if day not in required:
            raise DomainError({"dates": "Choose only dates from the list of days that need cover."})
        if day not in dates:
            dates.append(day)
    return sorted(dates)


def _check_assignment(leave, assignee, dates, required):
    if assignee.pk == leave.applicant_id:
        raise DomainError({"assignee": "You can't assign your own work to yourself."})
    duplicates = set(
        active_workloads().filter(assigned_staff=assignee, leave_day__in=[required[d] for d in dates])
        .values_list("date", flat=True)
    )
    if duplicates:
        raise DomainError({
            "assignee": f"{person_name(assignee)} is already covering {_dates_text(duplicates)} for this leave."
        })
    clashes = assignee_clash_dates(assignee, dates)
    if clashes:
        raise DomainError({
            "assignee": f"{person_name(assignee)} is on leave on {_dates_text(clashes)} and can't cover then."
        })


# ------------------------------------------------------------------ withdrawing
def remove_workload(*, workload, actor):
    """Withdraw a request nobody has answered yet (kept as history, not erased)."""
    require_role(actor, *ANY_ROLE)
    profile = get_profile(actor)
    with transaction.atomic():
        leave_id = LeaveWorkload.objects.select_related("leave_day").get(pk=workload.pk).leave_day.leave_id
        leave = _lock_leave(leave_id)
        row = LeaveWorkload.objects.select_for_update().get(pk=workload.pk)
        if leave.applicant_id != profile.pk:
            raise PermissionDenied
        _check_open(leave)
        if row.is_deleted or row.status != WStatus.PENDING:
            raise DomainError({"form": "Only a request that hasn't been answered yet can be withdrawn."})
        row.is_deleted = True
        row.deleted_at = timezone.now()
        row.save(update_fields=["is_deleted", "deleted_at"])
        # Taking away the one unanswered request can finish the stage.
        _advance_if_complete(leave)
    logger.info("audit action=LEAVE_UPDATED id=%s detail=workload_removed workload=%s by=%s", leave.pk, row.pk, actor.pk)
    return leave


# --------------------------------------------------------------------- deciding
def decide_workload(*, workload, decision, remarks, actor):
    """The assigned colleague accepts or rejects. Returns the (updated) leave."""
    require_role(actor, *ANY_ROLE)
    profile = get_profile(actor)
    with transaction.atomic():
        leave_id = LeaveWorkload.objects.select_related("leave_day").get(pk=workload.pk).leave_day.leave_id
        leave = _lock_leave(leave_id)
        row = LeaveWorkload.objects.select_for_update().get(pk=workload.pk)
        if row.assigned_staff_id != profile.pk:
            raise PermissionDenied  # only the person asked can answer
        cleaned = validate({
            "decision": lambda: parse_choice(decision, "decision", (ACCEPT, REJECT), "answer"),
        })
        if row.is_deleted or row.status != WStatus.PENDING:
            raise DomainError({"form": "This request was already answered or withdrawn."})
        if leave.is_deleted or leave.status != Status.WORKLOAD_PENDING:
            raise DomainError({"form": "This leave is no longer waiting for workload cover."})
        now = timezone.now()
        if cleaned["decision"] == ACCEPT:
            _accept(row, leave, remarks, profile, now)
        else:
            _reject(row, leave, remarks, now)
    return leave


def _accept(row, leave, remarks, profile, now):
    text = optional_text(remarks, "remarks", MAX_REMARKS, "Remarks")
    if row.date in assignee_clash_dates(profile, [row.date]):
        raise DomainError({
            "form": f"You are on leave on {_fmt(row.date)}, so you can't accept. Reject it and say why."
        })
    row.status, row.remarks, row.decided_at = WStatus.ACCEPTED, text, now
    row.save(update_fields=["status", "remarks", "decided_at"])
    logger.info("audit action=WORKLOAD_ACCEPTED leave=%s workload=%s by=%s", leave.pk, row.pk, profile.user_id)
    _advance_if_complete(leave)


def _reject(row, leave, remarks, now):
    text = required_text(remarks, "remarks", MAX_REMARKS, "Remarks")
    row.status, row.remarks, row.decided_at = WStatus.REJECTED, text, now
    row.save(update_fields=["status", "remarks", "decided_at"])
    # Decision D11: any rejection rejects the leave. Its dates are free again, and the
    # other open requests are closed but kept.
    leave.status, leave.decided_at = Status.REJECTED, now
    leave.save(update_fields=["status", "decided_at", "updated_at"])
    leave.days.update(status=LeaveDay.Status.REJECTED, blocks_overlap=False)
    active_workloads().filter(leave_day__leave=leave).exclude(pk=row.pk).update(
        status=WStatus.CANCELLED, decided_at=now
    )
    logger.info("audit action=WORKLOAD_REJECTED leave=%s workload=%s by=%s", leave.pk, row.pk, row.assigned_staff.user_id)
    logger.info("audit action=LEAVE_REJECTED id=%s reason=workload_rejected", leave.pk)
