from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError

from core.testutils import make_admin, make_staff
from core.validation import DomainError
from leaves import applications
from leaves.models import LeaveApplication, LeaveDay, StaffLeaveBalance
from leaves.testutils import FRI, MON, NEXT_MON, SAT, SUN, THU, TUE, WED, LeaveTestCase, Role

Status = LeaveApplication.Status


class SubmitTestCase(LeaveTestCase):
    def submit(self, user=None, **extra):
        return applications.submit_application(user=user or self.asha.user, data=self.payload(**extra))

    def days(self, application):
        return {d.date: d for d in application.days.all()}


class SubmitCreateTests(SubmitTestCase):
    def test_saves_the_application_with_snapshots_and_the_active_year(self):
        application = self.submit()
        application.refresh_from_db()
        self.assertEqual(
            (application.applicant, application.academic_year, application.leave_type, application.reason),
            (self.asha, self.year, self.cl, "Family function"),
        )
        self.assertEqual((application.start_date, application.end_date), (MON, FRI))
        self.assertEqual(application.status, Status.WORKLOAD_PENDING)
        self.assertTrue(application.requires_workload)
        self.assertEqual(application.balance_before, D("10"))
        self.assertIsNone(application.balance_after)
        self.assertEqual(
            (application.total_days_counted, application.total_days_paid, application.total_days_lwp),
            (D("5"), D("5"), D("0")),
        )
        self.assertIsNotNone(application.submitted_at)
        self.assertFalse(application.is_deleted or application.is_principal_auto_approved)

    def test_one_day_row_per_date_with_the_engines_flags(self):
        self.holiday(WED, "Diwali")
        application = self.submit(end_date=SUN.isoformat())
        days = self.days(application)
        self.assertEqual(len(days), 7)
        self.assertTrue(days[SUN].is_sunday and not days[SUN].is_counted and not days[SUN].is_working_day)
        self.assertTrue(days[WED].is_holiday and not days[WED].is_counted)
        self.assertEqual(days[WED].holiday.name, "Diwali")
        self.assertIsNone(days[MON].holiday)
        for day in days.values():
            self.assertEqual((day.status, day.paid_deducted, day.leave, day.staff), ("PENDING", D("0"), application, self.asha))
            self.assertTrue(day.blocks_overlap)

    def test_workload_is_required_only_on_counted_days_and_only_when_asked_for(self):
        self.holiday(WED)
        days = self.days(self.submit(end_date=SUN.isoformat()))
        self.assertEqual({d.date for d in days.values() if d.workload_required}, {MON, TUE, THU, FRI, SAT})
        self.holiday(THU)  # a second application in a later week, without workload
        data = self.payload(start_date=NEXT_MON.isoformat(), end_date=NEXT_MON.isoformat())
        data.pop("requires_workload")
        application = applications.submit_application(user=self.asha.user, data=data)
        self.assertFalse(application.requires_workload)
        self.assertEqual(application.status, Status.HOD_PENDING)
        self.assertFalse(any(d.workload_required for d in application.days.all()))

    def test_half_days_and_the_paid_unpaid_split_are_stored_per_day(self):
        StaffLeaveBalance.objects.update(total=D("2"))
        application = self.submit(**{f"part_{TUE}": "HALF_PM"})
        days = self.days(application)
        self.assertEqual((days[TUE].day_part, days[TUE].fraction), ("HALF_PM", D("0.5")))
        # 1 (Mon) + 0.5 (Tue) leaves 0.5, which cannot cover a full Wednesday
        self.assertEqual({d for d, row in days.items() if row.is_lwp}, {WED, THU, FRI})
        self.assertEqual(application.total_days_lwp, D("3.0"))

    def test_the_application_totals_reconcile_with_the_day_rows(self):
        self.holiday(WED)
        StaffLeaveBalance.objects.update(total=D("2.5"))
        application = self.submit(end_date=SUN.isoformat(), **{f"part_{MON}": "HALF_AM"})
        rows = list(application.days.all())
        counted = sum(d.fraction for d in rows if d.is_counted)
        lwp = sum(d.fraction for d in rows if d.is_counted and d.is_lwp)
        self.assertEqual((application.total_days_counted, application.total_days_lwp), (counted, lwp))
        self.assertEqual(application.total_days_paid, counted - lwp)

    def test_submitting_deducts_nothing(self):
        self.submit()
        balance = StaffLeaveBalance.objects.get()
        self.assertEqual((balance.total, balance.used), (D("10"), D("0")))

    def test_the_summary_matches_the_preview(self):
        preview = applications.build_preview(user=self.asha.user, data=self.payload())
        application = self.submit()
        self.assertEqual(application.total_days_counted, preview.summary["counted"])
        self.assertEqual(application.status, preview.status)

    def test_the_applicant_comes_from_the_login_not_the_form(self):
        other = make_staff("ravi", self.maths)
        application = self.submit(applicant=str(other.pk), staff=str(other.pk))
        self.assertEqual(application.applicant, self.asha)
        self.assertEqual({d.staff for d in application.days.all()}, {self.asha})


