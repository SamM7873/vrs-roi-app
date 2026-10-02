# Consumer Excellence Program — Reader's Guide

A plain-language guide to the **Consumer Excellence Program** report, so anyone on the
team can read it without knowing the underlying HubSpot data.

---

## 1. What question does this page answer?

> **Of the people who recently signed up for a VRS number, how many actually got
> activated and started calling — and where do they drop off along the way?**

It turns raw HubSpot Number records into a clean activation funnel plus a marketing
attribution view.

---

## 2. Who is counted (the population)

Every number on this page passes **all** of these rules:

| Rule | Why |
|---|---|
| **VRS only** (`service_type` = VRS) | We only care about VRS activation, not Convo Now / other services. |
| **English only** (`language_preference` = English; blank counts as English) | Scoped to the English consumer program. |
| **One row per phone number** (deduped) | A number can have more than one record; we keep the **Live** one so counts aren't doubled. |
| **Inside the date window** | By the date the **number was created**. Pick it with the Rolling window / Custom start controls at the top. |

The window shows an explicit date range, e.g. **Sep 4, 2026 – Oct 1, 2026**, and
uses the last *completed* day (PST).

---

## 3. The acquisition funnel (the 5 cards)

Each stage is a **subset** of the stage before it. The big number is the **conversion
%** relative to the **previous** stage, with the raw count in parentheses.

| Stage | What it means | Milestone field |
|---|---|---|
| **Sign-ups** | New VRS + English numbers created in the window | `number_created_at` |
| **Live consumers** | …that have a working (Live) PSTN number ready to call | `number_status` = Live |
| **First login rate** | …that then logged into the app | `ursa_first_login` |
| **First-call rate** | …that then made a first outbound call | `ursa_first_outbound_call` |
| **Keep calling** | …that made a **second** outbound call (stuck around) | `ursa_second_outbound_call` |

**How to read a %:** "First-call rate **42% (95)**" → 95 people made their first call,
and that is 42% of the people who had logged in. A **low %** at a stage is exactly
where consumers are being lost — that's the stage to improve.

**The mini bars under each card** = that metric's **last 12 months**, bucketed by the
month the number was created. The brightest bar is the current month. Hover any bar
to see its value.

---

## 4. The Sankey (flow diagram)

Same funnel, shown as a left-to-right flow:

- The **colored band** = consumers who **progressed** to the next stage.
- The grey **"Dropped (n)"** branches = consumers who **stopped** at that stage.

It makes the biggest drop-off points obvious at a glance.

---

## 5. The Type / Numbers pills

These re-slice the **entire** funnel, Sankey, and tables instantly:

- **Type** — *Personal* (B2C consumers) vs *Organisations* (from `usage_type`).
- **Numbers** — *Direct (New)* = a brand-new number, *Ported in* = brought from
  another carrier (from `portin_status`).

Example: *Personal + Direct (New)* reshapes everything to just new personal numbers.

---

## 6. The detail table

One row per number behind the funnel, with:

- **Name, Email, Number, Status**
- **Stage reached** (Sign-up (not live) / Live / First login / First call / Keep calling)
- **Registered at, Number created at, Number deleted at, Delete reason**
- **ursa_first_login / ursa_first_outbound_call / ursa_second_outbound_call** (the call milestones)

It has its own Stage / Number-status filters, a search box, and a **CSV export**.

---

## 7. The bottom "UTM attribution" section

Takes the same live VRS numbers and answers **"where did these sign-ups come from?"**:

- **New live VRS numbers** — total in the current filter.
- **With UTM** — how many carry marketing attribution (a UTM tag).
- **No UTM** — how many have none (unattributed).

Plus a searchable table of every number with its UTM / referral fields.

> Note: the Snowflake registration-tracking fields (`promo_code`, `rt_utm_*`) aren't
> available through the HubSpot API, so those columns are intentionally omitted.

---

## 8. Quick tips

- Click **▶ Run** after changing the date window to refresh the data.
- The **pills** (Type / Numbers / Rolling window) react instantly — no re-run needed.
- Numbers may differ slightly from other dashboards because this page is **VRS + English,
  deduped, preferring the live record** — a deliberately strict definition.
