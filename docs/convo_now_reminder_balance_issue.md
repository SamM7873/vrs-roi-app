# Convo Now Reminder Balance — Billing Issue Explainer

*For leadership / billing review · Prepared from the Convo Now Reminder Balance QA page*

## The issue in one sentence

The **Convo Now Remainder Balance is inaccurate when a consumer's billing cycle crosses a calendar-month boundary because Monthly Value records are calculated by calendar month instead of following the consumer's actual billing cycle.**

The remaining minutes from the previous calendar month should carry forward into the next month when both months are part of the same billing cycle.

---

## How HubSpot calculates it today

The Monthly Value `Remainder Balance` is a calculated property:

**Remainder Balance = Credit Minimum − Convo Now Minutes Used**

For **Convo Now: Access Complimentary**, the Credit Minimum is:

**20 minutes**

Therefore:

**Remainder Balance = 20 − Convo Now Minutes Used**

The calculation is currently performed at the **Monthly Value / calendar-month level**.

The issue is that the consumer's allowance needs to be evaluated against the **billing cycle**, not reset when the calendar month changes.

---

## Why the problem happens

The consumer's billing cycle and the calendar month do not always start and end on the same dates.

For example, a consumer may have a billing cycle of:

**September 21, 2026 → October 21, 2026**

This is one 30-day billing cycle.

However, the Monthly Value records are separated by calendar month:

* September 2026 Monthly Value
* October 2026 Monthly Value

When the consumer uses minutes during September, the September Monthly Value correctly calculates the remaining balance.

But when October begins, the October Monthly Value starts a new calculation based only on October usage.

It does not carry forward the remaining balance from September, even though the consumer is still within the same billing cycle.

### Example

The consumer has a **20-minute allowance** for the billing cycle.

During September 21–30:

* Minutes used: **8**
* Remaining: **12**

When October begins:

* Additional minutes used: **0**
* The consumer is still in the September 21 → October 21 billing cycle.
* The remaining balance should therefore still be **12 minutes**.

Instead, the October Monthly Value calculates:

**20 − 0 = 20 minutes**

This makes it look like the balance **reset to 20 minutes on October 1**, even though the billing cycle did not reset.

---

## Real example — Number 41599276 (Lori Wenzel)

* **Plan:** Convo Now: Access Complimentary
* **Status:** Live
* **Billing cycle:** September 21, 2026 → October 21, 2026
* **Billing cycle length:** 30 days
* **Allowance:** 20 minutes for the billing cycle

| Period          | Minutes Used | Expected Remainder | Current Monthly Value |
| --------------- | -----------: | -----------------: | --------------------: |
| September 21–30 |            8 |                 12 |                    12 |
| October 1–21    |            0 |                 12 |                    20 |

### What should happen

The consumer used **8 minutes** during September.

Therefore: **20 − 8 = 12 minutes remaining**

When October begins, the consumer is still within the same billing cycle. Because there was **0 additional usage in October**, the balance should remain **12 minutes**.

### What currently happens

The October Monthly Value sees **0 minutes used** and calculates **20 − 0 = 20 minutes**.

The result is an inaccurate remainder because the October record does not account for the **8 minutes already used during the same billing cycle**.

### Key point

**The calendar month changed, but the billing cycle did not.**

The remaining **12 minutes should carry forward from September into October** until the billing cycle ends on October 21.

---

## What the calculation should look like

Instead of calculating the remainder independently for each calendar month:

* **September:** 20 − 8 = **12**
* **October:** 20 − 0 = **20**

The system should calculate the remainder based on the **entire billing cycle**:

* **Billing cycle:** September 21 → October 21
* **Total usage during billing cycle:** 8 minutes
* **Correct remainder:** 20 − 8 = **12 minutes**

If there is no additional usage during October, **12 minutes remains 12 minutes** until additional usage occurs or the billing cycle ends.

---

## How we detect the issue — QA page logic

For every **Convo Now + Live + Convo Now: Access Complimentary** number, the QA page:

1. Pulls the associated Subscription billing cycle.
2. Identifies the billing-cycle start date.
3. Identifies the billing-cycle end date.
4. Calculates the end date from the billing-cycle type when the end date is missing.
5. Identifies all Monthly Value records that overlap the billing cycle.
6. Calculates the total Convo Now minutes used across the applicable portion of the billing cycle.
7. Calculates the correct billing-cycle remainder: **20 − total Convo Now minutes used during the billing cycle**.
8. Identifies billing cycles that cross calendar-month boundaries.
9. Flags cases where the Monthly Value records show an inaccurate remainder because the balance was effectively restarted at the calendar-month boundary.
10. Verifies that Guest credit-type records are not included in the calculation.

---

## Why calendar-month tracking causes the inaccurate balance

**Current Monthly Value approach** — calendar month, calculated independently:

