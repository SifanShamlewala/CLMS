"""Tests for the day engine. Make every one of these pass without editing them.

They need no database, so they run in a fraction of a second:
    python manage.py test leaves.test_day_engine
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase

from leaves.day_engine import (
    FULL, HALF_AM, HALF_PM, allocate_leave, plan_days, summarize,
)

# A real week in November 2026
MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 11, d) for d in range(9, 16))
NEXT_MON = date(2026, 11, 16)


def by_date(plans):
    return {p.date: p for p in plans}


class PlanDaysTests(SimpleTestCase):
    def test_one_row_per_date_in_order_both_ends_included(self):
        plans = plan_days(MON, SUN)
        self.assertEqual([p.date for p in plans], [MON + timedelta(days=i) for i in range(7)])

    def test_single_day_range(self):
        plans = plan_days(WED, WED)
        self.assertEqual(len(plans), 1)

    def test_ordinary_weekday_is_counted_full(self):
        p = by_date(plan_days(MON, SUN))[MON]
        self.assertEqual((p.day_part, p.fraction), (FULL, D("1.0")))
        self.assertTrue(p.is_working_day and p.is_counted)
        self.assertFalse(p.is_sunday or p.is_holiday or p.is_lwp)

    def test_sunday_is_flagged_and_never_counted(self):
        p = by_date(plan_days(MON, SUN))[SUN]
        self.assertTrue(p.is_sunday)
        self.assertFalse(p.is_working_day or p.is_counted)

    def test_saturday_is_a_normal_working_day_by_default(self):
        self.assertTrue(by_date(plan_days(MON, SUN))[SAT].is_counted)

    def test_holiday_is_flagged_and_not_counted_but_still_a_working_weekday(self):
        p = by_date(plan_days(MON, SUN, holidays={WED}))[WED]
        self.assertTrue(p.is_holiday)
        self.assertTrue(p.is_working_day)
        self.assertFalse(p.is_counted)

    def test_holiday_on_a_sunday_sets_both_flags(self):
        p = by_date(plan_days(MON, SUN, holidays={SUN}))[SUN]
        self.assertTrue(p.is_holiday and p.is_sunday)
        self.assertFalse(p.is_counted)

    def test_policy_can_add_saturday_as_a_weekly_off(self):
        p = by_date(plan_days(MON, SUN, off_days={5, 6}))[SAT]
        self.assertFalse(p.is_working_day or p.is_counted or p.is_sunday)

    def test_sunday_stays_off_even_if_the_policy_forgets_it(self):
        p = by_date(plan_days(MON, SUN, off_days=frozenset()))[SUN]
        self.assertTrue(p.is_sunday)
        self.assertFalse(p.is_working_day or p.is_counted)

    def test_half_day_overrides_on_counted_days(self):
        plans = by_date(plan_days(MON, WED, day_parts={MON: HALF_AM, TUE: HALF_PM}))
        self.assertEqual((plans[MON].day_part, plans[MON].fraction), (HALF_AM, D("0.5")))
        self.assertEqual((plans[TUE].day_part, plans[TUE].fraction), (HALF_PM, D("0.5")))
        self.assertEqual((plans[WED].day_part, plans[WED].fraction), (FULL, D("1.0")))

    def test_half_day_request_on_a_non_counted_day_is_ignored(self):
        plans = by_date(plan_days(MON, SUN, holidays={WED}, day_parts={SUN: HALF_AM, WED: HALF_PM}))
        for day in (SUN, WED):
            self.assertEqual((plans[day].day_part, plans[day].fraction), (FULL, D("1.0")))
            self.assertFalse(plans[day].is_counted)

    def test_invalid_day_part_rejected(self):
        with self.assertRaises(ValueError):
            plan_days(MON, WED, day_parts={MON: "QUARTER"})

    def test_start_after_end_rejected(self):
        with self.assertRaises(ValueError):
            plan_days(TUE, MON)

    def test_a_range_of_more_than_366_dates_is_rejected(self):
        self.assertEqual(len(plan_days(MON, MON + timedelta(days=365))), 366)
        with self.assertRaises(ValueError):
            plan_days(MON, MON + timedelta(days=366))


class AllocateLeaveTests(SimpleTestCase):
    def working_week(self):
        return plan_days(MON, FRI)  # five counted days

    def lwp_dates(self, plans):
        return [p.date for p in plans if p.is_lwp]

    def test_enough_balance_means_nothing_is_lwp(self):
        self.assertEqual(self.lwp_dates(allocate_leave(self.working_week(), D("10"))), [])

    def test_zero_none_and_negative_balance_mean_everything_is_lwp(self):
        for balance in (D("0"), None, D("-3")):
            plans = allocate_leave(self.working_week(), balance)
            self.assertEqual(len(self.lwp_dates(plans)), 5, balance)

    def test_partial_balance_pays_the_earliest_days_and_whole_days_only(self):
        plans = allocate_leave(self.working_week(), D("2.5"))
        self.assertEqual(self.lwp_dates(plans), [WED, THU, FRI])  # the spare 0.5 cannot cover a full day

    def test_a_leftover_half_can_still_pay_a_later_half_day(self):
        plans = plan_days(MON, WED, day_parts={WED: HALF_AM})
        result = by_date(allocate_leave(plans, D("1.5")))
        self.assertFalse(result[MON].is_lwp)   # 1.5 -> 0.5 left
        self.assertTrue(result[TUE].is_lwp)    # needs 1.0, only 0.5 left, balance untouched
        self.assertFalse(result[WED].is_lwp)   # half day uses the leftover 0.5

    def test_order_of_the_input_does_not_matter(self):
        shuffled = list(reversed(self.working_week()))
        result = allocate_leave(shuffled, D("1"))
        self.assertEqual([p.date for p in result], [MON, TUE, WED, THU, FRI])
        self.assertEqual(self.lwp_dates(result), [TUE, WED, THU, FRI])

    def test_days_that_are_not_counted_never_use_balance_and_never_become_lwp(self):
        plans = plan_days(MON, SUN, holidays={WED})  # counted: Mon Tue Thu Fri Sat
        result = by_date(allocate_leave(plans, D("4")))
        self.assertTrue(result[SAT].is_lwp)               # fifth counted day, only 4 available
        self.assertFalse(result[WED].is_lwp or result[SUN].is_lwp)

    def test_exact_balance_covers_everything(self):
        self.assertEqual(self.lwp_dates(allocate_leave(self.working_week(), D("5"))), [])


class SummarizeTests(SimpleTestCase):
    def test_totals_reconcile(self):
        plans = plan_days(MON, SUN, holidays={WED}, day_parts={TUE: HALF_AM})
        plans = allocate_leave(plans, D("3"))
        # counted: Mon 1 + Tue 0.5 + Thu 1 + Fri 1 + Sat 1 = 4.5
        # balance 3: Mon 1 (2 left), Tue 0.5 (1.5 left), Thu 1 (0.5 left),
        # then Fri and Sat need 1.0 each and are LWP -> paid 2.5, lwp 2.0
        self.assertEqual(summarize(plans), {"dates": 7, "counted": D("4.5"), "paid": D("2.5"), "lwp": D("2.0")})

    def test_nothing_counted(self):
        plans = allocate_leave(plan_days(SUN, SUN), D("5"))
        self.assertEqual(summarize(plans), {"dates": 1, "counted": D("0"), "paid": D("0"), "lwp": D("0")})


class EngineExtraTests(SimpleTestCase):
    def test_allocating_twice_with_a_bigger_balance_starts_from_scratch(self):
        plans = plan_days(MON, FRI)
        allocate_leave(plans, D("1"))
        self.assertEqual(sum(p.is_lwp for p in plans), 4)
        allocate_leave(plans, D("5"))
        self.assertEqual(sum(p.is_lwp for p in plans), 0)

    def test_a_full_year_range_is_fine_and_has_one_row_per_date(self):
        plans = plan_days(date(2026, 6, 1), date(2027, 5, 31))
        self.assertEqual(len(plans), 365)
        self.assertEqual(len({p.date for p in plans}), 365)

    def test_counted_days_never_fall_on_sunday_or_holiday(self):
        plans = plan_days(date(2026, 6, 1), date(2027, 5, 31), holidays={date(2026, 8, 15)}, off_days={5})
        for plan in plans:
            if plan.is_counted:
                self.assertTrue(plan.is_working_day and not plan.is_sunday and not plan.is_holiday)

    def test_fractions_always_add_up_exactly(self):
        plans = plan_days(MON, FRI, day_parts={d: HALF_AM for d in (MON, TUE, WED)})
        self.assertEqual(summarize(allocate_leave(plans, D("10")))["counted"], D("3.5"))
