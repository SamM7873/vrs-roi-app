import streamlit as st
import pandas as pd
import altair as alt
import requests
import os
from datetime import date, datetime
from collections import defaultdict
from utils import (
    require_auth, fetch_all, norm, to_float,
    COMMON_CSS, report_header, report_header_close, vrs_rate_for_month
)

st.set_page_config(page_title="VRS Zero / Convo Now Active", layout="wide", page_icon="🔄")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

CONVO_RATE = 2.60

report_header(
    "VRS Zero / Convo Now Active",
    "Contacts with 0 VRS minutes and active Convo Now usage in the selected period",
    section="Analytics"
)

# ── Filters ───────────────────────────────────────────────────────────────────
col_range, col_run = st.columns([3, 1])
with col_range:
    RANGE_OPTIONS = [
        "Jun 2026–Present",
        "Last 3 Months",
        "Last 6 Months",
        "Last 12 Months",
        "All Time",
    ]
    range_label = st.selectbox("Date range (month_date)", RANGE_OPTIONS)
with col_run:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)

report_header_close()

if not run:
    st.info("Select a date range and click **Run Report**.")
    st.stop()

# ── Resolve date floor ────────────────────────────────────────────────────────
today = date.today()
if range_label == "Jun 2026–Present":
    floor = date(2026, 6, 1)
elif range_label == "Last 3 Months":
    m = today.month - 3
    y = today.year + (m - 1) // 12
    floor = date(y if m > 0 else y - 1, ((m - 1) % 12) + 1, 1)
elif range_label == "Last 6 Months":
    m = today.month - 6
    y = today.year + (m - 1) // 12
    floor = date(y if m > 0 else y - 1, ((m - 1) % 12) + 1, 1)
elif range_label == "Last 12 Months":
    m = today.month - 12
    y = today.year - 1
    floor = date(y, today.month, 1)
else:
    floor = date(2000, 1, 1)

from datetime import timezone
floor_ms = str(int(datetime(floor.year, floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))
DATE_FILTER = {"propertyName": "month_date", "operator": "GTE", "value": floor_ms}

# ── Step 1: Convo Now MV records with usage > 0 ───────────────────────────────
with st.spinner("Fetching Convo Now monthly values with usage > 0…"):
    cn_mvs = fetch_all(
        "2-46246179",
        ["number", "month_date", "usage_minutes", "service_type"],
        filter_groups=[{"filters": [
            DATE_FILTER,
            {"propertyName": "service_type", "operator": "EQ",  "value": "Convo Now"},
            {"propertyName": "usage_minutes",  "operator": "GT",  "value": "0"},
        ]}]
    )

cn_numbers = set()
cn_num_month_usage = defaultdict(lambda: defaultdict(float))  # number → month → usage
for r in cn_mvs:
    p = r.get("properties", {})
    num = str(p.get("number") or "").strip()
    mk = (p.get("month_date") or "")[:7]
    usage = to_float(p.get("usage_minutes")) or 0.0
    if num and mk:
        cn_numbers.add(num)
        cn_num_month_usage[num][mk] += usage

if not cn_numbers:
    st.warning("No Convo Now records with usage > 0 found in this date range.")
    st.stop()

# ── Step 2: Look up number objects to get emails ──────────────────────────────
with st.spinner(f"Looking up {len(cn_numbers):,} Convo Now number objects…"):
    cn_num_list = list(cn_numbers)
    cn_obj_records = []
    for i in range(0, len(cn_num_list), 100):
        chunk = cn_num_list[i:i+100]
        cn_obj_records.extend(fetch_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "service_type", "number_status"],
            filter_groups=[{"filters": [
                {"propertyName": "number", "operator": "IN", "values": chunk},
            ]}]
        ))

