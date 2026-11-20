import datetime
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from academics.models import AcademicYear, PolicyConfig
from core.testutils import make_admin, make_staff
from core.validation import DomainError
from leaves import applications
from leaves.applications import RESULT_LWP, RESULT_NOT_COUNTED, RESULT_PAID
from leaves.models import LeaveApplication, LeaveDay, StaffLeaveBalance
from leaves.testutils import (
    FRI, MON, NEXT_MON, NEXT_TUE, SAT, SUN, THU, TUE, WED, LeaveTestCase, Role,
)

Status = LeaveApplication.Status


class PreviewTestCase(LeaveTestCase):
    def preview(self, user=None, **extra):
        return applications.build_preview(user=user or self.asha.user, data=self.payload(**extra))

    def errors(self, user=None, **extra):
        with self.assertRaises(DomainError) as ctx:
            self.preview(user, **extra)
        return ctx.exception.errors

    def results(self, preview):
        return {row.plan.date: row.result for row in preview.rows}


class PreviewRowsTests(PreviewTestCase):
    def test_one_row_per_date_both_ends_included_and_nothing_is_written(self):
        preview = self.preview(end_date=SUN.isoformat())
        self.assertEqual(len(preview.rows), 7)
        self.assertEqual(LeaveApplication.objects.count() + LeaveDay.objects.count(), 0)
        self.assertEqual(StaffLeaveBalance.objects.get().used, D("0"))

    def test_row_kinds(self):
        self.holiday(WED, "Diwali")
        preview = self.preview(end_date=SUN.isoformat())
        kinds = {row.plan.date: row.kind for row in preview.rows}
        self.assertEqual(kinds[MON], "Working day")
        self.assertEqual(kinds[WED], "Holiday: Diwali")
        self.assertEqual(kinds[SUN], "Sunday")
        self.assertEqual(self.results(preview)[WED], RESULT_NOT_COUNTED)
        self.assertEqual(self.results(preview)[SUN], RESULT_NOT_COUNTED)

    def test_a_holiday_on_a_sunday_is_labelled_as_a_holiday(self):
        self.holiday(SUN, "Festival")
        kinds = {r.plan.date: r.kind for r in self.preview(end_date=SUN.isoformat()).rows}
        self.assertEqual(kinds[SUN], "Holiday: Festival")

    def test_holidays_of_removed_or_other_years_are_ignored(self):
        removed = self.holiday(WED, "Gone")
        removed.is_deleted = True
        removed.save()
        preview = self.preview()
        self.assertEqual(self.results(preview)[WED], RESULT_PAID)

    def test_saturday_is_a_weekly_off_only_when_the_policy_says_so(self):
        self.assertEqual(self.results(self.preview(end_date=SAT.isoformat()))[SAT], RESULT_PAID)
        policy = PolicyConfig.get()
        policy.weekly_off_days = "5,6"
        policy.save()
        preview = self.preview(end_date=SAT.isoformat())
        self.assertEqual(self.results(preview)[SAT], RESULT_NOT_COUNTED)
        self.assertEqual({r.plan.date: r.kind for r in preview.rows}[SAT], "Weekly off")

    def test_half_days_are_applied_only_to_counted_days(self):
        self.holiday(WED)
        preview = self.preview(
            end_date=SUN.isoformat(), **{f"part_{MON}": "HALF_AM", f"part_{SUN}": "HALF_PM", f"part_{WED}": "HALF_AM"}
        )
        rows = {r.plan.date: r.plan for r in preview.rows}
        self.assertEqual((rows[MON].day_part, rows[MON].fraction), ("HALF_AM", D("0.5")))
        self.assertEqual((rows[SUN].day_part, rows[WED].day_part), ("FULL", "FULL"))
        self.assertEqual(preview.summary["counted"], D("4.5"))  # Mon .5 Tue 1 Thu 1 Fri 1 Sat 1

    def test_an_unknown_day_part_is_refused(self):
        self.assertIn("day_parts", self.errors(**{f"part_{MON}": "QUARTER"}))

    def test_blank_day_part_means_full_day(self):
        preview = self.preview(**{f"part_{MON}": ""})
        self.assertEqual(preview.rows[0].plan.day_part, "FULL")


