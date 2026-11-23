from decimal import Decimal as D

from academics.models import PolicyConfig
from core.testutils import make_staff
from leaves import approvals
from leaves.models import LeaveApplication, StaffLeaveBalance
from leaves.testutils import MON, SUN, TUE, WED, ApprovalTestCase

Status = LeaveApplication.Status


class HodQueueTests(ApprovalTestCase):
    def ids(self, status=Status.HOD_PENDING, head=None):
        return [l.pk for l in approvals.hod_queue(head or self.hod, status)]

    def test_shows_my_departments_leaves_waiting_for_the_head(self):
        a = self.pending_hod()
        b = self.pending_hod(self.ben)
        self.assertEqual(self.ids(), [a.pk, b.pk])  # oldest first

    def test_other_departments_are_not_shown(self):
        mathew = make_staff("mathew", self.maths)
        self.give(mathew, "5")
        theirs = self.pending_hod(mathew)
        mine = self.pending_hod()
        self.assertEqual(self.ids(), [mine.pk])
        self.assertEqual(self.ids(head=self.maths_hod), [theirs.pk])

    def test_the_heads_own_leave_is_never_in_their_queue(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        self.apply(self.hod, requires_workload="")
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(status=""), [])

    def test_a_principal_in_the_same_department_is_not_the_heads_to_review(self):
        self.principal.department = self.physics
        self.principal.save()
        self.apply(self.principal, requires_workload="")
        self.assertEqual(self.ids(status=Status.PRINCIPAL_PENDING), [])

    def test_leaves_still_waiting_for_cover_and_cancelled_ones_are_not_listed(self):
        self.apply(self.ben)  # cover needed
        cancelled = self.pending_hod()
        LeaveApplication.objects.filter(pk=cancelled.pk).update(status=Status.CANCELLED)
        self.assertEqual(self.ids(status=""), [])

    def test_removed_applications_are_not_listed(self):
        leave = self.pending_hod()
        LeaveApplication.objects.filter(pk=leave.pk).update(is_deleted=True)
        self.assertEqual(self.ids(), [])

    def test_the_status_filter_and_the_everything_choice(self):
        waiting = self.pending_hod()
        onward = self.pending_principal(self.ben)
        rejected = self.pending_hod(self.chitra)
        self.decide(rejected, self.hod, "reject", "No")
        self.assertEqual(self.ids(Status.HOD_PENDING), [waiting.pk])
        self.assertEqual(self.ids(Status.PRINCIPAL_PENDING), [onward.pk])
        self.assertEqual(self.ids(Status.REJECTED), [rejected.pk])
        self.assertEqual(sorted(self.ids("")), sorted([waiting.pk, onward.pk, rejected.pk]))
        self.assertEqual(sorted(self.ids("NONSENSE")), sorted([waiting.pk, onward.pk, rejected.pk]))


class PrincipalQueueTests(ApprovalTestCase):
    def ids(self, status=approvals.WAITING):
        return [l.pk for l in approvals.principal_queue(self.principal, status)]

    def test_waiting_means_the_final_stage(self):
        leave = self.pending_principal()
        self.assertEqual(self.ids(), [leave.pk])

    def test_a_staff_leave_still_at_the_head_stage_is_not_waiting_for_the_principal(self):
        self.pending_hod()
        self.assertEqual(self.ids(), [])

    def test_a_heads_own_leave_at_the_head_stage_is_waiting_when_the_principal_acts_as_head(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        leave = self.apply(self.hod, requires_workload="")
        self.assertEqual(self.ids(), [leave.pk])

    def test_a_heads_own_leave_goes_straight_to_the_final_stage_by_default(self):
        leave = self.apply(self.hod, requires_workload="")
        self.assertEqual(self.ids(), [leave.pk])

    def test_the_principals_own_leave_is_never_in_the_queue(self):
        self.apply(self.principal, requires_workload="")
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(""), [])

    def test_filters(self):
        waiting = self.pending_principal()
        rejected = self.pending_principal(self.ben)
        self.decide(rejected, self.principal, "reject", "No")
        approved = self.pending_hod(self.chitra)
        LeaveApplication.objects.filter(pk=approved.pk).update(status=Status.APPROVED)
        self.assertEqual(self.ids(approvals.WAITING), [waiting.pk])
        self.assertEqual(self.ids(Status.REJECTED), [rejected.pk])
        self.assertEqual(self.ids(Status.APPROVED), [approved.pk])
        self.assertEqual(len(self.ids("")), 3)

    def test_removed_applications_are_not_listed(self):
        leave = self.pending_principal()
        LeaveApplication.objects.filter(pk=leave.pk).update(is_deleted=True)
        self.assertEqual(self.ids(), [])


