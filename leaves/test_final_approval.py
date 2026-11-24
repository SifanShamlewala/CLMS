from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied

from core.validation import DomainError
from leaves.models import (
    BalanceTransaction, LeaveApplication, LeaveApprovalLog, LeaveDay, LeaveWorkload, StaffLeaveBalance,
)
from leaves.testutils import FRI, MON, SUN, TUE, WED, ApprovalTestCase

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action


class FinalApprovalCase(ApprovalTestCase):
    def balance(self, person=None):
        return StaffLeaveBalance.objects.get(staff=person or self.asha)

    def approve(self, leave, remarks=""):
        return self.decide(leave, self.principal, "approve", remarks)

    def set_remaining(self, remaining, person=None):
        balance = self.balance(person)
        balance.used = balance.total - D(remaining)
        balance.save()

    def ledger(self, leave):
        return list(BalanceTransaction.objects.filter(leave=leave).order_by("leave_day__date"))


class ApproveTests(FinalApprovalCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_principal()

    def test_the_leave_is_approved(self):
        leave = self.approve(self.leave, "Granted")
        self.assertEqual(leave.status, Status.APPROVED)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, Status.APPROVED)
        self.assertIsNotNone(self.leave.decided_at)
        self.assertFalse(self.leave.is_principal_auto_approved)

    def test_the_paid_days_are_deducted_once(self):
        self.approve(self.leave)
        self.assertEqual(self.balance().used, D("5"))
        self.assertEqual(self.balance().remaining, D("5"))

    def test_every_paid_day_is_a_ledger_row_with_a_chain(self):
        self.approve(self.leave, "Granted")
        rows = self.ledger(self.leave)
        self.assertEqual(len(rows), 5)
        self.assertEqual([r.type for r in rows], [BalanceTransaction.Type.DEDUCT] * 5)
        self.assertEqual([r.balance_before for r in rows], [D("10"), D("9"), D("8"), D("7"), D("6")])
        self.assertEqual([r.balance_after for r in rows], [D("9"), D("8"), D("7"), D("6"), D("5")])
        self.assertEqual({r.created_by for r in rows}, {self.principal.user})
        self.assertEqual({r.balance for r in rows}, {self.balance()})

    def test_the_days_and_the_snapshot_are_final(self):
        self.approve(self.leave)
        self.leave.refresh_from_db()
        days = self.leave.days.all()
        self.assertEqual({d.status for d in days}, {LeaveDay.Status.APPROVED})
        self.assertEqual({d.paid_deducted for d in days}, {D("1")})
        self.assertFalse(days.filter(is_lwp=True).exists())
        self.assertTrue(days.filter(blocks_overlap=True).count() == 5)  # approved days still hold the dates
        self.assertEqual((self.leave.total_days_paid, self.leave.total_days_lwp), (D("5"), D("0")))
        self.assertEqual(self.leave.balance_after, D("5"))
        self.assertEqual(self.leave.balance_before, D("10"))  # what the applicant saw is kept

    def test_the_decision_is_logged_with_the_remarks(self):
        self.approve(self.leave, "Granted")
        self.assertEqual(self.logs(self.leave), [Action.HOD_APPROVED, Action.PRINCIPAL_APPROVED])
        row = LeaveApprovalLog.objects.get(action=Action.PRINCIPAL_APPROVED)
        self.assertEqual((row.remarks, row.action_by), ("Granted", self.principal.user))

    def test_remarks_are_optional_but_limited(self):
        with self.assertRaises(DomainError) as caught:
            self.approve(self.leave, "x" * 301)
        self.assertIn("at most 300", caught.exception.errors["remarks"])
        self.assertEqual(self.refreshed(self.leave).status, Status.PRINCIPAL_PENDING)
        self.approve(self.leave, "")

    def test_sundays_and_holidays_are_approved_but_never_deducted(self):
        self.holiday(WED, "Diwali")
        leave = self.pending_principal(self.chitra, start_date=MON.isoformat(), end_date=SUN.isoformat())
        self.give(self.chitra, "10")
        self.approve(leave)
        days = {d.date: d for d in leave.days.all()}
        self.assertEqual(days[WED].status, LeaveDay.Status.APPROVED)
        self.assertEqual(days[WED].paid_deducted, 0)
        self.assertEqual(days[SUN].paid_deducted, 0)
        self.assertEqual(len(self.ledger(leave)), 5)
        self.assertEqual(self.balance(self.chitra).used, D("5"))

    def test_a_half_day_deducts_half(self):
        leave = self.pending_principal(self.chitra, start_date=MON.isoformat(), end_date=TUE.isoformat())
        self.give(self.chitra, "10")
        leave.days.filter(date=TUE).update(day_part="HALF_AM", fraction=D("0.5"))
        self.approve(leave)
        self.assertEqual(self.balance(self.chitra).used, D("1.5"))
        self.assertEqual([r.amount for r in self.ledger(leave)], [D("1.0"), D("0.5")])