class PreviewBalanceTests(PreviewTestCase):
    def test_enough_balance_pays_every_day(self):
        preview = self.preview()
        self.assertEqual(set(self.results(preview).values()), {RESULT_PAID})
        self.assertEqual(preview.warnings, [])
        self.assertEqual((preview.available, preview.has_balance), (D("10"), True))

    def test_zero_balance_makes_every_day_unpaid_and_never_blocks(self):
        StaffLeaveBalance.objects.update(total=D("0"))
        preview = self.preview()
        self.assertEqual(set(self.results(preview).values()), {RESULT_LWP})
        self.assertEqual(preview.summary["lwp"], D("5"))

    def test_partial_balance_pays_the_earliest_whole_days_first(self):
        StaffLeaveBalance.objects.update(total=D("2.5"))
        preview = self.preview()
        self.assertEqual(
            self.results(preview),
            {MON: RESULT_PAID, TUE: RESULT_PAID, WED: RESULT_LWP, THU: RESULT_LWP, FRI: RESULT_LWP},
        )
        self.assertTrue(any("3 of these days will be leave without pay" in w for w in preview.warnings))
        self.assertTrue(any("2.5 days left" in w for w in preview.warnings))

    def test_a_leftover_half_day_still_pays_a_later_half_day(self):
        StaffLeaveBalance.objects.update(total=D("0.5"))
        preview = self.preview(start_date=MON.isoformat(), end_date=TUE.isoformat(), **{f"part_{TUE}": "HALF_AM"})
        self.assertEqual(self.results(preview), {MON: RESULT_LWP, TUE: RESULT_PAID})

    def test_no_balance_row_counts_as_zero_and_says_so(self):
        StaffLeaveBalance.objects.all().delete()
        preview = self.preview()
        self.assertFalse(preview.has_balance)
        self.assertEqual(set(self.results(preview).values()), {RESULT_LWP})
        self.assertTrue(any("no CL balance for 2026-27" in w for w in preview.warnings))

    def test_the_balance_used_is_the_remaining_days_not_the_total(self):
        StaffLeaveBalance.objects.update(used=D("8"))
        preview = self.preview()
        self.assertEqual(preview.available, D("2"))
        self.assertEqual(preview.summary["paid"], D("2"))

    def test_another_leave_type_uses_its_own_balance(self):
        self.give(self.asha, "1", self.el)
        preview = self.preview(leave_type=str(self.el.pk))
        self.assertEqual((preview.summary["paid"], preview.summary["lwp"]), (D("1"), D("4")))

    def test_the_worked_example_from_the_plan(self):
        StaffLeaveBalance.objects.update(total=D("3.5"))
        preview = self.preview(
            start_date=THU.isoformat(), end_date=NEXT_TUE.isoformat(), **{f"part_{SAT}": "HALF_AM"}
        )
        self.assertEqual(preview.summary, {"dates": 6, "counted": D("4.5"), "paid": D("3.5"), "lwp": D("1.0")})
        self.assertEqual(self.results(preview)[NEXT_TUE], RESULT_LWP)

    def test_pending_applications_are_mentioned_but_do_not_reduce_the_estimate(self):
        LeaveApplication.objects.create(
            applicant=self.asha, academic_year=self.year, leave_type=self.cl, start_date=NEXT_MON,
            end_date=NEXT_MON, reason="x", status=Status.HOD_PENDING,
        )
        LeaveApplication.objects.create(
            applicant=self.asha, academic_year=self.year, leave_type=self.cl, start_date=NEXT_TUE,
            end_date=NEXT_TUE, reason="x", status=Status.APPROVED,
        )
        preview = self.preview()
        self.assertTrue(any("1 other CL application waiting for a decision" in w for w in preview.warnings))
        self.assertEqual(preview.available, D("10"))


