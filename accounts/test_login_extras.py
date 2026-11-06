from django.test import TestCase
from django.urls import reverse

from accounts.views import GENERIC_ERROR
from core.testutils import PASSWORD, make_admin, make_staff


class InactiveProfileLoginTests(TestCase):
    def test_inactive_profile_is_refused_with_generic_error(self):
        staff = make_staff("gone", is_active=False)
        response = self.client.post(reverse("login"), {"username": "gone", "password": PASSWORD})
        self.assertContains(response, GENERIC_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(staff.user.username, "gone")

    def test_active_profile_can_log_in(self):
        make_staff("here")
        response = self.client.post(reverse("login"), {"username": "here", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard"))

    def test_administrator_without_profile_can_log_in_and_see_dashboard(self):
        make_admin("boss")
        response = self.client.post(reverse("login"), {"username": "boss", "password": PASSWORD}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Administrator")
        self.assertContains(response, reverse("department_list"))

    def test_user_without_profile_sees_setup_warning_not_a_crash(self):
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("plain", password=PASSWORD)
        self.client.login(username="plain", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "no staff profile")
        self.assertNotContains(response, reverse("department_list"))


class NextRedirectTests(TestCase):
    def setUp(self):
        make_admin("boss")
        self.login_url = reverse("login")
        self.target = reverse("designation_list")

    def test_login_returns_to_requested_page(self):
        response = self.client.post(self.login_url, {"username": "boss", "password": PASSWORD, "next": self.target})
        self.assertRedirects(response, self.target)

    def test_anonymous_visit_then_login_round_trip(self):
        first = self.client.get(self.target)
        self.assertRedirects(first, f"{self.login_url}?next={self.target}")
        page = self.client.get(f"{self.login_url}?next={self.target}")
        self.assertContains(page, f'name="next" value="{self.target}"')

    def test_external_next_targets_are_ignored(self):
        for evil in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            self.client.logout()
            response = self.client.post(self.login_url, {"username": "boss", "password": PASSWORD, "next": evil})
            self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_next_survives_a_failed_attempt(self):
        response = self.client.post(self.login_url, {"username": "boss", "password": "bad", "next": self.target})
        self.assertContains(response, f'name="next" value="{self.target}"')