"""Business logic for leave types, academic years and holidays."""
import logging
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.lookup import parse_object
from core.permissions import require_administrator
from core.validation import (
    CONFLICT_MESSAGE, DomainError, ensure_unique, optional_text, parse_date,
    required_text, validate,
)

from .models import AcademicYear, Holiday, LeaveType

logger = logging.getLogger("vsclms.setup")


# ----------------------------------------------------------------- leave types
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
            ensure_unique(
                LeaveType, {"name": cleaned["name"], "code": cleaned["code"]}, DUPLICATE_MESSAGES
            )
            leave_type = LeaveType.objects.create(**cleaned)
    except IntegrityError:  # a concurrent request won the race
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info("leave type created id=%s code=%s by=%s", leave_type.pk, leave_type.code, actor.pk)
    return leave_type


def update_leave_type(*, leave_type, data, actor):
    require_administrator(actor)
    cleaned = _clean(data)
    try:
        with transaction.atomic():
            ensure_unique(
                LeaveType, {"name": cleaned["name"], "code": cleaned["code"]}, DUPLICATE_MESSAGES,
                exclude_pk=leave_type.pk,
            )
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


# --------------------------------------------------------------- academic years
DUPLICATE_YEAR = "An academic year with this name already exists."


def _fmt(day):
    return day.strftime("%d %b %Y")


def _clean_year(data):
    cleaned = validate({
        "name": lambda: required_text(data.get("name"), "name", 20, "Name"),
        "start_date": lambda: parse_date(data.get("start_date"), "start_date", "Start date"),
        "end_date": lambda: parse_date(data.get("end_date"), "end_date", "End date"),
    })
    if cleaned["start_date"] >= cleaned["end_date"]:
        raise DomainError({"end_date": "End date must be after the start date."})
    return cleaned


def _ensure_no_overlap(cleaned, exclude_pk=None):
    """Years must not share a day, otherwise one leave date could belong to two years."""
    clash = AcademicYear.objects.filter(
        start_date__lte=cleaned["end_date"], end_date__gte=cleaned["start_date"]
    )
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    other = clash.order_by("start_date").first()
    if other is not None:
        raise DomainError({
            "start_date": (
                f"These dates overlap {other.name} "
                f"({_fmt(other.start_date)} to {_fmt(other.end_date)})."
            )
        })


def create_academic_year(*, data, actor):
    """New years start inactive. Use ``activate_academic_year`` to switch."""
    require_administrator(actor)
    cleaned = _clean_year(data)
    try:
        with transaction.atomic():
            ensure_unique(AcademicYear, {"name": cleaned["name"]}, {"name": DUPLICATE_YEAR})
            _ensure_no_overlap(cleaned)
            year = AcademicYear.objects.create(is_active=False, **cleaned)
    except IntegrityError:
        raise DomainError({"name": DUPLICATE_YEAR}) from None
    logger.info("audit action=ACADEMIC_YEAR_CREATED id=%s name=%r by=%s", year.pk, year.name, actor.pk)
    return year


def update_academic_year(*, year, data, actor):
    require_administrator(actor)
    cleaned = _clean_year(data)
    try:
        with transaction.atomic():
            ensure_unique(
                AcademicYear, {"name": cleaned["name"]}, {"name": DUPLICATE_YEAR}, exclude_pk=year.pk
            )
            _ensure_no_overlap(cleaned, exclude_pk=year.pk)
            outside = (
                year.holidays.filter(is_deleted=False)
                .exclude(date__range=(cleaned["start_date"], cleaned["end_date"]))
                .count()
            )
            if outside:
                raise DomainError({
                    "form": (
                        f"{outside} holiday{'s' if outside != 1 else ''} in {year.name} "
                        "would fall outside these dates. Move or remove "
                        f"{'them' if outside != 1 else 'it'} first."
                    )
                })
            # Later phases also block date changes once leave applications exist.
            for field, value in cleaned.items():
                setattr(year, field, value)
            year.save(update_fields=list(cleaned))
    except IntegrityError:
        raise DomainError({"name": DUPLICATE_YEAR}) from None
    logger.info("audit action=ACADEMIC_YEAR_UPDATED id=%s by=%s", year.pk, actor.pk)
    return year


