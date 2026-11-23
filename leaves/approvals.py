"""Head-of-department and Principal decisions (spec 8.2) and the two review queues.

Rules (decisions D26 to D31):
* The head of the applicant's department decides at HOD_PENDING, never on their own leave.
  An HOD's own leave (route "Principal as HOD") is decided at that stage by the Principal.
* The Principal decides at PRINCIPAL_PENDING, never on their own leave.
* Approving at the HOD stage moves the leave to PRINCIPAL_PENDING. Rejecting at any stage
  needs remarks and rejects the leave for good; its dates are free again.
* Final approval, which deducts the balance, is Day 9. Until then the Principal can only reject,
  because approving without deducting would leave the books wrong.
* Every decision locks the leave row, so two clicks or two reviewers cannot both succeed, and is
  written to the append-only approval log with the reviewer's remarks.
"""
import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import StaffProfile
from core.permissions import get_profile, require_role
from core.validation import DomainError, optional_text, parse_choice, required_text, validate

from .day_engine import DayPlan, allocate_leave, summarize
from .models import LeaveApplication, LeaveApprovalLog, StaffLeaveBalance
from .workload import lock_leave, reject_leave

logger = logging.getLogger("vsclms.leave")

Status = LeaveApplication.Status
Role = StaffProfile.Role
Action = LeaveApprovalLog.Action

APPROVE, REJECT = "approve", "reject"
HOD_STAGE, PRINCIPAL_STAGE = "HOD", "PRINCIPAL"
MAX_REMARKS = 300
REVIEW_ROLES = (Role.HOD, Role.PRINCIPAL)

# Statuses a head of department can look at for the department.
HOD_STATUSES = (Status.HOD_PENDING, Status.PRINCIPAL_PENDING, Status.APPROVED, Status.REJECTED)
WAITING = "WAITING"  # the Principal's default filter: everything waiting for them


# ------------------------------------------------------------------ who may act
def may_act_at_hod_stage(profile, leave):
    """Identity check for the HOD stage (the status is checked separately)."""
    if profile is None or not profile.is_active or profile.pk == leave.applicant_id:
        return False
    if leave.applicant.role == Role.HOD:
        return profile.role == Role.PRINCIPAL  # route "Principal as HOD" (G2)
    return profile.role == Role.HOD and profile.department_id == leave.applicant.department_id


def may_act_at_principal_stage(profile, leave):
    return (
        profile is not None and profile.is_active
        and profile.role == Role.PRINCIPAL and profile.pk != leave.applicant_id
    )


def stage_for(profile, leave):
    """The decision this person can take on this leave right now: HOD_STAGE, PRINCIPAL_STAGE or None."""
    if leave.is_deleted:
        return None
    if leave.status == Status.HOD_PENDING and may_act_at_hod_stage(profile, leave):
        return HOD_STAGE
    if leave.status == Status.PRINCIPAL_PENDING and may_act_at_principal_stage(profile, leave):
        return PRINCIPAL_STAGE
    return None


# --------------------------------------------------------------------- deciding
def decide_leave(*, application, decision, remarks, actor):
    """Approve or reject at whichever stage the leave is in. Returns the updated leave."""
    require_role(actor, *REVIEW_ROLES)
    profile = get_profile(actor)
    with transaction.atomic():
        leave = lock_leave(application.pk)
        stage = _stage_of(leave)
        allowed = may_act_at_hod_stage if stage == HOD_STAGE else may_act_at_principal_stage
        if not allowed(profile, leave):
            raise PermissionDenied  # not your department, not your stage, or your own leave
        cleaned = validate({
            "decision": lambda: parse_choice(decision, "decision", (APPROVE, REJECT), "answer"),
        })
        if cleaned["decision"] == APPROVE:
            _approve(leave, stage, remarks, actor)
        else:
            _reject(leave, stage, remarks, actor)
    return leave


