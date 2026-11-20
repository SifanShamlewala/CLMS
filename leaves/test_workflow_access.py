from django.contrib.auth.models import AnonymousUser

from academics.models import PolicyConfig
from core.testutils import make_admin, make_staff
from leaves.access import can_view_application
from leaves.models import LeaveApplication
from leaves.testutils import FRI, MON, LeaveTestCase, Role
from leaves.workflow import Status, initial_status, next_step_text, stage_after_workload


class WorkflowTests(LeaveTestCase):
    def policy(self, route):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = route
        policy.save()
        return policy

    def test_staff_start_with_workload_or_go_straight_to_the_head(self):
        policy = self.policy("SKIP_TO_PRINCIPAL")
        self.assertEqual(initial_status(Role.STAFF, True, policy), Status.WORKLOAD_PENDING)
        self.assertEqual(initial_status(Role.STAFF, False, policy), Status.HOD_PENDING)

    def test_a_heads_own_leave_follows_the_policy_route(self):
        skip = self.policy("SKIP_TO_PRINCIPAL")
        self.assertEqual(initial_status(Role.HOD, True, skip), Status.WORKLOAD_PENDING)
        self.assertEqual(initial_status(Role.HOD, False, skip), Status.PRINCIPAL_PENDING)
        as_hod = self.policy("PRINCIPAL_AS_HOD")
        self.assertEqual(initial_status(Role.HOD, False, as_hod), Status.HOD_PENDING)
        self.assertEqual(stage_after_workload(Role.HOD, as_hod), Status.HOD_PENDING)

    def test_the_principals_own_leave_always_starts_at_the_principal_stage(self):
        policy = self.policy("SKIP_TO_PRINCIPAL")
        for requires in (True, False):
            self.assertEqual(initial_status(Role.PRINCIPAL, requires, policy), Status.PRINCIPAL_PENDING)

    def test_next_step_text_exists_for_every_status(self):
        for status in Status.values:
            self.assertTrue(next_step_text(status), status)
        self.assertEqual(next_step_text("NOPE"), "")


class CanViewApplicationTests(LeaveTestCase):
    def setUp(self):
        super().setUp()
        self.application = LeaveApplication.objects.create(
            applicant=self.asha, academic_year=self.year, leave_type=self.cl,
            start_date=MON, end_date=FRI, reason="Trip",
        )

    def can(self, user, application=None):
        return can_view_application(user, application or self.application)

    def test_applicant_head_of_the_same_department_principal_and_administrator_may_view(self):
        self.assertTrue(self.can(self.asha.user))
        self.assertTrue(self.can(self.hod.user))
        self.assertTrue(self.can(self.principal.user))
        self.assertTrue(self.can(make_admin()))

    def test_everyone_else_may_not(self):
        colleague = make_staff("colleague", self.physics)
        self.assertFalse(self.can(colleague.user))                 # same department, not the head
        self.assertFalse(self.can(self.maths_hod.user))            # a head of another department
        self.assertFalse(self.can(make_staff("other", self.maths).user))
        self.assertFalse(self.can(AnonymousUser()))

    def test_a_user_without_a_profile_who_is_not_the_administrator_may_not(self):
        from django.contrib.auth import get_user_model
        plain = get_user_model().objects.create_user("plain", password="x")
        self.assertFalse(self.can(plain))

    def test_inactive_profiles_and_inactive_administrators_are_refused(self):
        self.hod.is_active = False
        self.hod.save()
        self.assertFalse(self.can(self.hod.user))
        admin = make_admin("root")
        admin.is_active = False
        admin.save()
        self.assertFalse(self.can(admin))

    def test_removed_applications_are_invisible_to_everyone(self):
        self.application.is_deleted = True
        for user in (self.asha.user, self.hod.user, self.principal.user, make_admin("root")):
            self.assertFalse(self.can(user))
