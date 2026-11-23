from django.db import models

from leaves.models import LeaveApprovalLog
from leaves.testutils import ApprovalTestCase

Action = LeaveApprovalLog.Action


class ApprovalLogTests(ApprovalTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()

    def make(self, **extra):
        data = {"leave": self.leave, "action_by": self.hod.user, "action": Action.HOD_APPROVED}
        data.update(extra)
        return LeaveApprovalLog.objects.create(**data)

    def test_defaults(self):
        row = self.make()
        self.assertEqual((row.remarks, row.is_deleted), ("", False))
        self.assertIsNotNone(row.created_at)

    def test_rows_come_back_oldest_first(self):
        first = self.make(remarks="one")
        second = self.make(remarks="two")
        self.assertEqual(list(self.leave.approval_logs.all()), [first, second])

    def test_the_approved_action_list_is_all_there(self):
        self.assertEqual(len(Action.values), 27)
        for name in ("HOD_APPROVED", "HOD_REJECTED", "PRINCIPAL_APPROVED", "PRINCIPAL_REJECTED",
                     "PRINCIPAL_AUTO_APPROVED", "WORKLOAD_TRANSFERRED", "STAFF_ROLE_CHANGED"):
            self.assertIn(name, Action.values)

    def test_the_labels_for_the_decisions_read_well(self):
        self.assertEqual(Action.HOD_APPROVED.label, "Head of department approved")
        self.assertEqual(Action.PRINCIPAL_REJECTED.label, "Principal rejected")

    def test_a_saved_row_cannot_be_changed(self):
        row = self.make()
        row.remarks = "edited"
        with self.assertRaises(RuntimeError):
            row.save()
        self.assertEqual(LeaveApprovalLog.objects.get(pk=row.pk).remarks, "")

    def test_a_row_cannot_be_deleted(self):
        row = self.make()
        with self.assertRaises(RuntimeError):
            row.delete()
        self.assertEqual(LeaveApprovalLog.objects.count(), 1)

    def test_bulk_changes_are_refused_too(self):
        row = self.make()
        with self.assertRaises(RuntimeError):
            LeaveApprovalLog.objects.update(remarks="x")
        with self.assertRaises(RuntimeError):
            LeaveApprovalLog.objects.filter(pk=row.pk).delete()
        with self.assertRaises(RuntimeError):
            LeaveApprovalLog.objects.bulk_update([row], ["remarks"])
        self.assertEqual(LeaveApprovalLog.objects.get(pk=row.pk).remarks, "")

    def test_the_reviewer_may_be_removed_without_losing_the_row(self):
        row = self.make(action_by=self.hod.user)
        self.assertEqual(LeaveApprovalLog._meta.get_field("action_by").remote_field.on_delete, models.SET_NULL)
        self.assertTrue(LeaveApprovalLog._meta.get_field("action_by").null)
        self.assertEqual(row.action_by, self.hod.user)

    def test_the_leave_cannot_be_deleted_while_it_has_history(self):
        self.assertEqual(LeaveApprovalLog._meta.get_field("leave").remote_field.on_delete, models.PROTECT)
