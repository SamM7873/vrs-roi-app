# Convo Now Reminder Balance — Billing Issue Explainer

*For: leadership / billing review · Prepared from the Convo Now Reminder Balance QA page*

---

## The issue in one sentence

The **usage minutes recorded on the Monthly Values records are not correct**, so the
resulting **reminder / remainder balance** for Convo Now: Access Complimentary consumers
is wrong — and because the data is tracked per calendar month, a billing cycle that
crosses a month ends up showing a **double 20-minute allowance** (up to **40 min** for a
single 20-min cycle). **Root cause sits on the data-engineering side** — in how the usage
minutes are calculated and written to the Monthly Values records.

---

## Background: how the credit is supposed to work

- The **Convo Now: Access Complimentary** plan grants **20 free minutes per billing
  cycle** (for example a 30-day cycle running **Sept 21 → Oct 21**).
- There should be **one** 20-minute bucket for that whole cycle.

## How HubSpot calculates it today

`Remainder Balance` on the **Monthly Value** object is a **calculated property** (custom
equation):

```
Remainder Balance = string_to_number(Credit Minimum) − Convo Now Minutes Used
```

i.e. **`Remainder Balance = 20 − Convo Now Minutes Used`**, computed **per Monthly Value
(per calendar month)**. The `Credit Minimum` (20) is a **flat value reset every month**, and
the only input is `Convo Now Minutes Used`.

## Why the problem happens

**Primary cause — incorrect usage minutes (data engineering).**
The `Convo Now Minutes Used` value written to the **Monthly Values** records is not being
calculated correctly by the data pipeline. Since the formula is `20 − Convo Now Minutes
Used`, wrong usage minutes produce a wrong remainder balance directly.

**Compounding factor — per-month tracking across cycle boundaries.**
Usage is stored in Monthly Values **one per calendar month**, each resetting to a fresh 20.
When one billing cycle spans **two calendar months** (e.g. Sept *and* Oct), there are
**two** records, each starting at 20, treated as **two separate 20-minute buckets** instead
of one shared bucket for the cycle. Combined with the incorrect usage minutes, the cycle's
allowance effectively becomes **40 minutes (20 + 20)** instead of 20.

---

## Real example — number 41599276 (Lori Wenzel)

- Plan: **Convo Now: Access Complimentary**, Status: **Live**
- Billing cycle: **30 days, Sept 21, 2026 → Oct 21, 2026**

| Monthly Value | Minutes used | `remainder_balance` |
|---|---:|---:|
| September 2026 | 8 | **12** |
| October 2026 | 0 | **20** |

- **Correct** remaining for the cycle: `20 − 8 used = ` **12 minutes**.
- What HubSpot's two records imply: `12 + 20 = ` **32 minutes** still available, from a
  full **40-minute** allowance for the one cycle.
- This consumer is **over-credited by 20 minutes**.

---

## How we detect it (QA page logic)

For every **Convo Now + Live + Convo Now: Access Complimentary** number, the page:

1. Pulls the associated **Subscription** billing cycle (start / end date; calculates the
   end from the billing cycle type if it's missing).
2. Sums the **actual minutes used** (`convo_now_minutes_used`) across every Monthly Value
   that overlaps the billing cycle.
3. Computes the **correct remainder = 20 − minutes used** — one allowance per cycle.
4. **🚩 Red-flags** any number whose cycle crosses a calendar month and therefore receives
   a double 20-minute allowance, showing:
   - `cycle_months` — how many Monthly Values the cycle touches
   - `hubspot_allowance` — what HubSpot grants (months × 20)
   - `over_grant` — the excess minutes (e.g. +20)
   - `remainder` — the correct balance
5. Verifies **no Guest credit-type records** are counted in any calculation.

> Note: the Monthly Values `remainder_balance` is correct **per month**, but must **not be
> summed** across months — summing is what produces the inflated 32 / 40 figures.

---

## Impact

- Affected consumers can use **more free minutes than the plan allows** (up to 2× for a
  cycle that spans two months; more if a cycle spans more months).
- This is a **revenue leakage / cost** issue on the complimentary plan, scaled by how many
  Live complimentary numbers have month-crossing cycles (see the QA page's **Red flags**
  count).

---

## Recommendation

**Owner: Data Engineering.** The fix belongs at the source — in the pipeline that computes
and writes usage minutes to the Monthly Values records:

1. **Correct the usage-minutes calculation** so `convo_now_minutes_used` on each Monthly
   Value reflects true usage.
2. **Aggregate the allowance per billing cycle, not per calendar month** — one 20-minute
   bucket that **carries over** when the cycle crosses a month boundary, instead of
   resetting to 20 each calendar month.

Until the pipeline is corrected, the QA page gives the **correct per-cycle remainder** and
the **list of over-credited numbers** for manual review or correction.
