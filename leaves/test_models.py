"""One test per database rule: the database itself must refuse bad data."""
import datetime
from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.test import TestCase

from academics.models import AcademicYear, LeaveType
from core.testutils import make_staff
from leaves.models import LeaveApplication, LeaveDay, StaffLeaveBalance

DAY = datetime.date(2026, 11, 9)


class ModelTestCase(TestCase):
    def setUp(self):
        self.staff = make_staff("asha")
        self.year = AcademicYear.objects.create(
            name="2026-27", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31)
        )
        self.cl = LeaveType.objects.create(name="Casual Leave", code="CL")

    def rejected(self, create):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create()

    def application(self, **extra):
        values = dict(
            applicant=self.staff, academic_year=self.year, leave_type=self.cl,
            start_date=DAY, end_date=DAY, reason="Family event",
        )
        values.update(extra)
        return LeaveApplication.objects.create(**values)

    def day(self, leave, **extra):
        values = dict(leave=leave, staff=self.staff, date=DAY)
        values.update(extra)
        return LeaveDay.objects.create(**values)


class BalanceConstraintTests(ModelTestCase):
    def balance(self, **extra):
        values = dict(staff=self.staff, leave_type=self.cl, academic_year=self.year, total=D("10"))
        values.update(extra)
        return StaffLeaveBalance.objects.create(**values)

    def test_remaining_is_total_minus_used(self):
        self.assertEqual(self.balance(total=D("10"), used=D("2.5")).remaining, D("7.5"))

    def test_defaults_start_at_zero(self):
        b = StaffLeaveBalance.objects.create(staff=self.staff, leave_type=self.cl, academic_year=self.year)
        self.assertEqual((b.total, b.used), (0, 0))

    def test_one_balance_per_person_type_and_year(self):
        self.balance()
        self.rejected(self.balance)

    def test_same_person_may_hold_another_type_or_year(self):
        self.balance()
        el = LeaveType.objects.create(name="Earned Leave", code="EL")
        self.balance(leave_type=el)
        other_year = AcademicYear.objects.create(
            name="2027-28", start_date=datetime.date(2027, 6, 1), end_date=datetime.date(2028, 5, 31)
        )
        self.balance(academic_year=other_year)
        self.assertEqual(StaffLeaveBalance.objects.count(), 3)

    def test_total_cannot_be_negative(self):
        self.rejected(lambda: self.balance(total=D("-1")))

    def test_used_cannot_be_negative(self):
        self.rejected(lambda: self.balance(used=D("-0.5")))

    def test_used_cannot_exceed_total(self):
        self.rejected(lambda: self.balance(total=D("2"), used=D("2.5")))

    def test_used_equal_to_total_is_allowed(self):
        self.assertEqual(self.balance(total=D("2"), used=D("2")).remaining, 0)

    def test_str_is_readable(self):
        self.assertIn("CL", str(self.balance()))


class LeaveApplicationConstraintTests(ModelTestCase):
    def test_start_after_end_is_refused(self):
        self.rejected(lambda: self.application(start_date=DAY + datetime.timedelta(days=1), end_date=DAY))

    def test_single_day_and_multi_day_are_fine(self):
        self.application()
        self.application(end_date=DAY + datetime.timedelta(days=3))
        self.assertEqual(LeaveApplication.objects.count(), 2)

    def test_defaults(self):
        app = self.application()
        self.assertEqual(app.status, LeaveApplication.Status.WORKLOAD_PENDING)
        self.assertEqual((app.total_days_counted, app.total_days_paid, app.total_days_lwp), (0, 0, 0))
        self.assertFalse(app.is_deleted or app.is_principal_auto_approved)


class LeaveDayConstraintTests(ModelTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.application()

    def test_defaults_describe_a_pending_full_day(self):
        d = self.day(self.app)
        self.assertEqual((d.status, d.day_part, d.fraction, d.paid_deducted), ("PENDING", "FULL", D("1"), 0))
        self.assertTrue(d.blocks_overlap)

    def test_one_row_per_date_per_application(self):
        self.day(self.app, blocks_overlap=False)
        self.rejected(lambda: self.day(self.app, blocks_overlap=False))

    def test_same_person_cannot_hold_two_live_leaves_on_one_date(self):
        self.day(self.app)
        other = self.application()
        self.rejected(lambda: self.day(other))

    def test_a_rejected_or_cancelled_day_frees_the_date(self):
        first = self.day(self.app)
        first.blocks_overlap = False
        first.status = LeaveDay.Status.REJECTED
        first.save()
        self.day(self.application())
        self.assertEqual(LeaveDay.objects.filter(date=DAY).count(), 2)

    def test_two_different_people_may_take_the_same_date(self):
        self.day(self.app)
        other_staff = make_staff("ravi")
        other_app = self.application(applicant=other_staff)
        self.day(other_app, staff=other_staff)
        self.assertEqual(LeaveDay.objects.count(), 2)

    def test_fraction_must_be_half_or_full(self):
        for bad in (D("0.7"), D("0"), D("2.0"), D("0.4")):
            self.rejected(lambda bad=bad: self.day(self.app, fraction=bad, blocks_overlap=False))
        for good in (D("0.5"), D("1.0")):
            self.day(self.app, fraction=good, date=DAY + datetime.timedelta(days=int(good * 10)))

    def test_a_counted_day_cannot_be_a_sunday(self):
        self.rejected(lambda: self.day(self.app, is_counted=True, is_sunday=True, is_working_day=False))

    def test_a_counted_day_cannot_be_a_holiday_or_a_weekly_off(self):
        self.rejected(lambda: self.day(self.app, is_counted=True, is_holiday=True))
        self.rejected(lambda: self.day(self.app, is_counted=True, is_working_day=False))

    def test_a_non_counted_sunday_or_holiday_is_fine(self):
        self.day(self.app, is_counted=False, is_sunday=True, is_working_day=False)
        self.day(self.app, is_counted=False, is_holiday=True, date=DAY + datetime.timedelta(days=1))

    def test_lwp_is_never_deducted(self):
        self.rejected(lambda: self.day(self.app, is_lwp=True, paid_deducted=D("1.0")))
        self.day(self.app, is_lwp=True, paid_deducted=D("0"))

    def test_a_paid_day_may_record_its_deduction(self):
        d = self.day(self.app, is_counted=True, paid_deducted=D("1.0"))
        self.assertEqual(d.paid_deducted, D("1.0"))

    def test_deduction_cannot_exceed_the_days_fraction_or_be_negative(self):
        self.rejected(lambda: self.day(self.app, fraction=D("0.5"), paid_deducted=D("1.0")))
        self.rejected(lambda: self.day(self.app, paid_deducted=D("-0.5")))

    def test_str_is_readable(self):
        self.assertIn("2026-11-09", str(self.day(self.app)))
