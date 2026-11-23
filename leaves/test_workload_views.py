from django.urls import reverse

from core.testutils import make_admin
from leaves import workload
from leaves.models import LeaveApplication, LeaveWorkload
from leaves.testutils import MON, NEXT_MON, TUE, WED, WorkloadTestCase

Status = LeaveApplication.Status
W = LeaveWorkload.Status


class ViewTestCase(WorkloadTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.apply(start_date=MON.isoformat(), end_date=WED.isoformat())
        self.detail = reverse("leave_detail", args=[self.application.pk])
        self.assign_url = reverse("workload_assign", args=[self.application.pk])
        self.inbox = reverse("workload_inbox")

    def row(self, person, day):
        return self.rows(self.application, assigned_staff=person, date=day).get()

    def decide_url(self, row):
        return reverse("workload_decide", args=[row.pk])


class InboxTests(ViewTestCase):
    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(self.inbox)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_an_administrator_without_a_profile_is_refused(self):
        self.client.logout()
        self.client.force_login(make_admin())
        self.assertEqual(self.client.get(self.inbox).status_code, 403)

    def test_every_role_with_a_profile_gets_an_inbox(self):
        for person in (self.ben, self.hod, self.principal):
            self.sign_in(person)
            self.assertEqual(self.client.get(self.inbox).status_code, 200)

    def test_post_is_not_allowed_on_the_inbox(self):
        self.sign_in(self.ben)
        self.assertEqual(self.client.post(self.inbox).status_code, 405)

    def test_empty_inbox_says_so(self):
        self.sign_in(self.ben)
        self.assertContains(self.client.get(self.inbox), "No one is waiting for your answer.")

    def test_shows_only_my_own_pending_requests_by_default(self):
        self.assign(self.application, self.ben, [MON, TUE])
        self.assign(self.application, self.chitra, [WED])
        workload.decide_workload(
            workload=self.row(self.ben, TUE), decision="accept", remarks="", actor=self.ben.user
        )
        self.sign_in(self.ben)
        page = self.client.get(self.inbox)
        self.assertEqual([r.date for r in page.context["page_obj"]], [MON])
        self.assertEqual(page.context["waiting"], 1)
        self.assertContains(page, "Asha")
        self.assertContains(page, "Accept")

    def test_status_filter_and_the_everything_choice(self):
        self.assign(self.application, self.ben, [MON, TUE])
        workload.decide_workload(
            workload=self.row(self.ben, TUE), decision="accept", remarks="Fine", actor=self.ben.user
        )
        self.sign_in(self.ben)
        dates = lambda query: [r.date for r in self.client.get(f"{self.inbox}{query}").context["page_obj"]]
        self.assertEqual(dates(""), [MON])
        self.assertEqual(dates("?status=ACCEPTED"), [TUE])
        self.assertEqual(dates("?status="), [MON, TUE])
        self.assertEqual(dates("?status=NOPE"), [MON, TUE])

    def test_answered_rows_have_no_buttons(self):
        self.assign(self.application, self.ben, [MON])
        workload.decide_workload(
            workload=self.row(self.ben, MON), decision="accept", remarks="", actor=self.ben.user
        )
        self.sign_in(self.ben)
        page = self.client.get(f"{self.inbox}?status=ACCEPTED")
        self.assertNotContains(page, "name=\"decision\"")
        self.assertContains(page, "No answer needed")

    def test_withdrawn_requests_are_not_listed(self):
        self.assign(self.application, self.ben, [MON])
        workload.remove_workload(workload=self.row(self.ben, MON), actor=self.asha.user)
        self.sign_in(self.ben)
        self.assertEqual(len(self.client.get(f"{self.inbox}?status=").context["page_obj"]), 0)

    def test_a_rejected_leaves_rows_show_as_cancelled_without_buttons(self):
        self.assign(self.application, self.ben, [MON])
        self.assign(self.application, self.chitra, [TUE])
        workload.decide_workload(
            workload=self.row(self.chitra, TUE), decision="reject", remarks="No", actor=self.chitra.user
        )
        self.sign_in(self.ben)
        page = self.client.get(f"{self.inbox}?status=CANCELLED")
        self.assertEqual(len(page.context["page_obj"]), 1)
        self.assertNotContains(page, "name=\"decision\"")

    def test_the_nav_and_dashboard_link_to_the_inbox(self):
        self.sign_in(self.ben)
        self.assertContains(self.client.get(reverse("dashboard")), self.inbox)


class DecideViewTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.assign(self.application, self.ben)
        self.sign_in(self.ben)

    def post(self, row, **data):
        return self.client.post(self.decide_url(row), data)

    def test_accept_updates_the_row_and_redirects_with_a_message(self):
        response = self.post(self.row(self.ben, MON), decision="accept", remarks="Sure")
        self.assertRedirects(response, self.inbox)
        row = self.row(self.ben, MON)
        self.assertEqual((row.status, row.remarks), (W.ACCEPTED, "Sure"))
        self.assertEqual([str(m) for m in response.wsgi_request._messages], ["Accepted. Thank you."])

    def test_the_last_acceptance_says_the_leave_moved_on(self):
        self.post(self.row(self.ben, MON), decision="accept")
        self.post(self.row(self.ben, TUE), decision="accept")
        response = self.post(self.row(self.ben, WED), decision="accept")
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("All cover", messages[-1])
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.HOD_PENDING)

    def test_reject_without_remarks_changes_nothing_and_explains(self):
        response = self.post(self.row(self.ben, MON), decision="reject", remarks="")
        self.assertRedirects(response, self.inbox)
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)
        self.assertIn("Remarks is required.", [str(m) for m in response.wsgi_request._messages])

    def test_reject_with_remarks_rejects_the_leave(self):
        response = self.post(self.row(self.ben, MON), decision="reject", remarks="Exam duty")
        self.assertRedirects(response, self.inbox)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Status.REJECTED)
        self.assertIn("is rejected", [str(m) for m in response.wsgi_request._messages][0])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.decide_url(self.row(self.ben, MON))).status_code, 405)

    def test_someone_elses_request_looks_like_a_missing_one(self):
        row = self.row(self.ben, MON)
        for person in (self.chitra, self.asha, self.hod):
            self.sign_in(person)
            self.assertEqual(self.post(row, decision="accept").status_code, 404)
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)

    def test_anonymous_users_are_sent_to_login(self):
        self.client.logout()
        response = self.post(self.row(self.ben, MON), decision="accept")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_a_missing_request_is_a_404(self):
        self.assertEqual(self.client.post(reverse("workload_decide", args=[99999]), {"decision": "accept"}).status_code, 404)

    def test_a_withdrawn_request_is_a_404(self):
        row = self.row(self.ben, MON)
        workload.remove_workload(workload=row, actor=self.asha.user)
        self.assertEqual(self.post(row, decision="accept").status_code, 404)

    def test_a_forged_answer_value_changes_nothing(self):
        response = self.post(self.row(self.ben, MON), decision="approve")
        self.assertRedirects(response, self.inbox)
        self.assertEqual(self.row(self.ben, MON).status, W.PENDING)


