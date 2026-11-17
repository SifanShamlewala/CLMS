"""Leave balances, leave applications and the day-by-day ledger.

LeaveDay is the single source of truth for day counts, paid and LWP totals,
deduction, reversal and reports. The totals on LeaveApplication are snapshots
only. The constraints below make the database refuse bad data even when a view
or a service forgets a rule.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class StaffLeaveBalance(models.Model):
    """How many days of one leave type a person has in one academic year."""

    staff = models.ForeignKey("accounts.StaffProfile", on_delete=models.PROTECT, related_name="leave_balances")
    leave_type = models.ForeignKey("academics.LeaveType", on_delete=models.PROTECT, related_name="balances")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="balances")
    total = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    used = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "leave_type", "academic_year"], name="balance_unique_per_year"
            ),
            models.CheckConstraint(condition=Q(total__gte=0), name="balance_total_not_negative"),
            models.CheckConstraint(
                condition=Q(used__gte=0, used__lte=F("total")), name="balance_used_within_total"
            ),
        ]

    @property
    def remaining(self):
        return self.total - self.used

    def __str__(self):
        return f"{self.staff} {self.leave_type.code} {self.academic_year}"


class LeaveApplication(models.Model):
    class Status(models.TextChoices):
        WORKLOAD_PENDING = "WORKLOAD_PENDING", "Workload pending"
        HOD_PENDING = "HOD_PENDING", "HOD pending"
        PRINCIPAL_PENDING = "PRINCIPAL_PENDING", "Principal pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    applicant = models.ForeignKey("accounts.StaffProfile", on_delete=models.PROTECT, related_name="leave_applications")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="leave_applications")
    leave_type = models.ForeignKey("academics.LeaveType", on_delete=models.PROTECT, related_name="leave_applications")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(max_length=500)
    requires_workload = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.WORKLOAD_PENDING)
    is_principal_auto_approved = models.BooleanField(default=False)

    # Snapshots only. LeaveDay is the source of truth for every official count.
    balance_before = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    balance_after = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    total_days_counted = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    total_days_paid = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    total_days_lwp = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    submitted_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="deleted_leave_applications",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(start_date__lte=F("end_date")), name="leave_start_not_after_end"),
        ]
        indexes = [
            models.Index(fields=["applicant", "status"]),
            models.Index(fields=["status", "submitted_at"]),
        ]

    def __str__(self):
        return f"{self.applicant} {self.start_date} to {self.end_date} ({self.status})"


class LeaveDay(models.Model):
    """One row per requested date. The single source of truth for day counts."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REVERSED = "REVERSED", "Reversed"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    class DayPart(models.TextChoices):
        FULL = "FULL", "Full day"
        HALF_AM = "HALF_AM", "Morning half"
        HALF_PM = "HALF_PM", "Afternoon half"

    leave = models.ForeignKey(LeaveApplication, on_delete=models.PROTECT, related_name="days")
    # Denormalised applicant so the database can stop one person holding two leaves on a date.
    staff = models.ForeignKey("accounts.StaffProfile", on_delete=models.PROTECT, related_name="leave_days")
    date = models.DateField()
    day_part = models.CharField(max_length=10, choices=DayPart, default=DayPart.FULL)
    fraction = models.DecimalField(max_digits=2, decimal_places=1, default=1)

    is_working_day = models.BooleanField(default=True)
    is_sunday = models.BooleanField(default=False)
    is_holiday = models.BooleanField(default=False)
    holiday = models.ForeignKey(
        "academics.Holiday", on_delete=models.SET_NULL, null=True, blank=True, related_name="leave_days"
    )
    is_counted = models.BooleanField(default=False)
    workload_required = models.BooleanField(default=False)
    is_lwp = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    paid_deducted = models.DecimalField(max_digits=2, decimal_places=1, default=0)
    # True while this day should block another application on the same date. Set to
    # False when the leave is rejected, cancelled or deleted, or the day is reversed.
    blocks_overlap = models.BooleanField(default=True)

    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reversed_leave_days",
    )
    reverse_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["leave", "date"], name="leaveday_one_per_date"),
            models.UniqueConstraint(
                fields=["staff", "date"], condition=Q(blocks_overlap=True), name="leaveday_no_overlap"
            ),
            models.CheckConstraint(
                condition=Q(fraction__in=[Decimal("0.5"), Decimal("1.0")]),
                name="leaveday_fraction_half_or_full",
            ),
            models.CheckConstraint(
                condition=Q(is_counted=False) | Q(is_working_day=True, is_sunday=False, is_holiday=False),
                name="leaveday_counted_means_working",
            ),
            models.CheckConstraint(
                condition=Q(is_lwp=False) | Q(paid_deducted=0), name="leaveday_lwp_never_deducted"
            ),
            models.CheckConstraint(
                condition=Q(paid_deducted__gte=0, paid_deducted__lte=F("fraction")),
                name="leaveday_deduction_within_fraction",
            ),
        ]
        indexes = [
            models.Index(fields=["staff", "date"]),
            models.Index(fields=["leave", "status"]),
        ]

    def __str__(self):
        return f"{self.staff} {self.date} {self.day_part}"