class SubmitRolesTests(SubmitTestCase):
    def test_a_heads_own_leave_skips_the_head_stage(self):
        data = self.payload()
        data.pop("requires_workload")
        application = applications.submit_application(user=self.hod.user, data=data)
        self.assertEqual(application.status, Status.PRINCIPAL_PENDING)

    def test_the_principals_leave_waits_at_the_principal_stage_with_no_workload(self):
        application = self.submit(self.principal.user)
        self.assertEqual(application.status, Status.PRINCIPAL_PENDING)
        self.assertFalse(application.requires_workload)
        self.assertFalse(any(d.workload_required for d in application.days.all()))
        self.assertFalse(application.is_principal_auto_approved)  # automatic approval arrives with Day 9

    def test_the_administrator_without_a_profile_and_inactive_profiles_cannot_apply(self):
        with self.assertRaises(PermissionDenied):
            self.submit(make_admin())
        self.asha.is_active = False
        self.asha.save()
        with self.assertRaises(PermissionDenied):
            self.submit()
        self.assertEqual(LeaveApplication.objects.count(), 0)

    def test_blockers_stop_a_submission_and_save_nothing(self):
        self.physics.hod = None
        self.physics.save()
        self.asha.refresh_from_db()
        with self.assertRaises(DomainError):
            self.submit()
        self.assertEqual((LeaveApplication.objects.count(), LeaveDay.objects.count()), (0, 0))


class SubmitOverlapTests(SubmitTestCase):
    def test_a_double_submit_creates_one_application_and_the_second_is_refused(self):
        first = self.submit()
        with self.assertRaises(DomainError) as ctx:
            self.submit()
        self.assertIn(f"application #{first.pk}", ctx.exception.errors["start_date"])
        self.assertEqual(LeaveApplication.objects.count(), 1)
        self.assertEqual(LeaveDay.objects.count(), 5)

    def test_overlapping_dates_are_refused_but_adjacent_ranges_are_fine(self):
        self.submit(start_date=TUE.isoformat(), end_date=WED.isoformat())
        with self.assertRaises(DomainError):
            self.submit(start_date=WED.isoformat(), end_date=THU.isoformat())
        self.submit(start_date=THU.isoformat(), end_date=FRI.isoformat())
        self.assertEqual(LeaveApplication.objects.count(), 2)

    def test_dates_are_free_again_once_a_leave_stops_blocking(self):
        first = self.submit()
        first.days.update(blocks_overlap=False, status=LeaveDay.Status.REJECTED)
        self.submit()
        self.assertEqual(LeaveApplication.objects.count(), 2)

    def test_two_people_may_take_the_same_dates(self):
        self.give(self.hod, "10")
        self.submit()
        self.submit(self.hod.user)
        self.assertEqual(LeaveApplication.objects.count(), 2)

    def test_the_database_constraint_stops_an_overlap_that_slips_past_the_check(self):
        """If two requests both pass the Python check, the unique rule on (staff, date) still wins."""
        self.submit()
        with mock.patch("leaves.applications._ensure_no_overlap"):
            with self.assertRaises(DomainError) as ctx:
                self.submit()
        self.assertIn("Another application for these dates was just saved", ctx.exception.errors["form"])
        self.assertEqual((LeaveApplication.objects.count(), LeaveDay.objects.count()), (1, 5))

    def test_a_submission_locks_the_applicants_profile_row_first(self):
        """On PostgreSQL this serialises one person's submissions. SQLite ignores the lock,
        so this only proves the call is made."""
        from accounts.models import StaffProfile
        with mock.patch.object(
            StaffProfile.objects, "select_for_update", wraps=StaffProfile.objects.select_for_update
        ) as locked:
            self.submit()
        locked.assert_called_once()

    def test_losing_the_race_at_the_database_becomes_a_friendly_error_and_saves_nothing(self):
        with mock.patch.object(LeaveDay.objects, "bulk_create", side_effect=IntegrityError("race")):
            with self.assertRaises(DomainError) as ctx:
                self.submit()
        self.assertIn("Another application for these dates was just saved", ctx.exception.errors["form"])
        self.assertEqual((LeaveApplication.objects.count(), LeaveDay.objects.count()), (0, 0))

    def test_a_failure_while_saving_the_days_leaves_no_half_saved_application(self):
        with mock.patch.object(LeaveDay.objects, "bulk_create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual(LeaveApplication.objects.count(), 0)

    def test_invalid_input_saves_nothing(self):
        for extra in ({"reason": ""}, {"start_date": SUN.isoformat(), "end_date": SUN.isoformat()}, {"leave_type": "999"}):
            with self.assertRaises(DomainError):
                self.submit(**extra)
        self.assertEqual((LeaveApplication.objects.count(), LeaveDay.objects.count()), (0, 0))
