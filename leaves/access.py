"""Who may look at a leave application. Hiding a link is never security, so every
page that shows an application asks this function first."""
from accounts.models import StaffProfile
from core.permissions import get_profile, is_administrator

Role = StaffProfile.Role


def can_view_application(user, application):
    """Applicant, the head of the applicant's department, the Principal, or the administrator.

    Soft-deleted applications are never visible. Workload assignees get access on Day 7.
    """
    if application.is_deleted:
        return False
    if is_administrator(user):
        return True
    profile = get_profile(user)
    if profile is None or not profile.is_active:
        return False
    if application.applicant_id == profile.pk:
        return True
    if profile.role == Role.PRINCIPAL:
        return True
    if profile.role == Role.HOD:
        return application.applicant.department_id == profile.department_id
    return False
