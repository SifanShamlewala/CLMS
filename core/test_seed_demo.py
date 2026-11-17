import datetime
from decimal import Decimal as D
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from academics.models import AcademicYear, Holiday, LeaveType
from accounts.models import Department, StaffProfile
from core.testutils import make_staff
from leaves.models import StaffLeaveBalance

PASSWORD = "Demo-Pass-123x"


def seed(**kwargs):
    out = StringIO()
    call_command("seed_demo", password=PASSWORD, stdout=out, **kwargs)
    return out.getvalue()


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    def counts(self):
        return {
            "years": AcademicYear.objects.count(), "departments": Department.objects.count(),
            "staff": StaffProfile.objects.count(), "holidays": Holiday.objects.count(),
            "balances": StaffLeaveBalance.objects.count(), "types": LeaveType.objects.count(),
        }

    def test_creates_a_complete_labelled_demo_set(self):
        output = seed()
        self.assertIn("Demo data ready.", output)
        year = AcademicYear.objects.get()
        self.assertTrue(year.is_active)
        self.assertEqual(year.name, "DEMO 2026-27")
        self.assertEqual(StaffProfile.objects.count(), 7)
        for profile in StaffProfile.objects.all():
            self.assertTrue(profile.user.username.startswith("demo_"))
            self.assertTrue(profile.employee_id.startswith("DEMO-"))
            self.assertEqual(profile.user.first_name, "Demo")
        self.assertEqual(Holiday.objects.count(), 3)

    def test_running_twice_creates_nothing_new(self):
        seed()
        before = self.counts()
        output = seed()
        self.assertEqual(self.counts(), before)
        self.assertIn("nothing new", output)

    def test_roles_and_department_heads_are_linked(self):
        seed()
        self.assertEqual(StaffProfile.objects.filter(role=StaffProfile.Role.PRINCIPAL).count(), 1)
        for name, username in (("Demo Computer Science", "demo_hod_cs"), ("Demo Mathematics", "demo_hod_maths")):
            self.assertEqual(Department.objects.get(name=name).hod.user.username, username)
        self.assertIsNone(Department.objects.get(name="Demo Administration").hod)

    def test_balances_cover_zero_half_and_ten_day_cases(self):
        seed()
        def total(username, code):
            return StaffLeaveBalance.objects.get(staff__user__username=username, leave_type__code=code).total
        self.assertEqual(total("demo_staff1", "CL"), D("0"))
        self.assertEqual(total("demo_staff2", "CL"), D("0.5"))
        self.assertEqual(total("demo_staff3", "CL"), D("10"))
        self.assertTrue(all(b.used == 0 for b in StaffLeaveBalance.objects.all()))

    def test_demo_accounts_can_sign_in_and_the_password_is_hashed(self):
        seed()
        profile = StaffProfile.objects.get(user__username="demo_staff3")
        self.assertNotEqual(profile.user.password, PASSWORD)
        self.assertTrue(profile.user.check_password(PASSWORD))
        self.assertTrue(self.client.login(username="demo_staff3", password=PASSWORD))

    def test_a_total_changed_by_an_administrator_is_not_overwritten(self):
        seed()
        balance = StaffLeaveBalance.objects.get(staff__user__username="demo_staff1", leave_type__code="CL")
        balance.total = D("7")
        balance.save()
        seed()
        balance.refresh_from_db()
        self.assertEqual(balance.total, D("7"))

    def test_an_existing_year_covering_the_dates_is_reused_not_duplicated(self):
        real = AcademicYear.objects.create(
            name="2026-27", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31), is_active=True
        )
        output = seed()
        self.assertEqual(AcademicYear.objects.count(), 1)
        self.assertIn("Using the existing academic year 2026-27", output)
        self.assertEqual(StaffLeaveBalance.objects.first().academic_year, real)
        self.assertEqual(Holiday.objects.filter(academic_year=real).count(), 3)

    def test_existing_real_holidays_and_leave_types_are_reused(self):
        year = AcademicYear.objects.create(
            name="2026-27", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31)
        )
        Holiday.objects.create(academic_year=year, date=datetime.date(2026, 8, 15), name="Independence Day")
        LeaveType.objects.create(name="Casual leave (real)", code="cl")
        seed()
        self.assertEqual(Holiday.objects.filter(date=datetime.date(2026, 8, 15)).count(), 1)
        self.assertEqual(LeaveType.objects.filter(code__iexact="CL").count(), 1)

    def test_demo_year_stays_inactive_when_another_year_is_active(self):
        AcademicYear.objects.create(
            name="2030-31", start_date=datetime.date(2030, 6, 1), end_date=datetime.date(2031, 5, 31), is_active=True
        )
        seed()
        self.assertFalse(AcademicYear.objects.get(name="DEMO 2026-27").is_active)

    def test_no_demo_principal_when_a_real_principal_exists(self):
        make_staff("real_principal", role=StaffProfile.Role.PRINCIPAL)
        output = seed()
        self.assertIn("A Principal already exists", output)
        self.assertEqual(StaffProfile.objects.filter(role=StaffProfile.Role.PRINCIPAL).count(), 1)
        self.assertFalse(StaffProfile.objects.filter(user__username="demo_principal").exists())

    def test_real_data_is_left_untouched(self):
        real = make_staff("real_person")
        seed()
        real.refresh_from_db()
        self.assertEqual((real.user.username, real.is_active), ("real_person", True))

    def test_a_short_password_is_refused_and_nothing_is_created(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", password="short", stdout=StringIO())
        self.assertEqual(StaffProfile.objects.count(), 0)


class SeedDemoSafetyTests(TestCase):
    def test_refuses_to_run_when_debug_is_off(self):
        with self.assertRaises(CommandError) as ctx:
            seed()
        self.assertIn("DEBUG", str(ctx.exception))
        self.assertEqual(StaffProfile.objects.count(), 0)
