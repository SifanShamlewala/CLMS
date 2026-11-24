"""Final approval: the one place where a leave becomes APPROVED and the balance is deducted.

Spec 8.3 steps 5 and 6, decisions D12 and D29:
* The caller holds the lock on the leave row and is inside ``transaction.atomic()``.
* This function then locks the applicant's balance row, works out again which counted days
  the balance can pay for (another leave may have been approved since this one was applied
  for), and deducts the paid days exactly once.
* Leave without pay is never deducted. A day is paid only if its whole fraction is covered.
* Every deduction is one row in the balance ledger. The database refuses a second deduction
  for the same day, so even a retried or duplicated request cannot take the days twice.
* Lock order is always: leave row, then balance row. Nothing takes them the other way round.
"""
import logging
from decimal import Decimal

from django.utils import timezone

from .day_engine import DayPlan, allocate_leave
from .models import BalanceTransaction, LeaveApplication, LeaveApprovalLog, LeaveDay, StaffLeaveBalance

logger = logging.getLogger("vsclms.leave")

Status = LeaveApplication.Status
Action = LeaveApprovalLog.Action
ZERO = Decimal("0")


def finalize_approval(leave, *, actor, auto=False, remarks=""):
    """Approve ``leave``, recompute paid and unpaid days, deduct the balance, and log it.

    ``auto`` is True for the Principal's own leave (decision D12). Returns a dict with the
    final ``paid``, ``lwp`` and ``balance_after`` figures.
    """
    now = timezone.now()
    days = list(leave.days.select_for_update().order_by("date"))
    balance = (
        StaffLeaveBalance.objects.select_for_update()
        .filter(staff=leave.applicant, leave_type=leave.leave_type, academic_year=leave.academic_year)
        .first()
    )
    available = balance.remaining if balance else ZERO  # decision G6: no row means zero

    # Recompute from today's balance, never from what the applicant saw when applying.
    plans = [
        DayPlan(
            date=d.date, day_part=d.day_part, fraction=d.fraction, is_sunday=d.is_sunday,
            is_holiday=d.is_holiday, is_working_day=d.is_working_day, is_counted=d.is_counted,
        )
        for d in days
    ]
    allocate_leave(plans, available)
    unpaid = {plan.date for plan in plans if plan.is_counted and plan.is_lwp}

    paid_total = lwp_total = ZERO
    running = available
    ledger = []
    for day in days:
        day.status = LeaveDay.Status.APPROVED
        if day.is_counted and day.date not in unpaid:
            day.is_lwp = False
            day.paid_deducted = day.fraction
            paid_total += day.fraction
            ledger.append(BalanceTransaction(
                balance=balance, leave=leave, leave_day=day, type=BalanceTransaction.Type.DEDUCT,
                amount=day.fraction, balance_before=running, balance_after=running - day.fraction,
                created_by=actor, remarks="Final approval",
            ))
            running -= day.fraction
        else:
            day.is_lwp = day.is_counted  # a counted day that the balance cannot pay for
            day.paid_deducted = ZERO
            if day.is_counted:
                lwp_total += day.fraction
    LeaveDay.objects.bulk_update(days, ["status", "is_lwp", "paid_deducted"])

    if ledger:  # a paid day implies a balance row, because with no row nothing is available
        balance.used += paid_total
        balance.save(update_fields=["used", "updated_at"])
        BalanceTransaction.objects.bulk_create(ledger)

    leave.status = Status.APPROVED
    leave.decided_at = now
    leave.total_days_paid, leave.total_days_lwp = paid_total, lwp_total
    leave.balance_after = balance.remaining if balance else ZERO
    leave.is_principal_auto_approved = auto
    leave.save(update_fields=[
        "status", "decided_at", "total_days_paid", "total_days_lwp", "balance_after",
        "is_principal_auto_approved", "updated_at",
    ])

    action = Action.PRINCIPAL_AUTO_APPROVED if auto else Action.PRINCIPAL_APPROVED
    LeaveApprovalLog.objects.create(leave=leave, action_by=actor, action=action, remarks=remarks)
    logger.info("audit action=%s id=%s by=%s", action, leave.pk, actor.pk)
    logger.info(
        "audit action=LEAVE_APPROVED id=%s paid=%s lwp=%s status_before=%s status_after=%s",
        leave.pk, paid_total, lwp_total, "PRINCIPAL_PENDING", Status.APPROVED,
    )
    if ledger:
        logger.info(
            "audit action=BALANCE_DEDUCTED leave=%s balance=%s deducted=%s before=%s after=%s",
            leave.pk, balance.pk, paid_total, available, leave.balance_after,
        )
    return {"paid": paid_total, "lwp": lwp_total, "balance_after": leave.balance_after}
