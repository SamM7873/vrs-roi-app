# Convo Reporting — Team Guide (plain language)

What every page does, in words anyone can follow. No code.
App: `convo-reporting.streamlit.app` · Data lives in **HubSpot** and is read live each run.

> Companion: **`technical-guide.md`** explains the filters and Python for developers.

---

## How the app works (30 seconds)

`HubSpot → the app reads it → you see a report.` Nothing is stored in a separate
database. Click a page's **Run** button to pull fresh numbers.

**The objects it reads:** Contacts (people), Companies (their business), Numbers (each
VRS / Convo Now phone number), Monthly Values (minutes per number per month),
Registrations (registration events), Tickets (support cases). Times use **Central Time**.

**Glossary:** VRS minutes = relay call minutes · URSA = app minutes (iOS+Android+Web) ·
CfZ = Convo-for-Zoom minutes · Usage = CfZ + URSA · Convo Now = the consumer app ·
FCC rate = $8.61 (Jul 2026+) / $8.33 (Jul 2025–Jun 2026) · account_status = the real
Live/Deactivated field · Baseline = a number's typical past usage.

---

## Home

- **Overview** — one screen: KPI cards, a status donut, a 12-month trend, and cards linking to every report with live counts.
- **This Month** — new VRS, deactivated, and net-new this month vs last, plus port-in/out and detail tables.
- **Weekly Report** — same idea, Monday–Sunday, with a 12-week trend.
- **Daily Report** — today vs yesterday, with a 12-day trend.
- **VRS Lookup** — search one number/email/name → full profile, usage history, and estimated FCC value.

## Numbers

- **Numbers Report** — full inventory by status and type (VRS vs Convo Now, Personal vs Org).
- **Number Funnel** — how many numbers go Registered → Created → Live, with percentages.
- **Registrations Report** — registrations over any period, how many verified, who verified manually, and a not-verified worklist. *(See its own plain guide too.)*
- **Registration Funnel** — where sign-ups drop off before finishing.
- **Port-In Report** — numbers ported in, over time.
- **Port-Out Winback** — deactivated numbers that ported out, with usage history for winback targeting.
- **Geographic Report** — customers by state/region.
- **Year-over-Year** — this year vs last, month by month, for any usage metric. Includes a duplicate-record check.

## Customers

- **URSA Login Report** — who logged into the app, and when.
- **Sign-Up Journey** — the path from sign-up to active use.
- **Age Demographics** — age distribution of customers.
- **Churn Risk** — customers showing signs of disengaging.
- **VRS Zero / Convo Now Active** — customers using Convo Now but making 0 VRS/CfZ minutes. Can filter to "only Convo Now (no VRS number)" and by number-created date.
- **Retention Report** — do VRS/CfZ customers stay? Cohort retention at 3/6/12 months.
- **Org Retention Report** — are organizations holding usage vs their own 6-month baseline? Colour-coded green→red, with churn flags.

## Support

- **Consumer Success Tickets** — support volume, cost, and detail. Includes a winback view (did the same number come back?) and ticket descriptions.
- **Ticket Report** — tickets across pipelines, by type and owner.
- **Jira Ticket Report** — engineering tickets alongside support.
- **Survey** — customer feedback and CES sentiment (Difficult/Neutral/Easy), this-month vs last-month.

## Tools

- **Bulk Search** — look up many numbers/emails at once.
- **Data Explorer** — ad-hoc exploration of the objects.
- **Pendo Report** — product engagement (visits, days active, trend) + per-email URSA/CfZ usage lookup.
- **Data Quality** — general data-quality checks.
- **CONVO360 Import** — upload the CONVO360 CSV → AHT/KPI audit (handle time, missed calls, per-rep, peak hours).
- **Data Health Audit** — numbers with missing data, and primary vs secondary email matches to clean up.

## Admin

- **Audit Log** — who ran which report, and when.

---

## Good to know

- Reports **save their last run**, so re-opening is instant. Click **Run** for fresh data.
- Numbers are **live from HubSpot** — if HubSpot data is incomplete (e.g. a missing month), the report reflects that.
- If a figure looks off, check the report's on-page captions first, then this guide.
