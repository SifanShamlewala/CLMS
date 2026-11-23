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

## Day 7 defaults (workload cover; change on request)
| ID | Decision | Default |
|----|----------|---------|
| D19 | Who arranges and who may be asked | Only the applicant arranges cover, only while the leave is WORKLOAD_PENDING. Any active staff member except the applicant may be asked (any role, any department). Several colleagues may share a date; one colleague may take several dates in one request |
| D20 | Workload conflicts | Refused when the colleague is on a live leave of their own on that date (checked when asked and again when accepting). Applying for leave on a date you already cover only warns; it never blocks |
| D21 | Withdrawing | The applicant may withdraw a request nobody has answered. It is soft-deleted (kept). Withdrawing the last unanswered request can complete the stage |
| D22 | Rejection | Any rejection needs remarks, rejects the leave, frees its dates (`blocks_overlap` off, days REJECTED) and closes every other open request as CANCELLED (kept as history). `LeaveWorkload.Status` gains CANCELLED beside the spec's four values |
| D23 | Completion (applies D11) | Every counted date has at least one active request and every active request is ACCEPTED. Then the leave moves to `stage_after_workload` (HOD_PENDING, or PRINCIPAL_PENDING for a head under the default route) |
| D24 | Visibility | A colleague asked to cover can open that leave read-only, but never sees the balance or the paid / unpaid split. Access stays after they answer and ends if the request is withdrawn |
| D25 | Audit lines | WORKLOAD_ASSIGNED, WORKLOAD_ACCEPTED, WORKLOAD_REJECTED, LEAVE_REJECTED; withdrawal and stage completion use LEAVE_UPDATED with a `detail=` field (no new action names) |

## Day 8 defaults (HOD and Principal stages; change on request)
| ID | Decision | Default |
|----|----------|---------|
| D26 | Where reviewer remarks live | `LeaveApprovalLog` (spec model) is built on Day 8 instead of Day 11, because decisions need somewhere to keep remarks. Rows are only ever added (save, delete, update and bulk delete are refused). Day 8 writes HOD and Principal decisions; Day 11 adds the other actions and turns the `audit action=` log lines into rows. The `Action` list holds all 27 approved actions now, so Day 11 needs no migration for it |
| D27 | Who decides at the head stage | The head of the applicant's own department, never on their own leave. A head's own leave (route "Principal as HOD") is decided at that stage by the Principal. The status itself decides this, so a later policy change never strands a leave. A Principal in the same department is not reviewed by that head |
| D28 | Decision rules | Approving at the head stage needs no remarks (max 300) and moves the leave to PRINCIPAL_PENDING. Rejecting at any stage needs remarks (max 300), rejects the leave, frees its dates (days REJECTED, no longer blocking overlap) and cancels open cover requests, kept as history. Everything runs under a lock on the leave row, so two clicks or two reviewers cannot both succeed |
| D29 | Final approval is Day 9 | Until Day 9 the Principal can only reject (and approve a head's own leave at the head stage). Approving without the balance deduction would leave the books wrong, so it is refused with a clear message. The Principal's own leave still waits at PRINCIPAL_PENDING until Day 9 adds automatic approval (D12) |
| D30 | Queues | HOD queue: the department's leaves (default: waiting for the head; filter by waiting, with the Principal, approved, rejected). Principal queue: everything waiting for the Principal, which is final-stage leaves plus heads' leaves at the head stage, with a "if approved now" balance estimate (counted days allocated in date order against today's remaining balance, a day paid only if its whole fraction is covered). The estimate is recomputed under a lock at final approval (Day 9). Neither person ever sees their own leave in their queue |
| D31 | Transfer rejected after approval (for the future transfer build) | If the new colleague rejects a transferred assignment on an approved leave, the leave stays approved and the date goes back to the head as uncovered, so the head transfers it again |
| D32 | Leave with no active head after submitting | It waits and appears in the queue as soon as a head is set for the department (the queue follows the department's current head). No fallback to the Principal |
