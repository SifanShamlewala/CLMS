import datetime
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, LeaveType, PolicyConfig
from accounts.models import StaffProfile
from core.testutils import login, make_admin, make_department, make_staff
from core.validation import DomainError
from leaves import services
from leaves.models import StaffLeaveBalance

Role = StaffProfile.Role


class BalanceTestCase(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.year = AcademicYear.objects.create(
            name="2026-27", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31), is_active=True
        )
        self.cl = LeaveType.objects.create(name="Casual Leave", code="CL")
        self.el = LeaveType.objects.create(name="Earned Leave", code="EL")
        self.physics = make_department("Physics")
        self.maths = make_department("Mathematics")
        self.asha = make_staff("asha", self.physics)
        self.asha.user.first_name = "Asha"
        self.asha.user.save()

    def payload(self, **extra):
        data = {"staff": str(self.asha.pk), "leave_type": str(self.cl.pk),
                "academic_year": str(self.year.pk), "total": "12"}
        data.update(extra)
        return data

    def make_balance(self, staff=None, leave_type=None, total="10", used="0", year=None):
        balance = StaffLeaveBalance.objects.create(
            staff=staff or self.asha, leave_type=leave_type or self.cl,
            academic_year=year or self.year, total=D(total),
        )
        if D(used):
            StaffLeaveBalance.objects.filter(pk=balance.pk).update(used=D(used))
            balance.refresh_from_db()
        return balance


class BalanceAccessTests(BalanceTestCase):
    def setUp(self):
        super().setUp()
        self.balance = self.make_balance()
        self.urls = [
            reverse("balance_list"), reverse("balance_create"), reverse("balance_edit", args=[self.balance.pk]),
        ]

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        for url in self.urls:
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_non_administrators_get_403_and_change_nothing(self):
        for role in (Role.STAFF, Role.HOD, Role.PRINCIPAL):
            self.client.logout()
            user = make_staff(f"u_{role}", self.maths, role).user
            login(self.client, user)
            for url in self.urls:
                self.assertEqual(self.client.get(url).status_code, 403, f"{role} {url}")
            self.assertEqual(self.client.post(self.urls[1], self.payload()).status_code, 403)
            self.assertEqual(
                self.client.post(self.urls[2], {"total": "99", "remarks": "x"}).status_code, 403
            )
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total, D("10"))

    def test_administrator_can_open_every_page(self):
        for url in self.urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class BalanceListTests(BalanceTestCase):
    def test_no_years_shows_guidance(self):
        AcademicYear.objects.all().delete()
        self.assertContains(self.client.get(reverse("balance_list")), "Add an academic year first")

    def test_empty_year_shows_empty_state(self):
        self.assertContains(self.client.get(reverse("balance_list")), "No balances in 2026-27 yet")

    def test_rows_show_total_used_and_remaining(self):
        self.make_balance(total="10", used="2.5")
        response = self.client.get(reverse("balance_list"))
        self.assertContains(response, "Asha")
        self.assertContains(response, "<strong>7.5</strong>")

    def test_defaults_to_the_active_year_and_year_query_switches(self):
        other = AcademicYear.objects.create(
            name="2025-26", start_date=datetime.date(2025, 6, 1), end_date=datetime.date(2026, 5, 31)
        )
        self.make_balance(total="9", year=other)
        self.make_balance(total="11")
        self.assertEqual(self.client.get(reverse("balance_list")).context["selected"], self.year)
        response = self.client.get(f"{reverse('balance_list')}?year={other.pk}")
        self.assertEqual([b.total for b in response.context["page_obj"]], [D("9")])
        self.assertEqual(self.client.get(f"{reverse('balance_list')}?year=abc").context["selected"], self.year)

    def test_filters(self):
        ravi = make_staff("ravi", self.maths)
        self.make_balance(self.asha, self.cl)
        self.make_balance(self.asha, self.el)
        self.make_balance(ravi, self.cl)
        url = reverse("balance_list")
        count = lambda query: len(self.client.get(f"{url}?{query}").context["page_obj"])
        self.assertEqual(count(""), 3)
        self.assertEqual(count(f"department={self.maths.pk}"), 1)
        self.assertEqual(count(f"leave_type={self.el.pk}"), 1)
        self.assertEqual(count("q=asha"), 2)
        self.assertEqual(count("q=zzz"), 0)
        self.assertEqual(count("department=abc&leave_type=x"), 3)
        self.assertContains(self.client.get(f"{url}?q=zzz"), "No balances match these filters")

    def test_pagination_keeps_filters(self):
        for i in range(15):
            person = make_staff(f"p{i:02d}", self.physics)
            self.make_balance(person, self.cl)
            self.make_balance(person, self.el)
        url = reverse("balance_list")
        page1 = self.client.get(f"{url}?leave_type=")
        self.assertEqual(len(page1.context["page_obj"]), 25)
        self.assertContains(page1, "page=2")
        self.assertEqual(len(self.client.get(f"{url}?page=2").context["page_obj"]), 5)


