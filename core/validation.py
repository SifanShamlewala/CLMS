"""Input parsing and validation shared by every service.

Services call these helpers and raise ``DomainError`` on bad input. Views catch
``DomainError`` and re-render the page with the messages. Nothing here trusts
the browser: HTML attributes are only a convenience.
"""

import datetime
from decimal import Decimal, InvalidOperation

class DomainError(Exception):
    """A business or validation failure that is safe to show to the user.

    ``errors`` maps a field name to a message. The key ``"form"`` is for
    messages that belong to the whole page rather than one field.
    """
    def __init__(self, errors):
        if isinstance(errors, str):
            errors = {"form": errors}
        self.errors = dict(errors)
        super().__init__(str(self.errors))

    @property
    def message(self):
        """All messages as one readable sentence, for flash messages."""
        return " ".join(self.errors.values())

def _label(field, label):
    return label or field.replace("_", " ").capitalize()

def validate(checks):
    """Run several parsers, collect every error, then raise once.

    ``checks`` maps a field name to a zero-argument callable that returns the
    cleaned value or raises ``DomainError``. Returns ``{field: cleaned_value}``.
    """
    cleaned, errors = {},{}
    for name, check in checks.items():
        try:
            cleaned[name] = check()
        except DomainError as exc:
            errors.update(exc.errors)
    if errors:
        raise DomainError(errors)
    return cleaned

def required_text(value, field, max_length=None, label=None):
    label = _label(field, label)
    value = (value or "").strip()
    if not value:
        raise DomainError({field: f"{label} is required."})
    if max_length and len(value) > max_length:
        raise DomainError({field: f"{label} must be at most {max_length} characters long."})
    return value

def optional_text(value, field, max_length=None, label=None):
    label = _label(field, label)
    value = (value or "").strip()
    if max_length and len(value) > max_length:
        raise DomainError({field: f"{label} must be at most {max_length} characters long."})
    return value

def parse_date(value, field, label=None):
    label = _label(field, label)
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        raise DomainError({field: f"{label} must be a valid date."}) from None

def parse_decimal(value, field, label=None, min_value=None, max_value=None):
    label = _label(field, label)
    try:
        number = Decimal((value or "").strip())
    except InvalidOperation:
        raise DomainError({field: f"{label} must be a number."}) from None
    if not number.is_finite():
        raise DomainError({field: f"{label} must be a number."})
    if min_value is not None and number < Decimal(str(min_value)):
        raise DomainError({field: f"{label} must be at least {min_value}."})
    if max_value is not None and number > Decimal(str(max_value)):
        raise DomainError({field: f"{label} must be at most {max_value}."})
    return number

def parse_choice(value, field, choices, label=None):
    label = _label(field, label)
    value = (value or "").strip()
    if value not in set(choices):
        raise DomainError({field: f"Choose a valid {label.lower()}."})
    return value

def ensure_unique(model, values, messages, exclude_pk=None):
    """Case-insensitive uniqueness check that reports every clash at once.

    ``values`` is ``{field: value}`` and ``messages`` is ``{field: message}``.
    The database constraint stays as the last line of defence; SQLite compares
    text case-sensitively, so this check is what stops "Dean" and "dean".
    """
    errors={}
    for field, value in values.items():
        clash=model.objects.filter(**{f"{field}__iexact": value})
        if exclude_pk is not None:
            clash=clash.exclude(pk=exclude_pk)
        if clash.exists():
            errors[field] = messages[field]
    if errors:
        raise DomainError(errors)

CONFLICT_MESSAGE = (
    "Another change was saved at the same time and conflicts with this one. "
    "Reload the page and try again."
)