class AssignViewTests(ViewTestCase):
    def post(self, **data):
        return self.client.post(self.assign_url, data)

    def setUp(self):
        super().setUp()
        self.sign_in(self.asha)

    def test_the_applicant_sees_the_form_and_the_dates(self):
        page = self.client.get(self.detail)
        self.assertContains(page, "Ask a colleague to cover")
        self.assertContains(page, "0 of 3 dates covered and accepted.")
        self.assertContains(page, "Nobody asked yet")
        options = [text for _, text in page.context["cover"]["assignee_options"]]
        self.assertTrue(any(o.startswith("Ben") for o in options))
        self.assertFalse(any(o.startswith("Asha") for o in options))  # not oneself

    def test_assign_redirects_to_the_detail_page(self):
        response = self.post(assignee=self.ben.pk, dates=[MON.isoformat(), TUE.isoformat()])
        self.assertRedirects(response, self.detail)
        self.assertEqual(self.rows(self.application).count(), 2)
        page = self.client.get(self.detail)
        self.assertContains(page, "Waiting for answer")
        self.assertContains(page, "Withdraw")

    def test_errors_re_render_the_page_with_kept_values(self):
        response = self.post(assignee=self.asha.pk, dates=[MON.isoformat()])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You can&#x27;t assign your own work to yourself.")
        self.assertContains(response, 'value="2026-11-09" id="id_dates_2026-11-09" checked')

    def test_no_date_chosen_shows_the_error(self):
        response = self.post(assignee=self.ben.pk)
        self.assertContains(response, "Choose at least one date")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.assign_url).status_code, 405)

    def test_only_the_applicant_may_assign_others_get_403_or_404(self):
        data = {"assignee": self.chitra.pk, "dates": [MON.isoformat()]}
        for person, code in ((self.hod, 403), (self.principal, 403), (self.ben, 404), (self.maths_hod, 404)):
            self.sign_in(person)
            self.assertEqual(self.client.post(self.assign_url, data).status_code, code, person)
        self.assertEqual(self.rows(self.application).count(), 0)

    def test_the_form_is_hidden_from_everyone_but_the_applicant(self):
        for person in (self.hod, self.principal):
            self.sign_in(person)
            self.assertNotContains(self.client.get(self.detail), "Ask a colleague to cover")

    def test_the_form_disappears_once_the_stage_is_over(self):
        LeaveApplication.objects.filter(pk=self.application.pk).update(status=Status.HOD_PENDING)
        self.assertNotContains(self.client.get(self.detail), "Ask a colleague to cover")

    def test_withdraw_button_removes_the_request(self):
        self.assign(self.application, self.ben, [MON])
        row = self.row(self.ben, MON)
        response = self.client.post(reverse("workload_remove", args=[row.pk]))
        self.assertRedirects(response, self.detail)
        row.refresh_from_db()
        self.assertTrue(row.is_deleted)
        self.assertNotContains(self.client.get(self.detail), "Withdraw")

    def test_withdraw_is_post_only_and_applicant_only(self):
        self.assign(self.application, self.ben, [MON])
        row = self.row(self.ben, MON)
        url = reverse("workload_remove", args=[row.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.sign_in(self.hod)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.sign_in(self.maths_hod)
        self.assertEqual(self.client.post(url).status_code, 404)
        row.refresh_from_db()
        self.assertFalse(row.is_deleted)

    def test_withdrawing_an_answered_request_explains_why(self):
        self.assign(self.application, self.ben, [MON])
        row = self.row(self.ben, MON)
        LeaveWorkload.objects.filter(pk=row.pk).update(status=W.ACCEPTED)
        response = self.client.post(reverse("workload_remove", args=[row.pk]))
        self.assertRedirects(response, self.detail)
        self.assertIn("hasn't been answered", [str(m) for m in response.wsgi_request._messages][0])

    def test_no_panel_when_the_leave_needs_no_cover(self):
        application = self.apply(self.hod, start_date=NEXT_MON.isoformat(), end_date=NEXT_MON.isoformat(), requires_workload="")
        self.sign_in(self.hod)
        page = self.client.get(reverse("leave_detail", args=[application.pk]))
        self.assertIsNone(page.context["cover"])
        self.assertNotContains(page, "covered and accepted")


class VisibilityTests(ViewTestCase):
    def test_a_colleague_asked_to_cover_can_open_the_leave(self):
        self.assign(self.application, self.ben, [MON])
        self.sign_in(self.ben)
        page = self.client.get(self.detail)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Workload cover")
        self.assertNotContains(page, "Ask a colleague to cover")

    def test_a_cover_only_viewer_sees_no_balances_or_paid_split(self):
        self.assign(self.application, self.ben, [MON])
        self.sign_in(self.ben)
        page = self.client.get(self.detail)
        for hidden in ("Balance at submission", "Leave without pay", "Days counted", "Paid"):
            self.assertNotContains(page, hidden)

    def test_the_applicant_and_reviewers_still_see_the_balance_details(self):
        for person in (self.asha, self.hod, self.principal):
            self.sign_in(person)
            page = self.client.get(self.detail)
            self.assertContains(page, "Balance at submission")
            self.assertContains(page, "Days counted")

    def test_a_colleague_not_asked_still_gets_404(self):
        self.assign(self.application, self.ben, [MON])
        for person in (self.chitra, self.maths_hod):
            self.sign_in(person)
            self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_a_withdrawn_colleague_loses_access(self):
        self.assign(self.application, self.ben, [MON])
        workload.remove_workload(workload=self.row(self.ben, MON), actor=self.asha.user)
        self.sign_in(self.ben)
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_a_colleague_who_answered_keeps_access(self):
        self.assign(self.application, self.ben, [MON])
        workload.decide_workload(
            workload=self.row(self.ben, MON), decision="reject", remarks="No", actor=self.ben.user
        )
        self.sign_in(self.ben)
        self.assertEqual(self.client.get(self.detail).status_code, 200)

    def test_assignee_access_ends_with_a_removed_application(self):
        self.assign(self.application, self.ben, [MON])
        LeaveApplication.objects.filter(pk=self.application.pk).update(is_deleted=True)
        self.sign_in(self.ben)
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_the_panel_shows_each_assignment_and_remarks(self):
        self.assign(self.application, self.ben, [MON])
        workload.decide_workload(
            workload=self.row(self.ben, MON), decision="accept", remarks="Glad to", actor=self.ben.user
        )
        self.sign_in(self.hod)
        page = self.client.get(self.detail)
        self.assertContains(page, "1 of 3 dates covered and accepted.")
        self.assertContains(page, "Accepted")
        self.assertContains(page, "Glad to")


class PreviewWarningTests(WorkloadTestCase):
    def test_applying_on_a_date_you_are_covering_warns_but_does_not_block(self):
        application = self.apply(start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(application, self.ben)
        from leaves import applications
        preview = applications.build_preview(
            user=self.ben.user, data=self.payload(start_date=MON.isoformat(), end_date=TUE.isoformat())
        )
        self.assertTrue(any("asked to cover a colleague's work on Mon 09 Nov" in w for w in preview.warnings))
        self.assertEqual(len(preview.rows), 2)  # still previewable

    def test_no_warning_when_nothing_is_being_covered(self):
        from leaves import applications
        preview = applications.build_preview(user=self.ben.user, data=self.payload())
        self.assertFalse(any("cover a colleague" in w for w in preview.warnings))

    def test_cancelled_or_withdrawn_cover_gives_no_warning(self):
        application = self.apply(start_date=MON.isoformat(), end_date=MON.isoformat())
        self.assign(application, self.ben)
        LeaveWorkload.objects.update(status=W.CANCELLED)
        from leaves import applications
        preview = applications.build_preview(user=self.ben.user, data=self.payload())
        self.assertFalse(any("cover a colleague" in w for w in preview.warnings))
