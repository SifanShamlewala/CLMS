"""Server-side access control. Hiding a link is never security: every view
uses a decorator from here, and every state-changing service calls a
``require_*`` function again."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

def get_profile(user):
    """Return the user's StaffProfile, or None.

    Safe for anonymous users and for administrators who have no profile.
    """
    if not user.is_authenticated:
        return None
    return getattr(user, 'profile', None)

def is_administrator(user):
    """The authorized administrator is a Django superuser."""
    return bool(user.is_authenticated and user.is_active and user.is_superuser)

def require_administrator(user):
    if not is_administrator(user):
        raise PermissionDenied

def require_role(user, *roles):
    """Allow only an authenticated user with an active profile holding a role."""
    profile = get_profile(user)
    if (
        not user.is_authenticated
        or not user.is_active
        or profile is None
        or not profile.is_active
        or profile.role not in roles
    ):
        raise PermissionDenied

def administrator_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        require_administrator(request.user)
        return view(request, *args, **kwargs)

    return login_required(wrapper)

def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            require_role(request.user, *roles)
            return view(request, *args, **kwargs)

        return login_required(wrapper)

    return decorator