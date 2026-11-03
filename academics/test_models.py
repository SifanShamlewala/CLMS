import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from academics.models import AcademicYear, Holiday, LeaveType, PolicyConfig


def make_year(name, start, end, active=False):
    return AcademicYear.objects.create(
        name=name, start_date=start, end_date=end, is_active=active
    )


class AcademicYearTests(TestCase):
    def test_only_one_active_year(self):
        make_year("2026-27", datetime.date(2026, 6, 1), datetime.date(2027, 5, 31), True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_year("2027-28", datetime.date(2027, 6, 1), datetime.date(2028, 5, 31), True)

    def test_many_inactive_years_allowed(self):
        make_year("2024-25", datetime.date(2024, 6, 1), datetime.date(2025, 5, 31))
        make_year("2025-26", datetime.date(2025, 6, 1), datetime.date(2026, 5, 31))
        self.assertEqual(AcademicYear.objects.count(), 2)

    def test_start_must_be_before_end(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_year("bad", datetime.date(2026, 6, 1), datetime.date(2026, 6, 1))

    def test_duplicate_year_name_rejected(self):
        make_year("2026-27", datetime.date(2026, 6, 1), datetime.date(2027, 5, 31))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_year("2026-27", datetime.date(2027, 6, 1), datetime.date(2028, 5, 31))


class LeaveTypeTests(TestCase):
    def test_duplicate_name_rejected(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaveType.objects.create(name="Casual Leave", code="CL2")

    def test_duplicate_code_rejected(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaveType.objects.create(name="Other Leave", code="CL")


class HolidayTests(TestCase):
    def setUp(self):
        self.year = make_year(
            "2026-27", datetime.date(2026, 6, 1), datetime.date(2027, 5, 31), True
        )
        self.day = datetime.date(2026, 8, 15)

    def test_duplicate_active_holiday_rejected(self):
        Holiday.objects.create(date=self.day, name="Independence Day", academic_year=self.year)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Holiday.objects.create(date=self.day, name="Duplicate", academic_year=self.year)

    def test_same_date_allowed_after_soft_delete(self):
        old = Holiday.objects.create(date=self.day, name="Independence Day", academic_year=self.year)
        old.is_deleted = True
        old.save()
        Holiday.objects.create(date=self.day, name="Independence Day", academic_year=self.year)
        self.assertEqual(Holiday.objects.filter(date=self.day).count(), 2)

    def test_same_date_allowed_in_different_year(self):
        other = make_year("2027-28", datetime.date(2027, 6, 1), datetime.date(2028, 5, 31))
        Holiday.objects.create(date=self.day, name="A", academic_year=self.year)
        Holiday.objects.create(date=self.day, name="B", academic_year=other)
        self.assertEqual(Holiday.objects.count(), 2)


class PolicyConfigTests(TestCase):
    def test_get_creates_defaults_once(self):
        policy = PolicyConfig.get()
        self.assertEqual(policy.pk, 1)
        self.assertFalse(policy.allow_cross_year_leave)
        self.assertEqual(policy.hod_own_leave_route, "SKIP_TO_PRINCIPAL")
        self.assertTrue(policy.principal_bypass_stages)
        self.assertEqual(policy.weekly_off_days_set, {6})
        self.assertEqual(PolicyConfig.get().pk, policy.pk)
        self.assertEqual(PolicyConfig.objects.count(), 1)

    def test_saving_another_instance_never_creates_a_second_row(self):
        PolicyConfig.get()
        PolicyConfig(allow_cross_year_leave=True).save()
        self.assertEqual(PolicyConfig.objects.count(), 1)
        self.assertTrue(PolicyConfig.get().allow_cross_year_leave)

    def test_weekly_off_days_parsing(self):
        policy = PolicyConfig(weekly_off_days="5, 6")
        self.assertEqual(policy.weekly_off_days_set, {5, 6})