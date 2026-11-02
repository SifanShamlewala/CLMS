# Decisions Log

## Stakeholder rules
- No Django Admin; custom interfaces only.
- No Django forms; custom HTML forms with server-side validation.

## Approved defaults (policy-configurable)
| ID | Decision | Default |
|----|----------|---------|
| D7 | Paid/LWP order | Chronological; a day is paid only if its full fraction is covered |
| D8 | Half-days | One day_part per date; no AM/PM split across applications |
| D9 | Non-working days | Sunday only; Saturday configurable |
| D10 | Cross-year leave | Rejected; submit two applications |
| D11 | Workload completion | All active assignments accepted; any rejection rejects the leave |
| D12 | Principal own leave | Auto-approved, bypasses workload and HOD |
| D13 | "No action taken" | WORKLOAD_PENDING and all workload rows PENDING |
| D14 | Future/unutilized | LeaveDay.date strictly after today |
| D15 | Notifications | In-app plus email after commit |
| D16 | Database | SQLite now, PostgreSQL later |
| D17 | Audit | Enumerated actions with before/after JSON |
| D18 | Admin balance adjust | Administrator only, remarks required, ledger entry |
| G2 | HOD's own leave | Skips HOD stage, goes to Principal |
| G3 | Department without HOD | Submission blocked |
| G5 | Transfer initiator | HOD, Principal or administrator |
| G6 | Missing balance row | Treated as zero, all LWP |
| G7 | Holiday added later | Pending recalculated; approved flagged only |

## Schema additions approved
LeaveDay.staff, blocks_overlap, day_part, paid_deducted, holiday, reversal fields;
LeaveDay.status adds REJECTED and CANCELLED; LeaveWorkload.date;
BalanceTransaction; Notification; PolicyConfig; Decimal for fractions.

## Spec notes
- §17 numbering starting at 7 and the duplicate §18 are typos (confirmed).
- Out of scope: login rate limiting (production would add it).