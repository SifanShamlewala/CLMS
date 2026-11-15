"""Turn a value posted by a <select> into a database object, safely."""
from .validation import DomainError


def parse_object(queryset, value, field, label):
    """Return the object whose primary key is ``value``, searched in ``queryset``.

    ``queryset`` limits the allowed choices (for example only active
    departments), so a tampered form cannot pick something the screen never
    offered. Raises ``DomainError`` for blank, non-numeric or unknown values.
    """
    try:
        pk = int((value or "").strip())
    except (ValueError, AttributeError):
        raise DomainError({field: f"Choose a valid {label}."}) from None
    obj = queryset.filter(pk=pk).first()
    if obj is None:
        raise DomainError({field: f"Choose a valid {label}."})
    return obj
