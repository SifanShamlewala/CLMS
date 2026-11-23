from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError

from core.testutils import make_admin
from core.validation import DomainError
from leaves import workload
from leaves.models import LeaveApplication, LeaveWorkload
from leaves.testutils import FRI, MON, NEXT_MON, TUE, WED, WorkloadTestCase

Status = LeaveApplication.Status
W = LeaveWorkload.Status


class AssignTestCase(WorkloadTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.apply()

    def ask(self, colleague, dates, actor=None, application=None):
        data = {"assignee": str(colleague.pk) if colleague else "", "dates": [d.isoformat() for d in dates]}
        return workload.assign_workload(
            application=application or self.application, data=data, actor=actor or self.asha.user
        )

    def errors(self, *args, **kwargs):
        with self.assertRaises(DomainError) as caught:
            self.ask(*args, **kwargs)
        return caught.exception.errors


class AssignHappyPathTests(AssignTestCase):
    def test_creates_one_pending_row_per_chosen_date(self):
        self.ask(self.ben, [MON, TUE])
        rows = list(self.rows(self.application))
        self.assertEqual([(r.date, r.assigned_staff, r.status) for r in rows],
                         [(MON, self.ben, W.PENDING), (TUE, self.ben, W.PENDING)])
        self.assertEqual({r.assigned_by for r in rows}, {self.asha.user})
        for row in rows:
            self.assertEqual(row.leave_day.date, row.date)

    def test_the_leave_stays_at_the_workload_stage(self):
        self.ask(self.ben, [MON])
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.WORKLOAD_PENDING)

    def test_two_colleagues_can_share_a_date(self):
        self.ask(self.ben, [MON])
        self.ask(self.chitra, [MON])
        self.assertEqual(self.rows(self.application, date=MON).count(), 2)

    def test_repeated_dates_in_the_request_are_saved_once(self):
        self.ask(self.ben, [MON, MON])
        self.assertEqual(self.rows(self.application).count(), 1)

    def test_a_head_or_the_principal_may_be_asked(self):
        self.ask(self.hod, [MON])
        self.ask(self.principal, [TUE])
        self.assertEqual(self.rows(self.application).count(), 2)

    def test_dict_input_with_a_single_string_date_works(self):
        workload.assign_workload(
            application=self.application, data={"assignee": str(self.ben.pk), "dates": MON.isoformat()},
            actor=self.asha.user,
        )
        self.assertEqual(self.rows(self.application).count(), 1)


class AssignRuleTests(AssignTestCase):
    def test_no_self_assignment(self):
        self.assertIn("yourself", self.errors(self.asha, [MON])["assignee"])
        self.assertEqual(self.rows(self.application).count(), 0)

    def test_no_duplicate_for_the_same_person_and_date(self):
        self.ask(self.ben, [MON])
        errors = self.errors(self.ben, [MON, TUE])
        self.assertIn("already covering", errors["assignee"])
        self.assertIn("Mon 09 Nov", errors["assignee"])
        self.assertEqual(self.rows(self.application).count(), 1)  # nothing from the failed request

    def test_an_inactive_colleague_cannot_be_chosen(self):
        self.ben.is_active = False
        self.ben.save()
        self.assertEqual(self.errors(self.ben, [MON])["assignee"], "Choose a valid colleague.")

    def test_a_colleague_whose_login_is_switched_off_cannot_be_chosen(self):
        self.ben.user.is_active = False
        self.ben.user.save()
        self.assertEqual(self.errors(self.ben, [MON])["assignee"], "Choose a valid colleague.")

    def test_unknown_or_blank_colleague(self):
        self.assertEqual(self.errors(None, [MON])["assignee"], "Choose a valid colleague.")
        data = {"assignee": "9999", "dates": [MON.isoformat()]}
        with self.assertRaises(DomainError) as caught:
            workload.assign_workload(application=self.application, data=data, actor=self.asha.user)
        self.assertIn("assignee", caught.exception.errors)

    def test_a_date_clash_with_the_colleagues_own_leave_is_refused(self):
        self.apply(self.ben, start_date=TUE.isoformat(), end_date=TUE.isoformat())
        errors = self.errors(self.ben, [MON, TUE])
        self.assertIn("on leave on Tue 10 Nov", errors["assignee"])
        self.assertEqual(self.rows(self.application).count(), 0)

    def test_a_rejected_leave_of_the_colleague_is_no_clash(self):
        other = self.apply(self.ben, start_date=TUE.isoformat(), end_date=TUE.isoformat())
        other.days.update(blocks_overlap=False, status="REJECTED")
        self.ask(self.ben, [TUE])

    def test_at_least_one_date_is_needed(self):
        self.assertIn("at least one date", self.errors(self.ben, [])["dates"])

    def test_dates_must_be_days_that_need_cover(self):
        self.assertIn("only dates from the list", self.errors(self.ben, [NEXT_MON])["dates"])

    def test_a_date_that_is_not_a_date_is_refused(self):
        with self.assertRaises(DomainError) as caught:
            workload.assign_workload(
                application=self.application, data={"assignee": str(self.ben.pk), "dates": ["nonsense"]},
                actor=self.asha.user,
            )
        self.assertIn("valid date", caught.exception.errors["dates"])

    def test_dates_that_are_not_counted_are_refused(self):
        self.holiday(WED, "Diwali")
        application = self.apply(self.hod, start_date=MON.isoformat(), end_date=FRI.isoformat(), requires_workload="on")
        # The holiday row exists on the leave but needs no cover.
        errors = self.errors(self.ben, [WED], actor=self.hod.user, application=application)
        self.assertIn("only dates from the list", errors["dates"])

    def test_all_errors_are_reported_together(self):
        errors = self.errors(None, [])
        self.assertEqual(set(errors), {"assignee", "dates"})


