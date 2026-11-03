from django.conf import settings
from django.db import models


class Designation(models.Model):
    title = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    hod = models.OneToOneField(
        "accounts.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="headed_department",
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class StaffProfile(models.Model):

    class Role(models.TextChoices):
        STAFF = "STAFF", "Staff"
        HOD = "HOD", "Head of Department"
        PRINCIPAL = "PRINCIPAL", "Principal"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="profile",
    )

    employee_id = models.CharField(
        max_length=30,
        unique=True,
    )

    department = models.ForeignKey(
        "accounts.Department",
        on_delete=models.PROTECT,
        related_name="staff",
    )

    designation = models.ForeignKey(
        "accounts.Designation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    role = models.CharField(
        max_length=20,
        choices=Role,
        default=Role.STAFF,
    )

    is_active = models.BooleanField(default=True)

    phone = models.CharField(
        max_length=15,
        blank=True,
    )

    joined_on = models.DateField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role"],
                condition=models.Q(role="PRINCIPAL"),
                name="staff_only_one_principal",
            ),
        ]

    def __str__(self):
        return f"{self.employee_id} - {self.user}"
