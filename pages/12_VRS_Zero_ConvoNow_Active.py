import streamlit as st
import pandas as pd
import altair as alt
import os
from datetime import date, datetime, timezone
from collections import defaultdict
from utils import (
    dash_spinner,
    require_auth, fetch_all, norm, to_float,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="VRS Zero / Convo Now Active", layout="wide", page_icon="🔄")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

CONVO_RATE = 2.60

report_header(
    "VRS Zero / Convo Now Active",
    "Contacts with 0 VRS minutes, 0 CfZ minutes, and active Convo Now usage in the selected period",
    section="Analytics"
)

# ── Filters ───────────────────────────────────────────────────────────────────
col_range, col_run = st.columns([3, 1])
with col_range:
    RANGE_OPTIONS = ["This Month", "This Year", "Jun 2026–Present", "Last 3 Months", "Last 6 Months", "Last 12 Months", "All Time"]
    range_label = st.selectbox("Date range (month_date)", RANGE_OPTIONS)
with col_run:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)

report_header_close()

# Clear cached results if the date range changed since last run
cached = st.session_state.get("_vrs_zero_cache")
if cached and cached.get("range_label") != range_label:
    del st.session_state["_vrs_zero_cache"]
    cached = None

if not run and not cached:
    st.info("Select a date range and click **Run Report**.")
    st.stop()

# ── Resolve date floor ────────────────────────────────────────────────────────
today = date.today()
if range_label == "This Month":
    floor = date(today.year, today.month, 1)
elif range_label == "This Year":
    floor = date(today.year, 1, 1)
elif range_label == "Jun 2026–Present":
    floor = date(2026, 6, 1)
elif range_label == "Last 3 Months":
    m, y = today.month - 3, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 6 Months":
    m, y = today.month - 6, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 12 Months":
    floor = date(today.year - 1, today.month, 1)
else:
    floor = date(2000, 1, 1)

