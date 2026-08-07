# Convo Reporting — Full Guide to Every Report (plain language)

A detailed, no-code walkthrough of every page in the app. For each report you'll find:
**what it tells you · how to use it · how to read the numbers · watch-outs.**

App: `convo-reporting.streamlit.app`. Everything is read **live from HubSpot** each time
you click **Run**. Times are **Central**. Nothing is stored in a separate database.

**Quick glossary:** VRS minutes = relay call minutes · URSA = app minutes
(iOS+Android+Web) · CfZ = Convo-for-Zoom minutes · Usage = CfZ + URSA · Convo Now = the
consumer app · FCC rate = $8.61 (Jul 2026+) / $8.33 before · Live status = `account_status`
· Baseline = a number's typical past usage.

---

# 🏠 Home

## Overview
**What it tells you:** the health of the business on one screen.
**How to use it:** it's the landing page — open it first.
**Reading it:** KPI cards (with mini trend lines), a status donut (Live / Suspended /
Deactivated), a 12-month trend (VRS bars + Convo Now line), and cards linking to every
report with a live count on each.
**Watch-out:** "Convo Now Live" excludes guest accounts.

## This Month
**What it tells you:** what changed this calendar month vs last.
**How to use it:** open it for a monthly pulse; no inputs needed.
**Reading it:** top cards = New VRS, Deactivated, and **Net New** (New − Deactivated) with a
% change vs last month. Chips = Port-In, Port-Out, **Net Port** (In − Out), Deactivated,
Unregistered. Tables list port-outs (with age), unregistered/manual numbers, deactivations
(with reason), and new Convo Now.
**Watch-out:** a red % on Net New means fewer net additions than last month, not a loss
unless the number itself is negative.

## Weekly Report
Same as This Month but **Monday–Sunday**, with a 12-week trend. Tables always show headers
("No X this week" when empty).

## Daily Report
Same shape at a **daily** grain: today vs yesterday, with a 12-day trend.

## VRS Lookup
**What it tells you:** everything about one customer or number.
**How to use it:** type a number, email, or name and search.
**Reading it:** status, month-by-month usage, VRS vs Convo Now split, and estimated FCC
value (minutes × the FCC rate).
**Watch-out:** the rate tile shows the current month's rate; historical months are valued
at their own month's rate.

---

# #️⃣ Numbers

## Numbers Report
**What it tells you:** the full inventory of phone numbers.
**How to use it:** optionally filter by usage type (All / Personal / Organization).
**Reading it:** counts by status (Live/Active/other) and by VRS vs Convo Now.

## Number Funnel
**What it tells you:** how many numbers progress Registered → Created → Live.
**How to use it:** set the date range, usage type, and language.
**Reading it:** a funnel with percentages plus proportional bars. Each stage is a subset of
the one before, so it only goes down.
**Watch-out:** "Live" uses the real status field; earlier the funnel looked wrong when it
used the wrong field.

## Registrations Report
**What it tells you:** how many registrations happened in a period and how many verified.
**How to use it:** pick a **Period** (Today … Last Year, or Custom), pick **Filter dates
by** (Registered At / Manually Verified At / …), click Run.
**Reading it:** tiles = Total, ✅ Verified (auto + manual), ❌ Not verified, ✍️ Manually
verified. Breakdowns by reg type / usage type / Lex status (with %). A Reg-Type × Lex
grid shows how each type was verified. "Manual activity" shows who verified/edited.
The **Not-verified worklist** lists what still needs attention, with error reasons.
**Watch-out:** "Verified" = `automatic_success` + `manual_success` — not the literal word
"verified." (Full detail in `registrations_report_plain.md`.)

## Registration Funnel
**What it tells you:** where sign-ups drop off before finishing registration.

## Port-In Report
**What it tells you:** numbers ported into Convo, over time.

## Port-Out Winback
**What it tells you:** deactivated numbers that ported out — your winback targets.
**Reading it:** includes tenure (how long they were a customer) and usage history to
prioritize outreach.

## Geographic Report
**What it tells you:** where customers are, by state/region.

## Year-over-Year
**What it tells you:** this year vs last, month by month, for any usage metric.
**How to use it:** choose the metric (CfZ, URSA platforms, usage) and Run.
**Reading it:** a grouped bar chart and a month table with year-over-year deltas.
**Watch-out:** use the built-in **duplicate check** if a recent month looks high — it flags
numbers with more than one monthly record (which double-counts minutes).

---