class AssignPermissionAndStateTests(AssignTestCase):
    def test_only_the_applicant_may_arrange_cover(self):
        for person in (self.ben, self.hod, self.principal):
            with self.assertRaises(PermissionDenied):
                self.ask(self.chitra, [MON], actor=person.user)
        with self.assertRaises(PermissionDenied):
            self.ask(self.chitra, [MON], actor=make_admin("root2"))
        self.assertEqual(self.rows(self.application).count(), 0)

    def test_anonymous_and_profileless_users_are_refused(self):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.auth import get_user_model
        with self.assertRaises(PermissionDenied):
            self.ask(self.ben, [MON], actor=AnonymousUser())
        loner = get_user_model().objects.create_user("loner", password="x")
        with self.assertRaises(PermissionDenied):
            self.ask(self.ben, [MON], actor=loner)

    def test_an_inactive_applicant_is_refused(self):
        self.asha.is_active = False
        self.asha.save()
        with self.assertRaises(PermissionDenied):
            self.ask(self.ben, [MON])

    def test_only_while_the_leave_waits_for_workload(self):
        for status in (Status.HOD_PENDING, Status.PRINCIPAL_PENDING, Status.APPROVED, Status.REJECTED, Status.CANCELLED):
            LeaveApplication.objects.filter(pk=self.application.pk).update(status=status)
            self.assertIn("no longer waiting", self.errors(self.ben, [MON])["form"])
        self.assertEqual(self.rows(self.application).count(), 0)

    def test_a_leave_that_needs_no_cover_takes_no_assignments(self):
        application = self.apply(self.hod, requires_workload="")
        self.assertFalse(application.requires_workload)
        LeaveApplication.objects.filter(pk=application.pk).update(status=Status.WORKLOAD_PENDING)
        application.refresh_from_db()
        self.assertIn("no longer waiting", self.errors(self.ben, [MON], actor=self.hod.user, application=application)["form"])

    def test_a_removed_application_takes_no_assignments(self):
        LeaveApplication.objects.filter(pk=self.application.pk).update(is_deleted=True)
        self.assertIn("no longer available", self.errors(self.ben, [MON])["form"])

    def test_the_leave_row_is_locked_first(self):
        """SQLite ignores row locks, so this only proves the call is made (PostgreSQL honours it)."""
        with mock.patch.object(
            LeaveApplication.objects, "select_for_update", wraps=LeaveApplication.objects.select_for_update
        ) as locked:
            self.ask(self.ben, [MON])
        locked.assert_called_once()

    def test_a_concurrent_duplicate_becomes_a_friendly_error(self):
        with mock.patch.object(LeaveWorkload.objects, "bulk_create", side_effect=IntegrityError("race")):
            self.assertIn("Reload the page", self.errors(self.ben, [MON])["form"])


class WithdrawTests(AssignTestCase):
    def setUp(self):
        super().setUp()
        self.ask(self.ben, [MON, TUE])
        self.mon_row = self.rows(self.application, date=MON).get()

    def withdraw(self, row=None, actor=None):
        return workload.remove_workload(workload=row or self.mon_row, actor=actor or self.asha.user)

    def test_a_pending_request_is_withdrawn_but_kept(self):
        self.withdraw()
        self.mon_row.refresh_from_db()
        self.assertTrue(self.mon_row.is_deleted)
        self.assertIsNotNone(self.mon_row.deleted_at)
        self.assertEqual(self.mon_row.status, W.PENDING)
        self.assertEqual(LeaveWorkload.objects.filter(pk=self.mon_row.pk).count(), 1)

    def test_the_same_person_can_be_asked_again_after_a_withdrawal(self):
        self.withdraw()
        self.ask(self.ben, [MON])
        self.assertEqual(self.rows(self.application, date=MON, is_deleted=False).count(), 1)

    def test_an_answered_request_cannot_be_withdrawn(self):
        LeaveWorkload.objects.filter(pk=self.mon_row.pk).update(status=W.ACCEPTED)
        with self.assertRaises(DomainError):
            self.withdraw()

    def test_a_withdrawn_request_cannot_be_withdrawn_twice(self):
        self.withdraw()
        with self.assertRaises(DomainError):
            self.withdraw()

    def test_only_the_applicant_may_withdraw(self):
        for person in (self.ben, self.hod, self.principal):
            with self.assertRaises(PermissionDenied):
                self.withdraw(actor=person.user)
        self.mon_row.refresh_from_db()
        self.assertFalse(self.mon_row.is_deleted)

    def test_not_after_the_workload_stage(self):
        LeaveApplication.objects.filter(pk=self.application.pk).update(status=Status.HOD_PENDING)
        with self.assertRaises(DomainError):
            self.withdraw()

    def test_withdrawing_the_last_unanswered_extra_request_completes_the_stage(self):
        # Ben has accepted every date; Chitra's extra request is still open.
        self.rows(self.application).update(status=W.ACCEPTED)
        self.ask(self.chitra, [MON])
        extra = self.rows(self.application, assigned_staff=self.chitra).get()
        for day in self.application.days.filter(workload_required=True, is_counted=True).exclude(date__in=[MON, TUE]):
            LeaveWorkload.objects.create(leave_day=day, assigned_staff=self.ben, date=day.date, status=W.ACCEPTED)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.WORKLOAD_PENDING)
        leave = self.withdraw(extra)
        self.assertEqual(leave.status, Status.HOD_PENDING)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.HOD_PENDING)
