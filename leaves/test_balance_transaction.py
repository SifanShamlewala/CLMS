from decimal import Decimal as D

from django.db import IntegrityError, transaction

from leaves.models import BalanceTransaction, StaffLeaveBalance
from leaves.testutils import MON, TUE, ApprovalTestCase

T = BalanceTransaction.Type


class BalanceTransactionTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()
        self.day = self.leave.days.get(date=MON)
        self.balance = StaffLeaveBalance.objects.get(staff=self.asha)

    def make(self, **extra):
        data = {
            "balance": self.balance, "leave": self.leave, "leave_day": self.day, "type": T.DEDUCT,
            "amount": D("1.0"), "balance_before": D("10.0"), "balance_after": D("9.0"),
        }
        data.update(extra)
        return BalanceTransaction.objects.create(**data)

    def test_defaults(self):
        row = self.make()
        self.assertEqual((row.remarks, row.created_by), ("", None))
        self.assertIsNotNone(row.created_at)

    def test_a_day_can_be_deducted_only_once(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make()

    def test_a_day_can_be_restored_only_once(self):
        self.make(type=T.RESTORE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(type=T.RESTORE)

    def test_a_deducted_day_may_be_restored(self):
        self.make()
        self.make(type=T.RESTORE)
        self.assertEqual(BalanceTransaction.objects.count(), 2)

    def test_different_days_do_not_collide(self):
        self.make()
        self.make(leave_day=self.leave.days.get(date=TUE))

    def test_an_adjustment_needs_no_leave_day_and_may_repeat(self):
        for _ in range(2):
            self.make(type=T.ADJUST, leave=None, leave_day=None)

    def test_a_deduction_without_a_day_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(leave_day=None)

    def test_the_amount_must_be_positive(self):
        for bad in (D("0.0"), D("-1.0")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.make(amount=bad, type=T.ADJUST, leave=None, leave_day=None)

    def test_the_ledger_is_append_only(self):
        row = self.make()
        row.remarks = "edited"
        with self.assertRaises(RuntimeError):
            row.save()
        with self.assertRaises(RuntimeError):
            row.delete()
        with self.assertRaises(RuntimeError):
            BalanceTransaction.objects.update(amount=D("9.0"))
        with self.assertRaises(RuntimeError):
            BalanceTransaction.objects.all().delete()
        self.assertEqual(BalanceTransaction.objects.get().amount, D("1.0"))

    def test_rows_come_back_oldest_first(self):
        first = self.make()
        second = self.make(leave_day=self.leave.days.get(date=TUE))
        self.assertEqual(list(self.balance.transactions.all()), [first, second])
