from django.conf import settings
from django.db import models


class LeaveType(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
    )

    code = models.CharField(
        max_length=20,
        unique=True,
    )

    description = models.TextField(
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    def __str__(self):
        return f"{self.name} ({self.code})"


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_date__lt=models.F("end_date")),
                name="ay_start_before_end",
            ),
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="ay_only_one_active",
            ),
        ]

    def __str__(self):
        return self.name


class Holiday(models.Model):

    date = models.DateField()

    name = models.CharField(
        max_length=100,
    )

    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="holidays",
    )

    is_deleted = models.BooleanField(
        default=False,
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_holidays",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_holidays",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["date", "academic_year"],
                condition=models.Q(is_deleted=False),
                name="holiday_unique_active_date",
            ),
        ]

    def __str__(self):
        return f"{self.name} - {self.date}"


class PolicyConfig(models.Model):

    class HodOwnLeaveRoute(models.TextChoices):
        SKIP_TO_PRINCIPAL = (
            "SKIP_TO_PRINCIPAL",
            "Skip to Principal",
        )

        PRINCIPAL_AS_HOD = (
            "PRINCIPAL_AS_HOD",
            "Principal as HOD",
        )

    weekly_off_days = models.CharField(
        max_length=20,
        default="6",
    )

    allow_cross_year_leave = models.BooleanField(
        default=False,
    )

    hod_own_leave_route = models.CharField(
        max_length=30,
        choices=HodOwnLeaveRoute,
        default=HodOwnLeaveRoute.SKIP_TO_PRINCIPAL,
    )

    reversal_includes_today = models.BooleanField(
        default=False,
    )

    principal_bypass_stages = models.BooleanField(
        default=True,
    )

    admin_adjust_enabled = models.BooleanField(
        default=True,
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def weekly_off_days_set(self):
        return {
            int(day.strip())
            for day in self.weekly_off_days.split(",")
            if day.strip()
        }

    def __str__(self):
        return "Policy Configuration"
