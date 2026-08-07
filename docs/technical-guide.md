# Convo Reporting — Technical Guide (all pages)

For developers/analysts. How each page fetches, filters, and computes. Pairs with the
plain-language `team-guide.md`.

Entry point: `app.py` (uses `st.navigation` to group pages). Shared helpers: `utils.py`.

---

## Shared architecture (applies to most pages)

- **HubSpot REST v3**, private-app token in `utils.headers` / `BASE_URL`. Read-only.
- **Objects:** Contacts, Companies, `2-40974683` Number, `2-46246179` Monthly Values,
  `2-58833629` Registrations, standard Tickets, `feedback_submissions`.
- **`fetch_all(object, props, filter_groups)`** — search wrapper (`utils.py`). Note: the
  plain Search API caps at 10k; heavy pages use an **`hs_object_id` seek cursor** to page
  past it (see YoY `_search_seek`, Registrations `_seek`).
- **Counts** — `limit:1` and read `total` from the response instead of paging.
- **Canonical status** = `account_status` (Live/Deactivated). `number_status` is unreliable.
- **Central-time boundaries** — offset −5 (CDT, Mar–Nov) / −6 (CST) → epoch-ms, to match
  HubSpot's report windows.
- **FCC rate** — `utils.vrs_rate_for_month(month)`: $8.61 from Jul 2026, $8.33 before.
- **Caching** — pages persist results via `save_report`/`load_report` (pickle to disk) and
  `st.session_state`, so widget interaction and reloads don't re-fetch. Many gate the
  render on a cached copy so filters apply without re-running.
- **Key property names:** `service_type` (VRS/Convo Now), `usage_type` (Personal/
  Organization/Business), `credit_type` (guest exclusion — use all-live minus guest, not
  NOT_IN, which drops blanks), `credit_plan_name`, `bandwidth_order_type` (portins/
  portouts), `number_created_at`, `number_deleted_at`, `registered_at`, `deleted_reason`,
  `month_date`, `usage_minutes`, `ursa_*_minutes`, `cfz_minutes`,
  `fcc_cost_based_on_vrs_usage`.

---

## Home

**Overview** (`23_Overview.py`) — KPI cards with sparklines; status donut via
`account_status`; 12-month trend (VRS bars + Convo Now line); "browse reports" cards with
live count pills. Convo Now Live = all-live minus guest-live.

**This Month / Weekly / Daily** (`24/26/25`) — near-identical. Helpers: `_count` (search
`total`), `_ms` (CT epoch), `_btw` (between two ms), `_portout_detail` (avg age + rows),
`_deact_detail` (with `deleted_reason`), `_convo_detail` (excludes guest), `_unreg_detail`.
Net New = New VRS − Deactivated. Net Port = Port-In − Port-Out. `_delta(cur,prev)` decides
direction by `(cur-prev)` sign so negative baselines don't flip the arrow. Month/week/day
boundaries in Central time.

**VRS Lookup** (`0_Lookup.py`) — fetch a number's Monthly Values; cost per record uses
`vrs_rate_for_month(month)`; the summary rate tile uses the current-month rate.

## Numbers

**Numbers Report** (`1`) — `fetch_all` on the Number object; client-side counts by status
(`account_status`) and `usage_type`.

**Number Funnel** (`9`) — cumulative subset stages Registered → Created → Live in
chronological order (fixes a non-monotonic funnel). Filters: date range (CT), usage type,
language. Highcharts funnel via `components.html` loading the CDN.

**Registrations Report** (`30`) — object `2-58833629`. Reads the **object schema**
(`_schema`) and resolves field names by **HubSpot label** (`_resolve_fields`) so custom
field names aren't guessed. `_seek` pages the search by `hs_object_id`. Period presets in
`_range()`; "Filter dates by" selects which date field the window applies to. Verified =
Lex `automatic_success`/`manual_success` or a manual verification. See
`registrations_report.md` for the full breakdown.

**Registration Funnel** (`6`) — sign-up → completion stage counts.

**Port-In Report** (`7`) — `bandwidth_order_type = portins` over time.

**Port-Out Winback** (`10`) — deactivated + ported-out numbers; tenure = created→deleted;
usage history joined from Monthly Values.