# 👥 Customers

## URSA Login Report
**What it tells you:** who has logged into the app, and when.

## Sign-Up Journey
**What it tells you:** the path a customer takes from sign-up to active use.

## Age Demographics
**What it tells you:** the age distribution of the customer base.

## Churn Risk
**What it tells you:** customers showing signs of disengaging, so you can reach out early.

## VRS Zero / Convo Now Active
**What it tells you:** customers using Convo Now but making **0 VRS and 0 CfZ minutes**.
**How to use it:** pick a date range (incl. Custom months); optionally turn on "Only Convo
Now (no VRS number at all)" and a Number-Created date filter.
**Reading it:** count of these contacts, their Convo Now minutes and cost.
**Watch-out:** many of these have no VRS number at all — that's the point (Convo-Now-only
customers).

## Retention Report
**What it tells you:** do VRS/CfZ customers stay? Retention at 3, 6, and 12 months.
**Watch-out:** watch for data gaps in months — the report flags them.

## Org Retention Report
**What it tells you:** whether each organization is holding its usage vs its own baseline.
**How to use it:** Run, then filter by status, email domain, or search.
**Reading it:** each org number shows 12 months, its **baseline** (average of prior months
that actually had usage), the latest month, and **Retention %**. Colours: green = up, yellow
= steady, orange/red = down, **Churned** = dropped to zero.
**Watch-out:** if whole months read 0 for everyone, that's missing data, not zero usage —
the baseline skips those months so declines aren't hidden.

---

# 🎫 Support

## Consumer Success Tickets
**What it tells you:** the Consumer Success pipeline — volume, cost, and detail.
**How to use it:** set date range, open/closed, ticket type, team, language; Run.
**Reading it:** Usage Minutes (URSA + CfZ) and **VRS FCC Cost = those minutes × the FCC
rate** (so the two reconcile). A winback view shows whether a closed port-out's **same
number** came back Live. Tables: ticket descriptions and an association trace.
**Watch-out:** cost is rate-based now, not HubSpot's pre-calc, so minutes and cost match.

## Ticket Report
**What it tells you:** tickets across pipelines, by type and owner (incl. deactivated
owners, tagged).

## Jira Ticket Report
**What it tells you:** engineering/Jira tickets alongside the support view.

## Survey
**What it tells you:** customer feedback and effort sentiment.
**Reading it:** CES sentiment (Difficult / Neutral / Easy), with this-month vs last-month
comparison and date presets.

---

# 🛠️ Tools

## Bulk Search
**What it tells you:** results for many numbers/emails at once.
**How to use it:** paste a list and run.

## Data Explorer
**What it tells you:** ad-hoc look at the underlying objects and fields.

## Pendo Report
**What it tells you:** product engagement plus per-email usage.
**How to use it:** load Pendo contacts, filter by name/search; or use the **usage lookup**
— enter an email to see that person's VRS numbers and their URSA/CfZ minutes by month.
**Reading it:** first/last visit, events, days active, time on app, usage trend.
**Watch-out:** engagement (app opens) is different from call minutes — a user can browse
daily yet make no calls.

## Data Quality
**What it tells you:** general data-quality checks across objects.

## CONVO360 Import
**What it tells you:** a support/AHT audit from the CONVO360 interaction CSV.
**How to use it:** upload the CSV once (it's saved), then read the audit.
**Reading it:** AHT (average handle time), handle time by type (Call/Chat/Query/Video),
answered vs missed calls and answer rate, per-rep scorecard, peak hours & busiest days.
**Watch-out:** missed-call wait time only exists if the export logs `Missed · Wait: …`;
some exports just say "Missed."

## Data Health Audit
**What it tells you:** where the Number data is incomplete, and email-association issues.
**How to use it:** pick Service + Live-only, Run.
**Reading it:** counts of numbers missing email / status / usage type, and each number's
email matched to a Contact's **primary** email, a **secondary** email, or **none** — so you
can clean up or re-associate.

---

# 🛡️ Admin

## Audit Log
**What it tells you:** who ran which report, and when.

---

## Universal tips

- **Save & reuse:** most reports remember your last run — re-open is instant. Click **Run**
  for fresh data or after changing filters.
- **Live data:** if HubSpot is missing data (e.g. a month of usage), the report shows that
  gap honestly — it can't invent numbers.
- **Central time:** all day/week/month windows use Central Time to match HubSpot.
- **Something looks off?** Read the report's on-page captions first — they explain each
  number in context.