cn_emails = set()
num_to_email = {}
email_to_name = {}
for r in cn_obj_records:
    p = r.get("properties", {})
    num = str(p.get("number") or "").strip()
    email = str(p.get("email") or "").strip().lower()
    fn = (p.get("first_name") or "").strip()
    ln = (p.get("last_name") or "").strip()
    if email:
        cn_emails.add(email)
        num_to_email[num] = email
        if fn or ln:
            email_to_name[email] = f"{fn} {ln}".strip()

if not cn_emails:
    st.warning("No emails found for Convo Now numbers.")
    st.stop()

# ── Step 3: All number objects for those emails (VRS + Convo Now) ──────────────
with st.spinner(f"Fetching all numbers for {len(cn_emails):,} contacts…"):
    email_list = list(cn_emails)
    all_num_objs = []
    for i in range(0, len(email_list), 100):
        chunk = email_list[i:i+100]
        all_num_objs.extend(fetch_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "service_type", "number_status"],
            filter_groups=[{"filters": [
                {"propertyName": "email", "operator": "IN", "values": chunk},
            ]}]
        ))

# Build lookup: email → {vrs_numbers, cn_numbers}, number → email
email_vrs_nums = defaultdict(set)
email_cn_nums  = defaultdict(set)
all_numbers_by_email = defaultdict(set)

for r in all_num_objs:
    p = r.get("properties", {})
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

# ── Step 4: All MV records for those numbers in the period ────────────────────
with st.spinner(f"Fetching monthly values for {len(all_nums_flat):,} numbers…"):
    all_mvs = []
    for i in range(0, len(all_nums_flat), 100):
        chunk = all_nums_flat[i:i+100]
        all_mvs.extend(fetch_all(
            "2-46246179",
            ["number", "month_date", "usage_minutes", "service_type"],
            filter_groups=[{"filters": [
                DATE_FILTER,
                {"propertyName": "number",       "operator": "IN",  "values": chunk},
                {"propertyName": "service_type", "operator": "IN",  "values": ["VRS", "Convo Now"]},
            ]}]
        ))

# ── Step 5: Aggregate per email per service type ──────────────────────────────
email_vrs_total  = defaultdict(float)
email_cn_total   = defaultdict(float)
email_vrs_months = defaultdict(lambda: defaultdict(float))  # email → month → vrs_min
email_cn_months  = defaultdict(lambda: defaultdict(float))  # email → month → cn_min

for r in all_mvs:
    p    = r.get("properties", {})
    num  = str(p.get("number") or "").strip()
    mk   = (p.get("month_date") or "")[:7]
    svc  = norm(p.get("service_type") or "")
    usage = to_float(p.get("usage_minutes")) or 0.0
    email = num_to_email.get(num)
    if not email or not mk:
        continue
    if svc == "vrs":
        email_vrs_total[email]       += usage
        email_vrs_months[email][mk]  += usage
    elif svc == "convo now":
        email_cn_total[email]        += usage
        email_cn_months[email][mk]   += usage

# ── Step 6: Filter contacts where VRS = 0 AND Convo Now > 0 ──────────────────
rows = []
for email in sorted(cn_emails):
    vrs_total = email_vrs_total.get(email, 0.0)
    cn_total  = email_cn_total.get(email,  0.0)
    if vrs_total > 0 or cn_total == 0:
        continue  # skip: still has VRS usage, or no Convo Now usage

    cn_months = email_cn_months.get(email, {})
    active_months = len(cn_months)
    latest_month  = max(cn_months.keys()) if cn_months else ""
    latest_cn_min = cn_months.get(latest_month, 0.0) if latest_month else 0.0
    cn_cost = cn_total * CONVO_RATE
    vrs_nums = sorted(email_vrs_nums.get(email, set()))
    cn_nums  = sorted(email_cn_nums.get(email,  set()))

    rows.append({
        "Name":             email_to_name.get(email, "—"),
        "Email":            email,
        "VRS Numbers":      ", ".join(vrs_nums) if vrs_nums else "—",
        "Convo Now Numbers": ", ".join(cn_nums)  if cn_nums  else "—",
        "VRS Minutes":      0.0,
        "Convo Now Min":    round(cn_total, 1),
        "Convo Now Cost":   round(cn_cost, 2),
        "Active Months":    active_months,
        "Latest Month":     latest_month,
        "Latest Month Min": round(latest_cn_min, 1),
    })

