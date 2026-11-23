"""Applying for leave: the day-by-day preview and the submission (spec section 8.1).

``build_preview`` reads and calculates but never writes. ``submit_application``
runs the same preparation again inside a transaction, so what is saved never
depends on what the browser claims. The applicant always comes from the login,
never from the form.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction

from academics.models import AcademicYear, Holiday, LeaveType, PolicyConfig
from accounts.models import StaffProfile
from core.lookup import parse_object
from core.permissions import get_profile, require_role
from core.validation import DomainError, parse_date, required_text, validate

from .day_engine import FRACTIONS, FULL, MAX_DATES, DayPlan, allocate_leave, plan_days, summarize
from .models import LeaveApplication, LeaveDay, StaffLeaveBalance
from .workflow import initial_status
from .workload import cover_clash_dates

logger = logging.getLogger("vsclms.leave")

Role = StaffProfile.Role
Status = LeaveApplication.Status
APPLICANT_ROLES = (Role.STAFF, Role.HOD, Role.PRINCIPAL)
PENDING_STATUSES = (Status.WORKLOAD_PENDING, Status.HOD_PENDING, Status.PRINCIPAL_PENDING)
RESULT_PAID, RESULT_LWP, RESULT_NOT_COUNTED = "PAID", "LWP", "NOT_COUNTED"
MAX_CLASH_DATES_SHOWN = 3


def days_text(value):
    """10.0 -> '10', 7.5 -> '7.5'."""
    return f"{value:f}".rstrip("0").rstrip(".") or "0"


def _fmt(day):
    return day.strftime("%a %d %b %Y")


@dataclass
class PreviewRow:
    plan: DayPlan
    kind: str
    result: str
    holiday: Holiday | None = None

    @property
    def iso(self):
        return self.plan.date.isoformat()

    @property
    def day_part_label(self):
        return LeaveDay.DayPart(self.plan.day_part).label


@dataclass
class Preview:
    applicant: StaffProfile
    leave_type: LeaveType
    year: AcademicYear
    start: object
    end: object
    reason: str
    requires_workload: bool
    rows: list
    summary: dict
    available: Decimal
    has_balance: bool
    status: str
    warnings: list = field(default_factory=list)

    @property
    def status_label(self):
        return Status(self.status).label


# ------------------------------------------------------------------- blockers
def active_year():
    return AcademicYear.objects.filter(is_active=True).first()


def application_blocker(profile):
    """A plain-language reason this person cannot apply right now, or None.

    Checked on the page (so the form can be hidden) and again on every
    preview and submission (so it cannot be skipped).
    """
    policy = PolicyConfig.get()
    if active_year() is None:
        return "No academic year is active yet, so leave can't be applied for. Ask the administrator."
    if profile.role == Role.PRINCIPAL:
        if not policy.principal_bypass_stages:
            return (
                "Your own leave is approved automatically, and that is switched off in the "
                "policy settings. Ask the administrator."
            )
        return None
    if not StaffProfile.objects.filter(role=Role.PRINCIPAL, is_active=True).exists():
        return "There is no active Principal yet, so leave can't be routed for approval. Ask the administrator."
    if profile.role == Role.STAFF:  # decision G3
        head = profile.department.hod
        if head is None or not head.is_active:
            return (
                f"{profile.department.name} has no head of department yet, so your leave can't be "
                "routed for review. Ask the administrator."
            )
    return None


# -------------------------------------------------------------- preparation
def _day_parts(data, start, end):
    parts = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        value = (data.get(f"part_{day.isoformat()}") or "").strip()
        if not value:
            continue
        if value not in FRACTIONS:
            raise DomainError({"day_parts": "Choose full day, morning half or afternoon half for each date."})
        if value != FULL:
            parts[day] = value
    return parts


def _ensure_no_overlap(profile, start, end):
    """Rejected, cancelled and removed applications do not block (their days have
    ``blocks_overlap`` switched off). Sundays and holidays inside a live leave do."""
    clashes = list(
        LeaveDay.objects.filter(staff=profile, blocks_overlap=True, date__range=(start, end))
        .select_related("leave").order_by("date")
    )
    if not clashes:
        return
    shown = ", ".join(_fmt(c.date) for c in clashes[:MAX_CLASH_DATES_SHOWN])
    hidden = len(clashes) - MAX_CLASH_DATES_SHOWN
    extra = ""
    if hidden > 0:
        extra = f" and {hidden} more date{'s' if hidden != 1 else ''}"
    first = clashes[0].leave
    raise DomainError({
        "start_date": (
            f"You already have leave on {shown}{extra} "
            f"(application #{first.pk}, {first.get_status_display().lower()}). "
            "Choose other dates, or wait for that application to be decided."
        )
    })


def _row_kind(plan, holiday):
    if plan.is_holiday:
        return f"Holiday: {holiday.name}" if holiday else "Holiday"
    if plan.is_sunday:
        return "Sunday"
    if not plan.is_working_day:
        return "Weekly off"
    return "Working day"


def _prepare(profile, data):
    policy = PolicyConfig.get()
    blocker = application_blocker(profile)
    if blocker:
        raise DomainError({"form": blocker})
    year = active_year()

    cleaned = validate({
        "leave_type": lambda: parse_object(
            LeaveType.objects.filter(is_active=True), data.get("leave_type"), "leave_type", "leave type"
        ),
        "start_date": lambda: parse_date(data.get("start_date"), "start_date", "Start date"),
        "end_date": lambda: parse_date(data.get("end_date"), "end_date", "End date"),
        "reason": lambda: required_text(data.get("reason"), "reason", 500, "Reason"),
    })
    leave_type, start, end = cleaned["leave_type"], cleaned["start_date"], cleaned["end_date"]
    if start > end:
        raise DomainError({"end_date": "The end date can't be before the start date."})
    if not (year.start_date <= start <= year.end_date):
        raise DomainError({"start_date": _outside_year(year)})
    if not (year.start_date <= end <= year.end_date):
        raise DomainError({"end_date": _outside_year(year)})
    if (end - start).days + 1 > MAX_DATES:
        raise DomainError({"end_date": f"A request can cover at most {MAX_DATES} dates."})

    _ensure_no_overlap(profile, start, end)
    parts = _day_parts(data, start, end)

    holidays = {
        h.date: h for h in Holiday.objects.filter(academic_year=year, is_deleted=False, date__range=(start, end))
    }
    plans = plan_days(start, end, holidays=set(holidays), off_days=policy.weekly_off_days_set, day_parts=parts)
    balance = StaffLeaveBalance.objects.filter(staff=profile, leave_type=leave_type, academic_year=year).first()
    available = balance.remaining if balance else Decimal("0")  # decision G6: no row means zero
    plans = allocate_leave(plans, available)
    summary = summarize(plans)
    if summary["counted"] == 0:
        raise DomainError({
            "form": (
                "None of these dates count as leave. Sundays, weekly offs and holidays are not counted. "
                "Choose different dates."
            )
        })

    requires_workload = False if profile.role == Role.PRINCIPAL else data.get("requires_workload") == "on"
    rows = []
    for plan in plans:
        result = RESULT_NOT_COUNTED if not plan.is_counted else (RESULT_LWP if plan.is_lwp else RESULT_PAID)
        rows.append(PreviewRow(plan, _row_kind(plan, holidays.get(plan.date)), result, holidays.get(plan.date)))

    warnings = _warnings(profile, leave_type, year, balance, available, summary)
    warnings.extend(_cover_warnings(profile, start, end))
    if profile.role == Role.PRINCIPAL:
        warnings.append("As Principal, your own leave skips the workload and head-of-department stages.")
    return Preview(
        applicant=profile, leave_type=leave_type, year=year, start=start, end=end,
        reason=cleaned["reason"], requires_workload=requires_workload, rows=rows, summary=summary,
        available=available, has_balance=balance is not None,
        status=initial_status(profile.role, requires_workload, policy), warnings=warnings,
    )


def _outside_year(year):
    return (
        f"Dates must fall inside the active academic year {year.name} "
        f"({year.start_date.strftime('%d %b %Y')} to {year.end_date.strftime('%d %b %Y')}). "
        "Leave across two years isn't supported yet."
    )


def _warnings(profile, leave_type, year, balance, available, summary):
    warnings = []
    code = leave_type.code
    if balance is None:
        warnings.append(
            f"You have no {code} balance for {year.name}, so every counted day will be leave without pay."
        )
    elif summary["lwp"] > 0:
        warnings.append(
            f"{days_text(summary['lwp'])} of these days will be leave without pay, because your {code} balance "
            f"({days_text(available)} days left) doesn't cover them. This does not stop you applying."
        )
    waiting = LeaveApplication.objects.filter(
        applicant=profile, leave_type=leave_type, academic_year=year, is_deleted=False,
        status__in=PENDING_STATUSES,
    ).count()
    if waiting:
        warnings.append(
            f"You have {waiting} other {code} application{'s' if waiting != 1 else ''} waiting for a decision. "
            "Nothing is deducted until a final approval, so this estimate ignores them."
        )
    return warnings


def _cover_warnings(profile, start, end):
    """Not a block: the applicant has agreed, or been asked, to cover a colleague on these dates."""
    dates = cover_clash_dates(profile, start, end)
    if not dates:
        return []
    shown = ", ".join(_fmt(d) for d in sorted(dates)[:MAX_CLASH_DATES_SHOWN])
    extra = len(dates) - MAX_CLASH_DATES_SHOWN
    more = f" and {extra} more" if extra > 0 else ""
    return [
        f"You are asked to cover a colleague's work on {shown}{more}. "
        "If your leave is approved you won't be able to, so tell them or sort it out with the head of department."
    ]


# ------------------------------------------------------------------ public API
def build_preview(*, user, data):
    """Calculate what an application would look like. Writes nothing."""
    require_role(user, *APPLICANT_ROLES)
    return _prepare(get_profile(user), data)


def submit_application(*, user, data):
    """Save the application and one LeaveDay per date, or nothing at all."""
    require_role(user, *APPLICANT_ROLES)
    profile = get_profile(user)
    try:
        with transaction.atomic():
            # One person's submissions are handled one at a time, so a double click
            # cannot slip two applications for the same dates past the overlap check.
            StaffProfile.objects.select_for_update().get(pk=profile.pk)
            preview = _prepare(profile, data)
            summary = preview.summary
            application = LeaveApplication.objects.create(
                applicant=profile, academic_year=preview.year, leave_type=preview.leave_type,
                start_date=preview.start, end_date=preview.end, reason=preview.reason,
                requires_workload=preview.requires_workload, status=preview.status,
                balance_before=preview.available, balance_after=None,
                total_days_counted=summary["counted"], total_days_paid=summary["paid"],
                total_days_lwp=summary["lwp"],
            )
            # bulk_create skips model validation, so every field is set from the engine's
            # already-checked plan; the database constraints are the second line of defence.
            LeaveDay.objects.bulk_create([
                LeaveDay(
                    leave=application, staff=profile, date=row.plan.date, day_part=row.plan.day_part,
                    fraction=row.plan.fraction, is_working_day=row.plan.is_working_day,
                    is_sunday=row.plan.is_sunday, is_holiday=row.plan.is_holiday, holiday=row.holiday,
                    is_counted=row.plan.is_counted,
                    workload_required=preview.requires_workload and row.plan.is_counted,
                    is_lwp=row.plan.is_lwp, status=LeaveDay.Status.PENDING,
                    paid_deducted=Decimal("0"), blocks_overlap=True,
                )
                for row in preview.rows
            ])
    except IntegrityError:  # someone else's request got the same dates first
        raise DomainError({
            "form": "Another application for these dates was just saved. Check My leaves, then try again."
        }) from None
    logger.info(
        "audit action=LEAVE_CREATED id=%s applicant=%s type=%s status=%s counted=%s paid=%s lwp=%s",
        application.pk, profile.pk, preview.leave_type.code, application.status,
        summary["counted"], summary["paid"], summary["lwp"],
    )
    logger.info("audit action=LEAVE_DAY_CREATED leave=%s days=%s", application.pk, len(preview.rows))
    return application
