from decimal import Decimal as D

from django.urls import reverse

from academics.models import PolicyConfig
from core.testutils import make_admin, make_staff
from leaves import approvals
from leaves.models import LeaveApplication, LeaveApprovalLog, StaffLeaveBalance
from leaves.testutils import ApprovalTestCase

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action


class ViewTestCase(ApprovalTestCase):
    def decide_url(self, leave):
        return reverse("leave_decide", args=[leave.pk])

    def post(self, leave, **data):
        return self.client.post(self.decide_url(leave), data)

    def flashed(self, response):
        return [str(m) for m in response.wsgi_request._messages]


class QueueAccessTests(ViewTestCase):
    def test_login_is_required(self):
        for name in ("hod_queue", "principal_queue"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response["Location"])

    def test_each_queue_belongs_to_one_role(self):
        for person, allowed, refused in (
            (self.hod, "hod_queue", "principal_queue"),
            (self.principal, "principal_queue", "hod_queue"),
        ):
            self.sign_in(person)
            self.assertEqual(self.client.get(reverse(allowed)).status_code, 200)
            self.assertEqual(self.client.get(reverse(refused)).status_code, 403)

    def test_staff_and_a_profileless_administrator_are_refused(self):
        self.sign_in(self.asha)
        for name in ("hod_queue", "principal_queue"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.client.logout()
        self.client.force_login(make_admin())
        for name in ("hod_queue", "principal_queue"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_queues_are_read_only_pages(self):
        self.sign_in(self.hod)
        self.assertEqual(self.client.post(reverse("hod_queue")).status_code, 405)
        self.sign_in(self.principal)
        self.assertEqual(self.client.post(reverse("principal_queue")).status_code, 405)

    def test_the_navigation_shows_only_the_users_own_queue(self):
        for person, shown, hidden in (
            (self.hod, "hod_queue", "principal_queue"),
            (self.principal, "principal_queue", "hod_queue"),
            (self.asha, None, None),
        ):
            self.sign_in(person)
            page = self.client.get(reverse("dashboard"))
            if shown:
                self.assertContains(page, reverse(shown))
                self.assertNotContains(page, reverse(hidden))
            else:
                self.assertNotContains(page, reverse("hod_queue"))
                self.assertNotContains(page, reverse("principal_queue"))


class HodQueueViewTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()
        self.sign_in(self.hod)

    def test_lists_what_waits_for_me_with_buttons(self):
        page = self.client.get(reverse("hod_queue"))
        self.assertEqual([l.pk for l in page.context["page_obj"]], [self.leave.pk])
        self.assertEqual(page.context["waiting"], 1)
        self.assertContains(page, "Asha")
        self.assertContains(page, 'value="approve"')
        self.assertContains(page, 'value="reject"')

    def test_other_departments_leaves_are_not_listed(self):
        mathew = make_staff("mathew", self.maths)
        self.pending_hod(mathew)
        page = self.client.get(reverse("hod_queue") + "?status=")
        self.assertEqual([l.pk for l in page.context["page_obj"]], [self.leave.pk])

    def test_decided_leaves_have_no_buttons(self):
        self.decide(self.leave, self.hod, "approve")
        page = self.client.get(reverse("hod_queue") + "?status=PRINCIPAL_PENDING")
        self.assertEqual(len(page.context["page_obj"]), 1)
        self.assertNotContains(page, 'value="approve"')

    def test_the_empty_queue_says_so(self):
        self.decide(self.leave, self.hod, "approve")
        self.assertContains(self.client.get(reverse("hod_queue")), "No leave is waiting for your decision.")


class PrincipalQueueViewTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_principal()
        self.sign_in(self.principal)

    def test_shows_the_balance_effect_with_approve_and_reject_buttons(self):
        page = self.client.get(reverse("principal_queue"))
        self.assertEqual([l.pk for l in page.context["page_obj"]], [self.leave.pk])
        self.assertContains(page, "Balance after")
        self.assertContains(page, "Paid (deducted)")
        self.assertContains(page, 'value="reject"')
        self.assertContains(page, 'value="approve"')
        effect = page.context["page_obj"][0].effect
        self.assertEqual((effect["paid"], effect["after"]), (D("5"), D("5")))

    def test_a_heads_leave_at_the_head_stage_offers_approve_and_reject(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        leave = self.apply(self.hod, requires_workload="")
        page = self.client.get(reverse("principal_queue"))
        row = [l for l in page.context["page_obj"] if l.pk == leave.pk][0]
        self.assertEqual(row.stage, approvals.HOD_STAGE)
        self.assertIsNone(row.effect)
        self.assertContains(page, "Acting as head of department")
        self.assertContains(page, 'value="approve"')

    def test_the_principals_own_leave_is_not_listed(self):
        self.apply(self.principal, requires_workload="")
        page = self.client.get(reverse("principal_queue") + "?status=")
        self.assertEqual([l.applicant for l in page.context["page_obj"]], [self.asha])

    def test_the_page_explains_that_approving_is_final(self):
        page = self.client.get(reverse("principal_queue"))
        self.assertContains(page, "Approving is final")
        self.assertNotContains(page, "not available yet")


class DecideViewTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()
        self.sign_in(self.hod)

    def test_approve_redirects_to_the_queue_with_a_message(self):
        response = self.post(self.leave, decision="approve", remarks="Fine")
        self.assertRedirects(response, reverse("hod_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.PRINCIPAL_PENDING)
        self.assertIn("now waits for the Principal", self.flashed(response)[0])

    def test_reject_needs_a_reason_and_changes_nothing_without_one(self):
        response = self.post(self.leave, decision="reject", remarks="")
        self.assertRedirects(response, reverse("hod_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)
        self.assertIn("Remarks is required.", self.flashed(response))

    def test_reject_with_a_reason_rejects(self):
        response = self.post(self.leave, decision="reject", remarks="Exams")
        self.assertRedirects(response, reverse("hod_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.REJECTED)
        self.assertIn("rejected", self.flashed(response)[0])

    def test_the_detail_page_form_returns_to_the_detail_page(self):
        response = self.post(self.leave, decision="approve", **{"from": "detail"})
        self.assertRedirects(response, reverse("leave_detail", args=[self.leave.pk]))

    def test_an_arbitrary_return_address_is_ignored(self):
        response = self.post(self.leave, decision="approve", **{"from": "https://evil.example/"})
        self.assertRedirects(response, reverse("hod_queue"))

    def test_the_principal_is_sent_back_to_their_own_queue(self):
        leave = self.pending_principal(self.ben)
        self.sign_in(self.principal)
        response = self.post(leave, decision="reject", remarks="No")
        self.assertRedirects(response, reverse("principal_queue"))

    def test_the_principal_approves_and_the_balance_is_deducted(self):
        self.decide(self.leave, self.hod, "approve")
        self.sign_in(self.principal)
        response = self.post(self.leave, decision="approve", remarks="Granted")
        self.assertRedirects(response, reverse("principal_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.APPROVED)
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, D("5"))
        self.assertIn("approved", self.flashed(response)[0])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.decide_url(self.leave)).status_code, 405)

    def test_anonymous_users_go_to_login(self):
        self.client.logout()
        response = self.post(self.leave, decision="approve")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_staff_cannot_decide_even_their_own_leave(self):
        for person in (self.asha, self.ben):
            self.sign_in(person)
            self.assertEqual(self.post(self.leave, decision="approve").status_code, 403)
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)

    def test_another_departments_head_gets_a_404(self):
        self.sign_in(self.maths_hod)
        self.assertEqual(self.post(self.leave, decision="approve").status_code, 404)
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)

    def test_the_principal_cannot_decide_a_staff_leave_at_the_head_stage(self):
        self.sign_in(self.principal)
        self.assertEqual(self.post(self.leave, decision="approve").status_code, 403)

    def test_a_missing_application_is_a_404(self):
        self.assertEqual(self.client.post(reverse("leave_decide", args=[99999]), {"decision": "approve"}).status_code, 404)

    def test_deciding_twice_changes_nothing_the_second_time(self):
        self.post(self.leave, decision="reject", remarks="No")
        response = self.post(self.leave, decision="reject", remarks="No")
        self.assertRedirects(response, reverse("hod_queue"))
        self.assertIn("no longer waiting", self.flashed(response)[-1])  # the first message is still queued
        self.assertEqual(self.logs(self.leave), [Action.HOD_REJECTED])

    def test_a_forged_answer_changes_nothing(self):
        response = self.post(self.leave, decision="accept")
        self.assertRedirects(response, reverse("hod_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.HOD_PENDING)


class DetailPageTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_hod()
        self.detail = reverse("leave_detail", args=[self.leave.pk])

    def test_the_head_sees_the_decision_form_with_both_buttons(self):
        self.sign_in(self.hod)
        page = self.client.get(self.detail)
        self.assertContains(page, "Your decision")
        self.assertContains(page, 'value="approve"')
        self.assertContains(page, 'name="from" value="detail"')

    def test_nobody_else_sees_a_decision_form(self):
        for person in (self.asha, self.principal):
            self.sign_in(person)
            self.assertNotContains(self.client.get(self.detail), "Your decision")

    def test_the_principal_sees_the_effect_and_both_buttons_at_the_final_stage(self):
        leave = self.pending_principal(self.ben)
        self.sign_in(self.principal)
        page = self.client.get(reverse("leave_detail", args=[leave.pk]))
        self.assertContains(page, "Your decision")
        self.assertContains(page, "Balance after")
        self.assertContains(page, "Approving is final")
        self.assertContains(page, 'value="approve"')

    def test_the_form_goes_away_once_decided(self):
        self.decide(self.leave, self.hod, "approve")
        self.sign_in(self.hod)
        self.assertNotContains(self.client.get(self.detail), "Your decision")

    def test_the_history_shows_who_decided_and_why(self):
        self.decide(self.leave, self.hod, "reject", "Exams that week")
        for person in (self.asha, self.hod, self.principal):
            self.sign_in(person)
            page = self.client.get(self.detail)
            self.assertContains(page, "Review history")
            self.assertContains(page, "Head of department rejected")
            self.assertContains(page, "Exams that week")

    def test_no_history_section_before_any_decision(self):
        self.sign_in(self.asha)
        self.assertNotContains(self.client.get(self.detail), "Review history")

    def test_a_colleague_covering_does_not_see_the_review_history(self):
        leave = self.apply(self.ben, start_date="2026-11-16", end_date="2026-11-16")
        self.assign(leave, self.chitra)
        from leaves import workload
        workload.decide_workload(workload=self.rows(leave).get(), decision="accept", remarks="", actor=self.chitra.user)
        self.decide(leave, self.hod, "approve", "Private remark")
        self.sign_in(self.chitra)
        page = self.client.get(reverse("leave_detail", args=[leave.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Review history")
        self.assertNotContains(page, "Private remark")

    def test_a_head_acting_for_nothing_else_sees_no_form_on_their_own_leave(self):
        policy = PolicyConfig.get()
        policy.hod_own_leave_route = PolicyConfig.HodOwnLeaveRoute.PRINCIPAL_AS_HOD
        policy.save()
        own = self.apply(self.hod, requires_workload="")
        self.sign_in(self.hod)
        self.assertNotContains(self.client.get(reverse("leave_detail", args=[own.pk])), "Your decision")
        self.sign_in(self.principal)
        self.assertContains(self.client.get(reverse("leave_detail", args=[own.pk])), "Your decision")