def activate_academic_year(*, year, actor):
    """Make ``year`` the one active year, switching off the previous one.

    Both writes happen in one transaction and in this order: the database
    allows only one active row, so the old year must be switched off first.
    """
    require_administrator(actor)
    try:
        with transaction.atomic():
            year = AcademicYear.objects.select_for_update().get(pk=year.pk)
            if year.is_active:
                return year
            AcademicYear.objects.filter(is_active=True).update(is_active=False)
            year.is_active = True
            year.save(update_fields=["is_active"])
    except IntegrityError:  # two administrators activated different years at once
        raise DomainError({"form": CONFLICT_MESSAGE}) from None
    logger.info("audit action=ACADEMIC_YEAR_ACTIVATED id=%s name=%r by=%s", year.pk, year.name, actor.pk)
    return year


# --------------------------------------------------------------------- holidays
def _duplicate_holiday_message(year):
    return f"A holiday on this date already exists in {year.name}."


def _clean_holiday(data, year):
    cleaned = validate({
        "name": lambda: required_text(data.get("name"), "name", 100, "Name"),
        "date": lambda: parse_date(data.get("date"), "date", "Date"),
    })
    if not (year.start_date <= cleaned["date"] <= year.end_date):
        raise DomainError({
            "date": (
                f"Date must fall inside {year.name} "
                f"({_fmt(year.start_date)} to {_fmt(year.end_date)})."
            )
        })
    return cleaned


def _ensure_holiday_free(year, day, exclude_pk=None):
    clash = Holiday.objects.filter(academic_year=year, date=day, is_deleted=False)
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        raise DomainError({"date": _duplicate_holiday_message(year)})


def create_holiday(*, data, actor):
    require_administrator(actor)
    year = parse_object(AcademicYear.objects.all(), data.get("academic_year"), "academic_year", "academic year")
    cleaned = _clean_holiday(data, year)
    try:
        with transaction.atomic():
            _ensure_holiday_free(year, cleaned["date"])
            holiday = Holiday.objects.create(academic_year=year, created_by=actor, **cleaned)
    except IntegrityError:
        raise DomainError({"date": _duplicate_holiday_message(year)}) from None
    logger.info(
        "audit action=HOLIDAY_ADDED id=%s date=%s year=%s by=%s",
        holiday.pk, holiday.date, year.pk, actor.pk,
    )
    # Phase 6 hook: pending applications are recalculated and approved ones
    # flagged (HOLIDAY_ADJUSTED) once leave days exist.
    return holiday


def update_holiday(*, holiday, data, actor):
    require_administrator(actor)
    year = holiday.academic_year
    cleaned = _clean_holiday(data, year)
    try:
        with transaction.atomic():
            _ensure_holiday_free(year, cleaned["date"], exclude_pk=holiday.pk)
            holiday.name = cleaned["name"]
            holiday.date = cleaned["date"]
            holiday.save(update_fields=["name", "date"])
    except IntegrityError:
        raise DomainError({"date": _duplicate_holiday_message(year)}) from None
    logger.info("audit action=HOLIDAY_UPDATED id=%s by=%s", holiday.pk, actor.pk)
    return holiday


def delete_holiday(*, holiday, actor):
    """Soft delete: the row stays for history and the date becomes free again."""
    require_administrator(actor)
    if holiday.is_deleted:
        return holiday
    holiday.is_deleted = True
    holiday.deleted_at = timezone.now()
    holiday.deleted_by = actor
    holiday.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])
    logger.info("audit action=HOLIDAY_DELETED id=%s by=%s", holiday.pk, actor.pk)
    return holiday
