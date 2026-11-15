import datetime

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from academics import services
from academics.models import AcademicYear, Holiday
from core.testutils import login, make_admin, make_staff

D = datetime.date


def make_year(name="2026-27", start=D(2026, 6, 1), end=D(2027, 5, 31), active=False):
    return AcademicYear.objects.create(name=name, start_date=start, end_date=end, is_active=active)


def make_holiday(year, day=D(2026, 8, 15), name="Independence Day", **kwargs):
    return Holiday.objects.create(academic_year=year, date=day, name=name, **kwargs)


def payload(year, date="2026-08-15", name="Independence Day"):
    return {"academic_year": str(year.pk), "date": date, "name": name}


class HolidayAccessTests(TestCase):
    def setUp(self):
        self.year = make_year(active=True)
        self.holiday = make_holiday(self.year)
        self.read_urls = [
            reverse("holiday_list"), reverse("holiday_create"), reverse("holiday_edit", args=[self.holiday.pk]),
        ]
        self.delete_url = reverse("holiday_delete", args=[self.holiday.pk])

    def test_anonymous_redirected_to_login(self):
        for url in self.read_urls + [self.delete_url]:
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_staff_get_403_and_cannot_delete(self):
        login(self.client, make_staff("s").user)
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(self.delete_url).status_code, 403)
        self.holiday.refresh_from_db()
        self.assertFalse(self.holiday.is_deleted)

    def test_administrator_can_open_pages_and_delete_rejects_get(self):
        login(self.client, make_admin())
        for url in self.read_urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(self.delete_url).status_code, 405)


class HolidayListTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.url = reverse("holiday_list")

    def test_no_years_shows_guidance(self):
        self.assertContains(self.client.get(self.url), "Add an academic year first")

    def test_defaults_to_the_active_year(self):
        old = make_year("2025-26", D(2025, 6, 1), D(2026, 5, 31))
        current = make_year(active=True)
        make_holiday(old, D(2025, 10, 2), "Old holiday")
        make_holiday(current, D(2026, 10, 2), "Current holiday")
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected"], current)
        self.assertContains(response, "Current holiday")
        self.assertNotContains(response, "Old holiday")

    def test_year_query_picks_another_year_and_bad_value_falls_back(self):
        old = make_year("2025-26", D(2025, 6, 1), D(2026, 5, 31))
        current = make_year(active=True)
        make_holiday(old, D(2025, 10, 2), "Old holiday")
        self.assertContains(self.client.get(f"{self.url}?year={old.pk}"), "Old holiday")
        self.assertEqual(self.client.get(f"{self.url}?year=abc").context["selected"], current)

    def test_with_no_active_year_the_latest_year_is_shown(self):
        make_year("2025-26", D(2025, 6, 1), D(2026, 5, 31))
        latest = make_year("2026-27")
        self.assertEqual(self.client.get(self.url).context["selected"], latest)

    def test_removed_holidays_are_hidden(self):
        year = make_year(active=True)
        make_holiday(year, name="Visible")
        make_holiday(year, D(2026, 10, 2), "Hidden", is_deleted=True)
        response = self.client.get(self.url)
        self.assertContains(response, "Visible")
        self.assertNotContains(response, "Hidden")

    def test_holidays_on_sundays_are_flagged(self):
        year = make_year(active=True)
        sunday = next(d for d in (D(2026, 8, 10) + datetime.timedelta(i) for i in range(7)) if d.weekday() == 6)
        make_holiday(year, sunday, "Sunday holiday")
        self.assertContains(self.client.get(self.url), ">Sunday<")

    def test_empty_year_shows_add_button(self):
        make_year(active=True)
        self.assertContains(self.client.get(self.url), "No holidays in 2026-27 yet")


class HolidayCreateTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.year = make_year(active=True)
        self.url = reverse("holiday_create")

    def test_valid_create_saves_creator_and_returns_to_that_year(self):
        response = self.client.post(self.url, payload(self.year, name="  Independence Day "), follow=True)
        self.assertRedirects(response, f"{reverse('holiday_list')}?year={self.year.pk}")
        self.assertContains(response, "Holiday “Independence Day” added.")
        holiday = Holiday.objects.get()
        self.assertEqual((holiday.name, holiday.date, holiday.created_by), ("Independence Day", D(2026, 8, 15), self.admin))

    def test_form_preselects_requested_year(self):
        other = make_year("2027-28", D(2027, 6, 1), D(2028, 5, 31))
        page = self.client.get(f"{self.url}?year={other.pk}")
        self.assertContains(page, f'<option value="{other.pk}" selected>')

    def test_blank_and_bad_values_report_every_error(self):
        response = self.client.post(self.url, {"academic_year": str(self.year.pk), "date": "x", "name": ""})
        self.assertContains(response, "Date must be a valid date.")
        self.assertContains(response, "Name is required.")
        self.assertEqual(Holiday.objects.count(), 0)

    def test_year_is_required_and_must_exist(self):
        for value in ("", "999", "abc"):
            response = self.client.post(self.url, {"academic_year": value, "date": "2026-08-15", "name": "X"})
            self.assertContains(response, "Choose a valid academic year.")
        self.assertEqual(Holiday.objects.count(), 0)

    def test_date_must_fall_inside_the_year_and_edges_are_allowed(self):
        for bad in ("2026-05-31", "2027-06-01"):
            response = self.client.post(self.url, payload(self.year, bad))
            self.assertContains(response, "Date must fall inside 2026-27")
        self.assertEqual(Holiday.objects.count(), 0)
        for edge in ("2026-06-01", "2027-05-31"):
            self.client.post(self.url, payload(self.year, edge, f"Edge {edge}"))
        self.assertEqual(Holiday.objects.count(), 2)

    def test_duplicate_date_in_same_year_rejected(self):
        make_holiday(self.year)
        response = self.client.post(self.url, payload(self.year, name="Another"))
        self.assertContains(response, "A holiday on this date already exists in 2026-27.")
        self.assertEqual(Holiday.objects.count(), 1)

    def test_date_can_be_added_again_after_removal(self):
        make_holiday(self.year, is_deleted=True)
        self.client.post(self.url, payload(self.year))
        self.assertEqual(Holiday.objects.filter(is_deleted=False).count(), 1)
        self.assertEqual(Holiday.objects.count(), 2)

    def test_no_years_shows_warning_instead_of_form(self):
        AcademicYear.objects.all().delete()
        self.assertContains(self.client.get(self.url), "Add an academic year first")


class HolidayEditDeleteTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        login(self.client, self.admin)
        self.year = make_year(active=True)
        self.holiday = make_holiday(self.year)
        self.url = reverse("holiday_edit", args=[self.holiday.pk])

    def test_prefill_and_update(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'value="2026-08-15"')
        self.assertContains(page, 'value="Independence Day"')
        self.client.post(self.url, {"date": "2026-08-16", "name": "Freedom Day"})
        self.holiday.refresh_from_db()
        self.assertEqual((self.holiday.date, self.holiday.name), (D(2026, 8, 16), "Freedom Day"))

    def test_resubmitting_own_date_is_fine(self):
        response = self.client.post(self.url, {"date": "2026-08-15", "name": "Renamed"})
        self.assertRedirects(response, f"{reverse('holiday_list')}?year={self.year.pk}")

    def test_cannot_move_onto_another_holidays_date(self):
        make_holiday(self.year, D(2026, 10, 2), "Gandhi Jayanti")
        response = self.client.post(self.url, {"date": "2026-10-02", "name": "Independence Day"})
        self.assertContains(response, "already exists")
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.date, D(2026, 8, 15))

    def test_cannot_move_outside_the_year(self):
        response = self.client.post(self.url, {"date": "2028-01-01", "name": "X"})
        self.assertContains(response, "Date must fall inside 2026-27")

    def test_year_cannot_be_changed_by_tampering(self):
        other = make_year("2027-28", D(2027, 6, 1), D(2028, 5, 31))
        self.client.post(self.url, {"date": "2026-08-15", "name": "X", "academic_year": str(other.pk)})
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.academic_year, self.year)

    def test_removed_holiday_cannot_be_edited(self):
        self.holiday.is_deleted = True
        self.holiday.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_delete_is_soft_and_records_who_and_when(self):
        response = self.client.post(reverse("holiday_delete", args=[self.holiday.pk]), follow=True)
        self.assertContains(response, "Holiday “Independence Day” removed.")
        self.holiday.refresh_from_db()
        self.assertTrue(self.holiday.is_deleted)
        self.assertEqual(self.holiday.deleted_by, self.admin)
        self.assertIsNotNone(self.holiday.deleted_at)
        self.assertEqual(Holiday.objects.count(), 1)  # history is kept

    def test_deleting_twice_is_a_404_not_a_crash(self):
        url = reverse("holiday_delete", args=[self.holiday.pk])
        self.client.post(url)
        self.assertEqual(self.client.post(url).status_code, 404)


class HolidayServiceTests(TestCase):
    def test_non_administrators_cannot_call_services_directly(self):
        staff = make_staff("s").user
        year = make_year(active=True)
        holiday = make_holiday(year)
        with self.assertRaises(PermissionDenied):
            services.create_holiday(data=payload(year, "2026-09-01"), actor=staff)
        with self.assertRaises(PermissionDenied):
            services.update_holiday(holiday=holiday, data={"date": "2026-09-01", "name": "X"}, actor=staff)
        with self.assertRaises(PermissionDenied):
            services.delete_holiday(holiday=holiday, actor=staff)
        holiday.refresh_from_db()
        self.assertFalse(holiday.is_deleted)
        self.assertEqual(Holiday.objects.count(), 1)