def _stage_of(leave):
    if leave.is_deleted or leave.status not in (Status.HOD_PENDING, Status.PRINCIPAL_PENDING):
        raise DomainError({"form": "This application is no longer waiting for a decision."})
    return HOD_STAGE if leave.status == Status.HOD_PENDING else PRINCIPAL_STAGE


def _approve(leave, stage, remarks, actor):
    text = optional_text(remarks, "remarks", MAX_REMARKS, "Remarks")
    if stage == PRINCIPAL_STAGE:
        # Decision D29: no approval without the balance deduction, which is built on Day 9.
        raise DomainError({"form": "Final approval is not available yet. You can reject, or wait for the next release."})
    leave.status = Status.PRINCIPAL_PENDING
    leave.save(update_fields=["status", "updated_at"])
    _log(leave, Action.HOD_APPROVED, text, actor)


def _reject(leave, stage, remarks, actor):
    text = required_text(remarks, "remarks", MAX_REMARKS, "Remarks")
    reject_leave(leave, timezone.now())
    _log(leave, Action.HOD_REJECTED if stage == HOD_STAGE else Action.PRINCIPAL_REJECTED, text, actor)
    logger.info("audit action=LEAVE_REJECTED id=%s reason=%s_rejected", leave.pk, stage.lower())


def _log(leave, action, remarks, actor):
    LeaveApprovalLog.objects.create(leave=leave, action_by=actor, action=action, remarks=remarks)
    logger.info("audit action=%s id=%s by=%s", action, leave.pk, actor.pk)


# ----------------------------------------------------------------------- queues
def _base():
    return LeaveApplication.objects.filter(is_deleted=False).select_related(
        "applicant__user", "applicant__department", "leave_type"
    )


def hod_queue(profile, status):
    """Leaves of the head's department. ``status`` is one of HOD_STATUSES, or anything else for all four."""
    rows = (
        _base()
        .filter(applicant__department=profile.department, status__in=HOD_STATUSES)
        .exclude(applicant=profile)
        .exclude(applicant__role=Role.PRINCIPAL)
    )
    if status in HOD_STATUSES:
        rows = rows.filter(status=status)
    return rows.order_by("submitted_at", "id")


def principal_queue(profile, status):
    """Everything the Principal must decide (WAITING), or a status, or all."""
    rows = _base().exclude(applicant=profile)
    if status == WAITING:
        rows = rows.filter(
            Q(status=Status.PRINCIPAL_PENDING) | Q(status=Status.HOD_PENDING, applicant__role=Role.HOD)
        )
    elif status in Status.values:
        rows = rows.filter(status=status)
    return rows.order_by("submitted_at", "id")


# --------------------------------------------------------------------- preview
def final_effect(leave):
    """What approving now would do to the applicant's balance (an estimate; Day 9 recomputes under a lock).

    Counted days are allocated one by one, in date order, against what the applicant has left today.
    A day is paid only if its whole fraction is covered, otherwise it is leave without pay.
    """
    balance = StaffLeaveBalance.objects.filter(
        staff=leave.applicant, leave_type=leave.leave_type, academic_year=leave.academic_year
    ).first()
    available = balance.remaining if balance else Decimal("0")  # decision G6: no row means zero
    plans = [
        DayPlan(
            date=d.date, day_part=d.day_part, fraction=d.fraction, is_sunday=d.is_sunday,
            is_holiday=d.is_holiday, is_working_day=d.is_working_day, is_counted=d.is_counted,
        )
        for d in leave.days.all()
    ]
    allocate_leave(plans, available)
    summary = summarize(plans)
    return {
        "available": available,
        "has_balance": balance is not None,
        "counted": summary["counted"],
        "paid": summary["paid"],
        "lwp": summary["lwp"],
        "after": available - summary["paid"],
        # The balance moved since the applicant applied (another leave was approved meanwhile).
        "differs": summary["paid"] != leave.total_days_paid or summary["lwp"] != leave.total_days_lwp,
    }
