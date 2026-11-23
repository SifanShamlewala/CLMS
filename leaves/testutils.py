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


class WorkloadTestCase(LeaveTestCase):
    """Adds two Physics colleagues (Ben, Chitra) and helpers to apply and arrange cover."""

    def setUp(self):
        super().setUp()
        self.ben = make_staff("ben", self.physics)
        self.chitra = make_staff("chitra", self.physics)
        for profile, first in ((self.ben, "Ben"), (self.chitra, "Chitra")):
            profile.user.first_name = first
            profile.user.save()

    def apply(self, person=None, **extra):
        from leaves import applications
        return applications.submit_application(user=(person or self.asha).user, data=self.payload(**extra))

    def assign(self, application, colleague, days=None):
        """Ask ``colleague`` to cover ``days`` (default: every date needing cover)."""
        from leaves import workload
        if days is None:
            days = [d.date for d in application.days.filter(workload_required=True, is_counted=True)]
        data = {"assignee": str(colleague.pk), "dates": [d.isoformat() for d in days]}
        return workload.assign_workload(application=application, data=data, actor=application.applicant.user)

    def rows(self, application, **filters):
        from leaves.models import LeaveWorkload
        return LeaveWorkload.objects.filter(leave_day__leave=application, **filters).order_by("date", "id")


class ApprovalTestCase(WorkloadTestCase):
    """Helpers to put a leave at the head's or the Principal's stage and to decide it."""

    def pending_hod(self, person=None, **extra):
        """A staff leave that needs no cover, so it starts waiting for the head."""
        return self.apply(person, requires_workload="", **extra)

    def pending_principal(self, person=None, **extra):
        from leaves import approvals
        leave = self.pending_hod(person, **extra)
        head = leave.applicant.department.hod
        return approvals.decide_leave(application=leave, decision="approve", remarks="", actor=head.user)

    def decide(self, leave, person, decision, remarks=""):
        from leaves import approvals
        return approvals.decide_leave(application=leave, decision=decision, remarks=remarks, actor=person.user)

    def logs(self, leave):
        from leaves.models import LeaveApprovalLog
        return list(LeaveApprovalLog.objects.filter(leave=leave).values_list("action", flat=True))

    def refreshed(self, leave):
        leave.refresh_from_db()
        return leave