class LeaveWithoutPayTests(FinalApprovalCase):
    def test_a_short_balance_pays_the_early_days_and_the_rest_are_unpaid(self):
        self.set_remaining("3")
        leave = self.pending_principal()
        self.approve(leave)
        days = list(leave.days.order_by("date"))
        self.assertEqual([d.is_lwp for d in days], [False, False, False, True, True])
        self.assertEqual([d.paid_deducted for d in days], [D("1")] * 3 + [D("0")] * 2)
        self.assertEqual(self.balance().used, self.balance().total)
        self.assertEqual(len(self.ledger(leave)), 3)  # leave without pay is never in the ledger
        leave.refresh_from_db()
        self.assertEqual((leave.total_days_paid, leave.total_days_lwp, leave.balance_after), (D("3"), D("2"), D("0")))

    def test_no_balance_row_means_everything_is_unpaid_and_no_row_is_invented(self):
        leave = self.pending_principal(self.ben)
        self.approve(leave)
        self.assertEqual(self.refreshed(leave).status, Status.APPROVED)
        self.assertEqual(leave.total_days_lwp, D("5"))
        self.assertEqual(BalanceTransaction.objects.count(), 0)
        self.assertFalse(StaffLeaveBalance.objects.filter(staff=self.ben).exists())
        self.assertEqual(leave.balance_after, D("0"))

    def test_a_day_is_paid_only_if_its_whole_fraction_is_covered(self):
        self.set_remaining("0.5")
        leave = self.pending_principal(start_date=MON.isoformat(), end_date=MON.isoformat())
        self.approve(leave)
        self.assertEqual(self.balance().remaining, D("0.5"))
        self.assertEqual(self.refreshed(leave).total_days_lwp, D("1"))
        self.assertEqual(self.ledger(leave), [])

    def test_the_figures_are_worked_out_again_from_todays_balance(self):
        leave = self.pending_principal()  # the applicant saw 10 days available
        self.set_remaining("2")           # since then the balance fell
        self.approve(leave)
        leave.refresh_from_db()
        self.assertEqual((leave.total_days_paid, leave.total_days_lwp), (D("2"), D("3")))
        self.assertEqual(leave.balance_before, D("10"))

    def test_two_leaves_cannot_both_spend_the_same_days(self):
        """Both were previewed against 3 days; whoever is approved second finds them gone."""
        self.set_remaining("3")
        first = self.pending_principal(start_date=MON.isoformat(), end_date=WED.isoformat())
        second = self.pending_principal(start_date="2026-11-16", end_date="2026-11-18")
        self.approve(first)
        self.approve(second)
        self.assertEqual(self.refreshed(first).total_days_paid, D("3"))
        self.assertEqual(self.refreshed(second).total_days_paid, D("0"))
        self.assertEqual(self.refreshed(second).total_days_lwp, D("3"))
        self.assertEqual(self.balance().used, self.balance().total)
        self.assertEqual(BalanceTransaction.objects.count(), 3)


