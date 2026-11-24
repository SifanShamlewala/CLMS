from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from academics.models import PolicyConfig
from core.testutils import make_admin
from core.validation import DomainError
from leaves import approvals
from leaves.models import LeaveApplication, LeaveApprovalLog, LeaveDay, LeaveWorkload, StaffLeaveBalance
from leaves.testutils import MON, ApprovalTestCase

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action
W = LeaveWorkload.Status


class HodApproveTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()

    def test_approving_sends_the_leave_to_the_principal(self):
        leave = self.decide(self.leave, self.hod, "approve", "Fine by me")
        self.assertEqual(leave.status, Status.PRINCIPAL_PENDING)
        self.assertEqual(self.refreshed(self.leave).status, Status.PRINCIPAL_PENDING)

    def test_the_decision_is_written_to_the_log_with_the_remarks(self):
        self.decide(self.leave, self.hod, "approve", "Fine by me")
        row = LeaveApprovalLog.objects.get()
        self.assertEqual((row.leave, row.action, row.remarks, row.action_by),
                         (self.leave, Action.HOD_APPROVED, "Fine by me", self.hod.user))

    def test_remarks_are_optional_but_limited(self):
        self.decide(self.leave, self.hod, "approve", "")
        leave = self.apply(self.ben, requires_workload="")
        with self.assertRaises(DomainError) as caught:
            self.decide(leave, self.hod, "approve", "x" * 301)
        self.assertIn("at most 300", caught.exception.errors["remarks"])
        self.assertEqual(self.refreshed(leave).status, Status.HOD_PENDING)

    def test_nothing_else_changes(self):
        self.decide(self.leave, self.hod, "approve")
        self.assertIsNone(self.refreshed(self.leave).decided_at)
        days = self.leave.days.all()
        self.assertEqual({d.status for d in days}, {LeaveDay.Status.PENDING})
        self.assertFalse(days.filter(blocks_overlap=False).exists())
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, 0)

    def test_the_whole_chain_for_a_staff_leave_with_cover(self):
        leave = self.apply(self.ben, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assertEqual(leave.status, Status.WORKLOAD_PENDING)
        self.assign(leave, self.chitra)
        from leaves import workload
        workload.decide_workload(
            workload=self.rows(leave).get(), decision="accept", remarks="", actor=self.chitra.user
        )
        self.assertEqual(self.refreshed(leave).status, Status.HOD_PENDING)
        self.decide(leave, self.hod, "approve")
        self.assertEqual(self.refreshed(leave).status, Status.PRINCIPAL_PENDING)


class RejectTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()

    def test_a_head_rejection_rejects_the_leave(self):
        leave = self.decide(self.leave, self.hod, "reject", "Exams that week")
        self.assertEqual(leave.status, Status.REJECTED)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, Status.REJECTED)
        self.assertIsNotNone(self.leave.decided_at)

    def test_the_reason_is_logged(self):
        self.decide(self.leave, self.hod, "reject", "Exams that week")
        row = LeaveApprovalLog.objects.get()
        self.assertEqual((row.action, row.remarks, row.action_by), (Action.HOD_REJECTED, "Exams that week", self.hod.user))

    def test_a_reason_is_required(self):
        for blank in ("", "   ", None):
            with self.assertRaises(DomainError) as caught:
                self.decide(self.leave, self.hod, "reject", blank)
            self.assertEqual(caught.exception.errors["remarks"], "Remarks is required.")
        with self.assertRaises(DomainError):
            self.decide(self.leave, self.hod, "reject", "x" * 301)
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)
        self.assertEqual(LeaveApprovalLog.objects.count(), 0)

    def test_the_dates_are_free_again(self):
        self.decide(self.leave, self.hod, "reject", "No")
        days = self.leave.days.all()
        self.assertEqual({d.status for d in days}, {LeaveDay.Status.REJECTED})
        self.assertFalse(days.filter(blocks_overlap=True).exists())
        again = self.apply(requires_workload="")
        self.assertNotEqual(again.pk, self.leave.pk)

    def test_balances_are_untouched(self):
        self.decide(self.leave, self.hod, "reject", "No")
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, 0)

    def test_accepted_cover_is_cancelled_but_kept(self):
        leave = self.apply(self.ben, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(leave, self.chitra)
        from leaves import workload
        workload.decide_workload(workload=self.rows(leave).get(), decision="accept", remarks="", actor=self.chitra.user)
        self.decide(leave, self.hod, "reject", "No")
        row = self.rows(leave).get()
        self.assertEqual((row.status, row.is_deleted), (W.CANCELLED, False))

    def test_the_principal_can_reject_at_the_final_stage(self):
        leave = self.pending_principal(self.ben)
        result = self.decide(leave, self.principal, "reject", "Not this month")
        self.assertEqual(result.status, Status.REJECTED)
        self.assertEqual(self.logs(leave), [Action.HOD_APPROVED, Action.PRINCIPAL_REJECTED])
        self.assertFalse(leave.days.filter(blocks_overlap=True).exists())


class WhoMayDecideTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()

    def refuse(self, leave, person_or_user, decision="approve", remarks="x"):
        user = getattr(person_or_user, "user", person_or_user)
        with self.assertRaises(PermissionDenied):
            approvals.decide_leave(application=leave, decision=decision, remarks=remarks, actor=user)
        self.assertEqual(self.refreshed(leave).status, leave.status)

    def test_staff_cannot_decide(self):
        for person in (self.ben, self.asha):
            self.refuse(self.leave, person)

    def test_nobody_without_a_profile_can_decide(self):
        self.refuse(self.leave, make_admin())
        self.refuse(self.leave, AnonymousUser())

    def test_another_departments_head_cannot_decide(self):
        self.refuse(self.leave, self.maths_hod)
        self.refuse(self.leave, self.maths_hod, "reject")

    def test_the_principal_cannot_decide_a_staff_leave_at_the_head_stage(self):
        self.refuse(self.leave, self.principal)

    def test_an_inactive_head_cannot_decide(self):
        self.hod.is_active = False
        self.hod.save()
        self.refuse(self.leave, self.hod)

    def test_a_head_cannot_decide_at_the_principal_stage(self):
        leave = self.pending_principal(self.ben)
        self.refuse(leave, self.hod, "reject", "No")

    def test_a_head_cannot_approve_their_own_leave(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        leave = self.apply(self.hod, requires_workload="")
        self.assertEqual(leave.status, Status.HOD_PENDING)
        self.refuse(leave, self.hod)
        self.refuse(leave, self.hod, "reject")
        self.refuse(leave, self.maths_hod)  # another head is not the Principal

    def test_the_principal_cannot_decide_their_own_leave(self):
        leave = self.apply(self.principal, requires_workload="")
        # It is approved on submission; force it back to the final stage to prove the rule itself.
        LeaveApplication.objects.filter(pk=leave.pk).update(status=Status.PRINCIPAL_PENDING)
        leave.refresh_from_db()
        self.refuse(leave, self.principal, "reject")
        self.refuse(leave, self.principal, "approve")


class PrincipalActingAsHeadTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        self.leave = self.apply(self.hod, requires_workload="")

    def test_the_principal_decides_at_the_head_stage_for_a_heads_leave(self):
        self.decide(self.leave, self.principal, "approve", "OK")
        self.assertEqual(self.refreshed(self.leave).status, Status.PRINCIPAL_PENDING)
        self.assertEqual(self.logs(self.leave), [Action.HOD_APPROVED])

    def test_then_the_principal_decides_again_at_the_final_stage(self):
        self.decide(self.leave, self.principal, "approve")
        self.decide(self.leave, self.principal, "reject", "On reflection, no")
        self.assertEqual(self.refreshed(self.leave).status, Status.REJECTED)
        self.assertEqual(self.logs(self.leave), [Action.HOD_APPROVED, Action.PRINCIPAL_REJECTED])

    def test_the_principal_can_reject_at_the_head_stage(self):
        self.decide(self.leave, self.principal, "reject", "No")
        self.assertEqual(self.logs(self.leave), [Action.HOD_REJECTED])

    def test_a_policy_change_after_submitting_does_not_strand_the_leave(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.SKIP_TO_PRINCIPAL
        policy.save()
        self.decide(self.leave, self.principal, "approve")
        self.assertEqual(self.refreshed(self.leave).status, Status.PRINCIPAL_PENDING)


class StateGuardTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()

    def test_a_leave_at_any_other_status_cannot_be_decided(self):
        for status in (Status.WORKLOAD_PENDING, Status.APPROVED, Status.REJECTED, Status.CANCELLED):
            LeaveApplication.objects.filter(pk=self.leave.pk).update(status=status)
            with self.assertRaises(DomainError) as caught:
                self.decide(self.leave, self.hod, "approve")
            self.assertIn("no longer waiting", caught.exception.errors["form"])
        self.assertEqual(LeaveApprovalLog.objects.count(), 0)

    def test_a_removed_application_cannot_be_decided(self):
        LeaveApplication.objects.filter(pk=self.leave.pk).update(is_deleted=True)
        with self.assertRaises(DomainError):
            self.decide(self.leave, self.hod, "approve")

    def test_a_second_decision_on_the_same_stage_is_refused(self):
        """A double click: the second request arrives after the first has finished."""
        self.decide(self.leave, self.hod, "approve")
        with self.assertRaises(PermissionDenied):  # the head cannot act at the Principal stage
            self.decide(self.leave, self.hod, "approve")
        leave = self.apply(self.ben, requires_workload="")
        self.decide(leave, self.hod, "reject", "No")
        with self.assertRaises(DomainError):
            self.decide(leave, self.hod, "reject", "No")
        self.assertEqual(self.logs(leave), [Action.HOD_REJECTED])

    def test_two_reviewers_cannot_both_win(self):
        self.decide(self.leave, self.hod, "reject", "No")
        with self.assertRaises(DomainError):
            self.decide(self.leave, self.hod, "approve")
        self.assertEqual(self.refreshed(self.leave).status, Status.REJECTED)

    def test_the_answer_must_be_approve_or_reject(self):
        for value in ("", "maybe", None, "APPROVE"):
            with self.assertRaises(DomainError) as caught:
                self.decide(self.leave, self.hod, value)
            self.assertIn("decision", caught.exception.errors)
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)

    def test_the_leave_row_is_locked_first(self):
        """SQLite ignores row locks, so this only proves the call is made (PostgreSQL honours it)."""
        with mock.patch.object(
            LeaveApplication.objects, "select_for_update", wraps=LeaveApplication.objects.select_for_update
        ) as locked:
            self.decide(self.leave, self.hod, "approve")
        locked.assert_called_once()

    def test_a_failure_part_way_leaves_everything_as_it_was(self):
        with mock.patch("leaves.approvals._log", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.decide(self.leave, self.hod, "reject", "No")
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, Status.HOD_PENDING)
        self.assertTrue(self.leave.days.filter(blocks_overlap=True).exists())
