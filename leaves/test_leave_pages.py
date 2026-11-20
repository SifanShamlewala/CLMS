import datetime
from decimal import Decimal as D

from django.urls import reverse

from core.testutils import PASSWORD, make_admin, make_staff
from leaves import applications
from leaves.models import LeaveApplication, LeaveDay, StaffLeaveBalance
from leaves.testutils import FRI, MON, NEXT_MON, SAT, SUN, THU, TUE, WED, LeaveTestCase, Role

Status = LeaveApplication.Status


class PagesTestCase(LeaveTestCase):
    def apply(self, person=None, **extra):
        return applications.submit_application(user=(person or self.asha).user, data=self.payload(**extra))

    def detail_url(self, application):
        return reverse("leave_detail", args=[application.pk])

    def bare_application(self, day, applicant=None, **extra):
        values = dict(
            applicant=applicant or self.asha, academic_year=self.year, leave_type=self.cl,
            start_date=day, end_date=day, reason="x",
        )
        values.update(extra)
        return LeaveApplication.objects.create(**values)


class MyLeavesTests(PagesTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.asha)
        self.url = reverse("leave_mine")

    def test_empty_state_invites_the_first_application(self):
        self.assertContains(self.client.get(self.url), "You haven’t applied for leave yet.")

    def test_only_my_own_applications_are_listed_newest_dates_first(self):
        mine_old = self.bare_application(MON)
        mine_new = self.bare_application(THU)
        self.bare_application(WED, applicant=self.hod)
        self.bare_application(TUE, is_deleted=True)
        listed = [a.pk for a in self.client.get(self.url).context["page_obj"]]
        self.assertEqual(listed, [mine_new.pk, mine_old.pk])

    def test_totals_come_from_the_day_rows_not_the_snapshot_fields(self):
        StaffLeaveBalance.objects.update(total=D("2"))
        application = self.apply(end_date=SUN.isoformat(), **{f"part_{MON}": "HALF_AM"})
        LeaveApplication.objects.filter(pk=application.pk).update(
            total_days_counted=D("99"), total_days_paid=D("99"), total_days_lwp=D("99")
        )
        row = list(self.client.get(self.url).context["page_obj"])[0]
        # Mon 0.5 + Tue 1 paid, Wed 1 + Thu 1 + Fri 1 + Sat 1 unpaid, Sunday not counted
        self.assertEqual((row.counted, row.paid, row.lwp), (D("5.5"), D("1.5"), D("4.0")))

    def test_an_application_without_day_rows_shows_zeros_not_blanks(self):
        self.bare_application(MON)
        row = list(self.client.get(self.url).context["page_obj"])[0]
        self.assertEqual((row.counted, row.paid, row.lwp), (D("0"), D("0"), D("0")))

    def test_balances_for_the_active_year_are_shown(self):
        StaffLeaveBalance.objects.update(used=D("2.5"))
        page = self.client.get(self.url)
        self.assertContains(page, "My balances in 2026-27")
        self.assertContains(page, "<strong>7.5</strong>")

    def test_without_balances_the_page_explains_unpaid_leave(self):
        StaffLeaveBalance.objects.all().delete()
        self.assertContains(self.client.get(self.url), "No balances are set for you yet")

    def test_filters_by_status_and_year_and_ignores_junk(self):
        self.bare_application(MON, status=Status.APPROVED)
        self.bare_application(TUE, status=Status.REJECTED)
        count = lambda query: len(self.client.get(f"{self.url}?{query}").context["page_obj"])
        self.assertEqual(count(""), 2)
        self.assertEqual(count("status=APPROVED"), 1)
        self.assertEqual(count("status=NOPE&year=abc"), 2)
        self.assertEqual(count(f"year={self.year.pk}"), 2)
        self.assertEqual(count("year=999"), 0)
        self.assertContains(self.client.get(f"{self.url}?status=CANCELLED"), "No applications match these filters.")

    def test_pages_of_twenty_keep_the_filters(self):
        for i in range(25):
            self.bare_application(datetime.date(2026, 7, 1) + datetime.timedelta(days=i), status=Status.APPROVED)
        page1 = self.client.get(f"{self.url}?status=APPROVED")
        self.assertEqual(len(page1.context["page_obj"]), 20)
        self.assertContains(page1, "status=APPROVED&amp;page=2")
        self.assertEqual(len(self.client.get(f"{self.url}?status=APPROVED&page=2").context["page_obj"]), 5)

    def test_every_row_links_to_its_detail_page(self):
        application = self.bare_application(MON)
        self.assertContains(self.client.get(self.url), self.detail_url(application))


