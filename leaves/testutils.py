"""Shared set-up for the leave tests (not collected as tests)."""
import datetime
from decimal import Decimal as D

from django.test import TestCase

from academics.models import AcademicYear, Holiday, LeaveType
from accounts.models import StaffProfile
from core.testutils import login, make_department, make_staff
from leaves.models import StaffLeaveBalance

Role = StaffProfile.Role
# A real week in November 2026: Monday the 9th to Sunday the 15th.
MON, TUE, WED, THU, FRI, SAT, SUN = (datetime.date(2026, 11, d) for d in range(9, 16))
NEXT_MON = datetime.date(2026, 11, 16)
NEXT_TUE = datetime.date(2026, 11, 17)


class LeaveTestCase(TestCase):
    """An active year, two leave types, a Principal, a Physics head, and Asha in Physics with 10 CL."""

    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2027, 5, 31),
            is_active=True,
        )
        self.cl = LeaveType.objects.create(name="Casual Leave", code="CL")
        self.el = LeaveType.objects.create(name="Earned Leave", code="EL")
        self.admin_dept = make_department("Administration")
        self.physics = make_department("Physics")
        self.maths = make_department("Mathematics")
        self.principal = make_staff("principal", self.admin_dept, Role.PRINCIPAL)
        self.hod = make_staff("hod", self.physics, Role.HOD)
        self.physics.hod = self.hod
        self.physics.save()
        self.maths_hod = make_staff("mhod", self.maths, Role.HOD)
        self.maths.hod = self.maths_hod
        self.maths.save()
        self.asha = make_staff("asha", self.physics)
        self.asha.user.first_name = "Asha"
        self.asha.user.save()
        self.give(self.asha, "10")

    def give(self, profile, total, leave_type=None, used="0"):
        balance = StaffLeaveBalance.objects.create(
            staff=profile, leave_type=leave_type or self.cl, academic_year=self.year, total=D(total)
        )
        if D(used):
            StaffLeaveBalance.objects.filter(pk=balance.pk).update(used=D(used))
        return balance

    def holiday(self, day, name="Holiday"):
        return Holiday.objects.create(academic_year=self.year, date=day, name=name)

    def payload(self, **extra):
        data = {
            "leave_type": str(self.cl.pk), "start_date": MON.isoformat(), "end_date": FRI.isoformat(),
            "reason": "Family function", "requires_workload": "on",
        }
        data.update(extra)
        return data

    def sign_in(self, profile):
        self.client.logout()
        login(self.client, profile.user)