**Geographic Report** (`3`) — group by `state` on the Number object.

**Year-over-Year** (`16`) — `_search_seek` on Monthly Values with `usage_minutes > 0`;
aggregates each metric by `YYYY-MM`. URSA Minutes = iOS+Android+Web; Usage = CfZ +
platform-summed URSA (excludes untagged URSA). Includes a **duplicate check** — counts
records per Number+Service for a month to catch double-counting.

## Customers

**URSA Login** (`2`), **Sign-Up Journey** (`8`), **Age Demographics** (`13`),
**Churn Risk** (`5`) — Number/Contact reads with the shared cache/auto-refresh pattern.

**VRS Zero / Convo Now Active** (`12`) — multi-step: Convo Now MV (usage>0) → qualifying
Number objects (Personal + Convo Now: Access Complimentary + Live) → all numbers for those
contacts → MV rollups. Keeps contacts with VRS=0, CfZ=0, Convo Now>0. Filters: month_date
range incl. Custom (GTE+LTE), "only Convo Now (no VRS number)", Number-Created date range
(VRS or CN). Cost = Convo Now min × 2.60.

**Retention Report** (`22`) — cohort retention at 3/6/12 months via `_paged_search`
(hs_object_id cursor); Active Users per calendar month; data-gap detector.

**Org Retention Report** (`28`) — Number where `usage_type=Organization`, VRS,
`account_status=Live` → Monthly Values by phone. Baseline = **average of the 6 prior months
that have usage** (skips data-gap months; shows "Months counted"). Retention % = latest ÷
baseline. Colour bands + **Churned** when a number with a real baseline drops to 0. Filters:
status, email domain, search; 12-month table for context; session-state cache.

## Support

**Consumer Success Tickets** (`11`) — big pipeline report. Ticket → contact → number
(v4 associations) → Monthly Values (by phone). Usage tiles: Usage = URSA + CfZ; **VRS FCC
Cost = usage minutes × `vrs_rate_for_month(month)`** (reconciles with minutes). Winback lens
matches the **same** ported-out number now Live. Caches to session + disk (`_CS_CACHE_VARS`,
signature includes a pipeline version). Includes ticket-descriptions table and inspector.

**Ticket Report** (`20`) / **Jira** (`21`) — tickets by pipeline/type/owner; owners include
archived (deactivated) tagged accordingly.

**Survey** (`19`) — `feedback_submissions`; CES sentiment groups (Difficult/Neutral/Easy),
date presets, this-vs-last-month; owners resolved incl. archived.

## Tools

**Bulk Search** (`4`) — many-number lookup; VRS cost per month via `vrs_rate_for_month`.

**Data Explorer** (`17`) — generic object/property explorer.

**Pendo Report** (`14`) — Contacts with a Pendo ID (`convo_now_account_id`) + `pendo_*`
fields. First/Last name filters. Usage lookup: email → Number(email, VRS) → Monthly Values
→ URSA & CfZ per number/month; Number-Created date filter.

**Data Quality** (`15`) — object-level quality checks.

**CONVO360 Import** (`27`) — upload CSV, saved to disk. Parses `Wait Time` incl.
`Missed · Wait: HH:MM:SS`; AHT = mean Duration; per-type/per-rep tables; answered vs missed
(missed = Wait == "Missed"); peak hours converted PT→Central.

**Data Health Audit** (`29`) — Number object scan: missing email / `account_status` /
`usage_type`, and `account_status` vs `number_status` mismatch. Email review: each Number's
email matched to a Contact primary email or a secondary (`hs_additional_emails`) or none.

## Admin

**Audit Log** (`18`) — records report views via `utils.log_report_view`.

---

## Conventions & gotchas

- Guest exclusion: **all-live minus guest-live**, never `NOT_IN` (drops blanks).
- Deltas: decide direction by `(cur-prev)`, show absolute change when baseline ≤ 0.
- Period `strftime`: use `m.strftime("%b %Y")` (pandas `Period` rejects format-specs).
- MCP tools can't read custom objects — property discovery is via the CRM properties API
  or the HubSpot UI.
- Deploy: Streamlit Community Cloud from `main`; reboot to pick up new features.