class BalanceCreateTests(BalanceTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("balance_create")

    def test_valid_create_starts_with_nothing_used(self):
        response = self.client.post(self.url, self.payload(total="12"), follow=True)
        self.assertRedirects(response, f"{reverse('balance_list')}?year={self.year.pk}")
        self.assertContains(response, "Asha now has 12 days of CL for 2026-27.")
        balance = StaffLeaveBalance.objects.get()
        self.assertEqual((balance.total, balance.used), (D("12.0"), D("0")))

    def test_half_days_and_zero_are_allowed(self):
        self.client.post(self.url, self.payload(total="7.5"))
        self.client.post(self.url, self.payload(leave_type=str(self.el.pk), total="0"))
        self.assertEqual(sorted(b.total for b in StaffLeaveBalance.objects.all()), [D("0"), D("7.5")])

    def test_form_preselects_the_active_year(self):
        self.assertContains(self.client.get(self.url), f'<option value="{self.year.pk}" selected>')

    def test_blank_form_reports_every_field(self):
        response = self.client.post(self.url, {})
        for text in ("Choose a valid staff member.", "Choose a valid leave type.",
                     "Choose a valid academic year.", "Total days must be a number."):
            self.assertContains(response, text)
        self.assertEqual(StaffLeaveBalance.objects.count(), 0)

    def test_total_rules(self):
        cases = {"abc": "must be a number", "-1": "at least 0", "366.5": "at most 366",
                 "7.3": "steps of 0.5", "NaN": "must be a number"}
        for bad, expected in cases.items():
            response = self.client.post(self.url, self.payload(total=bad))
            self.assertContains(response, expected, msg_prefix=bad)
        self.assertEqual(StaffLeaveBalance.objects.count(), 0)
        self.client.post(self.url, self.payload(total="366"))
        self.assertEqual(StaffLeaveBalance.objects.get().total, D("366.0"))

    def test_inactive_staff_and_leave_type_cannot_be_chosen(self):
        gone = make_staff("gone", self.maths, is_active=False)
        dead = LeaveType.objects.create(name="Old", code="OLD", is_active=False)
        self.assertContains(self.client.post(self.url, self.payload(staff=str(gone.pk))), "Choose a valid staff member.")
        self.assertContains(self.client.post(self.url, self.payload(leave_type=str(dead.pk))), "Choose a valid leave type.")
        self.assertEqual(StaffLeaveBalance.objects.count(), 0)

    def test_duplicate_balance_is_refused_and_points_to_edit(self):
        self.make_balance()
        response = self.client.post(self.url, self.payload())
        self.assertContains(response, "Asha already has a CL balance for 2026-27. Edit that balance instead.")
        self.assertEqual(StaffLeaveBalance.objects.count(), 1)

    def test_used_cannot_be_set_by_posting_it(self):
        self.client.post(self.url, self.payload(used="5"))
        self.assertEqual(StaffLeaveBalance.objects.get().used, D("0"))

    def test_failed_post_keeps_typed_values(self):
        response = self.client.post(self.url, self.payload(total="7.3"))
        self.assertContains(response, 'value="7.3"')
        self.assertContains(response, f'<option value="{self.asha.pk}" selected>')

    def test_losing_a_race_becomes_a_friendly_error(self):
        with mock.patch.object(StaffLeaveBalance.objects, "create", side_effect=IntegrityError("race")):
            with self.assertRaises(DomainError) as ctx:
                services.create_balance(data=self.payload(), actor=self.admin)
        self.assertIn("form", ctx.exception.errors)


class BalanceEditTests(BalanceTestCase):
    def setUp(self):
        super().setUp()
        self.balance = self.make_balance(total="10", used="3")
        self.url = reverse("balance_edit", args=[self.balance.pk])

    def post(self, **extra):
        data = {"total": "12", "remarks": "Extra allocation approved"}
        data.update(extra)
        return self.client.post(self.url, data, follow=True)

    def test_page_shows_who_what_and_how_much_is_used(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Asha")
        self.assertContains(page, "Casual Leave (CL)")
        self.assertContains(page, "3 days")
        self.assertContains(page, 'value="10"')

    def test_update_changes_only_the_total(self):
        response = self.post(total="12.5")
        self.assertContains(response, "Total changed to 12.5 days.")
        self.balance.refresh_from_db()
        self.assertEqual((self.balance.total, self.balance.used), (D("12.5"), D("3")))

    def test_a_reason_is_required(self):
        response = self.post(remarks="  ")
        self.assertContains(response, "Remarks is required.")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total, D("10"))

    def test_total_cannot_go_below_what_is_used_but_equal_is_fine(self):
        self.assertContains(self.post(total="2.5"), "can&#x27;t be below the 3.0 days already used")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total, D("10"))
        self.post(total="3")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total, D("3"))

    def test_unchanged_total_is_refused(self):
        self.assertContains(self.post(total="10"), "already the current total")

    def test_same_validation_rules_as_create(self):
        self.assertContains(self.post(total="7.3"), "steps of 0.5")
        self.assertContains(self.post(total="-1"), "at least 0")

    def test_person_type_and_year_cannot_be_changed_by_tampering(self):
        other = make_staff("ravi", self.maths)
        self.post(staff=str(other.pk), leave_type=str(self.el.pk), used="0")
        self.balance.refresh_from_db()
        self.assertEqual((self.balance.staff, self.balance.leave_type, self.balance.used), (self.asha, self.cl, D("3")))

    def test_adjustments_can_be_switched_off_in_the_policy(self):
        policy = PolicyConfig.get()
        policy.admin_adjust_enabled = False
        policy.save()
        self.assertContains(self.post(), "switched off in the policy settings")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total, D("10"))

    def test_creating_still_works_when_adjustments_are_off(self):
        policy = PolicyConfig.get()
        policy.admin_adjust_enabled = False
        policy.save()
        response = self.client.post(reverse("balance_create"), self.payload(leave_type=str(self.el.pk)))
        self.assertEqual(response.status_code, 302)

    def test_unknown_id_is_404(self):
        self.assertEqual(self.client.get(reverse("balance_edit", args=[999])).status_code, 404)


class BalanceServiceTests(BalanceTestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        actor = make_staff("actor", self.maths).user
        balance = self.make_balance()
        with self.assertRaises(PermissionDenied):
            services.create_balance(data=self.payload(leave_type=str(self.el.pk)), actor=actor)
        with self.assertRaises(PermissionDenied):
            services.update_balance(balance=balance, data={"total": "1", "remarks": "x"}, actor=actor)
        self.assertEqual(StaffLeaveBalance.objects.count(), 1)
        balance.refresh_from_db()
        self.assertEqual(balance.total, D("10"))
