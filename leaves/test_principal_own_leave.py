from decimal import Decimal as D
from unittest import mock

from django.urls import reverse

from core.validation import DomainError
from leaves import applications
from leaves.models import BalanceTransaction, LeaveApplication, LeaveApprovalLog, LeaveDay, StaffLeaveBalance
from leaves.testutils import FRI, MON, ApprovalTestCase

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action


class PrincipalOwnLeaveTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.give(self.principal, "10")

    def submit(self, **extra):
        return applications.submit_application(user=self.principal.user, data=self.payload(**extra))

    def test_it_is_approved_the_moment_it_is_submitted(self):
        leave = self.submit()
        leave.refresh_from_db()
        self.assertEqual(leave.status, Status.APPROVED)
        self.assertTrue(leave.is_principal_auto_approved)
        self.assertIsNotNone(leave.decided_at)

    def test_it_skips_cover_and_the_head_stage(self):
        leave = self.submit(requires_workload="on")
        self.assertFalse(leave.requires_workload)
        self.assertEqual(self.logs(leave), [Action.PRINCIPAL_AUTO_APPROVED])

    def test_the_balance_is_still_deducted_and_every_day_is_in_the_ledger(self):
        leave = self.submit()
        balance = StaffLeaveBalance.objects.get(staff=self.principal)
        self.assertEqual(balance.used, D("5"))
        self.assertEqual(BalanceTransaction.objects.filter(leave=leave).count(), 5)
        self.assertEqual({d.status for d in leave.days.all()}, {LeaveDay.Status.APPROVED})
        self.assertEqual(leave.balance_before, D("10"))
        self.assertEqual(leave.balance_after, D("5"))

    def test_a_short_balance_makes_the_later_days_unpaid(self):
        StaffLeaveBalance.objects.filter(staff=self.principal).update(used=D("8"))
        leave = self.submit()
        self.assertEqual((leave.total_days_paid, leave.total_days_lwp), (D("2"), D("3")))

    def test_the_log_names_the_principal_as_the_actor(self):
        leave = self.submit()
        row = LeaveApprovalLog.objects.get(leave=leave)
        self.assertEqual((row.action, row.action_by), (Action.PRINCIPAL_AUTO_APPROVED, self.principal.user))

    def test_overlap_is_still_refused(self):
        self.submit()
        with self.assertRaises(DomainError):
            self.submit()
        self.assertEqual(LeaveApplication.objects.filter(applicant=self.principal).count(), 1)

    def test_a_failure_in_the_approval_saves_nothing_at_all(self):
        with mock.patch.object(BalanceTransaction.objects, "bulk_create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual(LeaveApplication.objects.count(), 0)
        self.assertEqual(LeaveDay.objects.count(), 0)
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.principal).used, 0)
        self.submit()  # and trying again works

    def test_the_preview_says_it_will_be_approved_automatically(self):
        preview = applications.build_preview(user=self.principal.user, data=self.payload())
        self.assertTrue(preview.auto_approved)
        self.assertEqual(preview.status_label, "Approved automatically")
        self.assertTrue(any("approved automatically" in w for w in preview.warnings))
        self.assertTrue(any("skips the workload and head-of-department stages" in w for w in preview.warnings))

    def test_nobody_else_gets_automatic_approval(self):
        for person in (self.asha, self.hod):
            preview = applications.build_preview(user=person.user, data=self.payload(start_date="2026-12-07", end_date="2026-12-07"))
            self.assertFalse(preview.auto_approved)
        self.assertNotEqual(applications.build_preview(user=self.asha.user, data=self.payload(start_date="2026-12-07", end_date="2026-12-07")).status_label, "Approved automatically")

    def test_the_principals_own_leave_is_not_in_any_queue(self):
        self.submit()
        self.sign_in(self.principal)
        page = self.client.get(reverse("principal_queue") + "?status=")
        self.assertEqual(len(page.context["page_obj"]), 0)

    def test_the_leave_page_shows_it_as_approved_with_the_balance_after(self):
        leave = self.submit()
        self.sign_in(self.principal)
        page = self.client.get(reverse("leave_detail", args=[leave.pk]))
        self.assertContains(page, "Balance after approval")
        self.assertNotContains(page, "Your decision")
        self.assertNotContains(page, "Cancel application")