if not rows:
    st.success("No contacts found with VRS = 0 and Convo Now > 0 in this period.")
    st.stop()

df = pd.DataFrame(rows).sort_values("Convo Now Min", ascending=False)

# ── Summary tiles ─────────────────────────────────────────────────────────────
total_contacts  = len(df)
total_cn_min    = df["Convo Now Min"].sum()
total_cn_cost   = df["Convo Now Cost"].sum()
avg_months      = df["Active Months"].mean()

def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.4rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;line-height:1.15;">{value}</div>
  {f'<div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
</div>"""

st.markdown(f"""
<div style="font-size:0.8rem;color:#6B7280;margin-bottom:1rem;">
  Period: <strong>{range_label}</strong> &nbsp;·&nbsp; VRS = 0 min &nbsp;·&nbsp; Convo Now &gt; 0 min
</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;margin-bottom:1.5rem;">
  {tile("Contacts", f"{total_contacts:,}", "VRS=0, Convo Now active")}
  {tile("Convo Now Minutes", f"{total_cn_min:,.1f}", "total across period", "#3B82F6")}
  {tile("Convo Now Cost", f"${total_cn_cost:,.2f}", f"@ ${CONVO_RATE}/min", "#3B82F6")}
  {tile("Avg Active Months", f"{avg_months:.1f}", "months with Convo Now usage")}
</div>""", unsafe_allow_html=True)

# ── Monthly bar chart: Convo Now usage across contacts ────────────────────────
all_month_keys = sorted({mk for e in cn_emails for mk in email_cn_months.get(e, {})})
if all_month_keys:
    # Only include contacts that passed the filter
    filtered_emails = set(df["Email"])
    month_totals = defaultdict(float)
    for email in filtered_emails:
        for mk, usage in email_cn_months.get(email, {}).items():
            month_totals[mk] += usage

    chart_df = pd.DataFrame([
        {"Month": mk, "Convo Now Minutes": round(v, 1)}
        for mk, v in sorted(month_totals.items())
    ])
    bar = (
        alt.Chart(chart_df)
        .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Month:N", sort=list(chart_df["Month"]), axis=alt.Axis(title=None, labelAngle=-20)),
            y=alt.Y("Convo Now Minutes:Q", title="Minutes"),
            tooltip=["Month", alt.Tooltip("Convo Now Minutes:Q", format=",.1f")],
        )
        .properties(height=220, title="Convo Now Usage by Month (VRS=0 contacts)")
    )
    st.altair_chart(bar, use_container_width=True)

# ── Contact table ─────────────────────────────────────────────────────────────
st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin:1.25rem 0 0.5rem;'>Contact Detail</div>", unsafe_allow_html=True)

display_df = df[[
    "Name", "Email", "Convo Now Numbers", "VRS Numbers",
    "Convo Now Min", "Convo Now Cost", "Active Months", "Latest Month", "Latest Month Min"
]].rename(columns={
    "Convo Now Min":    "CN Min (Total)",
    "Convo Now Cost":   "CN Cost ($)",
    "Latest Month Min": "Latest Month Min",
})

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "CN Min (Total)":   st.column_config.NumberColumn(format="%.1f"),
        "CN Cost ($)":      st.column_config.NumberColumn(format="$%.2f"),
        "Latest Month Min": st.column_config.NumberColumn(format="%.1f"),
    }
)

# ── CSV download ──────────────────────────────────────────────────────────────
csv = df.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV",
    csv,
    file_name=f"vrs_zero_convo_active_{range_label.replace(' ', '_').replace('–','_')}.csv",
    mime="text/csv",
)