class PreviewValidationTests(PreviewTestCase):
    def test_a_blank_form_reports_every_field(self):
        errors = self.errors(leave_type="", start_date="", end_date="", reason="")
        self.assertEqual(set(errors), {"leave_type", "start_date", "end_date", "reason"})
        self.assertEqual(errors["leave_type"], "Choose a valid leave type.")

    def test_bad_dates_and_a_reversed_range(self):
        self.assertEqual(self.errors(start_date="09/11/2026")["start_date"], "Start date must be a valid date.")
        self.assertEqual(
            self.errors(start_date=FRI.isoformat(), end_date=MON.isoformat())["end_date"],
            "The end date can't be before the start date.",
        )

    def test_reason_is_required_and_limited(self):
        self.assertEqual(self.errors(reason="   ")["reason"], "Reason is required.")
        self.assertIn("at most 500", self.errors(reason="x" * 501)["reason"])
        self.preview(reason="x" * 500)

    def test_inactive_and_unknown_leave_types_are_refused(self):
        self.el.is_active = False
        self.el.save()
        self.assertIn("leave_type", self.errors(leave_type=str(self.el.pk)))
        self.assertIn("leave_type", self.errors(leave_type="999"))

    def test_dates_must_fall_inside_the_active_year_and_the_edges_are_allowed(self):
        self.assertIn("Dates must fall inside the active academic year 2026-27",
                      self.errors(start_date="2026-05-31", end_date="2026-06-02")["start_date"])
        self.assertIn("end_date", self.errors(start_date="2027-05-30", end_date="2027-06-01"))
        self.give(self.asha, "5", self.el)
        self.preview(leave_type=str(self.el.pk), start_date="2026-06-01", end_date="2026-06-02")
        self.preview(leave_type=str(self.el.pk), start_date="2027-05-28", end_date="2027-05-31")

    def test_leave_across_two_years_is_refused_even_if_the_policy_flag_is_on(self):
        AcademicYear.objects.create(
            name="2027-28", start_date=datetime.date(2027, 6, 1), end_date=datetime.date(2028, 5, 31)
        )
        policy = PolicyConfig.get()
        policy.allow_cross_year_leave = True
        policy.save()
        self.assertIn("end_date", self.errors(start_date="2027-05-30", end_date="2027-06-02"))

    def test_no_active_year_blocks_applying(self):
        AcademicYear.objects.update(is_active=False)
        self.assertIn("No academic year is active yet", self.errors()["form"])

    def test_a_range_with_nothing_countable_is_refused(self):
        self.holiday(THU)
        message = "None of these dates count as leave."
        self.assertIn(message, self.errors(start_date=SUN.isoformat(), end_date=SUN.isoformat())["form"])
        self.assertIn(message, self.errors(start_date=THU.isoformat(), end_date=THU.isoformat())["form"])

    def test_a_request_longer_than_366_dates_is_refused(self):
        AcademicYear.objects.filter(pk=self.year.pk).update(
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2027, 12, 31)
        )
        self.assertIn("at most 366", self.errors(start_date="2026-01-01", end_date="2027-01-05")["end_date"])


class PreviewOverlapTests(PreviewTestCase):
    def live_leave(self, profile, day, status=Status.APPROVED, blocks=True):
        application = LeaveApplication.objects.create(
            applicant=profile, academic_year=self.year, leave_type=self.cl, start_date=day, end_date=day,
            reason="x", status=status,
        )
        LeaveDay.objects.create(leave=application, staff=profile, date=day, blocks_overlap=blocks)
        return application

    def test_an_existing_live_leave_blocks_and_is_named(self):
        existing = self.live_leave(self.asha, WED)
        message = self.errors()["start_date"]
        self.assertIn("You already have leave on Wed 11 Nov 2026", message)
        self.assertIn(f"application #{existing.pk}, approved", message)

    def test_rejected_or_cancelled_days_do_not_block(self):
        self.live_leave(self.asha, WED, Status.REJECTED, blocks=False)
        self.preview()

    def test_someone_elses_leave_does_not_block(self):
        self.live_leave(make_staff("ravi", self.physics), WED)
        self.preview()

    def test_leave_outside_the_range_does_not_block(self):
        self.live_leave(self.asha, NEXT_TUE)
        self.preview()

    def test_a_sunday_inside_another_live_leave_still_blocks(self):
        self.live_leave(self.asha, SUN)
        self.assertIn("Sun 15 Nov 2026", self.errors(start_date=SAT.isoformat(), end_date=NEXT_MON.isoformat())["start_date"])

    def test_many_clashes_are_summarised(self):
        for day in (MON, TUE, WED, THU, FRI):
            self.live_leave(self.asha, day)
        message = self.errors()["start_date"]
        self.assertIn("and 2 more dates", message)


