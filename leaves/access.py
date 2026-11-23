"""Who may look at a leave application. Hiding a link is never security, so every
page that shows an application asks these functions first."""
from accounts.models import StaffProfile
from core.permissions import get_profile, is_administrator

from .models import LeaveWorkload

Role = StaffProfile.Role


def is_cover_assignee(user, application):
    """True for a colleague who has been asked to cover a date of this leave (any answer)."""
    profile = get_profile(user)
    if profile is None or not profile.is_active:
        return False
    return LeaveWorkload.objects.filter(
        assigned_staff=profile, leave_day__leave=application, is_deleted=False
    ).exists()


def can_see_balance_details(user, application):
    """Applicant, the head of the applicant's department, the Principal, or the administrator.

    These may see the leave balance (days remaining per leave type) and the paid /
    leave-without-pay split of the days. A colleague who is only covering a date may not.
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


def can_view_application(user, application):
    """The people in ``can_see_balance_details`` plus colleagues asked to cover a date.

    Soft-deleted applications are never visible.
    """
    if application.is_deleted:
        return False
    return can_see_balance_details(user, application) or is_cover_assignee(user, application)