class LeaveDetailTests(PagesTestCase):
    def setUp(self):
        super().setUp()
        self.holiday(WED, "Diwali")
        StaffLeaveBalance.objects.update(total=D("2"))
        self.application = self.apply(end_date=SUN.isoformat())
        self.url = self.detail_url(self.application)

    def test_the_applicant_sees_the_summary_the_day_table_and_the_next_step(self):
        self.sign_in(self.asha)
        page = self.client.get(self.url)
        self.assertContains(page, f"Leave application #{self.application.pk}")
        self.assertContains(page, "Workload pending")
        self.assertContains(page, "Waiting for workload cover")
        self.assertContains(page, "Family function")
        self.assertContains(page, "Holiday: Diwali")
        self.assertContains(page, ">Sunday<")
        self.assertContains(page, "Leave without pay")
        self.assertContains(page, "Balance at submission")
        self.assertContains(page, "My leaves")

    def test_totals_are_added_up_from_the_day_rows(self):
        self.sign_in(self.asha)
        LeaveApplication.objects.filter(pk=self.application.pk).update(total_days_counted=D("99"))
        totals = self.client.get(self.url).context["totals"]
        self.assertEqual((totals["counted"], totals["paid"], totals["lwp"]), (D("5"), D("2"), D("3")))

    def test_each_day_row_shows_its_result_and_status(self):
        self.sign_in(self.asha)
        days = self.client.get(self.url).context["days"]
        self.assertEqual([d.date for d in days], [MON + datetime.timedelta(days=i) for i in range(7)])

    def test_who_may_open_it(self):
        allowed = [self.asha, self.hod, self.principal]
        for person in allowed:
            self.sign_in(person)
            self.assertEqual(self.client.get(self.url).status_code, 200, person)
        self.client.logout()
        self.client.login(username=make_admin().username, password=PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_everyone_else_gets_a_404_never_a_403(self):
        for person in (make_staff("colleague", self.physics), self.maths_hod, make_staff("other", self.maths)):
            self.sign_in(person)
            self.assertEqual(self.client.get(self.url).status_code, 404, person)

    def test_anonymous_users_are_sent_to_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")

    def test_removed_and_unknown_applications_are_404(self):
        self.sign_in(self.asha)
        LeaveApplication.objects.filter(pk=self.application.pk).update(is_deleted=True)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("leave_detail", args=[9999])).status_code, 404)

    def test_the_reason_is_escaped(self):
        other = self.apply(start_date=NEXT_MON.isoformat(), end_date=NEXT_MON.isoformat(), reason="<b>bold</b>")
        self.sign_in(self.asha)
        page = self.client.get(self.detail_url(other))
        self.assertNotContains(page, "<b>bold</b>")
        self.assertContains(page, "&lt;b&gt;bold&lt;/b&gt;")

    def test_a_heads_view_does_not_offer_the_applicants_my_leaves_button(self):
        self.sign_in(self.hod)
        page = self.client.get(self.url)
        self.assertFalse(page.context["is_owner"])
        self.assertNotContains(page, 'btn btn-outline-secondary" href="/leaves/mine/"')
        self.sign_in(self.asha)
        self.assertContains(self.client.get(self.url), 'btn btn-outline-secondary" href="/leaves/mine/"')

    def test_the_next_step_text_follows_the_status(self):
        self.sign_in(self.asha)
        for status, text in (
            (Status.HOD_PENDING, "Waiting for review by the head of department."),
            (Status.PRINCIPAL_PENDING, "Waiting for the Principal's decision."),
            (Status.APPROVED, "Approved."),
        ):
            LeaveApplication.objects.filter(pk=self.application.pk).update(status=status)
            self.assertContains(self.client.get(self.url), text.replace("'", "&#x27;"))


class NavigationTests(PagesTestCase):
    def test_people_with_a_profile_see_the_leave_links(self):
        self.sign_in(self.asha)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, reverse("leave_apply"))
        self.assertContains(page, reverse("leave_mine"))
        self.assertNotContains(page, reverse("department_list"))

    def test_the_administrator_without_a_profile_sees_setup_but_not_the_leave_links(self):
        self.client.login(username=make_admin().username, password=PASSWORD)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, reverse("department_list"))
        self.assertNotContains(page, reverse("leave_apply"))

    def test_an_administrator_who_is_also_staff_sees_both(self):
        admin = make_admin("boss")
        from accounts.models import StaffProfile
        StaffProfile.objects.create(user=admin, employee_id="BOSS", department=self.admin_dept)
        self.client.login(username="boss", password=PASSWORD)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, reverse("leave_apply"))
        self.assertContains(page, reverse("department_list"))

    def test_the_current_page_is_highlighted(self):
        self.sign_in(self.asha)
        html = self.client.get(reverse("leave_mine")).content.decode()
        self.assertRegex(html, r'class="nav-link active"\s+href="/leaves/mine/"')
