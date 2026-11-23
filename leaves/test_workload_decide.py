from unittest import mock

from django.core.exceptions import PermissionDenied

from academics.models import PolicyConfig
from core.testutils import make_admin
from core.validation import DomainError
from leaves import workload
from leaves.models import LeaveApplication, LeaveDay, LeaveWorkload
from leaves.testutils import MON, TUE, WED, WorkloadTestCase

Status = LeaveApplication.Status
W = LeaveWorkload.Status


class DecideTestCase(WorkloadTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())

    def row(self, person, day):
        return self.rows(self.application, assigned_staff=person, date=day).get()

    def accept(self, person, day, remarks="", actor=None):
        return workload.decide_workload(
            workload=self.row(person, day), decision="accept", remarks=remarks, actor=(actor or person).user
        )

    def reject(self, person, day, remarks="Teaching a class that day", actor=None):
        return workload.decide_workload(
            workload=self.row(person, day), decision="reject", remarks=remarks, actor=(actor or person).user
        )

    def status(self):
        self.application.refresh_from_db()
        return self.application.status


class AcceptTests(DecideTestCase):
    def test_accepting_marks_the_row_with_remarks_and_time(self):
        self.assign(self.application, self.ben)
        self.accept(self.ben, MON, "Happy to help")
        row = self.row(self.ben, MON)
        self.assertEqual((row.status, row.remarks), (W.ACCEPTED, "Happy to help"))
        self.assertIsNotNone(row.decided_at)

    def test_the_leave_waits_until_every_date_is_covered(self):
        self.assign(self.application, self.ben, [MON, TUE])
        self.accept(self.ben, MON)
        self.accept(self.ben, TUE)
        self.assertEqual(self.status(), Status.WORKLOAD_PENDING)  # Wednesday has nobody

    def test_a_date_with_nobody_assigned_blocks_the_leave(self):
        self.assign(self.application, self.ben, [MON, TUE])
        self.accept(self.ben, MON)
        self.accept(self.ben, TUE)
        self.assertFalse(workload.workload_complete(self.application))
        self.assertEqual(workload.cover_summary(self.application), (3, 2))

    def test_the_leave_moves_on_when_every_date_is_accepted(self):
        self.assign(self.application, self.ben, [MON, TUE])
        self.assign(self.application, self.chitra, [WED])
        self.accept(self.ben, MON)
        self.accept(self.ben, TUE)
        self.assertEqual(self.status(), Status.WORKLOAD_PENDING)
        leave = self.accept(self.chitra, WED)
        self.assertEqual(leave.status, Status.HOD_PENDING)
        self.assertEqual(self.status(), Status.HOD_PENDING)

    def test_every_assignee_on_a_date_must_accept(self):
        self.assign(self.application, self.ben)
        self.assign(self.application, self.chitra, [MON])
        for day in (MON, TUE, WED):
            self.accept(self.ben, day)
        self.assertEqual(self.status(), Status.WORKLOAD_PENDING)  # Chitra has not answered on Monday
        self.accept(self.chitra, MON)
        self.assertEqual(self.status(), Status.HOD_PENDING)

    def test_a_heads_own_leave_skips_the_head_stage_by_default(self):
        application = self.apply(self.hod, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(application, self.ben)
        workload.decide_workload(
            workload=self.rows(application).get(), decision="accept", remarks="", actor=self.ben.user
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Status.PRINCIPAL_PENDING)

    def test_a_heads_own_leave_goes_to_the_head_stage_when_the_policy_says_so(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        application = self.apply(self.hod, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(application, self.ben)
        workload.decide_workload(
            workload=self.rows(application).get(), decision="accept", remarks="", actor=self.ben.user
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Status.HOD_PENDING)

    def test_accepting_does_not_touch_balances_or_days(self):
        self.assign(self.application, self.ben)
        for day in (MON, TUE, WED):
            self.accept(self.ben, day)
        self.assertEqual(self.asha.leave_balances.get().used, 0)
        self.assertFalse(self.application.days.exclude(status=LeaveDay.Status.PENDING).exists())
        self.assertFalse(self.application.days.filter(blocks_overlap=False).exists())

    def test_remarks_are_optional_but_limited(self):
        self.assign(self.application, self.ben)
        self.accept(self.ben, MON, "")
        with self.assertRaises(DomainError) as caught:
            self.accept(self.ben, TUE, "x" * 301)
        self.assertIn("at most 300", caught.exception.errors["remarks"])
        self.assertEqual(self.row(self.ben, TUE).status, W.PENDING)

    def test_cannot_accept_a_date_on_which_the_colleague_is_now_on_leave(self):
        self.assign(self.application, self.ben)
        self.apply(self.ben, start_date=TUE.isoformat(), end_date=TUE.isoformat())
        with self.assertRaises(DomainError) as caught:
            self.accept(self.ben, TUE)
        self.assertIn("on leave on Tue 10 Nov", caught.exception.errors["form"])
        self.assertEqual(self.row(self.ben, TUE).status, W.PENDING)
        self.accept(self.ben, MON)  # other dates are unaffected

    def test_a_clash_can_still_be_answered_by_rejecting(self):
        self.assign(self.application, self.ben)
        self.apply(self.ben, start_date=TUE.isoformat(), end_date=TUE.isoformat())
        self.reject(self.ben, TUE, "I am on leave")
        self.assertEqual(self.status(), Status.REJECTED)


class RejectTests(DecideTestCase):
    def setUp(self):
        super().setUp()
        self.assign(self.application, self.ben, [MON, TUE])
        self.assign(self.application, self.chitra, [MON, WED])
        self.accept(self.ben, MON)

    def test_a_rejection_rejects_the_whole_leave(self):
        leave = self.reject(self.chitra, WED)
        self.assertEqual(leave.status, Status.REJECTED)
        self.assertEqual(self.status(), Status.REJECTED)
        self.assertIsNotNone(self.application.decided_at)

    def test_the_rejecting_row_keeps_the_reason(self):
        self.reject(self.chitra, WED, "Exam duty")
        row = self.row(self.chitra, WED)
        self.assertEqual((row.status, row.remarks), (W.REJECTED, "Exam duty"))
        self.assertIsNotNone(row.decided_at)

    def test_a_reason_is_required(self):
        for blank in ("", "   ", None):
            with self.assertRaises(DomainError) as caught:
                self.reject(self.chitra, WED, blank)
            self.assertEqual(caught.exception.errors["remarks"], "Remarks is required.")
        self.assertEqual(self.status(), Status.WORKLOAD_PENDING)
        self.assertEqual(self.row(self.chitra, WED).status, W.PENDING)

    def test_the_reason_is_limited_to_300_characters(self):
        with self.assertRaises(DomainError):
            self.reject(self.chitra, WED, "x" * 301)
        self.assertEqual(self.status(), Status.WORKLOAD_PENDING)

    def test_the_dates_are_free_again_and_the_days_rejected(self):
        self.reject(self.chitra, WED)
        days = self.application.days.all()
        self.assertEqual({d.status for d in days}, {LeaveDay.Status.REJECTED})
        self.assertFalse(days.filter(blocks_overlap=True).exists())
        again = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())  # can apply again
        self.assertNotEqual(again.pk, self.application.pk)

    def test_other_open_requests_are_cancelled_not_erased(self):
        self.reject(self.chitra, WED)
        statuses = {(r.assigned_staff.user.username, r.date): r.status for r in self.rows(self.application)}
        self.assertEqual(statuses[("ben", MON)], W.CANCELLED)   # was accepted, leave is now dead
        self.assertEqual(statuses[("ben", TUE)], W.CANCELLED)
        self.assertEqual(statuses[("chitra", MON)], W.CANCELLED)
        self.assertEqual(statuses[("chitra", WED)], W.REJECTED)
        self.assertEqual(self.rows(self.application, is_deleted=False).count(), 4)

    def test_a_cancelled_row_cannot_be_answered(self):
        self.reject(self.chitra, WED)
        with self.assertRaises(DomainError):
            self.accept(self.ben, TUE)

    def test_the_applicant_can_ask_the_same_colleague_on_a_new_application(self):
        self.reject(self.chitra, WED)
        again = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())
        self.assign(again, self.chitra)  # the old cancelled rows do not count as duplicates


class DecideGuardTests(DecideTestCase):
    def setUp(self):
        super().setUp()
        self.assign(self.application, self.ben)

    def test_only_the_person_asked_may_answer(self):
        for person in (self.chitra, self.asha, self.hod, self.principal):
            with self.assertRaises(PermissionDenied):
                self.accept(self.ben, MON, actor=person)
        with self.assertRaises(PermissionDenied):
            workload.decide_workload(
                workload=self.row(self.ben, MON), decision="accept", remarks="", actor=make_admin()
            )
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)

    def test_an_inactive_colleague_cannot_answer(self):
        self.ben.is_active = False
        self.ben.save()
        with self.assertRaises(PermissionDenied):
            self.accept(self.ben, MON)

    def test_an_answered_request_cannot_be_answered_again(self):
        self.accept(self.ben, MON)
        for call in (self.accept, self.reject):
            with self.assertRaises(DomainError):
                call(self.ben, MON)

    def test_a_withdrawn_request_cannot_be_answered(self):
        row = self.row(self.ben, MON)
        workload.remove_workload(workload=row, actor=self.asha.user)
        with self.assertRaises(DomainError):
            self.accept(self.ben, MON)

    def test_not_once_the_leave_has_left_the_workload_stage(self):
        LeaveApplication.objects.filter(pk=self.application.pk).update(status=Status.HOD_PENDING)
        with self.assertRaises(DomainError) as caught:
            self.accept(self.ben, MON)
        self.assertIn("no longer waiting", caught.exception.errors["form"])

    def test_not_for_a_removed_application(self):
        LeaveApplication.objects.filter(pk=self.application.pk).update(is_deleted=True)
        with self.assertRaises(DomainError):
            self.accept(self.ben, MON)

    def test_the_answer_must_be_accept_or_reject(self):
        for value in ("", "maybe", None, "ACCEPT"):
            with self.assertRaises(DomainError) as caught:
                workload.decide_workload(
                    workload=self.row(self.ben, MON), decision=value, remarks="", actor=self.ben.user
                )
            self.assertIn("decision", caught.exception.errors)
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)

    def test_the_leave_row_is_locked_first(self):
        """SQLite ignores row locks, so this only proves the call is made (PostgreSQL honours it)."""
        with mock.patch.object(
            LeaveApplication.objects, "select_for_update", wraps=LeaveApplication.objects.select_for_update
        ) as locked:
            self.accept(self.ben, MON)
        locked.assert_called_once()

    def test_a_failure_part_way_leaves_everything_as_it_was(self):
        with mock.patch("leaves.workload._advance_if_complete", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.accept(self.ben, MON)
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)
