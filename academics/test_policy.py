from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from academics import services
from academics.models import PolicyConfig
from accounts.models import StaffProfile
from core.testutils import login, make_admin, make_staff
from core.validation import DomainError

Role = StaffProfile.Role
URL_NAME = "policy_edit"


def post_data(**extra):
    """What a browser sends: only ticked boxes appear."""
    data = {"hod_own_leave_route": "SKIP_TO_PRINCIPAL"}
    data.update(extra)
    return data


class PolicyAccessTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        url = reverse(URL_NAME)
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_non_administrators_get_403_and_cannot_save(self):
        for role in (Role.STAFF, Role.HOD, Role.PRINCIPAL):
            self.client.logout()
            login(self.client, make_staff(f"u_{role}", role=role).user)
            self.assertEqual(self.client.get(reverse(URL_NAME)).status_code, 403)
            self.assertEqual(self.client.post(reverse(URL_NAME), post_data(allow_cross_year_leave="on")).status_code, 403)
        self.assertFalse(PolicyConfig.get().allow_cross_year_leave)

    def test_administrator_can_open_the_page(self):
        login(self.client, make_admin())
        self.assertEqual(self.client.get(reverse(URL_NAME)).status_code, 200)


class PolicyFormTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.url = reverse(URL_NAME)

    def save(self, **extra):
        return self.client.post(self.url, post_data(**extra), follow=True)

    def test_first_visit_shows_the_approved_defaults(self):
        page = self.client.get(self.url)
        form = page.context["form"]
        self.assertEqual(form["weekly_off_days"], ["6"])
        self.assertFalse(form["allow_cross_year_leave"] or form["reversal_includes_today"])
        self.assertTrue(form["principal_bypass_stages"] and form["admin_adjust_enabled"])
        self.assertEqual(form["hod_own_leave_route"], "SKIP_TO_PRINCIPAL")
        self.assertEqual(PolicyConfig.objects.count(), 1)

    def test_sunday_is_shown_ticked_and_locked(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'id="id_sunday" checked disabled')

    def test_save_reads_ticked_boxes_and_treats_missing_ones_as_off(self):
        response = self.save(
            weekly_off_days=["5"], allow_cross_year_leave="on", reversal_includes_today="on",
            hod_own_leave_route="PRINCIPAL_AS_HOD",
        )
        self.assertContains(response, "Policy settings saved.")
        policy = PolicyConfig.get()
        self.assertEqual(policy.weekly_off_days_set, {5, 6})
        self.assertTrue(policy.allow_cross_year_leave and policy.reversal_includes_today)
        self.assertFalse(policy.principal_bypass_stages or policy.admin_adjust_enabled)  # unticked
        self.assertEqual(policy.hod_own_leave_route, "PRINCIPAL_AS_HOD")

    def test_sunday_is_always_stored_even_when_nothing_is_posted(self):
        self.save()
        self.assertEqual(PolicyConfig.get().weekly_off_days, "6")

    def test_tampering_cannot_remove_sunday(self):
        self.save(weekly_off_days=["5"])
        self.assertIn(6, PolicyConfig.get().weekly_off_days_set)

    def test_saved_values_are_shown_again(self):
        self.save(weekly_off_days=["4", "5"], allow_cross_year_leave="on")
        form = self.client.get(self.url).context["form"]
        self.assertEqual(form["weekly_off_days"], ["4", "5", "6"])
        self.assertTrue(form["allow_cross_year_leave"])
        html = self.client.get(self.url).content.decode()
        self.assertRegex(html, r'value="5"\s+checked')       # Saturday ticked
        self.assertNotRegex(html, r'value="1"\s+checked')    # Tuesday not ticked

    def test_invalid_weekdays_rejected_and_nothing_is_saved(self):
        for bad in ("7", "-1", "x", "", "²"):
            response = self.client.post(self.url, post_data(weekly_off_days=[bad], allow_cross_year_leave="on"))
            self.assertContains(response, "Choose valid weekdays.", msg_prefix=bad)
        self.assertFalse(PolicyConfig.get().allow_cross_year_leave)

    def test_every_weekday_cannot_be_off(self):
        response = self.client.post(self.url, post_data(weekly_off_days=["0", "1", "2", "3", "4", "5"]))
        self.assertContains(response, "At least one weekday must stay a working day.")
        self.assertEqual(PolicyConfig.get().weekly_off_days, "6")

    def test_invalid_route_rejected(self):
        response = self.client.post(self.url, post_data(hod_own_leave_route="NOBODY"))
        self.assertContains(response, "Choose a valid route.")

    def test_failed_post_keeps_what_was_ticked(self):
        response = self.client.post(self.url, post_data(
            weekly_off_days=["5", "9"], allow_cross_year_leave="on", hod_own_leave_route="NOBODY"))
        form = response.context["form"]
        self.assertTrue(form["allow_cross_year_leave"])
        self.assertIn("5", form["weekly_off_days"])

    def test_saving_never_creates_a_second_policy_row(self):
        self.save(allow_cross_year_leave="on")
        self.save()
        self.assertEqual(PolicyConfig.objects.count(), 1)


class PolicyServiceTests(TestCase):
    def test_non_administrators_cannot_call_the_service_directly(self):
        with self.assertRaises(PermissionDenied):
            services.update_policy(data=post_data(allow_cross_year_leave="on"), actor=make_staff("s").user)
        self.assertFalse(PolicyConfig.get().allow_cross_year_leave)

    def test_plain_dict_input_works_too(self):
        services.update_policy(
            data={"weekly_off_days": ["5"], "hod_own_leave_route": "SKIP_TO_PRINCIPAL"}, actor=make_admin()
        )
        self.assertEqual(PolicyConfig.get().weekly_off_days_set, {5, 6})

    def test_errors_are_reported_together(self):
        with self.assertRaises(DomainError) as ctx:
            services.update_policy(data={"weekly_off_days": ["9"], "hod_own_leave_route": "X"}, actor=make_admin())
        self.assertEqual(set(ctx.exception.errors), {"weekly_off_days", "hod_own_leave_route"})