floor_ms = str(int(datetime(floor.year, floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))
DATE_FILTER = {"propertyName": "month_date", "operator": "GTE", "value": floor_ms}

# Skip data fetch if results are already cached for this range
if run or not cached:
    # ── Step 1: Convo Now MV records with usage > 0 ───────────────────────────
    with dash_spinner("Fetching Convo Now monthly values with usage > 0…"):
        cn_mvs = fetch_all(
            "2-46246179",
            ["number", "month_date", "usage_minutes", "service_type"],
            filter_groups=[{"filters": [
                DATE_FILTER,
                {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
            ]}]
        )

    cn_numbers = set()
    cn_num_month_usage: dict = defaultdict(lambda: defaultdict(float))
    for r in cn_mvs:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        mk  = (p.get("month_date") or "")[:7]
        usage = to_float(p.get("usage_minutes")) or 0.0
        if num and mk:
            cn_numbers.add(num)
            cn_num_month_usage[num][mk] += usage

    if not cn_numbers:
        st.warning("No Convo Now records with usage > 0 found in this date range.")
        st.stop()

    # ── Step 2: Number objects — filter by usage_type & credit_plan_name ──────
    with dash_spinner(f"Looking up {len(cn_numbers):,} Convo Now number objects…"):
        cn_obj_records = []
        for i in range(0, len(cn_numbers), 100):
            chunk = list(cn_numbers)[i:i+100]
            cn_obj_records.extend(fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name", "service_type",
                 "number_status", "usage_type", "credit_plan_name"],
                filter_groups=[{"filters": [
                    {"propertyName": "number",           "operator": "IN", "values": chunk},
                    {"propertyName": "usage_type",       "operator": "EQ", "value": "Personal"},
                    {"propertyName": "credit_plan_name", "operator": "EQ", "value": "Convo Now: Access Complimentary"},
                    {"propertyName": "number_status",    "operator": "EQ", "value": "Live"},
                ]}]
            ))

    cn_emails: set = set()
    num_to_email: dict = {}
    email_to_name: dict = {}
    for r in cn_obj_records:
        p = r.get("properties", {})
        num   = str(p.get("number") or "").strip()
        email = str(p.get("email")  or "").strip().lower()
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name")  or "").strip()
        if email:
            cn_emails.add(email)
            num_to_email[num] = email
            if fn or ln:
                email_to_name[email] = f"{fn} {ln}".strip()

    if not cn_emails:
        st.warning("No qualifying Convo Now numbers found (Personal + Convo Now: Access Complimentary + Live).")
        st.stop()

    # ── Step 3: All numbers for those contacts (VRS + Convo Now) ─────────────
    with dash_spinner(f"Fetching all numbers for {len(cn_emails):,} contacts…"):
        all_num_objs = []
        for i in range(0, len(cn_emails), 100):
            chunk = list(cn_emails)[i:i+100]
            all_num_objs.extend(fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name", "service_type", "number_status"],
                filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk},
                ]}]
            ))

    email_vrs_nums: dict = defaultdict(set)
    email_cn_nums:  dict = defaultdict(set)
    all_numbers_by_email: dict = defaultdict(set)

    for r in all_num_objs:
        p     = r.get("properties", {})
        num   = str(p.get("number") or "").strip()
        email = str(p.get("email")  or "").strip().lower()
        svc   = norm(p.get("service_type") or "")
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name")  or "").strip()
        if not email or not num:
            continue
        num_to_email[num] = email
        all_numbers_by_email[email].add(num)
        if fn or ln:
            email_to_name.setdefault(email, f"{fn} {ln}".strip())
        if svc == "vrs":
            email_vrs_nums[email].add(num)
        elif svc == "convo now":
            email_cn_nums[email].add(num)

    all_nums_flat = list({n for nums in all_numbers_by_email.values() for n in nums})

    # ── Step 4: MV records for all numbers in the period ─────────────────────
    with dash_spinner(f"Fetching monthly values for {len(all_nums_flat):,} numbers…"):
        all_mvs = []
        for i in range(0, len(all_nums_flat), 100):
            chunk = all_nums_flat[i:i+100]
            all_mvs.extend(fetch_all(
                "2-46246179",
                ["number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"],
                filter_groups=[{"filters": [
                    DATE_FILTER,
                    {"propertyName": "number",       "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                ]}]
            ))

    # ── Step 5: Aggregate per contact ────────────────────────────────────────
    email_vrs_total:  dict = defaultdict(float)
    email_cfz_total:  dict = defaultdict(float)
    email_ursa_total: dict = defaultdict(float)
    email_cn_total:   dict = defaultdict(float)
    email_vrs_months: dict = defaultdict(lambda: defaultdict(float))
    email_cn_months:  dict = defaultdict(lambda: defaultdict(float))

    for r in all_mvs:
        p    = r.get("properties", {})
        num  = str(p.get("number") or "").strip()
        mk   = (p.get("month_date") or "")[:7]
        svc  = norm(p.get("service_type") or "")
        email = num_to_email.get(num)
        if not email or not mk:
            continue
        usage = to_float(p.get("usage_minutes")) or 0.0
        if svc == "vrs":
            cfz_min  = to_float(p.get("cfz_minutes"))  or 0.0
            ursa_min = to_float(p.get("ursa_minutes")) or 0.0
            email_vrs_total[email]       += usage
            email_cfz_total[email]       += cfz_min
            email_ursa_total[email]      += ursa_min
            email_vrs_months[email][mk]  += usage
        elif svc == "convo now":
            email_cn_total[email]        += usage
            email_cn_months[email][mk]   += usage

    # ── Step 6: Filter — VRS = 0 AND CfZ = 0 AND Convo Now > 0 ──────────────
    rows = []
    for email in sorted(cn_emails):
        vrs_total  = email_vrs_total.get(email,  0.0)
        cfz_total  = email_cfz_total.get(email,  0.0)
        ursa_total = email_ursa_total.get(email, 0.0)
        cn_total   = email_cn_total.get(email,   0.0)
        if vrs_total > 0 or cfz_total > 0 or cn_total == 0:
            continue
        cn_months     = email_cn_months.get(email, {})
        active_months = len(cn_months)
        latest_month  = max(cn_months.keys()) if cn_months else ""
        latest_cn_min = cn_months.get(latest_month, 0.0) if latest_month else 0.0
        cn_cost  = cn_total * CONVO_RATE
        vrs_nums = sorted(email_vrs_nums.get(email, set()))
        cn_nums  = sorted(email_cn_nums.get(email,  set()))
        rows.append({
            "Name":              email_to_name.get(email, "—"),
            "Email":             email,
            "VRS Numbers":       ", ".join(vrs_nums) if vrs_nums else "—",
            "Convo Now Numbers": ", ".join(cn_nums)  if cn_nums  else "—",
            "VRS Minutes":       round(vrs_total,  1),
            "URSA Minutes":      round(ursa_total, 1),
            "CfZ Minutes":       round(cfz_total,  1),
            "Convo Now Min":     round(cn_total,   1),
            "Convo Now Cost":    round(cn_cost,    2),
            "Active Months":     active_months,
            "Latest Month":      latest_month,
            "Latest Month Min":  round(latest_cn_min, 1),
        })

    if not rows:
        st.success("No contacts found matching VRS = 0, CfZ = 0, Convo Now > 0 in this period.")
        st.stop()

    df_full = pd.DataFrame(rows).sort_values("Convo Now Min", ascending=False).reset_index(drop=True)

    # Persist to session state so reruns (search, etc.) don't re-fetch
    st.session_state["_vrs_zero_cache"] = {
        "range_label":   range_label,
        "df_full":       df_full,
        "email_cn_months": {k: dict(v) for k, v in email_cn_months.items()},
    }
else:
    df_full          = cached["df_full"]
    email_cn_months  = cached["email_cn_months"]

# ── Summary tiles ─────────────────────────────────────────────────────────────
total_contacts = len(df_full)
total_cn_min   = df_full["Convo Now Min"].sum()
total_cn_cost  = df_full["Convo Now Cost"].sum()
avg_months     = df_full["Active Months"].mean()

def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.4rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;line-height:1.15;">{value}</div>
  {f'<div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
</div>"""

st.markdown(f"""
<div style="font-size:0.8rem;color:#6B7280;margin-bottom:1rem;">
  Period: <strong>{range_label}</strong>
  &nbsp;·&nbsp; VRS = 0 min &nbsp;·&nbsp; CfZ = 0 min &nbsp;·&nbsp; Convo Now &gt; 0 min
</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin-bottom:1.5rem;">
  {tile("Contacts", f"{total_contacts:,}", "VRS=0, CfZ=0, CN active")}
  {tile("VRS Minutes", "0", "confirmed zero", "#6B7280")}
  {tile("CfZ Minutes", "0", "confirmed zero", "#6B7280")}
  {tile("Convo Now Min", f"{total_cn_min:,.1f}", "total across period", "#3B82F6")}
  {tile("Convo Now Cost", f"${total_cn_cost:,.2f}", f"@ ${CONVO_RATE}/min", "#3B82F6")}
</div>""", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────────
filtered_emails = set(df_full["Email"])

# Monthly Convo Now usage
month_totals: dict = defaultdict(float)
for email in filtered_emails:
    for mk, usage in email_cn_months.get(email, {}).items():
        month_totals[mk] += usage

ch_left, ch_right = st.columns(2)

with ch_left:
    if month_totals:
        chart_df = pd.DataFrame([
            {"Month": mk, "Convo Now Minutes": round(v, 1)}
            for mk, v in sorted(month_totals.items())
        ])
        bar = (
            alt.Chart(chart_df)
            .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Month:N", sort=list(chart_df["Month"]),
                        axis=alt.Axis(title=None, labelAngle=-20)),
                y=alt.Y("Convo Now Minutes:Q", title="Minutes"),
                tooltip=["Month", alt.Tooltip("Convo Now Minutes:Q", format=",.1f")],
            )
            .properties(height=220, title="Convo Now Usage by Month")
        )
        st.altair_chart(bar, use_container_width=True)

with ch_right:
    # Top 15 contacts by Convo Now minutes
    top_df = df_full.head(15)[["Name", "Email", "Convo Now Min"]].copy()
    top_df["Label"] = top_df.apply(
        lambda r: r["Name"] if r["Name"] != "—" else r["Email"], axis=1
    )
    top_chart = (
        alt.Chart(top_df)
        .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Convo Now Min:Q", title="Minutes"),
            y=alt.Y("Label:N", sort="-x", title=None,
                    axis=alt.Axis(labelLimit=200)),
            tooltip=["Label", alt.Tooltip("Convo Now Min:Q", format=",.1f")],
        )
        .properties(height=max(220, min(15, len(top_df)) * 26),
                    title="Top 15 Contacts by Convo Now Usage")
    )
    st.altair_chart(top_chart, use_container_width=True)

# Active months distribution
month_dist_df = df_full["Active Months"].value_counts().reset_index()
month_dist_df.columns = ["Active Months", "Count"]
month_dist_df = month_dist_df.sort_values("Active Months")
dist_chart = (
    alt.Chart(month_dist_df)
    .mark_bar(color="#8B5CF6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
    .encode(
        x=alt.X("Active Months:O", title="Active Months"),
        y=alt.Y("Count:Q", title="# Contacts"),
        tooltip=["Active Months", "Count"],
    )
    .properties(height=180, title="Distribution: Active Months per Contact")
)
st.altair_chart(dist_chart, use_container_width=True)

# ── Search + table ────────────────────────────────────────────────────────────
st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin:1.25rem 0 0.5rem;'>Contact Detail</div>", unsafe_allow_html=True)

search = st.text_input(
    "Search contacts",
    placeholder="Filter by name, email, or phone number…",
    label_visibility="collapsed",
)

df_view = df_full.copy()
if search.strip():
    q = search.strip().lower()
    mask = (
        df_view["Name"].str.lower().str.contains(q, na=False) |
        df_view["Email"].str.lower().str.contains(q, na=False) |
        df_view["VRS Numbers"].str.lower().str.contains(q, na=False) |
        df_view["Convo Now Numbers"].str.lower().str.contains(q, na=False)
    )
    df_view = df_view[mask]
    st.caption(f'{len(df_view):,} of {total_contacts:,} contacts match "{search}"')

display_df = df_view[[
    "Name", "Email", "Convo Now Numbers", "VRS Numbers",
    "VRS Minutes", "URSA Minutes", "CfZ Minutes",
    "Convo Now Min", "Convo Now Cost", "Active Months", "Latest Month", "Latest Month Min"
]]

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "VRS Minutes":      st.column_config.NumberColumn(format="%.1f"),
        "URSA Minutes":     st.column_config.NumberColumn(format="%.1f"),
        "CfZ Minutes":      st.column_config.NumberColumn(format="%.1f"),
        "Convo Now Min":    st.column_config.NumberColumn("CN Min (Total)", format="%.1f"),
        "Convo Now Cost":   st.column_config.NumberColumn("CN Cost ($)",    format="$%.2f"),
        "Latest Month Min": st.column_config.NumberColumn(format="%.1f"),
    }
)

# ── CSV download ──────────────────────────────────────────────────────────────
csv = df_view.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV",
    csv,
    file_name=f"vrs_zero_convo_active_{range_label.replace(' ', '_').replace('–','_')}.csv",
    mime="text/csv",
)
