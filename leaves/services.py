"""Business logic for leave balances. Leave applications arrive on Day 6."""
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction

from academics.models import AcademicYear, LeaveType, PolicyConfig
from accounts.models import StaffProfile
from core.lookup import parse_object
from core.permissions import require_administrator
from core.validation import (
    CONFLICT_MESSAGE, DomainError, optional_text, parse_decimal, required_text, validate,
)

from .models import StaffLeaveBalance

logger = logging.getLogger("vsclms.setup")

MAX_TOTAL = 366
STEP = Decimal("0.5")


def _name(profile):
    return profile.user.get_full_name() or profile.user.get_username()


def _total(value):
    """Days come in whole or half steps, from 0 to 366."""
    total = parse_decimal(value, "total", "Total days", min_value=0, max_value=MAX_TOTAL)
    if total % STEP != 0:
        raise DomainError({"total": "Total days must be in steps of 0.5, for example 7 or 7.5."})
    return total.quantize(Decimal("0.1"))


def create_balance(*, data, actor):
    """Give one person a yearly total for one leave type. ``used`` always starts at 0."""
    require_administrator(actor)
    cleaned = validate({
        "staff": lambda: parse_object(
            StaffProfile.objects.select_related("user").filter(is_active=True),
            data.get("staff"), "staff", "staff member",
        ),
        "leave_type": lambda: parse_object(
            LeaveType.objects.filter(is_active=True), data.get("leave_type"), "leave_type", "leave type"
        ),
        "academic_year": lambda: parse_object(
            AcademicYear.objects.all(), data.get("academic_year"), "academic_year", "academic year"
        ),
        "total": lambda: _total(data.get("total")),
    })
    staff, leave_type, year = cleaned["staff"], cleaned["leave_type"], cleaned["academic_year"]
    try:
        with transaction.atomic():
            if StaffLeaveBalance.objects.filter(staff=staff, leave_type=leave_type, academic_year=year).exists():
                raise DomainError({
                    "form": (
                        f"{_name(staff)} already has a {leave_type.code} balance for {year.name}. "
                        "Edit that balance instead."
                    )
                })
            balance = StaffLeaveBalance.objects.create(
                staff=staff, leave_type=leave_type, academic_year=year, total=cleaned["total"]
            )
    except IntegrityError:  # a concurrent request created the same balance first
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info(
        "audit action=BALANCE_CREATED id=%s staff=%s type=%s year=%s total=%s by=%s",
        balance.pk, staff.pk, leave_type.code, year.pk, balance.total, actor.pk,
    )
    return balance


def update_balance(*, balance, data, actor):
    """Change an existing total. Needs a remark, and can't go below what is already used.

    Setup never touches ``used``: only final approval and reversal change it.
    """
    require_administrator(actor)
    if not PolicyConfig.get().admin_adjust_enabled:
        raise DomainError({"form": "Balance adjustments are switched off in the policy settings."})
    cleaned = validate({
        "total": lambda: _total(data.get("total")),
        "remarks": lambda: required_text(data.get("remarks"), "remarks", 300, "Remarks"),
    })
    with transaction.atomic():
        locked = StaffLeaveBalance.objects.select_for_update().get(pk=balance.pk)
        if cleaned["total"] < locked.used:
            raise DomainError({"total": f"Total can't be below the {locked.used} days already used."})
        if cleaned["total"] == locked.total:
            raise DomainError({"total": "That is already the current total."})
        before = locked.total
        locked.total = cleaned["total"]
        locked.save(update_fields=["total", "updated_at"])
    logger.info(
        "audit action=BALANCE_ADJUSTED id=%s before=%s after=%s remarks=%r by=%s",
        locked.pk, before, locked.total, cleaned["remarks"], actor.pk,
    )
    return locked
