import datetime
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from academics import services
from academics.models import AcademicYear, Holiday
from core.testutils import login, make_admin, make_staff
from core.validation import DomainError

D = datetime.date


def make_year(name="2026-27", start=D(2026, 6, 1), end=D(2027, 5, 31), active=False):
    return AcademicYear.objects.create(name=name, start_date=start, end_date=end, is_active=active)


def payload(name="2027-28", start="2027-06-01", end="2028-05-31"):
    return {"name": name, "start_date": start, "end_date": end}


class YearAccessTests(TestCase):
    def setUp(self):
        self.year = make_year()
        self.read_urls = [
            reverse("year_list"), reverse("year_create"), reverse("year_edit", args=[self.year.pk]),
        ]
        self.activate_url = reverse("year_activate", args=[self.year.pk])

    def test_anonymous_redirected_to_login(self):
        for url in self.read_urls + [self.activate_url]:
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_staff_get_403_and_cannot_activate(self):
        login(self.client, make_staff("s").user)
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(self.activate_url).status_code, 403)
        self.year.refresh_from_db()
        self.assertFalse(self.year.is_active)

    def test_administrator_can_open_pages_and_activate_rejects_get(self):
        login(self.client, make_admin())
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(self.activate_url).status_code, 405)


class YearCreateTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())
        self.url = reverse("year_create")

    def test_valid_create_is_inactive_and_confirmed(self):
        response = self.client.post(self.url, payload(" 2026-27 ", "2026-06-01", "2027-05-31"), follow=True)
        self.assertRedirects(response, reverse("year_list"))
        self.assertContains(response, "Academic year “2026-27” created.")
        year = AcademicYear.objects.get()
        self.assertFalse(year.is_active)
        self.assertEqual((year.name, year.start_date, year.end_date), ("2026-27", D(2026, 6, 1), D(2027, 5, 31)))

    def test_blank_fields_report_every_error(self):
        response = self.client.post(self.url, {"name": "", "start_date": "", "end_date": ""})
        self.assertContains(response, "Name is required.")
        self.assertContains(response, "Start date must be a valid date.")
        self.assertContains(response, "End date must be a valid date.")
        self.assertEqual(AcademicYear.objects.count(), 0)

    def test_bad_date_text_rejected(self):
        response = self.client.post(self.url, payload(start="01/06/2026"))
        self.assertContains(response, "Start date must be a valid date.")

    def test_end_must_be_after_start(self):
        for end in ("2026-06-01", "2026-05-01"):
            response = self.client.post(self.url, payload("X", "2026-06-01", end))
            self.assertContains(response, "End date must be after the start date.")
        self.assertEqual(AcademicYear.objects.count(), 0)

    def test_overlapping_years_rejected_and_other_year_is_named(self):
        make_year("2026-27")
        response = self.client.post(self.url, payload("Overlap", "2027-05-31", "2028-05-30"))
        self.assertContains(response, "overlap 2026-27")
        self.assertEqual(AcademicYear.objects.count(), 1)

    def test_adjacent_years_are_allowed(self):
        make_year("2026-27")
        self.client.post(self.url, payload("2027-28", "2027-06-01", "2028-05-31"))
        self.assertEqual(AcademicYear.objects.count(), 2)

    def test_duplicate_name_is_case_insensitive(self):
        make_year("AY-2026")
        response = self.client.post(self.url, payload("ay-2026", "2030-06-01", "2031-05-31"))
        self.assertContains(response, "An academic year with this name already exists.")

    def test_name_length_limit_and_kept_values(self):
        response = self.client.post(self.url, payload("x" * 21))
        self.assertContains(response, "Name must be at most 20")
        self.assertContains(response, 'value="2027-06-01"')


class YearEditTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())
        self.year = make_year()
        self.url = reverse("year_edit", args=[self.year.pk])

    def test_prefills_iso_dates(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'value="2026-06-01"')
        self.assertContains(page, 'value="2027-05-31"')

    def test_update_and_resubmitting_own_values_works(self):
        self.client.post(self.url, payload("2026-27 (revised)", "2026-06-01", "2027-06-30"))
        self.year.refresh_from_db()
        self.assertEqual((self.year.name, self.year.end_date), ("2026-27 (revised)", D(2027, 6, 30)))
        response = self.client.post(self.url, payload("2026-27 (revised)", "2026-06-01", "2027-06-30"))
        self.assertRedirects(response, reverse("year_list"))

    def test_cannot_overlap_another_year(self):
        make_year("2027-28", D(2027, 6, 1), D(2028, 5, 31))
        response = self.client.post(self.url, payload("2026-27", "2026-06-01", "2027-06-15"))
        self.assertContains(response, "overlap 2027-28")

    def test_cannot_shrink_range_past_existing_holidays(self):
        Holiday.objects.create(date=D(2026, 8, 15), name="Independence Day", academic_year=self.year)
        response = self.client.post(self.url, payload("2026-27", "2026-06-01", "2026-07-31"))
        self.assertContains(response, "1 holiday in 2026-27 would fall outside these dates")
        self.year.refresh_from_db()
        self.assertEqual(self.year.end_date, D(2027, 5, 31))

    def test_removed_holidays_do_not_block_shrinking(self):
        Holiday.objects.create(date=D(2026, 8, 15), name="Old", academic_year=self.year, is_deleted=True)
        response = self.client.post(self.url, payload("2026-27", "2026-06-01", "2026-07-31"))
        self.assertRedirects(response, reverse("year_list"))

    def test_cannot_take_another_years_name(self):
        make_year("2027-28", D(2027, 6, 1), D(2028, 5, 31))
        response = self.client.post(self.url, payload("2027-28", "2026-06-01", "2027-05-31"))
        self.assertContains(response, "already exists")

    def test_unknown_id_is_404(self):
        self.assertEqual(self.client.get(reverse("year_edit", args=[999])).status_code, 404)


class YearActivateTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.old = make_year("2025-26", D(2025, 6, 1), D(2026, 5, 31), active=True)
        self.new = make_year("2026-27")

    def activate(self, year):
        return self.client.post(reverse("year_activate", args=[year.pk]), follow=True)

    def test_switches_the_active_year_in_one_step(self):
        response = self.activate(self.new)
        self.assertContains(response, "“2026-27” is now the active year.")
        self.old.refresh_from_db()
        self.new.refresh_from_db()
        self.assertFalse(self.old.is_active)
        self.assertTrue(self.new.is_active)
        self.assertEqual(AcademicYear.objects.filter(is_active=True).count(), 1)

    def test_activating_the_active_year_changes_nothing(self):
        self.activate(self.old)
        self.old.refresh_from_db()
        self.assertTrue(self.old.is_active)
        self.assertEqual(AcademicYear.objects.filter(is_active=True).count(), 1)

    def test_first_activation_with_no_current_active_year(self):
        self.old.is_active = False
        self.old.save()
        self.activate(self.new)
        self.new.refresh_from_db()
        self.assertTrue(self.new.is_active)

    def test_unknown_year_is_404(self):
        self.assertEqual(self.client.post(reverse("year_activate", args=[999])).status_code, 404)

    def test_losing_a_race_becomes_a_friendly_error(self):
        with mock.patch.object(AcademicYear, "save", side_effect=IntegrityError("race")):
            with self.assertRaises(DomainError) as ctx:
                services.activate_academic_year(year=self.new, actor=self.admin)
        self.assertIn("form", ctx.exception.errors)
        self.old.refresh_from_db()
        self.assertTrue(self.old.is_active)  # the switch-off was rolled back too


class YearListTests(TestCase):
    def setUp(self):
        login(self.client, make_admin())

    def test_empty_state(self):
        self.assertContains(self.client.get(reverse("year_list")), "No academic years yet")

    def test_warns_when_no_year_is_active(self):
        make_year()
        self.assertContains(self.client.get(reverse("year_list")), "No academic year is active")

    def test_no_warning_when_a_year_is_active(self):
        make_year(active=True)
        response = self.client.get(reverse("year_list"))
        self.assertNotContains(response, "No academic year is active")
        self.assertContains(response, "Active")

    def test_holiday_count_ignores_removed_holidays(self):
        year = make_year()
        Holiday.objects.create(date=D(2026, 8, 15), name="A", academic_year=year)
        Holiday.objects.create(date=D(2026, 10, 2), name="B", academic_year=year, is_deleted=True)
        response = self.client.get(reverse("year_list"))
        self.assertEqual(list(response.context["years"])[0].holiday_count, 1)


class YearServiceTests(TestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        staff = make_staff("s").user
        year = make_year()
        with self.assertRaises(PermissionDenied):
            services.create_academic_year(data=payload(), actor=staff)
        with self.assertRaises(PermissionDenied):
            services.update_academic_year(year=year, data=payload(), actor=staff)
        with self.assertRaises(PermissionDenied):
            services.activate_academic_year(year=year, actor=staff)
        year.refresh_from_db()
        self.assertFalse(year.is_active)