class FinalEffectTests(ApprovalTestCase):
    """What approving would do to the balance, worked out afresh from today's balance."""

    def effect(self, leave):
        return approvals.final_effect(leave)

    def test_a_balance_that_covers_everything(self):
        leave = self.pending_principal()
        e = self.effect(leave)
        self.assertEqual((e["available"], e["counted"], e["paid"], e["lwp"], e["after"]),
                         (D("10"), D("5"), D("5"), D("0"), D("5")))
        self.assertTrue(e["has_balance"])
        self.assertFalse(e["differs"])

    def test_a_short_balance_makes_the_later_days_unpaid(self):
        StaffLeaveBalance.objects.filter(staff=self.asha).update(used=D("7.0"))  # 3 left
        leave = self.pending_principal()
        e = self.effect(leave)
        self.assertEqual((e["paid"], e["lwp"], e["after"]), (D("3"), D("2"), D("0")))

    def test_no_balance_row_means_everything_is_unpaid(self):
        leave = self.pending_principal(self.ben)
        e = self.effect(leave)
        self.assertEqual((e["available"], e["paid"], e["lwp"], e["has_balance"]), (D("0"), D("0"), D("5"), False))

    def test_it_notices_when_the_balance_changed_since_applying(self):
        leave = self.pending_principal()
        StaffLeaveBalance.objects.filter(staff=self.asha).update(used=D("8.0"))  # 2 left now
        e = self.effect(leave)
        self.assertEqual((e["paid"], e["lwp"]), (D("2"), D("3")))
        self.assertTrue(e["differs"])

    def test_sundays_and_holidays_are_not_counted(self):
        self.holiday(WED, "Diwali")
        # Monday to Sunday: Wednesday is a holiday, Sunday is always off, Saturday is a working day.
        leave = self.pending_principal(end_date=SUN.isoformat())
        e = self.effect(leave)
        self.assertEqual((e["counted"], e["paid"], e["lwp"]), (D("5"), D("5"), D("0")))

    def test_a_half_day_costs_half(self):
        leave = self.pending_principal(start_date=MON.isoformat(), end_date=TUE.isoformat())
        leave.days.filter(date=TUE).update(day_part="HALF_AM", fraction=D("0.5"))
        e = self.effect(leave)
        self.assertEqual((e["counted"], e["paid"], e["after"]), (D("1.5"), D("1.5"), D("8.5")))

    def test_a_day_is_paid_only_if_its_whole_fraction_is_covered(self):
        StaffLeaveBalance.objects.filter(staff=self.asha).update(used=D("9.5"))  # half a day left
        leave = self.pending_principal(start_date=MON.isoformat(), end_date=MON.isoformat())
        e = self.effect(leave)
        self.assertEqual((e["paid"], e["lwp"], e["after"]), (D("0"), D("1"), D("0.5")))

    def test_it_does_not_change_anything(self):
        leave = self.pending_principal()
        self.effect(leave)
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, 0)
        self.assertEqual(self.refreshed(leave).status, Status.PRINCIPAL_PENDING)
