import re
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.urls import reverse

from academics.models import PolicyConfig
from core.testutils import PASSWORD, make_admin, make_staff
from leaves.models import LeaveApplication, LeaveDay, StaffLeaveBalance
from leaves.testutils import FRI, MON, NEXT_TUE, SAT, SUN, THU, WED, LeaveTestCase, Role

Status = LeaveApplication.Status
URL = "leave_apply"


class ApplyAccessTests(LeaveTestCase):
    def test_anonymous_users_are_sent_to_login(self):
        for name in (URL, "leave_mine"):
            url = reverse(name)
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_the_administrator_without_a_profile_and_unprofiled_users_get_403(self):
        login_as = lambda user: self.client.login(username=user.username, password=PASSWORD)
        admin = make_admin()
        plain = get_user_model().objects.create_user("plain", password=PASSWORD)
        for user in (admin, plain):
            self.client.logout()
            login_as(user)
            for name in (URL, "leave_mine"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, f"{user} {name}")
            self.assertEqual(self.client.post(reverse(URL), self.payload()).status_code, 403)
        self.assertEqual(LeaveApplication.objects.count(), 0)

    def test_inactive_profiles_get_403(self):
        self.asha.is_active = False
        self.asha.save()
        self.client.login(username="asha", password=PASSWORD)
        self.assertEqual(self.client.get(reverse(URL)).status_code, 403)

    def test_staff_heads_and_the_principal_can_open_the_pages(self):
        for person in (self.asha, self.hod, self.principal):
            self.sign_in(person)
            for name in (URL, "leave_mine"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, f"{person} {name}")


