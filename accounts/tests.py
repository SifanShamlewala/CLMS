from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()

GENERIC_ERROR = "Invalid username or password."
REQUIRED_ERROR = "Both username and password are required."


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="asha", password="Str0ng-pass!"
        )
        self.login_url = reverse("login")
        self.dashboard_url = reverse("dashboard")

    def test_empty_fields_show_error(self):
        response = self.client.post(self.login_url, {"username": "", "password": ""})
        self.assertContains(response, REQUIRED_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_shows_generic_error(self):
        response = self.client.post(
            self.login_url, {"username": "asha", "password": "wrong-pass"}
        )
        self.assertContains(response, GENERIC_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_username_shows_same_generic_error(self):
        response = self.client.post(
            self.login_url, {"username": "nobody", "password": "Str0ng-pass!"}
        )
        self.assertContains(response, GENERIC_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_correct_login_redirects_to_dashboard(self):
        response = self.client.post(
            self.login_url, {"username": "asha", "password": "Str0ng-pass!"}
        )
        self.assertRedirects(response, self.dashboard_url)
        self.assertIn("_auth_user_id", self.client.session)

    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertRedirects(
            response, f"{self.login_url}?next={self.dashboard_url}"
        )

    def test_username_is_kept_after_failed_login(self):
        response = self.client.post(
            self.login_url, {"username": "asha", "password": "wrong-pass"}
        )
        self.assertContains(response, 'value="asha"')
        self.assertNotContains(response, "wrong-pass")

    def test_logout_requires_post_and_logs_out(self):
        self.client.login(username="asha", password="Str0ng-pass!")
        get_response = self.client.get(reverse("logout"))
        self.assertEqual(get_response.status_code, 405)
        post_response = self.client.post(reverse("logout"))
        self.assertRedirects(post_response, self.login_url)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logged_in_user_is_redirected_away_from_login_page(self):
        self.client.login(username="asha", password="Str0ng-pass!")
        response = self.client.get(self.login_url)
        self.assertRedirects(response, self.dashboard_url)