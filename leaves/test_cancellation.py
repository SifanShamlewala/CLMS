from decimal import Decimal as D

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from core.testutils import make_admin
from core.validation import DomainError
from leaves import cancellation, workload
from leaves.models import (
    BalanceTransaction, LeaveApplication, LeaveApprovalLog, LeaveDay, LeaveWorkload, StaffLeaveBalance,
)
from leaves.testutils import MON, TUE, WED, ApprovalTestCase

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action
W = LeaveWorkload.Status


class CancelTestCase(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())  # needs cover

    def cancel(self, leave=None, remarks=""):
        """The applicant (Asha) cancels."""
        return cancellation.cancel_leave(application=leave or self.leave, remarks=remarks, actor=self.asha.user)


class CancelTests(CancelTestCase):
    def test_the_applicant_can_cancel_before_anyone_has_acted(self):
        leave = self.cancel(remarks="Plans changed")
        self.assertEqual(leave.status, Status.CANCELLED)
        self.assertEqual(self.refreshed(self.leave).status, Status.CANCELLED)
        self.assertIsNotNone(self.leave.decided_at)

    def test_it_is_logged_with_the_reason(self):
        self.cancel(remarks="Plans changed")
        row = LeaveApprovalLog.objects.get()
        self.assertEqual((row.action, row.remarks, row.action_by), (Action.LEAVE_CANCELLED, "Plans changed", self.asha.user))

    def test_the_reason_is_optional_but_limited(self):
        with self.assertRaises(DomainError) as caught:
            self.cancel(remarks="x" * 301)
        self.assertIn("at most 300", caught.exception.errors["remarks"])
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)
        self.cancel(remarks="")

    def test_the_dates_are_free_again(self):
        self.cancel()
        days = self.leave.days.all()
        self.assertEqual({d.status for d in days}, {LeaveDay.Status.CANCELLED})
        self.assertFalse(days.filter(blocks_overlap=True).exists())
        again = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())
        self.assertNotEqual(again.pk, self.leave.pk)

    def test_the_balance_is_untouched(self):
        self.cancel()
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, 0)
        self.assertEqual(BalanceTransaction.objects.count(), 0)

    def test_open_cover_requests_are_cancelled_but_kept(self):
        self.assign(self.leave, self.ben, [MON, TUE])
        self.cancel()
        rows = list(self.rows(self.leave))
        self.assertEqual([(r.status, r.is_deleted) for r in rows], [(W.CANCELLED, False)] * 2)
        self.assertTrue(all(r.decided_at for r in rows))

    def test_a_withdrawn_request_does_not_count_as_action(self):
        self.assign(self.leave, self.ben, [MON])
        workload.remove_workload(workload=self.rows(self.leave).get(), actor=self.asha.user)
        self.cancel()

    def test_a_colleague_cannot_answer_a_cancelled_request(self):
        self.assign(self.leave, self.ben, [MON])
        self.cancel()
        with self.assertRaises(DomainError):
            workload.decide_workload(
                workload=self.rows(self.leave).get(), decision="accept", remarks="", actor=self.ben.user
            )


class CancelSafetyTests(CancelTestCase):
    def test_the_leave_row_is_locked_first(self):
        """SQLite ignores row locks, so this only proves the call is made (PostgreSQL honours it)."""
        from unittest import mock
        with mock.patch.object(
            LeaveApplication.objects, "select_for_update", wraps=LeaveApplication.objects.select_for_update
        ) as locked:
            self.cancel()
        locked.assert_called_once()

    def test_a_failure_part_way_leaves_everything_as_it_was(self):
        from unittest import mock
        self.assign(self.leave, self.ben, [MON])
        with mock.patch("leaves.cancellation.LeaveApprovalLog.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.cancel()
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)
        self.assertTrue(self.leave.days.filter(blocks_overlap=True).exists())
        self.assertEqual(self.rows(self.leave).get().status, W.PENDING)


class WhenCancellingIsRefused(CancelTestCase):
    def refused(self, leave=None):
        with self.assertRaises(DomainError) as caught:
            self.cancel(leave)
        self.assertIn("can no longer be cancelled", caught.exception.errors["form"])

    def test_not_once_a_colleague_has_accepted(self):
        self.assign(self.leave, self.ben, [MON])
        self.assign(self.leave, self.chitra, [TUE])
        workload.decide_workload(
            workload=self.rows(self.leave, assigned_staff=self.ben).get(), decision="accept", remarks="", actor=self.ben.user
        )
        self.refused()
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)
        self.assertEqual({r.status for r in self.rows(self.leave)}, {W.ACCEPTED, W.PENDING})

    def test_not_once_it_has_moved_on(self):
        for status in (Status.HOD_PENDING, Status.PRINCIPAL_PENDING, Status.APPROVED, Status.REJECTED, Status.CANCELLED):
            LeaveApplication.objects.filter(pk=self.leave.pk).update(status=status)
            self.refused(self.refreshed(self.leave))

    def test_a_leave_that_needed_no_cover_cannot_be_cancelled_by_the_applicant(self):
        leave = self.pending_hod(self.ben)  # starts at the head stage
        with self.assertRaises(DomainError):
            cancellation.cancel_leave(application=leave, remarks="", actor=self.ben.user)

    def test_not_a_removed_application(self):
        LeaveApplication.objects.filter(pk=self.leave.pk).update(is_deleted=True)
        self.refused(self.refreshed(self.leave))

    def test_twice(self):
        self.cancel()
        self.refused(self.refreshed(self.leave))
        self.assertEqual(LeaveApprovalLog.objects.count(), 1)


class WhoMayCancelTests(CancelTestCase):
    def test_only_the_applicant(self):
        for person in (self.ben, self.hod, self.maths_hod, self.principal):
            with self.assertRaises(PermissionDenied):
                cancellation.cancel_leave(application=self.leave, remarks="", actor=person.user)
        with self.assertRaises(PermissionDenied):
            cancellation.cancel_leave(application=self.leave, remarks="", actor=make_admin())
        with self.assertRaises(PermissionDenied):
            cancellation.cancel_leave(application=self.leave, remarks="", actor=AnonymousUser())
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)

    def test_an_inactive_applicant_cannot(self):
        self.asha.is_active = False
        self.asha.save()
        with self.assertRaises(PermissionDenied):
            cancellation.cancel_leave(application=self.leave, remarks="", actor=self.asha.user)

    def test_the_no_action_rule_in_one_place(self):
        self.assertTrue(cancellation.no_action_taken(self.leave))
        self.assign(self.leave, self.ben, [MON])
        self.assertTrue(cancellation.no_action_taken(self.leave))
        LeaveWorkload.objects.update(status=W.ACCEPTED)
        self.assertFalse(cancellation.no_action_taken(self.leave))
