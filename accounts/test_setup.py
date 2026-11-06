from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from accounts import services
from accounts.models import Department, Designation, StaffProfile
from core.testutils import login, make_admin, make_department, make_staff
from core.validation import DomainError

Role = StaffProfile.Role


class SetupAccessTests(TestCase):
    """Every setup URL is protected on the server, not just hidden in the menu."""

    def setUp(self):
        self.designation = Designation.objects.create(title="Professor")
        self.department = make_department("Physics")
        self.read_urls = [
            reverse("designation_list"), reverse("designation_create"),
            reverse("designation_edit", args=[self.designation.pk]),
            reverse("department_list"), reverse("department_create"),
            reverse("department_edit", args=[self.department.pk]),
        ]
        self.toggle_urls = [
            reverse("designation_toggle", args=[self.designation.pk]),
            reverse("department_toggle", args=[self.department.pk]),
        ]

    def test_anonymous_users_are_sent_to_login(self):
        for url in self.read_urls + self.toggle_urls:
            response = self.client.get(url)
            self.assertRedirects(response, f"{reverse('login')}?next={url}", msg_prefix=url)

    def test_non_administrators_get_403_everywhere(self):
        dept = make_department("Maths")
        users = [
            make_staff("s", dept, Role.STAFF).user,
            make_staff("h", dept, Role.HOD).user,
            make_staff("p", dept, Role.PRINCIPAL).user,
        ]
        from django.contrib.auth import get_user_model
        users.append(get_user_model().objects.create_user("noprofile", password="Str0ng-pass!"))
        for user in users:
            self.client.logout()
            login(self.client, user)
            for url in self.read_urls:
                self.assertEqual(self.client.get(url).status_code, 403, f"{user} GET {url}")
            for url in self.toggle_urls:
                self.assertEqual(self.client.post(url, {"active": "0"}).status_code, 403, f"{user} POST {url}")
        self.designation.refresh_from_db()
        self.assertTrue(self.designation.is_active)

    def test_403_page_explains_what_happened(self):
        login(self.client, make_staff("s").user)
        response = self.client.get(reverse("designation_list"))
        self.assertContains(response, "don’t have access", status_code=403)

    def test_administrator_can_open_every_page(self):
        login(self.client, make_admin())
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_toggle_urls_reject_get_for_administrator(self):
        login(self.client, make_admin())
        for url in self.toggle_urls:
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_setup_links_visible_only_to_administrator(self):
        login(self.client, make_staff("s").user)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("designation_list"))
        self.client.logout()
        login(self.client, make_admin())
        self.assertContains(self.client.get(reverse("dashboard")), reverse("designation_list"))


class DesignationScreenTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())
        self.create_url = reverse("designation_create")

    def test_valid_create_trims_saves_and_confirms(self):
        response = self.client.post(self.create_url, {"title": "  Professor "}, follow=True)
        self.assertRedirects(response, reverse("designation_list"))
        self.assertContains(response, "Designation “Professor” created.")
        self.assertEqual(Designation.objects.get().title, "Professor")

    def test_blank_title_shows_error_and_creates_nothing(self):
        response = self.client.post(self.create_url, {"title": "   "})
        self.assertContains(response, "Title is required.")
        self.assertEqual(Designation.objects.count(), 0)

    def test_too_long_title_rejected(self):
        response = self.client.post(self.create_url, {"title": "x" * 101})
        self.assertContains(response, "at most 100 characters")
        self.assertEqual(Designation.objects.count(), 0)

    def test_duplicate_differing_only_by_case_rejected(self):
        Designation.objects.create(title="Professor")
        response = self.client.post(self.create_url, {"title": "pROFESSOR"})
        self.assertContains(response, "A designation with this title already exists.")
        self.assertEqual(Designation.objects.count(), 1)

    def test_typed_value_is_kept_after_error(self):
        Designation.objects.create(title="Professor")
        response = self.client.post(self.create_url, {"title": "professor"})
        self.assertContains(response, 'value="professor"')

    def test_user_input_is_escaped(self):
        response = self.client.post(self.create_url, {"title": "<script>alert(1)</script>" + "x" * 100})
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_edit_prefills_and_updates(self):
        d = Designation.objects.create(title="Lecturer")
        url = reverse("designation_edit", args=[d.pk])
        self.assertContains(self.client.get(url), 'value="Lecturer"')
        self.client.post(url, {"title": "Senior Lecturer"})
        d.refresh_from_db()
        self.assertEqual(d.title, "Senior Lecturer")

    def test_saving_own_unchanged_title_is_allowed(self):
        d = Designation.objects.create(title="Lecturer")
        response = self.client.post(reverse("designation_edit", args=[d.pk]), {"title": "lecturer"})
        self.assertRedirects(response, reverse("designation_list"))
        d.refresh_from_db()
        self.assertEqual(d.title, "lecturer")

    def test_edit_to_another_existing_title_rejected(self):
        Designation.objects.create(title="Professor")
        d = Designation.objects.create(title="Lecturer")
        response = self.client.post(reverse("designation_edit", args=[d.pk]), {"title": "PROFESSOR"})
        self.assertContains(response, "already exists")
        d.refresh_from_db()
        self.assertEqual(d.title, "Lecturer")

    def test_edit_unknown_id_is_404(self):
        self.assertEqual(self.client.get(reverse("designation_edit", args=[999])).status_code, 404)

    def test_toggle_deactivates_and_reactivates_and_is_idempotent(self):
        d = Designation.objects.create(title="Lecturer")
        url = reverse("designation_toggle", args=[d.pk])
        self.client.post(url, {"active": "0"})
        self.client.post(url, {"active": "0"})
        d.refresh_from_db()
        self.assertFalse(d.is_active)
        self.client.post(url, {"active": "1"})
        d.refresh_from_db()
        self.assertTrue(d.is_active)

    def test_list_empty_state_and_rows(self):
        response = self.client.get(reverse("designation_list"))
        self.assertContains(response, "No designations yet")
        Designation.objects.create(title="Lecturer")
        response = self.client.get(reverse("designation_list"))
        self.assertContains(response, "Lecturer")
        self.assertNotContains(response, "No designations yet")


