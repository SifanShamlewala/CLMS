from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from academics import services
from academics.models import LeaveType
from core.testutils import login, make_admin, make_staff
from core.validation import DomainError


class LeaveTypeAccessTests(TestCase):
    def setUp(self):
        self.leave_type = LeaveType.objects.create(name="Casual Leave", code="CL")
        self.read_urls = [
            reverse("leave_type_list"), reverse("leave_type_create"),
            reverse("leave_type_edit", args=[self.leave_type.pk]),
        ]
        self.toggle_url = reverse("leave_type_toggle", args=[self.leave_type.pk])

    def test_anonymous_redirected_to_login(self):
        for url in self.read_urls + [self.toggle_url]:
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_staff_get_403_and_cannot_toggle(self):
        login(self.client, make_staff("s").user)
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(self.toggle_url, {"active": "0"}).status_code, 403)
        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.is_active)

    def test_administrator_can_open_pages_and_toggle_rejects_get(self):
        login(self.client, make_admin())
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(self.toggle_url).status_code, 405)


class LeaveTypeScreenTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())
        self.create_url = reverse("leave_type_create")

    def test_valid_create_uppercases_code(self):
        response = self.client.post(
            self.create_url, {"name": " Casual Leave ", "code": " cl ", "description": " Short breaks "}, follow=True
        )
        self.assertRedirects(response, reverse("leave_type_list"))
        self.assertContains(response, "Leave type “Casual Leave” created.")
        t = LeaveType.objects.get()
        self.assertEqual((t.name, t.code, t.description, t.is_active), ("Casual Leave", "CL", "Short breaks", True))

    def test_description_is_optional(self):
        self.client.post(self.create_url, {"name": "Earned Leave", "code": "EL"})
        self.assertEqual(LeaveType.objects.get().description, "")

    def test_blank_fields_report_every_error_at_once(self):
        response = self.client.post(self.create_url, {"name": "", "code": ""})
        self.assertContains(response, "Name is required.")
        self.assertContains(response, "Code is required.")
        self.assertEqual(LeaveType.objects.count(), 0)

    def test_invalid_code_characters_rejected(self):
        for bad in ("C L", "C/L", "CL!", "<b>"):
            response = self.client.post(self.create_url, {"name": "Bad", "code": bad})
            self.assertContains(response, "only letters, numbers, hyphens and underscores")
        self.assertEqual(LeaveType.objects.count(), 0)

    def test_valid_code_with_hyphen_and_underscore(self):
        self.client.post(self.create_url, {"name": "Study", "code": "st-1_a"})
        self.assertEqual(LeaveType.objects.get().code, "ST-1_A")

    def test_length_limits(self):
        response = self.client.post(self.create_url, {"name": "x" * 101, "code": "C" * 21, "description": "d" * 501})
        self.assertContains(response, "Name must be at most 100")
        self.assertContains(response, "Code must be at most 20")
        self.assertContains(response, "Description must be at most 500")

    def test_duplicate_name_and_code_are_case_insensitive_and_both_reported(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        response = self.client.post(self.create_url, {"name": "casual leave", "code": "cl"})
        self.assertContains(response, "A leave type with this name already exists.")
        self.assertContains(response, "A leave type with this code already exists.")
        self.assertEqual(LeaveType.objects.count(), 1)

    def test_only_code_duplicate_reports_only_code(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        response = self.client.post(self.create_url, {"name": "Other", "code": "cl"})
        self.assertContains(response, "A leave type with this code already exists.")
        self.assertNotContains(response, "this name already exists")

    def test_typed_values_kept_after_error(self):
        response = self.client.post(self.create_url, {"name": "Keep me", "code": "bad code", "description": "note"})
        self.assertContains(response, 'value="Keep me"')
        self.assertContains(response, ">note</textarea>")

    def test_edit_prefills_updates_and_allows_own_values(self):
        t = LeaveType.objects.create(name="Casual Leave", code="CL", description="old")
        url = reverse("leave_type_edit", args=[t.pk])
        page = self.client.get(url)
        self.assertContains(page, 'value="Casual Leave"')
        self.assertContains(page, 'value="CL"')
        self.client.post(url, {"name": "Casual Leave", "code": "cl", "description": "new"})
        t.refresh_from_db()
        self.assertEqual((t.name, t.code, t.description), ("Casual Leave", "CL", "new"))

    def test_edit_cannot_take_another_types_name_or_code(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        t = LeaveType.objects.create(name="Earned Leave", code="EL")
        response = self.client.post(reverse("leave_type_edit", args=[t.pk]), {"name": "casual leave", "code": "el"})
        self.assertContains(response, "this name already exists")
        t.refresh_from_db()
        self.assertEqual(t.name, "Earned Leave")

    def test_toggle(self):
        t = LeaveType.objects.create(name="Casual Leave", code="CL")
        url = reverse("leave_type_toggle", args=[t.pk])
        self.client.post(url, {"active": "0"})
        t.refresh_from_db()
        self.assertFalse(t.is_active)
        self.client.post(url, {"active": "1"})
        t.refresh_from_db()
        self.assertTrue(t.is_active)

    def test_unknown_id_is_404(self):
        self.assertEqual(self.client.get(reverse("leave_type_edit", args=[999])).status_code, 404)

    def test_list_empty_state_and_rows(self):
        self.assertContains(self.client.get(reverse("leave_type_list")), "No leave types yet")
        LeaveType.objects.create(name="Casual Leave", code="CL")
        response = self.client.get(reverse("leave_type_list"))
        self.assertContains(response, "Casual Leave")
        self.assertContains(response, "CL")


class LeaveTypeServiceTests(TestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        staff = make_staff("s").user
        t = LeaveType.objects.create(name="Casual Leave", code="CL")
        data = {"name": "X", "code": "X"}
        with self.assertRaises(PermissionDenied):
            services.create_leave_type(data=data, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.update_leave_type(leave_type=t, data=data, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.set_leave_type_active(leave_type=t, active=False, actor=staff)
        self.assertEqual(LeaveType.objects.count(), 1)

    def test_concurrent_duplicate_becomes_a_friendly_error(self):
        LeaveType.objects.create(name="Casual Leave", code="CL")
        with mock.patch("academics.services.ensure_unique"):
            with self.assertRaises(DomainError) as ctx:
                services.create_leave_type(data={"name": "Casual Leave", "code": "CL"}, actor=make_admin())
        self.assertIn("form", ctx.exception.errors)