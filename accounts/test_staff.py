import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from accounts import services
from accounts.models import Department, Designation, StaffProfile
from core.testutils import PASSWORD, login, make_admin, make_department, make_staff
from core.validation import DomainError

User = get_user_model()
Role = StaffProfile.Role
GOOD_PASSWORD = "Tr1cky-Pass-93"


def data(dept, **extra):
    base = {
        "username": "asha.k", "first_name": "Asha", "last_name": "Kulkarni", "email": "asha@example.edu",
        "password": GOOD_PASSWORD, "password2": GOOD_PASSWORD, "employee_id": "E001",
        "department": str(dept.pk), "designation": "", "role": "STAFF", "phone": "", "joined_on": "",
    }
    base.update(extra)
    return base


def edit_data(profile, **extra):
    base = {
        "first_name": profile.user.first_name or "Sam", "last_name": profile.user.last_name,
        "email": profile.user.email, "employee_id": profile.employee_id,
        "department": str(profile.department_id), "designation": str(profile.designation_id or ""),
        "role": profile.role, "phone": profile.phone,
        "joined_on": profile.joined_on.isoformat() if profile.joined_on else "",
    }
    base.update(extra)
    return base


class StaffTestCase(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.physics = make_department("Physics")
        self.maths = make_department("Mathematics")

    def create(self, **extra):
        """Create through the service so department heads are linked as in real use."""
        base = data(self.physics)
        base.update(extra)
        return services.create_staff(data=base, actor=self.admin)


class StaffAccessTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_staff("target", self.physics)
        self.read_urls = [
            reverse("staff_list"), reverse("staff_create"), reverse("staff_edit", args=[self.profile.pk]),
        ]
        self.post_urls = [
            reverse("staff_toggle", args=[self.profile.pk]), reverse("staff_password", args=[self.profile.pk]),
        ]

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        for url in self.read_urls + self.post_urls:
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_every_non_administrator_gets_403(self):
        users = [
            make_staff("s", self.maths, Role.STAFF).user,
            make_staff("h", self.maths, Role.HOD).user,
            make_staff("p", self.maths, Role.PRINCIPAL).user,
            User.objects.create_user("noprofile", password=PASSWORD),
        ]
        for user in users:
            self.client.logout()
            login(self.client, user)
            for url in self.read_urls:
                self.assertEqual(self.client.get(url).status_code, 403, f"{user} GET {url}")
            for url in self.post_urls:
                self.assertEqual(self.client.post(url, {"active": "0"}).status_code, 403, f"{user} POST {url}")
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_active)

    def test_administrator_can_open_pages_and_post_only_urls_reject_get(self):
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in self.post_urls:
            self.assertEqual(self.client.get(url).status_code, 405, url)


class StaffCreateTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("staff_create")

    def test_valid_create_makes_login_and_profile_together(self):
        response = self.client.post(self.url, data(self.physics, joined_on="2024-07-01", phone="+91 98765 43210"), follow=True)
        self.assertRedirects(response, reverse("staff_list"))
        self.assertContains(response, "Staff member “Asha Kulkarni” created.")
        profile = StaffProfile.objects.get(employee_id="E001")
        user = profile.user
        self.assertEqual((user.username, user.first_name, user.last_name, user.email),
                         ("asha.k", "Asha", "Kulkarni", "asha@example.edu"))
        self.assertEqual((profile.department, profile.role, profile.is_active), (self.physics, Role.STAFF, True))
        self.assertEqual(profile.joined_on, datetime.date(2024, 7, 1))
        self.assertEqual(profile.phone, "+91 98765 43210")
        self.assertTrue(user.is_active)

    def test_password_is_hashed_and_new_user_can_sign_in(self):
        self.client.post(self.url, data(self.physics))
        user = User.objects.get(username="asha.k")
        self.assertNotEqual(user.password, GOOD_PASSWORD)
        self.assertTrue(user.check_password(GOOD_PASSWORD))
        fresh = Client()
        response = fresh.post(reverse("login"), {"username": "asha.k", "password": GOOD_PASSWORD})
        self.assertRedirects(response, reverse("dashboard"))

    def test_new_user_is_never_an_administrator(self):
        self.client.post(self.url, data(self.physics))
        user = User.objects.get(username="asha.k")
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_blank_form_reports_every_required_field(self):
        response = self.client.post(self.url, {})
        for text in ("Username is required.", "First name is required.", "Employee ID is required.",
                     "Choose a valid department.", "Choose a valid role.", "Password is required."):
            self.assertContains(response, text)
        self.assertEqual(StaffProfile.objects.count(), 0)

    def test_field_and_password_errors_show_together(self):
        response = self.client.post(self.url, data(self.physics, first_name="", password="", password2=""))
        self.assertContains(response, "First name is required.")
        self.assertContains(response, "Password is required.")

    def test_username_rules(self):
        response = self.client.post(self.url, data(self.physics, username="bad name!"))
        self.assertContains(response, "Username may contain only letters")
        User.objects.create_user("Asha.K", password=PASSWORD)
        response = self.client.post(self.url, data(self.physics))
        self.assertContains(response, "This username is already taken.")

    def test_duplicate_username_and_employee_id_reported_together(self):
        self.create()
        response = self.client.post(self.url, data(self.physics, username="ASHA.K", employee_id="e001"))
        self.assertContains(response, "This username is already taken.")
        self.assertContains(response, "A staff member with this employee ID already exists.")
        self.assertEqual(StaffProfile.objects.count(), 1)

    def test_email_phone_and_date_validation(self):
        response = self.client.post(self.url, data(self.physics, email="nope", phone="call me", joined_on="soon"))
        self.assertContains(response, "Enter a valid email address.")
        self.assertContains(response, "Phone may contain only digits")
        self.assertContains(response, "Joining date must be a valid date.")

    def test_optional_fields_may_be_blank(self):
        response = self.client.post(self.url, data(self.physics, last_name="", email="", phone="", joined_on=""))
        self.assertRedirects(response, reverse("staff_list"))
        self.assertIsNone(StaffProfile.objects.get().joined_on)

    def test_password_rules(self):
        cases = {
            "short": ("Ab1-x", "Ab1-x", "too short"),
            "common": ("password", "password", "too common"),
            "numeric": ("84736201958", "84736201958", "entirely numeric"),
            "similar": ("asha.k1234", "asha.k1234", "too similar"),
        }
        for label, (p1, p2, expected) in cases.items():
            response = self.client.post(self.url, data(self.physics, password=p1, password2=p2))
            self.assertContains(response, expected, msg_prefix=label)
        self.assertEqual(User.objects.filter(username="asha.k").count(), 0)

    def test_password_mismatch(self):
        response = self.client.post(self.url, data(self.physics, password2="Different-Pass-12"))
        self.assertContains(response, "The two passwords don&#x27;t match.")

    def test_passwords_are_never_echoed_back(self):
        response = self.client.post(self.url, data(self.physics, first_name=""))
        self.assertNotContains(response, GOOD_PASSWORD)
        self.assertContains(response, 'value="asha.k"')

    def test_tampered_or_unavailable_choices_rejected(self):
        response = self.client.post(self.url, data(self.physics, department="999", designation="999", role="ADMIN"))
        self.assertContains(response, "Choose a valid department.")
        self.assertContains(response, "Choose a valid designation.")
        self.assertContains(response, "Choose a valid role.")

    def test_inactive_department_and_designation_cannot_be_chosen(self):
        old = make_department("Old", is_active=False)
        dead = Designation.objects.create(title="Retired", is_active=False)
        response = self.client.post(self.url, data(self.physics, department=str(old.pk), designation=str(dead.pk)))
        self.assertContains(response, "Old is inactive")
        self.assertContains(response, "Retired is inactive")

    def test_designation_is_saved(self):
        d = Designation.objects.create(title="Lecturer")
        self.client.post(self.url, data(self.physics, designation=str(d.pk)))
        self.assertEqual(StaffProfile.objects.get().designation, d)

    def test_only_one_principal(self):
        self.create(username="p1", employee_id="P1", role="PRINCIPAL", first_name="Priya", last_name="Rao")
        response = self.client.post(self.url, data(self.maths, username="p2", employee_id="P2", role="PRINCIPAL"))
        self.assertContains(response, "Priya Rao is already the Principal.")
        self.assertEqual(StaffProfile.objects.filter(role=Role.PRINCIPAL).count(), 1)
        self.assertFalse(User.objects.filter(username="p2").exists())

    def test_creating_a_head_links_the_department(self):
        hod = self.create(role="HOD")
        self.physics.refresh_from_db()
        self.assertEqual(self.physics.hod, hod)

    def test_a_department_cannot_have_two_heads(self):
        self.create(username="h1", employee_id="H1", role="HOD", first_name="Hari", last_name="Das")
        response = self.client.post(self.url, data(self.physics, username="h2", employee_id="H2", role="HOD"))
        self.assertContains(response, "Hari Das already heads Physics.")
        self.assertEqual(StaffProfile.objects.filter(role=Role.HOD).count(), 1)

    def test_different_departments_each_get_their_own_head(self):
        self.create(username="h1", employee_id="H1", role="HOD")
        self.create(username="h2", employee_id="H2", role="HOD", department=str(self.maths.pk))
        self.assertIsNotNone(Department.objects.get(pk=self.physics.pk).hod)
        self.assertIsNotNone(Department.objects.get(pk=self.maths.pk).hod)

    def test_failure_halfway_leaves_no_orphan_login(self):
        with mock.patch.object(StaffProfile.objects, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(DomainError):
                services.create_staff(data=data(self.physics), actor=self.admin)
        self.assertFalse(User.objects.filter(username="asha.k").exists())


class StaffEditTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.profile = self.create()
        self.url = reverse("staff_edit", args=[self.profile.pk])

    def post(self, profile=None, **extra):
        profile = profile or self.profile
        return self.client.post(reverse("staff_edit", args=[profile.pk]), edit_data(profile, **extra), follow=True)

    def test_prefill_shows_values_and_username_is_read_only(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'value="Asha"')
        self.assertContains(page, 'value="E001"')
        self.assertContains(page, "asha.k")
        self.assertNotContains(page, 'name="username"')
        self.assertNotContains(page, GOOD_PASSWORD)

    def test_update_details(self):
        d = Designation.objects.create(title="Lecturer")
        response = self.post(first_name="Asha M", email="new@example.edu", phone="12345", designation=str(d.pk),
                             joined_on="2025-01-02", department=str(self.maths.pk))
        self.assertContains(response, "Staff details updated.")
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.user.first_name, self.profile.user.email, self.profile.phone),
                         ("Asha M", "new@example.edu", "12345"))
        self.assertEqual((self.profile.designation, self.profile.department), (d, self.maths))

    def test_username_cannot_be_changed_by_tampering(self):
        self.client.post(self.url, {**edit_data(self.profile), "username": "hacker"})
        self.profile.user.refresh_from_db()
        self.assertEqual(self.profile.user.username, "asha.k")

    def test_own_employee_id_is_fine_but_anothers_is_not(self):
        other = self.create(username="b", employee_id="E002")
        self.assertContains(self.post(employee_id="e001"), "Staff details updated.")
        response = self.post(employee_id="E002")
        self.assertContains(response, "already exists")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.employee_id, "e001")
        self.assertEqual(other.employee_id, "E002")

    def test_promoting_to_head_links_the_department(self):
        self.post(role="HOD")
        self.physics.refresh_from_db()
        self.assertEqual(self.physics.hod_id, self.profile.pk)

    def test_demoting_a_head_clears_the_link_and_warns(self):
        hod = self.create(username="h", employee_id="H1", role="HOD")
        response = self.post(hod, role="STAFF")
        self.assertContains(response, "Physics has no head of department now.")
        self.physics.refresh_from_db()
        self.assertIsNone(self.physics.hod)

    def test_moving_a_head_updates_both_departments(self):
        hod = self.create(username="h", employee_id="H1", role="HOD")
        response = self.post(hod, department=str(self.maths.pk))
        self.assertContains(response, "Physics has no head of department now.")
        self.physics.refresh_from_db()
        self.maths.refresh_from_db()
        self.assertIsNone(self.physics.hod)
        self.assertEqual(self.maths.hod_id, hod.pk)

    def test_cannot_promote_into_a_department_that_has_a_head(self):
        self.create(username="h", employee_id="H1", role="HOD", first_name="Hari", last_name="Das")
        response = self.post(role="HOD")
        self.assertContains(response, "Hari Das already heads Physics.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, Role.STAFF)

    def test_saving_an_existing_head_unchanged_works(self):
        hod = self.create(username="h", employee_id="H1", role="HOD")
        self.assertContains(self.post(hod), "Staff details updated.")
        self.physics.refresh_from_db()
        self.assertEqual(self.physics.hod_id, hod.pk)

    def test_principal_rules_on_edit(self):
        principal = self.create(username="p", employee_id="P1", role="PRINCIPAL", first_name="Priya", last_name="Rao")
        self.assertContains(self.post(principal), "Staff details updated.")
        self.assertContains(self.post(role="PRINCIPAL"), "Priya Rao is already the Principal.")
        self.post(principal, role="STAFF")
        self.assertContains(self.post(role="PRINCIPAL"), "Staff details updated.")
        self.assertEqual(StaffProfile.objects.filter(role=Role.PRINCIPAL).count(), 1)

    def test_current_inactive_department_stays_selectable_but_cannot_be_newly_chosen(self):
        self.physics.is_active = False
        self.physics.save()
        self.assertContains(self.post(first_name="Still ok"), "Staff details updated.")
        self.assertContains(self.client.get(self.url), ">Physics<")
        other = make_staff("zed", self.maths)
        response = self.post(other, department=str(self.physics.pk))
        self.assertContains(response, "Physics is inactive")

    def test_inactive_member_does_not_take_over_a_head_seat(self):
        head = self.create(username="h", employee_id="H1", role="HOD")
        sleeper = make_staff("sleeper", self.physics, is_active=False)
        self.assertContains(self.post(sleeper, role="HOD"), "Staff details updated.")
        self.physics.refresh_from_db()
        self.assertEqual(self.physics.hod_id, head.pk)

    def test_validation_errors_keep_posted_values(self):
        response = self.post(first_name="", email="bad")
        self.assertContains(response, "First name is required.")
        self.assertContains(response, 'value="bad"')

    def test_unknown_id_is_404(self):
        self.assertEqual(self.client.get(reverse("staff_edit", args=[999])).status_code, 404)


class StaffToggleTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.profile = self.create()

    def toggle(self, profile, active):
        return self.client.post(reverse("staff_toggle", args=[profile.pk]), {"active": "1" if active else "0"}, follow=True)

    def login_attempt(self):
        return Client().post(reverse("login"), {"username": "asha.k", "password": GOOD_PASSWORD})

    def test_deactivating_blocks_login_and_reactivating_restores_it(self):
        self.assertRedirects(self.login_attempt(), reverse("dashboard"))
        response = self.toggle(self.profile, False)
        self.assertContains(response, "Asha Kulkarni deactivated.")
        self.profile.refresh_from_db()
        self.profile.user.refresh_from_db()
        self.assertFalse(self.profile.is_active)
        self.assertFalse(self.profile.user.is_active)
        self.assertContains(self.login_attempt(), "Invalid username or password.")
        self.toggle(self.profile, True)
        self.profile.refresh_from_db()
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.is_active and self.profile.user.is_active)
        self.assertRedirects(self.login_attempt(), reverse("dashboard"))

    def test_toggle_is_idempotent(self):
        self.toggle(self.profile, False)
        self.toggle(self.profile, False)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_active)

    def test_deactivating_a_head_clears_the_department_and_warns(self):
        hod = self.create(username="h", employee_id="H1", role="HOD")
        response = self.toggle(hod, False)
        self.assertContains(response, "Physics has no head of department now.")
        self.physics.refresh_from_db()
        self.assertIsNone(self.physics.hod)

    def test_reactivating_a_head_restores_the_seat_when_free(self):
        hod = self.create(username="h", employee_id="H1", role="HOD")
        self.toggle(hod, False)
        self.toggle(hod, True)
        self.physics.refresh_from_db()
        self.assertEqual(self.physics.hod_id, hod.pk)

    def test_reactivating_a_head_fails_if_someone_else_took_the_seat(self):
        old = self.create(username="h", employee_id="H1", role="HOD")
        self.toggle(old, False)
        self.create(username="h2", employee_id="H2", role="HOD", first_name="New", last_name="Head")
        response = self.toggle(old, True)
        self.assertContains(response, "New Head already heads Physics.")
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_administrator_cannot_deactivate_their_own_account(self):
        mine = StaffProfile.objects.create(user=self.admin, employee_id="BOSS", department=self.physics)
        response = self.toggle(mine, False)
        self.assertContains(response, "You can&#x27;t deactivate your own account.")
        mine.refresh_from_db()
        self.assertTrue(mine.is_active)


class PasswordResetTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.profile = self.create()
        self.url = reverse("staff_password", args=[self.profile.pk])

    def reset(self, p1, p2=None):
        return self.client.post(self.url, {"password": p1, "password2": p2 if p2 is not None else p1}, follow=True)

    def test_valid_reset_changes_the_password(self):
        response = self.reset("New-Secret-47x")
        self.assertContains(response, "Password changed.")
        user = User.objects.get(username="asha.k")
        self.assertTrue(user.check_password("New-Secret-47x"))
        self.assertFalse(user.check_password(GOOD_PASSWORD))

    def test_existing_sessions_stop_working(self):
        member = Client()
        member.post(reverse("login"), {"username": "asha.k", "password": GOOD_PASSWORD})
        self.assertEqual(member.get(reverse("dashboard")).status_code, 200)
        self.reset("New-Secret-47x")
        self.assertEqual(member.get(reverse("dashboard")).status_code, 302)

    def test_invalid_resets_change_nothing(self):
        self.assertContains(self.reset("New-Secret-47x", "Other-Secret-47x"), "don&#x27;t match")
        self.assertContains(self.reset(""), "Password is required.")
        self.assertContains(self.reset("password"), "too common")
        self.assertTrue(User.objects.get(username="asha.k").check_password(GOOD_PASSWORD))

    def test_reset_form_is_on_the_edit_page_and_never_prefilled(self):
        page = self.client.get(reverse("staff_edit", args=[self.profile.pk]))
        self.assertContains(page, "Reset password")
        self.assertNotContains(page, GOOD_PASSWORD)


