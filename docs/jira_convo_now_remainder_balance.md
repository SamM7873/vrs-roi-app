# Jira Ticket — Convo Now Remainder Balance

**Summary:** Convo Now Remainder Balance is inaccurate for billing cycles that cross a
calendar month (Monthly Value usage not carried forward)

**Type:** Bug
**Priority:** Medium
**Component:** Data Engineering / Monthly Value pipeline
**Labels:** convo-now, billing, remainder-balance, data-accuracy

---

## Description

The `Remainder Balance` on the Convo Now **Monthly Value** object is calculated **per
calendar month**, not per **billing cycle**. For *Convo Now: Access Complimentary* (20-min)
accounts whose billing cycle crosses a calendar-month boundary, the usage from the first
month is **not carried forward** into the next month's Monthly Value, so the displayed
remaining-minutes balance is **overstated**.

Current formula (calculated property on Monthly Value):

```
Remainder Balance = string_to_number(Credit Minimum) − Convo Now Minutes Used
```

where `Credit Minimum = 20` and the calculation runs at the Monthly-Value / calendar-month
level. The allowance should instead be evaluated against the consumer's **billing cycle**.

## Steps to reproduce

1. Find a CN20 account whose billing cycle spans two calendar months (e.g. Sept 21 → Oct 21).
2. Use some minutes in the first month (e.g. 8 min in September).
3. View the next month's Monthly Value once the calendar month rolls over.

## Expected

- The billing cycle has one 20-minute allowance. After 8 minutes used, the balance is **12**
  and stays **12** into October (no new usage).

## Actual

- October's Monthly Value resets to `20 − 0 = 20`, ignoring the 8 minutes already used in the
  same cycle. The balance is shown as **20** instead of **12**.

## Example — Number 41599276

| Period | Minutes used | Expected balance | Monthly Value shows |
| --- | ---: | ---: | ---: |
| Sep 21–30 | 8 | 12 | 12 |
| Oct 1–21 | 0 | 12 | **20** ❌ |

## Impact

- Data-accuracy / misinformation issue: the reported remaining-minutes balance is wrong for
  cross-month cycles. Affects consumer-facing balances, support, and internal reporting.
- Not a billing-enforcement issue — the plan's actual allowance is enforced; only the
  **displayed balance** is wrong.
- Secondary signal: some records show usage **far above 20 min** (up to 277), pushing
  `remainder_balance` negative (e.g. −257). The high usage values and whether the balance
  should floor at 0 both need review.

## Acceptance criteria

1. Remainder is computed **per billing cycle**: `20 − total Convo Now minutes used during the
   current billing cycle`.
2. The remaining balance **carries forward** across calendar-month boundaries until the cycle
   ends.
3. A new calendar month does **not** reset the balance to 20 while it is still the same cycle.
4. (Decision needed) Define behavior when usage exceeds 20: floor at 0, or treat overage as
   paid.
5. Validation: for number 41599276, October reflects **12**, not 20.

## Scope (from the QA page)

Using the corrected per-cycle logic (overstated only when earlier-month usage exists):

- **1,353** live CN20 numbers currently display an **overstated** remaining balance.
- **Overstatement:** median **13 min**, mean **12.5 min**, up to **20 min** per number.
- **Total overstated minutes across all affected numbers: ~16,874.**
- For most affected numbers the balance shows **20** while the correct value is **0–4**.
- Usage ranges up to **277 min** (far above the 20-min allowance), driving some stored
  balances negative — see the secondary signal above.

## Supporting material

- QA page: **Convo Now Reminder Balance** — recomputes the correct per-cycle remainder and
  lists affected numbers.
- `docs/convo_now_reminder_balance_issue.md` — full explainer.
- `docs/convonow_red_flags_export.csv` — affected-numbers data snapshot.
