from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from accounts.models import Department, Designation, StaffProfile

User = get_user_model()
Role = StaffProfile.Role


def make_profile(username, department, role=Role.STAFF):
    user = User.objects.create_user(username=username, password="Str0ng-pass!")
    return StaffProfile.objects.create(
        user=user,
        employee_id=f"EMP-{username}",
        department=department,
        role=role,
    )


class DesignationDepartmentTests(TestCase):
    def test_duplicate_designation_title_rejected(self):
        Designation.objects.create(title="Professor")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Designation.objects.create(title="Professor")

    def test_duplicate_department_name_rejected(self):
        Department.objects.create(name="Computer Science")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Department.objects.create(name="Computer Science")


class StaffProfileTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computer Science")

    def test_only_one_principal_allowed(self):
        make_profile("p1", self.dept, Role.PRINCIPAL)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_profile("p2", self.dept, Role.PRINCIPAL)

    def test_second_principal_blocked_even_if_first_is_inactive(self):
        first = make_profile("p1", self.dept, Role.PRINCIPAL)
        first.is_active = False
        first.save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_profile("p2", self.dept, Role.PRINCIPAL)

    def test_many_staff_and_hod_profiles_allowed(self):
        make_profile("s1", self.dept, Role.STAFF)
        make_profile("s2", self.dept, Role.STAFF)
        make_profile("h1", self.dept, Role.HOD)
        make_profile("h2", self.dept, Role.HOD)
        self.assertEqual(StaffProfile.objects.count(), 4)

    def test_duplicate_employee_id_rejected(self):
        make_profile("a", self.dept)
        user = User.objects.create_user(username="b", password="Str0ng-pass!")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffProfile.objects.create(
                    user=user, employee_id="EMP-a", department=self.dept
                )

    def test_one_profile_per_user(self):
        profile = make_profile("a", self.dept)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffProfile.objects.create(
                    user=profile.user, employee_id="EMP-other", department=self.dept
                )

    def test_department_with_staff_cannot_be_deleted(self):
        make_profile("a", self.dept)
        with self.assertRaises(ProtectedError):
            self.dept.delete()

    def test_profile_cannot_head_two_departments(self):
        hod = make_profile("h", self.dept, Role.HOD)
        self.dept.hod = hod
        self.dept.save()
        other = Department.objects.create(name="Physics")
        other.hod = hod
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                other.save()

    def test_deleting_hod_profile_clears_department_hod(self):
        hod = make_profile("h", self.dept, Role.HOD)
        self.dept.hod = hod
        self.dept.save()
        hod.delete()
        self.dept.refresh_from_db()
        self.assertIsNone(self.dept.hod)