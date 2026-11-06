"""Business logic for leave types."""
import logging
import re

from django.db import IntegrityError, transaction

from core.permissions import require_administrator
from core.validation import (
    CONFLICT_MESSAGE, DomainError, ensure_unique, optional_text, required_text, validate,
)

from .models import LeaveType

logger = logging.getLogger("vsclms.setup")

DUPLICATE_MESSAGES = {
    "name": "A leave type with this name already exists.",
    "code": "A leave type with this code already exists.",
}
CODE_PATTERN = re.compile(r"[A-Z0-9_-]+")

def _code(value):
    code = required_text(value, "code", 20, "Code").upper()
    if not CODE_PATTERN.fullmatch(code):
        raise DomainError({"code": "Code may contain only letters, numbers, hyphens and underscores."})
    return code

def _clean(data):
    return validate({
        "name": lambda: required_text(data.get("name"), "name", 100, "Name"),
        "code": lambda: _code(data.get("code")),
        "description": lambda: optional_text(data.get("description"), "description", 500, "Description"),
    })

def create_leave_type(*, data, actor):
    require_administrator(actor)
    cleaned = _clean(data)
    try:
        with transaction.atomic():
            ensure_unique(LeaveType, {"name": cleaned["name"], "code": cleaned["code"]}, DUPLICATE_MESSAGES)
            leave_type = LeaveType.objects.create(**cleaned)
    except IntegrityError:   # a concurrent request won the race
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info("leave type created id=%s code=%s by=%s", leave_type.pk, leave_type.code, actor.pk)
    return leave_type

def update_leave_type(*, leave_type, data, actor):
    require_administrator(actor)
    cleaned = _clean(data)
    try:
        with transaction.atomic():
            ensure_unique(LeaveType, {"name": cleaned["name"], "code": cleaned["code"]}, DUPLICATE_MESSAGES, exclude_pk=leave_type.pk)
            for field, value in cleaned.items():
                setattr(leave_type, field, value)
            leave_type.save(update_fields=list(cleaned))
    except IntegrityError:
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info("leave type updated id=%s code=%s by=%s", leave_type.pk, leave_type.code, actor.pk)
    return leave_type

def set_leave_type_active(*, leave_type, active, actor):
    """Existing applications keep their leave type; inactive types are only
    hidden from new applications."""
    require_administrator(actor)
    leave_type.is_active = bool(active)
    leave_type.save(update_fields=["is_active"])
    logger.info("leave type active=%s id=%s by=%s", leave_type.is_active, leave_type.pk, actor.pk)
    return leave_type
