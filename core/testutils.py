"""Helpers shared by test modules (not collected as tests)."""
from django.contrib.auth import get_user_model

from accounts.models import Department, StaffProfile

PASSWORD = "Str0ng-pass!"
User = get_user_model()


def make_admin(username="boss"):
    return User.objects.create_superuser(username=username, password=PASSWORD)


def make_department(name="Computer Science", **kwargs):
    return Department.objects.create(name=name, **kwargs)


def make_staff(username="staff1", department=None, role=StaffProfile.Role.STAFF, is_active=True):
    department = department or Department.objects.get_or_create(name="Computer Science")[0]
    user = User.objects.create_user(username=username, password=PASSWORD)
    return StaffProfile.objects.create(
        user=user,
        employee_id=f"EMP-{username}",
        department=department,
        role=role,
        is_active=is_active,
    )


def login(client, user):
    assert client.login(username=user.username, password=PASSWORD)