class OnceOnlyTests(FinalApprovalCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_principal()

    def test_approving_twice_does_not_deduct_twice(self):
        self.approve(self.leave)
        with self.assertRaises(DomainError):
            self.approve(self.leave)
        self.assertEqual(self.balance().used, D("5"))
        self.assertEqual(BalanceTransaction.objects.count(), 5)
        self.assertEqual(self.logs(self.leave).count(Action.PRINCIPAL_APPROVED), 1)

    def test_the_database_itself_refuses_a_second_deduction_for_a_day(self):
        from django.db import IntegrityError, transaction
        self.approve(self.leave)
        day = self.leave.days.get(date=MON)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BalanceTransaction.objects.create(
                balance=self.balance(), leave=self.leave, leave_day=day, type=BalanceTransaction.Type.DEDUCT,
                amount=D("1"), balance_before=D("5"), balance_after=D("4"),
            )

    def test_a_failure_part_way_leaves_everything_as_it_was(self):
        with mock.patch.object(BalanceTransaction.objects, "bulk_create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.approve(self.leave)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, Status.PRINCIPAL_PENDING)
        self.assertEqual(self.balance().used, 0)
        self.assertEqual({d.status for d in self.leave.days.all()}, {LeaveDay.Status.PENDING})
        self.assertEqual(BalanceTransaction.objects.count(), 0)
        self.assertEqual(self.logs(self.leave), [Action.HOD_APPROVED])
        self.approve(self.leave)  # and a retry then succeeds exactly once
        self.assertEqual(self.balance().used, D("5"))

    def test_both_the_leave_and_the_balance_are_locked_in_that_order(self):
        order = []
        real_leave = LeaveApplication.objects.select_for_update
        real_balance = StaffLeaveBalance.objects.select_for_update
        with mock.patch.object(LeaveApplication.objects, "select_for_update",
                               side_effect=lambda *a, **k: (order.append("leave"), real_leave(*a, **k))[1]), \
             mock.patch.object(StaffLeaveBalance.objects, "select_for_update",
                               side_effect=lambda *a, **k: (order.append("balance"), real_balance(*a, **k))[1]):
            self.approve(self.leave)
        self.assertEqual(order, ["leave", "balance"])  # SQLite ignores the locks; PostgreSQL honours them

    def test_cover_requests_are_left_as_they_were(self):
        leave = self.apply(self.ben, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(leave, self.chitra)
        from leaves import workload
        workload.decide_workload(workload=self.rows(leave).get(), decision="accept", remarks="", actor=self.chitra.user)
        self.decide(leave, self.hod, "approve")
        self.approve(leave)
        self.assertEqual(self.rows(leave).get().status, LeaveWorkload.Status.ACCEPTED)


class WhoMayApproveTests(FinalApprovalCase):
    def test_only_the_principal_can_give_final_approval(self):
        leave = self.pending_principal()
        for person in (self.hod, self.maths_hod, self.asha, self.ben):
            with self.assertRaises(PermissionDenied):
                self.decide(leave, person, "approve")
        self.assertEqual(self.refreshed(leave).status, Status.PRINCIPAL_PENDING)
        self.assertEqual(self.balance().used, 0)

    def test_the_principal_cannot_approve_a_staff_leave_at_the_head_stage(self):
        leave = self.pending_hod()
        with self.assertRaises(PermissionDenied):
            self.approve(leave)
        self.assertEqual(self.balance().used, 0)

    def test_a_head_cannot_skip_the_head_stage_to_final_approval(self):
        leave = self.pending_hod()
        self.decide(leave, self.hod, "approve")
        self.assertEqual(self.refreshed(leave).status, Status.PRINCIPAL_PENDING)
        self.assertEqual(self.balance().used, 0)  # the head's approval deducts nothing

    def test_an_approved_leave_still_blocks_its_dates(self):
        leave = self.pending_principal()
        self.approve(leave)
        from core.validation import DomainError as DE
        with self.assertRaises(DE):
            self.pending_hod(self.asha)