class StaffListTests(StaffTestCase):
    def named(self, username, first, dept=None, role=Role.STAFF, **kwargs):
        profile = make_staff(username, dept or self.physics, role, **kwargs)
        profile.user.first_name = first
        profile.user.save()
        return profile

    def test_lists_staff_but_not_administrators_without_profile(self):
        self.named("asha", "Asha")
        response = self.client.get(reverse("staff_list"))
        self.assertContains(response, "Asha")
        rows = list(response.context["page_obj"])
        self.assertEqual([r.user.username for r in rows], ["asha"])  # the administrator has no profile

    def test_empty_state_guides_the_administrator(self):
        self.assertContains(self.client.get(reverse("staff_list")), "No staff yet. Add the Principal first")

    def test_filters(self):
        self.named("asha", "Asha")
        self.named("ravi", "Ravi", self.maths, Role.HOD)
        self.named("gone", "Gita", is_active=False)
        url = reverse("staff_list")
        self.assertEqual(len(self.client.get(f"{url}?q=asha").context["page_obj"]), 1)
        self.assertEqual(len(self.client.get(f"{url}?q=EMP-ravi").context["page_obj"]), 1)
        self.assertEqual(len(self.client.get(f"{url}?department={self.maths.pk}").context["page_obj"]), 1)
        self.assertEqual(len(self.client.get(f"{url}?role=HOD").context["page_obj"]), 1)
        self.assertEqual(len(self.client.get(f"{url}?status=inactive").context["page_obj"]), 1)
        self.assertEqual(len(self.client.get(f"{url}?status=active").context["page_obj"]), 2)
        self.assertEqual(len(self.client.get(url).context["page_obj"]), 3)

    def test_no_match_message_and_junk_filters_are_ignored(self):
        self.named("asha", "Asha")
        url = reverse("staff_list")
        self.assertContains(self.client.get(f"{url}?q=zzz"), "No staff match these filters.")
        self.assertEqual(len(self.client.get(f"{url}?department=abc&role=BOSS&status=x").context["page_obj"]), 1)

    def test_pagination_keeps_the_filters(self):
        for i in range(25):
            self.named(f"user{i:02d}", f"Person{i:02d}")
        url = reverse("staff_list")
        page1 = self.client.get(f"{url}?status=active")
        self.assertEqual(len(page1.context["page_obj"]), 20)
        self.assertContains(page1, "status=active&amp;page=2")
        page2 = self.client.get(f"{url}?status=active&page=2")
        self.assertEqual(len(page2.context["page_obj"]), 5)
        self.assertEqual(len(self.client.get(f"{url}?page=999").context["page_obj"]), 5)  # last page, no crash


class StaffServiceTests(StaffTestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        actor = make_staff("actor", self.maths).user
        profile = make_staff("target", self.physics)
        with self.assertRaises(PermissionDenied):
            services.create_staff(data=data(self.physics), actor=actor)
        with self.assertRaises(PermissionDenied):
            services.update_staff(profile=profile, data=edit_data(profile), actor=actor)
        with self.assertRaises(PermissionDenied):
            services.set_staff_active(profile=profile, active=False, actor=actor)
        with self.assertRaises(PermissionDenied):
            services.reset_staff_password(profile=profile, data={"password": GOOD_PASSWORD, "password2": GOOD_PASSWORD}, actor=actor)
        profile.refresh_from_db()
        self.assertTrue(profile.is_active)
        self.assertFalse(User.objects.filter(username="asha.k").exists())
