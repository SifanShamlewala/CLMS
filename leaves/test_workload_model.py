import datetime

from django.db import IntegrityError, transaction

from leaves.models import LeaveWorkload
from leaves.testutils import MON, TUE, WorkloadTestCase

W = LeaveWorkload.Status


class WorkloadConstraintTests(WorkloadTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.apply()
        self.day = self.application.days.get(date=MON)

    def make(self, person=None, status=W.PENDING, **extra):
        return LeaveWorkload.objects.create(
            leave_day=self.day, assigned_staff=person or self.ben, date=self.day.date, status=status, **extra
        )

    def test_defaults(self):
        row = self.make()
        self.assertEqual((row.status, row.remarks, row.is_deleted), (W.PENDING, "", False))
        self.assertIsNone(row.decided_at)
        self.assertIsNone(row.transferred_from)

    def test_one_active_row_per_person_and_day(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make()

    def test_accepted_rows_also_count_as_active(self):
        self.make(status=W.ACCEPTED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(status=W.PENDING)

    def test_history_rows_do_not_block_a_new_assignment(self):
        for status in (W.REJECTED, W.TRANSFERRED, W.CANCELLED):
            self.make(status=status)
        self.make()  # still allowed: nothing active yet

    def test_a_withdrawn_row_does_not_block_a_new_assignment(self):
        self.make(is_deleted=True)
        self.make()

    def test_two_people_may_cover_the_same_day(self):
        self.make(self.ben)
        self.make(self.chitra)
        self.assertEqual(self.rows(self.application).count(), 2)

    def test_the_same_person_may_cover_different_days(self):
        self.make()
        LeaveWorkload.objects.create(
            leave_day=self.application.days.get(date=TUE), assigned_staff=self.ben, date=TUE
        )

    def test_transfer_lineage_is_kept(self):
        old = self.make(status=W.TRANSFERRED)
        new = self.make(self.chitra, transferred_from=old)
        self.assertEqual(list(old.transferred_to.all()), [new])
