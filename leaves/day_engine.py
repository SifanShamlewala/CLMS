"""The day engine: turns a date range into one plan row per date.

This module is PURE PYTHON. It never touches the database, so it is easy to
test and easy to reason about. Services load the holidays, the policy and the
balance from the database, call these functions, and save the result as
LeaveDay rows.

Rules it follows (spec sections 4 and 8.3, decisions D7 to D9):

* One row per calendar date from ``start`` to ``end``, both inclusive.
* Sundays are NEVER counted, even if the policy forgets to list them.
* A policy may add more weekly off days (for example Saturday = 5).
* A holiday is not counted. It keeps its own flag so reports can tell
  "holiday" from "weekly off".
* A day is COUNTED only if it is a working day and not a holiday.
* A counted day is FULL (1.0) or a half day (0.5) depending on ``day_parts``.
  A day that is not counted is always stored as FULL; a half-day request for
  it is ignored.
* Paid versus LWP is decided chronologically, and a day is paid only if its
  WHOLE fraction is covered by what is left of the balance. Otherwise the
  whole day is LWP and the balance is not touched, so a later half day can
  still use a leftover half.
* A missing or negative balance counts as zero.
* Decimal is used everywhere, never float.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

FULL = "FULL"
HALF_AM = "HALF_AM"
HALF_PM = "HALF_PM"
FRACTIONS = {FULL: Decimal("1.0"), HALF_AM: Decimal("0.5"), HALF_PM: Decimal("0.5")}

SUNDAY = 6  # date.weekday(): Monday is 0, Sunday is 6
MAX_DATES = 366  # a request longer than a year is almost certainly a typo


@dataclass
class DayPlan:
    date: date
    day_part: str
    fraction: Decimal
    is_sunday: bool
    is_holiday: bool
    is_working_day: bool  # False on weekly off days (Sunday, plus any the policy adds)
    is_counted: bool
    is_lwp: bool = False


def plan_days(start, end, *, holidays=frozenset(), off_days=frozenset({SUNDAY}), day_parts=None):
    """Return a list of DayPlan, one per date from start to end inclusive.

    ``holidays``  set of dates that are holidays.
    ``off_days``  set of weekday numbers that are weekly offs (Sunday is always added).
    ``day_parts`` optional {date: FULL | HALF_AM | HALF_PM}.

    Raises ValueError when start is after end, when more than MAX_DATES dates are
    requested, or when a day part is not one of the three allowed values.
    """
    if start > end:
        raise ValueError("The start date is after the end date.")
    count = (end - start).days + 1
    if count > MAX_DATES:
        raise ValueError(f"A request can cover at most {MAX_DATES} dates.")
    day_parts = day_parts or {}
    for part in day_parts.values():
        if part not in FRACTIONS:
            raise ValueError(f"Unknown day part: {part!r}.")
    weekly_off = set(off_days) | {SUNDAY}

    plans = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        is_working_day = day.weekday() not in weekly_off
        is_holiday = day in holidays
        is_counted = is_working_day and not is_holiday
        part = day_parts.get(day, FULL) if is_counted else FULL
        plans.append(DayPlan(
            date=day,
            day_part=part,
            fraction=FRACTIONS[part],
            is_sunday=day.weekday() == SUNDAY,
            is_holiday=is_holiday,
            is_working_day=is_working_day,
            is_counted=is_counted,
        ))
    return plans


def allocate_leave(plans, available):
    """Mark each counted day paid or LWP and return the plans sorted by date.

    ``available`` is a Decimal balance (or None). The plans are updated in place,
    so calling this again with a different balance starts from scratch.
    """
    remaining = Decimal("0") if available is None else Decimal(available)
    remaining = max(remaining, Decimal("0"))
    ordered = sorted(plans, key=lambda plan: plan.date)
    for plan in ordered:
        if not plan.is_counted:
            plan.is_lwp = False
        elif remaining >= plan.fraction:
            plan.is_lwp = False
            remaining -= plan.fraction
        else:
            plan.is_lwp = True  # the whole day is LWP; the balance is left alone
    return ordered


def summarize(plans):
    """Return {"dates": int, "counted": Decimal, "paid": Decimal, "lwp": Decimal}."""
    counted = sum((p.fraction for p in plans if p.is_counted), Decimal("0"))
    lwp = sum((p.fraction for p in plans if p.is_counted and p.is_lwp), Decimal("0"))
    return {"dates": len(plans), "counted": counted, "paid": counted - lwp, "lwp": lwp}