* September: 20 − September usage
* October: 20 − October usage

**Required billing-cycle approach** — one allowance maintained across the entire cycle:

* September 21 → October 21: 20 − all usage during the billing cycle

The second approach correctly reflects the consumer's remaining balance.

---

## Impact — inaccurate remainder balance

The impact is a **data accuracy and reporting issue**.

When a billing cycle crosses a calendar-month boundary, the Remainder Balance shown on the new Monthly Value can be **higher than the actual remaining balance** for the billing cycle.

In the example:

* **Correct remainder:** 12 minutes
* **October Monthly Value:** 20 minutes

The October value is inaccurate because it does not account for the usage that occurred during September within the same billing cycle.

This can affect:

* Consumer-facing balance information
* Support investigations
* Internal reporting
* QA and billing reviews
* Any workflow or process that relies on the Monthly Value Remainder Balance

---

## Important: Do not treat each Monthly Value as a separate billing-cycle allowance

The Monthly Value records are **calendar-month records**. They should not be interpreted as separate billing cycles simply because there is a new record when the calendar month changes.

For a billing cycle such as **September 21 → October 21**, the September and October Monthly Values can both belong to the **same billing cycle**. The remainder therefore needs to carry forward between those records.

* September: 8 minutes used → 12 minutes remaining
* October: 0 additional minutes used → 12 minutes remaining

The October balance should **not restart at 20 minutes** simply because October is a new calendar month.

---

## Recommendation — Owner: Data Engineering

The correction should be made in the source data pipeline and Monthly Value calculation.

**1. Calculate usage based on the billing cycle.** The Convo Now Minutes Used calculation should account for the consumer's actual billing-cycle start and end dates.

**2. Carry the remainder across calendar-month boundaries.** When a billing cycle crosses into a new calendar month, the remaining balance from the previous month should carry forward. For **September 21 → October 21**, if 8 minutes were used in September (20 − 8 = 12), that **12-minute remainder should carry into October**.

**3. Do not reset the remainder when the calendar month changes.** October should not independently start with "20 − October usage" when October is still part of the same billing cycle; it should continue from the existing billing-cycle balance.

**4. Calculate one remainder for the billing cycle:** **20 − total Convo Now minutes used during the current billing cycle**. This ensures the Remainder Balance represents the consumer's actual remaining balance for that billing cycle.

---

## Until the pipeline is corrected

Until the underlying Monthly Value calculation is corrected, the QA page can be used to:

* Identify billing cycles that cross calendar-month boundaries.
* Compare Monthly Value usage against the actual billing cycle.
* Calculate the correct billing-cycle remainder.
* Identify Monthly Values displaying an inaccurate remainder.
* Provide the affected numbers for manual review or correction.

The QA calculation should be treated as the **billing-cycle-level reference** while the Monthly Value data remains calendar-month based.

---

## Findings from the current data snapshot

An export of all cross-month Convo Now: Access Complimentary cycles (30-day cycles that
span two calendar months) returned **8,333 numbers**. Splitting them clarifies the real
scope:

| Group | Count | Meaning |
| --- | ---: | --- |
| Cross-month cycles (all) | 8,333 | 30-day cycle spanning two calendar months |
| **Had 0 minutes used** | **6,712 (80.5%)** | **Not inaccurate** — the later month correctly shows 20; nothing to carry forward |
| **Had usage > 0** | **1,621 (19.5%)** | The only cases where the displayed balance can be overstated |

**Takeaway:** only about **1 in 5** cross-month cycles actually displays an overstated
balance. A number is only mis-reporting when there was **usage in an earlier month of the
cycle** that the later month failed to carry forward. (The QA page now flags only this
subset.)

**Secondary data-quality signal:** among the 1,621 with usage, the median is ~15 minutes,
but some records show **far more than the 20-minute allowance** (up to **277 minutes**),
which drives the stored `remainder_balance` **negative** (e.g. −237). Usage values well
beyond the plan's allowance reinforce that the **usage minutes themselves** need review on
the data-engineering side, independent of the carry-forward logic.

---

## Bottom line

The issue is that **Convo Now Monthly Values follow calendar months, while the consumer's allowance follows the billing cycle.**

When a billing cycle crosses from one calendar month into another, the remaining balance should **carry forward**. For a **September 21 → October 21** billing cycle:

* 20-minute allowance
* 8 minutes used in September
* 12 minutes remaining
* 0 additional minutes used in October
* **October should still show 12 minutes remaining**

Instead, the current October Monthly Value calculates **20 minutes remaining**, making it appear that the balance reset when October began.

**The calendar month changed; the billing cycle did not.**

The required fix is to have the usage and remainder calculation follow the **billing cycle**, so the remaining balance carries across calendar-month boundaries until the billing cycle ends.