class PreviewBlockerTests(PreviewTestCase):
    def blocker(self, profile):
        return applications.application_blocker(profile)

    def test_nothing_blocks_a_normal_member(self):
        self.assertIsNone(self.blocker(self.asha))
        self.assertIsNone(self.blocker(self.hod))
        self.assertIsNone(self.blocker(self.principal))

    def test_a_department_without_a_head_blocks_its_staff_only(self):
        self.physics.hod = None
        self.physics.save()
        self.asha.refresh_from_db()
        self.assertIn("Physics has no head of department yet", self.blocker(self.asha))
        self.assertIn("form", self.errors())
        self.assertIsNone(self.blocker(self.principal))

    def test_an_inactive_head_blocks_the_departments_staff(self):
        self.hod.is_active = False
        self.hod.save()
        self.asha.refresh_from_db()
        self.assertIn("no head of department", self.blocker(self.asha))

    def test_without_an_active_principal_nobody_else_can_apply(self):
        self.principal.is_active = False
        self.principal.save()
        for person in (self.asha, self.hod):
            self.assertIn("no active Principal yet", self.blocker(person))

    def test_the_principal_cannot_apply_when_automatic_approval_is_switched_off(self):
        policy = PolicyConfig.get()
        policy.principal_bypass_stages = False
        policy.save()
        self.assertIn("switched off in the policy settings", self.blocker(self.principal))
        self.assertIn("form", self.errors(self.principal.user))

    def test_no_active_year_blocks_everyone(self):
        AcademicYear.objects.update(is_active=False)
        for person in (self.asha, self.hod, self.principal):
            self.assertIn("No academic year is active", self.blocker(person))


class PreviewRolesTests(PreviewTestCase):
    def test_workload_choice_and_starting_status_by_role(self):
        policy = PolicyConfig.get()
        cases = [
            (self.asha, {}, True, Status.WORKLOAD_PENDING),
            (self.asha, {"requires_workload": ""}, False, Status.HOD_PENDING),
            (self.hod, {"requires_workload": ""}, False, Status.PRINCIPAL_PENDING),
            (self.hod, {}, True, Status.WORKLOAD_PENDING),
            (self.principal, {}, False, Status.PRINCIPAL_PENDING),  # a ticked box is ignored for the Principal
        ]
        for person, extra, workload, status in cases:
            data = self.payload(**extra)
            if not extra.get("requires_workload", "x"):
                data.pop("requires_workload")
            preview = applications.build_preview(user=person.user, data=data)
            self.assertEqual((preview.requires_workload, preview.status), (workload, status), person)
        policy.hod_own_leave_route = "PRINCIPAL_AS_HOD"
        policy.save()
        data = self.payload()
        data.pop("requires_workload")
        self.assertEqual(applications.build_preview(user=self.hod.user, data=data).status, Status.HOD_PENDING)

    def test_the_principals_preview_mentions_the_skipped_stages(self):
        preview = self.preview(self.principal.user)
        self.assertTrue(any("skips the workload and head-of-department stages" in w for w in preview.warnings))

    def test_the_applicant_always_comes_from_the_login(self):
        other = make_staff("ravi", self.maths)
        preview = self.preview(applicant=str(other.pk), staff=str(other.pk))
        self.assertEqual(preview.applicant, self.asha)

    def test_only_active_staff_profiles_may_use_it(self):
        with self.assertRaises(PermissionDenied):
            applications.build_preview(user=make_admin(), data=self.payload())
        get_user_model().objects.create_user("plain", password="x")
        with self.assertRaises(PermissionDenied):
            applications.build_preview(user=get_user_model().objects.get(username="plain"), data=self.payload())
        self.asha.is_active = False
        self.asha.save()
        with self.assertRaises(PermissionDenied):
            self.preview()
