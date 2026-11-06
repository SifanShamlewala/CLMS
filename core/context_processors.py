from .permissions import get_profile, is_administrator

def user_context(request):
    """Expose ``profile`` and ``is_administrator`` to every template."""
    return {
        "profile" : get_profile(request.user),
        "is_administrator" : is_administrator(request.user),
    }