class DesignationServiceTests(TestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        staff = make_staff("s").user
        d = Designation.objects.create(title="Lecturer")
        with self.assertRaises(PermissionDenied):
            services.create_designation(data={"title": "X"}, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.update_designation(designation=d, data={"title": "X"}, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.set_designation_active(designation=d, active=False, actor=staff)
        d.refresh_from_db()
        self.assertEqual((d.title, d.is_active), ("Lecturer", True))

    def test_concurrent_duplicate_becomes_a_friendly_error(self):
        Designation.objects.create(title="Professor")
        with mock.patch("accounts.services.ensure_unique"):  # simulate losing the race
            with self.assertRaises(DomainError) as ctx:
                services.create_designation(data={"title": "Professor"}, actor=make_admin())
        self.assertIn("title", ctx.exception.errors)


class DepartmentScreenTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())
        self.create_url = reverse("department_create")

    def test_valid_create(self):
        response = self.client.post(self.create_url, {"name": " Computer Science "}, follow=True)
        self.assertRedirects(response, reverse("department_list"))
        self.assertContains(response, "Department “Computer Science” created.")
        self.assertEqual(Department.objects.get().name, "Computer Science")
        self.assertTrue(Department.objects.get().is_active)

    def test_blank_and_duplicate_rejected(self):
        self.assertContains(self.client.post(self.create_url, {"name": ""}), "Name is required.")
        Department.objects.create(name="Physics")
        response = self.client.post(self.create_url, {"name": "PHYSICS"})
        self.assertContains(response, "A department with this name already exists.")
        self.assertEqual(Department.objects.count(), 1)

    def test_edit_rename_and_duplicate_guard(self):
        Department.objects.create(name="Physics")
        d = Department.objects.create(name="Chemistry")
        url = reverse("department_edit", args=[d.pk])
        self.assertContains(self.client.get(url), 'value="Chemistry"')
        self.assertContains(self.client.post(url, {"name": "physics"}), "already exists")
        self.client.post(url, {"name": "Applied Chemistry"})
        d.refresh_from_db()
        self.assertEqual(d.name, "Applied Chemistry")

    def test_cannot_deactivate_department_with_active_staff(self):
        d = make_department("Physics")
        make_staff("s1", d)
        make_staff("s2", d)
        response = self.client.post(reverse("department_toggle", args=[d.pk]), {"active": "0"}, follow=True)
        self.assertContains(response, "still has 2 active staff members")
        d.refresh_from_db()
        self.assertTrue(d.is_active)

    def test_singular_wording_for_one_staff_member(self):
        d = make_department("Physics")
        make_staff("s1", d)
        response = self.client.post(reverse("department_toggle", args=[d.pk]), {"active": "0"}, follow=True)
        self.assertContains(response, "still has 1 active staff member.")

    def test_inactive_staff_do_not_block_deactivation_and_reactivation_works(self):
        d = make_department("Physics")
        make_staff("s1", d, is_active=False)
        url = reverse("department_toggle", args=[d.pk])
        self.client.post(url, {"active": "0"})
        d.refresh_from_db()
        self.assertFalse(d.is_active)
        self.client.post(url, {"active": "1"})
        d.refresh_from_db()
        self.assertTrue(d.is_active)

    def test_list_shows_active_staff_count_and_hod(self):
        d = make_department("Physics")
        hod = make_staff("h", d, Role.HOD)
        make_staff("s1", d)
        make_staff("s2", d, is_active=False)
        d.hod = hod
        d.save()
        response = self.client.get(reverse("department_list"))
        self.assertContains(response, "Physics")
        self.assertContains(response, "h")  # HOD username shown
        row = [r for r in response.context["departments"] if r.pk == d.pk][0]
        self.assertEqual(row.active_staff, 2)

    def test_list_empty_state(self):
        self.assertContains(self.client.get(reverse("department_list")), "No departments yet")

    def test_services_enforce_administrator_role(self):
        d = make_department("Physics")
        staff = make_staff("s").user
        with self.assertRaises(PermissionDenied):
            services.create_department(data={"name": "X"}, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.update_department(department=d, data={"name": "X"}, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.set_department_active(department=d, active=False, actor=staff)

    def test_concurrent_duplicate_becomes_a_friendly_error(self):
        Department.objects.create(name="Physics")
        with mock.patch("accounts.services.ensure_unique"):
            with self.assertRaises(DomainError) as ctx:
                services.create_department(data={"name": "Physics"}, actor=make_admin("root"))
        self.assertIn("name", ctx.exception.errors)