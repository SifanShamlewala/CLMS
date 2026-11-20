"""Where an application starts, and what it is waiting for (spec section 8.2).

Later days add the transitions themselves. This module only answers two
questions that Day 6 needs: which status does a new application start in, and
what plain-language "next step" text belongs to a status.
"""
from academics.models import PolicyConfig
from accounts.models import StaffProfile

from .models import LeaveApplication

Status = LeaveApplication.Status
Role = StaffProfile.Role

NEXT_STEP = {
    Status.WORKLOAD_PENDING: "Waiting for workload cover to be arranged and accepted.",
    Status.HOD_PENDING: "Waiting for review by the head of department.",
    Status.PRINCIPAL_PENDING: "Waiting for the Principal's decision.",
    Status.APPROVED: "Approved.",
    Status.REJECTED: "Rejected.",
    Status.CANCELLED: "Cancelled.",
}


def stage_after_workload(role, policy):
    """The review stage that follows the workload stage (or replaces it)."""
    if role == Role.PRINCIPAL:
        return Status.PRINCIPAL_PENDING
    if role == Role.HOD:
        # Decision G2: a head's own leave skips the HOD stage, or the Principal acts as HOD.
        if policy.hod_own_leave_route == PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD:
            return Status.HOD_PENDING
        return Status.PRINCIPAL_PENDING
    return Status.HOD_PENDING


def initial_status(role, requires_workload, policy):
    """The status a brand-new application starts in."""
    if role == Role.PRINCIPAL:
        # Decision D12: the Principal's own leave skips workload and HOD. The automatic
        # approval itself is added with the approval service on Day 9.
        return Status.PRINCIPAL_PENDING
    if requires_workload:
        return Status.WORKLOAD_PENDING
    return stage_after_workload(role, policy)


def next_step_text(status):
    return NEXT_STEP.get(status, "")