class ApplyFormTests(LeaveTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.asha)
        self.url = reverse(URL)

    def test_the_form_shows_the_year_and_the_days_left_for_each_leave_type(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Academic year 2026-27")
        self.assertContains(page, "Casual Leave (CL), 10 days left")
        self.assertContains(page, "Earned Leave (EL), no balance (leave without pay)")

    def test_the_workload_box_is_ticked_by_default_for_staff(self):
        html = self.client.get(self.url).content.decode()
        self.assertRegex(html, r'name="requires_workload"\s+value="on"\s+checked')

    def test_the_principal_sees_a_note_instead_of_the_workload_box(self):
        self.sign_in(self.principal)
        page = self.client.get(self.url)
        self.assertNotContains(page, 'name="requires_workload"')
        self.assertContains(page, "skips the workload and head-of-department stages")

    def test_a_blocker_is_explained_and_the_form_is_hidden(self):
        self.physics.hod = None
        self.physics.save()
        page = self.client.get(self.url)
        self.assertContains(page, "Physics has no head of department yet")
        self.assertNotContains(page, 'name="leave_type"')

    def test_no_active_year_is_explained(self):
        self.year.is_active = False
        self.year.save()
        self.assertContains(self.client.get(self.url), "No academic year is active yet")


class ApplyPreviewTests(LeaveTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.asha)
        self.url = reverse(URL)

    def preview(self, **extra):
        return self.client.post(self.url, {**self.payload(), "step": "preview", **extra})

    def test_the_preview_lists_every_date_and_saves_nothing(self):
        self.holiday(WED, "Diwali")
        page = self.preview(end_date=SUN.isoformat())
        self.assertContains(page, "Check your leave")
        self.assertContains(page, "Mon, 09 Nov 2026")
        self.assertContains(page, "Holiday: Diwali")
        self.assertContains(page, ">Sunday<")
        self.assertContains(page, "Not counted")
        self.assertContains(page, "Paid")
        self.assertEqual((LeaveApplication.objects.count(), LeaveDay.objects.count()), (0, 0))

    def test_only_counted_days_offer_a_day_part_choice(self):
        page = self.preview(end_date=SUN.isoformat())
        html = page.content.decode()
        self.assertIn(f'name="part_{MON.isoformat()}"', html)
        self.assertNotIn(f'name="part_{SUN.isoformat()}"', html)

    def test_unpaid_days_and_warnings_are_shown(self):
        StaffLeaveBalance.objects.update(total=D("2"))
        page = self.preview()
        self.assertContains(page, "Leave without pay")
        self.assertContains(page, "will be leave without pay")
        self.assertContains(page, "This does not stop you applying.")

    def test_changing_a_day_to_a_half_day_updates_the_totals(self):
        page = self.preview(**{f"part_{MON}": "HALF_AM"})
        self.assertContains(page, '<div class="h5 mb-0">4.5</div>')
        self.assertContains(page, f'<option value="HALF_AM" selected>')

    def test_the_next_status_is_shown_in_words(self):
        self.assertContains(self.preview(), "<strong>Workload pending</strong>")
        data = self.payload()
        data.pop("requires_workload")
        page = self.client.post(self.url, {**data, "step": "preview"})
        self.assertContains(page, "<strong>HOD pending</strong>")

    def test_details_are_carried_to_the_next_step(self):
        html = self.preview().content.decode()
        self.assertIn(f'name="leave_type" value="{self.cl.pk}"', html)
        self.assertIn('name="start_date" value="2026-11-09"', html)
        self.assertIn('name="requires_workload" value="on"', html)

    def test_a_multi_line_reason_survives_the_preview(self):
        page = self.preview(reason="Line one\nLine two")
        self.assertContains(page, ">Line one\nLine two</textarea>")

    def test_user_text_is_escaped(self):
        page = self.preview(reason="<script>alert(1)</script>")
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(page, "&lt;script&gt;")

    def test_invalid_input_returns_to_the_form_with_errors_and_kept_values(self):
        page = self.preview(reason="", start_date="nonsense", end_date=FRI.isoformat())
        self.assertContains(page, "Start date must be a valid date.")
        self.assertContains(page, "Reason is required.")
        self.assertContains(page, 'value="2026-11-13"')
        self.assertContains(page, "Apply for leave")

    def test_an_overlap_is_explained(self):
        application = LeaveApplication.objects.create(
            applicant=self.asha, academic_year=self.year, leave_type=self.cl, start_date=WED, end_date=WED,
            reason="x", status=Status.APPROVED,
        )
        LeaveDay.objects.create(leave=application, staff=self.asha, date=WED)
        self.assertContains(self.preview(), "You already have leave on Wed 11 Nov 2026")

    def test_edit_returns_to_the_form_with_what_was_typed_and_no_errors(self):
        page = self.client.post(self.url, {**self.payload(), "step": "edit", "reason": "Keep me"})
        self.assertContains(page, "Keep me")
        self.assertNotContains(page, "invalid-feedback")

    def test_an_unknown_step_is_treated_as_a_preview(self):
        self.assertContains(self.preview(step="whatever"), "Check your leave")


class ApplySubmitTests(LeaveTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.asha)
        self.url = reverse(URL)

    def submit(self, **extra):
        return self.client.post(self.url, {**self.payload(), "step": "submit", **extra}, follow=True)

    def test_submitting_saves_and_opens_the_detail_page(self):
        response = self.submit()
        application = LeaveApplication.objects.get()
        self.assertRedirects(response, reverse("leave_detail", args=[application.pk]))
        self.assertContains(response, f"Leave application #{application.pk} submitted.")
        self.assertContains(response, "Waiting for workload cover to be arranged and accepted.")
        self.assertEqual(application.days.count(), 5)

    def test_the_principal_ends_at_the_principal_stage(self):
        self.sign_in(self.principal)
        self.submit()
        self.assertEqual(LeaveApplication.objects.get().status, Status.PRINCIPAL_PENDING)

    def test_a_second_identical_submit_is_refused_and_nothing_extra_is_saved(self):
        self.submit()
        response = self.submit()
        self.assertContains(response, "You already have leave on")
        self.assertEqual(LeaveApplication.objects.count(), 1)

    def test_an_invalid_submit_returns_to_the_form_and_saves_nothing(self):
        response = self.submit(reason="")
        self.assertContains(response, "Reason is required.")
        self.assertEqual(LeaveApplication.objects.count(), 0)

    def test_a_multi_line_reason_is_saved_exactly(self):
        self.submit(reason="Line one\nLine two")
        self.assertEqual(LeaveApplication.objects.get().reason, "Line one\nLine two")

    def test_getting_the_page_never_saves_anything(self):
        self.client.get(self.url + "?step=submit&leave_type=1&start_date=2026-11-09&end_date=2026-11-13&reason=x")
        self.assertEqual(LeaveApplication.objects.count(), 0)


class ApplyDisplayTests(LeaveTestCase):
    def test_the_policy_page_says_cross_year_leave_is_not_available_yet(self):
        self.client.login(username=make_admin().username, password=PASSWORD)
        self.assertContains(self.client.get(reverse("policy_edit")), "(not available yet)")
