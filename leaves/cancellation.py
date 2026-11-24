"""Cancelling a leave before anyone has acted on it (spec 8.4, decision D13).

"No action taken" means the leave is still waiting for workload cover and every cover
request is still unanswered. Once a colleague has accepted, or the leave has moved to the head
or the Principal, the applicant can no longer cancel it. Cancelling never touches the balance,
because nothing has been deducted before approval.
"""
import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.models import StaffProfile
from core.permissions import get_profile, require_role
from core.validation import DomainError, optional_text

from .models import LeaveApplication, LeaveApprovalLog, LeaveDay, LeaveWorkload
from .workload import active_workloads, lock_leave

logger = logging.getLogger("vsclms.leave")

Status = LeaveApplication.Status
Role = StaffProfile.Role
MAX_REMARKS = 300


def no_action_taken(leave):
    """Decision D13: waiting for cover, and no colleague has answered any request."""
    if leave.is_deleted or leave.status != Status.WORKLOAD_PENDING:
        return False
    answered = LeaveWorkload.objects.filter(leave_day__leave=leave, is_deleted=False).exclude(
        status=LeaveWorkload.Status.PENDING
    )
    return not answered.exists()


def cancel_leave(*, application, remarks, actor):
    """Cancel the applicant's own leave. Returns the updated leave."""
    require_role(actor, Role.STAFF, Role.HOD, Role.PRINCIPAL)
    profile = get_profile(actor)
    with transaction.atomic():
        leave = lock_leave(application.pk)
        if leave.applicant_id != profile.pk:
            raise PermissionDenied  # only the applicant may cancel
        text = optional_text(remarks, "remarks", MAX_REMARKS, "Reason")
        if not no_action_taken(leave):
            raise DomainError({
                "form": (
                    "This leave can no longer be cancelled, because it has moved on or a colleague "
                    "has already answered a cover request."
                )
            })
        now = timezone.now()
        leave.status, leave.decided_at = Status.CANCELLED, now
        leave.save(update_fields=["status", "decided_at", "updated_at"])
        # The dates are free again; open cover requests are closed but kept as history.
        leave.days.update(status=LeaveDay.Status.CANCELLED, blocks_overlap=False)
        active_workloads().filter(leave_day__leave=leave).update(
            status=LeaveWorkload.Status.CANCELLED, decided_at=now
        )
        LeaveApprovalLog.objects.create(
            leave=leave, action_by=actor, action=LeaveApprovalLog.Action.LEAVE_CANCELLED, remarks=text
        )
    logger.info("audit action=LEAVE_CANCELLED id=%s by=%s", leave.pk, actor.pk)
    return leave
