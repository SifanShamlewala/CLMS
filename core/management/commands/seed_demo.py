"""Create clearly labelled DEMO data for local development.

    python manage.py seed_demo                 (asks for a demo password)
    python manage.py seed_demo --password ...

Rules (spec section 11): demo data is for local testing only and must never be
mistaken for real records. So this command

* refuses to run unless DEBUG is on,
* is IDEMPOTENT: running it again creates nothing twice and never overwrites
  anything an administrator has changed since,
* labels everything as demo (usernames start with "demo_", names say "Demo",
  employee IDs start with "DEMO-"),
* never writes a password anywhere. All demo accounts share the one password
  typed in, which is hashed like any other.

It reuses an existing academic year that already covers the demo dates, so it
never creates two overlapping years.
"""
import datetime
import getpass
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import AcademicYear, Holiday, LeaveType
from accounts.models import Department, Designation, StaffProfile
from leaves.models import StaffLeaveBalance

User = get_user_model()
Role = StaffProfile.Role

YEAR_NAME = "DEMO 2026-27"
YEAR_START = datetime.date(2026, 6, 1)
YEAR_END = datetime.date(2027, 5, 31)

DEPARTMENTS = ["Demo Administration", "Demo Computer Science", "Demo Mathematics"]
DESIGNATIONS = ["Principal", "Head of Department", "Assistant Professor", "Lecturer"]
LEAVE_TYPES = [("CL", "Casual Leave"), ("EL", "Earned Leave")]
HOLIDAYS = [
    (datetime.date(2026, 8, 15), "Independence Day (demo)"),
    (datetime.date(2026, 10, 2), "Gandhi Jayanti (demo)"),
    (datetime.date(2026, 12, 25), "Christmas (demo)"),
]
# username, first name, last name, role, department, designation, {leave code: total days}
PEOPLE = [
    ("demo_principal", "Demo", "Principal", Role.PRINCIPAL, "Demo Administration", "Principal", {"CL": "12", "EL": "20"}),
    ("demo_hod_cs", "Demo", "HodCS", Role.HOD, "Demo Computer Science", "Head of Department", {"CL": "12", "EL": "20"}),
    ("demo_hod_maths", "Demo", "HodMaths", Role.HOD, "Demo Mathematics", "Head of Department", {"CL": "12", "EL": "20"}),
    # Balances of 0, 0.5, 10 and 10 so every paid / LWP case can be tried.
    ("demo_staff1", "Demo", "StaffZero", Role.STAFF, "Demo Computer Science", "Lecturer", {"CL": "0"}),
    ("demo_staff2", "Demo", "StaffHalf", Role.STAFF, "Demo Computer Science", "Lecturer", {"CL": "0.5"}),
    ("demo_staff3", "Demo", "StaffTen", Role.STAFF, "Demo Mathematics", "Assistant Professor", {"CL": "10", "EL": "10"}),
    ("demo_staff4", "Demo", "StaffTen2", Role.STAFF, "Demo Mathematics", "Assistant Professor", {"CL": "10"}),
]


class Command(BaseCommand):
    help = "Create demo departments, staff, a year, holidays and balances (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for every demo account (asked for if missing).")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs when DEBUG is on. It never runs in production.")
        password = self._password(options.get("password"))
        with transaction.atomic():
            self.created = {"year": 0, "department": 0, "staff": 0, "holiday": 0, "balance": 0}
            self.notes = []
            year = self._year()
            departments = self._departments()
            designations = {title: self._designation(title) for title in DESIGNATIONS}
            leave_types = {code: self._leave_type(code, name) for code, name in LEAVE_TYPES}
            self._holidays(year)
            for person in PEOPLE:
                self._person(person, password, year, departments, designations, leave_types)
        self._report()

    # ------------------------------------------------------------------ helpers
    def _password(self, given):
        password = given
        if not password:
            password = getpass.getpass("Demo password (shared by all demo accounts): ")
            if password != getpass.getpass("Repeat it: "):
                raise CommandError("The two passwords don't match.")
        if len(password) < 8:
            raise CommandError("Use a demo password of at least 8 characters.")
        return password

    def _year(self):
        overlapping = (
            AcademicYear.objects.filter(start_date__lte=YEAR_END, end_date__gte=YEAR_START)
            .order_by("-is_active", "start_date").first()
        )
        if overlapping is not None:
            self.notes.append(f"Using the existing academic year {overlapping.name}.")
            return overlapping
        year = AcademicYear.objects.create(
            name=YEAR_NAME, start_date=YEAR_START, end_date=YEAR_END,
            is_active=not AcademicYear.objects.filter(is_active=True).exists(),
        )
        self.created["year"] += 1
        return year

    def _departments(self):
        out = {}
        for name in DEPARTMENTS:
            out[name], created = Department.objects.get_or_create(name=name)
            self.created["department"] += created
        return out

    def _designation(self, title):
        # Match case-insensitively so an existing "lecturer" is reused, not duplicated.
        return Designation.objects.filter(title__iexact=title).first() or Designation.objects.create(title=title)

    def _leave_type(self, code, name):
        return LeaveType.objects.filter(code__iexact=code).first() or LeaveType.objects.create(code=code, name=name)

    def _holidays(self, year):
        for day, name in HOLIDAYS:
            if not (year.start_date <= day <= year.end_date):
                continue
            _, created = Holiday.objects.get_or_create(
                academic_year=year, date=day, is_deleted=False, defaults={"name": name}
            )
            self.created["holiday"] += created

    def _person(self, person, password, year, departments, designations, leave_types):
        username, first, last, role, dept_name, designation, balances = person
        department = departments[dept_name]
        if role == Role.PRINCIPAL and StaffProfile.objects.filter(role=Role.PRINCIPAL).exclude(
            user__username=username
        ).exists():
            self.notes.append("A Principal already exists, so no demo Principal was created.")
            return
        user, user_created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "email": f"{username}@example.invalid"},
        )
        if user_created:
            user.set_password(password)
            user.save(update_fields=["password"])
        profile, profile_created = StaffProfile.objects.get_or_create(
            user=user,
            defaults={
                "employee_id": f"DEMO-{username.removeprefix('demo_').upper()}",
                "department": department,
                "designation": designations[designation],
                "role": role,
            },
        )
        self.created["staff"] += profile_created
        if role == Role.HOD and department.hod_id is None and profile.is_active:
            department.hod = profile
            department.save(update_fields=["hod"])
        for code, total in balances.items():
            # get_or_create with defaults: a total an administrator changed later is never overwritten.
            _, created = StaffLeaveBalance.objects.get_or_create(
                staff=profile, leave_type=leave_types[code], academic_year=year,
                defaults={"total": Decimal(total)},
            )
            self.created["balance"] += created

    def _report(self):
        made = ", ".join(f"{count} {label}" for label, count in self.created.items() if count) or "nothing new"
        self.stdout.write(self.style.SUCCESS(f"Demo data ready. Created: {made}."))
        for note in self.notes:
            self.stdout.write(self.style.WARNING(note))
        self.stdout.write("Demo accounts (all share the password you typed):")
        for username, *_ in PEOPLE:
            self.stdout.write(f"  {username}")
        self.stdout.write("Existing demo accounts keep their old password. Delete demo data before a real launch.")
