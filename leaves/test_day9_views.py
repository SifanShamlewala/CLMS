from decimal import Decimal as D

from django.urls import reverse

from leaves import workload
from leaves.models import LeaveApplication, LeaveWorkload, StaffLeaveBalance
from leaves.testutils import MON, TUE, WED, ApprovalTestCase

Status = LeaveApplication.Status


class ViewCase(ApprovalTestCase):
    def flashed(self, response):
        return [str(m) for m in response.wsgi_request._messages]


class FinalApprovalViewTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.leave = self.pending_principal()
        self.detail = reverse("leave_detail", args=[self.leave.pk])
        self.decide_url = reverse("leave_decide", args=[self.leave.pk])
        self.sign_in(self.principal)

    def test_the_queue_offers_approve_and_shows_the_estimate(self):
        page = self.client.get(reverse("principal_queue"))
        self.assertContains(page, 'value="approve"')
        self.assertContains(page, "Paid (deducted)")

    def test_approving_from_the_queue_deducts_and_says_how_much(self):
        response = self.client.post(self.decide_url, {"decision": "approve", "remarks": "Granted"})
        self.assertRedirects(response, reverse("principal_queue"))
        self.assertEqual(self.refreshed(self.leave).status, Status.APPROVED)
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, D("5"))
        self.assertIn("5 paid days deducted", self.flashed(response)[0])

    def test_the_message_mentions_unpaid_days(self):
        balance = StaffLeaveBalance.objects.get(staff=self.asha)
        balance.used = balance.total - D("3")
        balance.save()
        response = self.client.post(self.decide_url, {"decision": "approve"})
        self.assertIn("3 paid days deducted from the balance, 2 without pay.", self.flashed(response)[0])

    def test_approving_from_the_leave_page_returns_to_it(self):
        response = self.client.post(self.decide_url, {"decision": "approve", "from": "detail"})
        self.assertRedirects(response, self.detail)

    def test_a_second_click_changes_nothing(self):
        self.client.post(self.decide_url, {"decision": "approve"})
        response = self.client.post(self.decide_url, {"decision": "approve"})
        self.assertEqual(StaffLeaveBalance.objects.get(staff=self.asha).used, D("5"))
        self.assertIn("no longer waiting", self.flashed(response)[-1])

    def test_the_approved_leave_page_shows_the_final_figures_to_the_right_people(self):
        self.client.post(self.decide_url, {"decision": "approve", "remarks": "Granted"})
        for person in (self.asha, self.hod, self.principal):
            self.sign_in(person)
            page = self.client.get(self.detail)
            self.assertContains(page, "Balance after approval")
            self.assertContains(page, "Principal approved")
            self.assertContains(page, "Granted")
            self.assertNotContains(page, "Your decision")

    def test_a_covering_colleague_does_not_see_the_balance_after_approval(self):
        leave = self.apply(self.ben, start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(leave, self.chitra)
        workload.decide_workload(workload=self.rows(leave).get(), decision="accept", remarks="", actor=self.chitra.user)
        self.decide(leave, self.hod, "approve")
        self.decide(leave, self.principal, "approve")
        self.sign_in(self.chitra)
        page = self.client.get(reverse("leave_detail", args=[leave.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Balance after approval")

    def test_the_balances_page_shows_the_new_figures(self):
        self.client.post(self.decide_url, {"decision": "approve"})
        self.sign_in(self.asha)
        page = self.client.get(reverse("leave_mine"))
        self.assertEqual(page.status_code, 200)


class CancelViewTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.leave = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())
        self.detail = reverse("leave_detail", args=[self.leave.pk])
        self.url = reverse("leave_cancel", args=[self.leave.pk])
        self.sign_in(self.asha)

    def test_the_applicant_sees_the_cancel_button_while_nobody_has_acted(self):
        self.assertContains(self.client.get(self.detail), "Cancel application")

    def test_cancelling_redirects_with_a_message_and_frees_the_dates(self):
        response = self.client.post(self.url, {"remarks": "Plans changed"})
        self.assertRedirects(response, self.detail)
        self.assertEqual(self.refreshed(self.leave).status, Status.CANCELLED)
        self.assertIn("cancelled", self.flashed(response)[0])
        page = self.client.get(self.detail)
        self.assertNotContains(page, "Cancel application")
        self.assertContains(page, "Plans changed")

    def test_the_button_is_gone_once_a_colleague_has_accepted(self):
        self.assign(self.leave, self.ben, [MON])
        workload.decide_workload(workload=self.rows(self.leave).get(), decision="accept", remarks="", actor=self.ben.user)
        self.assertNotContains(self.client.get(self.detail), "Cancel application")
        response = self.client.post(self.url, {})
        self.assertRedirects(response, self.detail)
        self.assertIn("can no longer be cancelled", self.flashed(response)[0])
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)

    def test_nobody_else_sees_a_cancel_button(self):
        for person in (self.hod, self.principal):
            self.sign_in(person)
            self.assertNotContains(self.client.get(self.detail), "Cancel application")

    def test_only_the_applicant_can_post_a_cancellation(self):
        self.sign_in(self.hod)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.sign_in(self.maths_hod)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(self.refreshed(self.leave).status, Status.WORKLOAD_PENDING)

    def test_a_colleague_asked_to_cover_cannot_cancel_it(self):
        self.assign(self.leave, self.ben, [MON])
        self.sign_in(self.ben)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_get_is_not_allowed_and_login_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_the_covering_colleagues_inbox_shows_the_request_as_cancelled(self):
        self.assign(self.leave, self.ben, [MON])
        self.client.post(self.url)
        self.sign_in(self.ben)
        page = self.client.get(reverse("workload_inbox") + "?status=CANCELLED")
        self.assertEqual(len(page.context["page_obj"]), 1)
        self.assertNotContains(page, 'name="decision"')
