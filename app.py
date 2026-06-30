import streamlit as st
import requests
import pandas as pd
import altair as alt
import os
from datetime import datetime
from collections import defaultdict

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
if not HUBSPOT_TOKEN:
    st.error("HUBSPOT_TOKEN is not set. Add it to .streamlit/secrets.toml or set it as an environment variable.")
    st.stop()

APP_PASSWORD = st.secrets.get("APP_PASSWORD", os.environ.get("APP_PASSWORD", ""))
if APP_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("""
        <div style="background: linear-gradient(135deg, #0072CE 0%, #00A1E4 100%);
                    padding: 2.5rem 2rem; border-radius: 16px; margin-bottom: 2rem;
                    text-align: center; color: white;">
            <h1 style="color: white; font-weight: 700; margin-bottom: 0.5rem;">VRS / Convo Now Lookup</h1>
            <p style="color: #E6F2FF; margin: 0;">Please enter the password to continue</p>
        </div>
        """, unsafe_allow_html=True)
        entered_password = st.text_input("Enter password:", type="password")
        if st.button("Login"):
            if entered_password == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

BASE_URL = "https://api.hubapi.com"
headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

def fetch_all(object_type_id, properties, filter_groups=None):
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}/search"
    all_results = []
    after = None
    while True:
        payload = {"limit": 100, "properties": properties, "filterGroups": filter_groups or []}
        if after:
            payload["after"] = after
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            st.error(f"Error {resp.status_code}: {resp.text}")
            break
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return all_results

def month_key(month_str):
    try:
        return datetime.fromisoformat(month_str.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        return month_str

def month_sort_key(m):
    try:
        return datetime.strptime(m, "%m/%d/%Y")
    except Exception:
        return datetime.min

def norm(s):
    return (s or "").strip().lower()

def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

VRS_RATE_PER_MINUTE = 8.33
CONVO_NOW_RATE_PER_MINUTE = 2.60

def classify_roi(vrs_minutes, convo_minutes):
    vrs = to_float(vrs_minutes) or 0
    convo = to_float(convo_minutes) or 0
    diff = vrs - convo
    total = vrs + convo
    roi_pct = (diff / total * 100) if total > 0 else 0.0

    if diff > 0:
        return "PROFIT", diff, roi_pct
    elif diff < 0:
        return "LOSS", diff, roi_pct
    else:
        return "-", diff, roi_pct

def classify_cost_roi(vrs_minutes, convo_minutes):
    vrs = to_float(vrs_minutes) or 0
    convo = to_float(convo_minutes) or 0
    vrs_cost = vrs * VRS_RATE_PER_MINUTE
    convo_cost = convo * CONVO_NOW_RATE_PER_MINUTE
    diff = vrs_cost - convo_cost

    if diff > 0:
        roi = "PROFIT"
    elif diff < 0:
        roi = "LOSS"
    else:
        roi = "-"
    return vrs_cost, convo_cost, diff, roi

def highlight_roi(val):
    if val == "LOSS":
        return "background-color: #ffcccc"
    if val == "PROFIT":
        return "background-color: #ccffcc"
    return ""

def build_report(matched_numbers):
    """Given number-object records, merge by email and join monthly VRS/CFZ/Convo Now data.
    Returns (df, person_numbers, person_month_values, person_email_display)."""
    num_to_person = {}
    person_numbers = defaultdict(set)
    person_email_display = {}
    person_credit_types = defaultdict(set)
    num_to_status = {}
    person_usage_types = defaultdict(set)

    person_names = {}

    for r in matched_numbers:
        props = r.get("properties", {})
        num = str(props.get("number") or "").strip()
        email_raw = str(props.get("email") or "")
        credit_type = str(props.get("credit_type") or "")
        number_status = str(props.get("number_status") or "")
        usage_type = str(props.get("usage_type") or "")
        first_name = str(props.get("first_name") or "").strip()
        last_name = str(props.get("last_name") or "").strip()
        person_key = norm(email_raw) or f"num:{num}"

        num_to_person[num] = person_key
        person_numbers[person_key].add(num)
        if email_raw and person_key not in person_email_display:
            person_email_display[person_key] = email_raw
        if credit_type:
            person_credit_types[person_key].add(credit_type)
        if number_status:
            num_to_status[num] = number_status
        if usage_type:
            person_usage_types[person_key].add(usage_type)
        if (first_name or last_name) and person_key not in person_names:
            person_names[person_key] = f"{first_name} {last_name}".strip()

    distinct_numbers = sorted(num_to_person.keys())

    # HubSpot's IN filter allows at most 100 values, so batch large number lists
    monthly_records = []
    for i in range(0, len(distinct_numbers), 100):
        chunk = distinct_numbers[i:i + 100]
        monthly_records.extend(fetch_all(
            "2-46246179",
            ["number", "month_date", "usage_minutes", "cfz_minutes", "service_type"],
            filter_groups=[
                {"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]}
                ]}
            ]
        ))

    person_month_values = defaultdict(lambda: defaultdict(lambda: {"vrs": [], "cfz": [], "convo": []}))

    for r in monthly_records:
        props = r.get("properties", {})
        num = str(props.get("number") or "").strip()
        person_key = num_to_person.get(num)
        if person_key is None:
            continue

        mkey = month_key(props.get("month_date") or "")
        usage = to_float(props.get("usage_minutes"))
        cfz = to_float(props.get("cfz_minutes"))
        service = norm(props.get("service_type"))

        if service == "vrs":
            if usage is not None:
                person_month_values[person_key][mkey]["vrs"].append(usage)
            if cfz is not None:
                person_month_values[person_key][mkey]["cfz"].append(cfz)
        elif service == "convo now" and usage is not None:
            person_month_values[person_key][mkey]["convo"].append(usage)

    rows = []
    for person_key in sorted(person_numbers.keys()):
        name_display = person_names.get(person_key, "")
        email_display = person_email_display.get(person_key, "")
        credit_display = ", ".join(sorted(person_credit_types.get(person_key, [])))
        sorted_nums = sorted(person_numbers[person_key])
        status_display = ", ".join(f"{n}: {num_to_status.get(n, '-')}" for n in sorted_nums)
        usage_type_display = ", ".join(sorted(person_usage_types.get(person_key, [])))
        numbers_display = ", ".join(sorted_nums)

        months = person_month_values.get(person_key)
        if not months:
            rows.append({"Name": name_display, "Email": email_display, "Numbers": numbers_display, "Credit Type": credit_display,
                         "Number Status": status_display, "Usage Type": usage_type_display,
                         "Month": "-", "VRS Minutes": "-", "CFZ Minutes": "-",
                         "Convo Now Minutes": "-", "VRS - Convo Now": "-", "ROI %": "-", "ROI": "-",
                         "VRS Cost ($)": "-", "Convo Now Cost ($)": "-", "Cost Diff ($)": "-", "Cost ROI": "-"})
            continue

        for mkey in sorted(months.keys(), key=month_sort_key):
            vrs_list = months[mkey]["vrs"]
            cfz_list = months[mkey]["cfz"]
            convo_list = months[mkey]["convo"]
            vrs_merged = sum(vrs_list) if vrs_list else None
            cfz_merged = sum(cfz_list) if cfz_list else None
            convo_merged = sum(convo_list) if convo_list else None
            roi, diff, roi_pct = classify_roi(vrs_merged, convo_merged)
            vrs_cost, convo_cost, cost_diff, cost_roi = classify_cost_roi(vrs_merged, convo_merged)

            rows.append({"Name": name_display, "Email": email_display, "Numbers": numbers_display, "Credit Type": credit_display,
                         "Number Status": status_display, "Usage Type": usage_type_display,
                         "Month": mkey, "VRS Minutes": vrs_merged, "CFZ Minutes": cfz_merged,
                         "Convo Now Minutes": convo_merged, "VRS - Convo Now": round(diff, 1),
                         "ROI %": round(roi_pct, 1), "ROI": roi,
                         "VRS Cost ($)": round(vrs_cost, 2), "Convo Now Cost ($)": round(convo_cost, 2),
                         "Cost Diff ($)": round(cost_diff, 2), "Cost ROI": cost_roi})

    df = pd.DataFrame(rows)
    return df, person_numbers, person_month_values, person_email_display

st.set_page_config(page_title="VRS / Convo Now Lookup", layout="wide")

st.markdown("""
<style>
    .stApp {
        background-color: #FFFFFF;
    }
    .hero-banner {
        background: linear-gradient(135deg, #0072CE 0%, #00A1E4 100%);
        padding: 2.5rem 2rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        text-align: center;
        color: white;
    }
    .hero-banner h1 {
        color: white;
        font-weight: 700;
        margin-bottom: 0.5rem;
    }
    .hero-banner p {
        color: #E6F2FF;
        font-size: 1.05rem;
        margin: 0;
    }
    div.stButton > button {
        background-color: #0072CE;
        color: white;
        border-radius: 999px;
        border: none;
        padding: 0.5rem 1.5rem;
        font-weight: 600;
    }
    div.stButton > button:hover {
        background-color: #005BA1;
        color: white;
    }
    .stTextInput > div > div > input {
        border-radius: 999px;
        border: 1px solid #CFE3F7;
        padding: 0.6rem 1rem;
    }
    div[data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 2px 10px rgba(0, 114, 206, 0.08);
    }
    h2, h3 {
        color: #0A2540;
    }
</style>
<div class="hero-banner">
    <h1>VRS / Convo Now Minutes Lookup</h1>
    <p>Search a number or email to see usage minutes and ROI by month</p>
</div>
""", unsafe_allow_html=True)

COLOR_MAP = {
    "VRS Minutes": "green",
    "CFZ Minutes": "blue",
    "Convo Now Minutes": "grey",
}

def render_table_and_summary(df):
    styler = df.style
    if hasattr(styler, "map"):
        styler = styler.map(highlight_roi, subset=["ROI", "Cost ROI"])
    else:
        styler = styler.applymap(highlight_roi, subset=["ROI", "Cost ROI"])

    st.dataframe(styler, use_container_width=True)

    total_months = len(df[df["Month"] != "-"])
    loss_count = (df["ROI"] == "LOSS").sum()
    profit_count = (df["ROI"] == "PROFIT").sum()

    profit_pct = (profit_count / total_months * 100) if total_months > 0 else 0.0
    loss_pct = (loss_count / total_months * 100) if total_months > 0 else 0.0

    st.write(
        f"**Minutes-based Summary:** {profit_count} PROFIT month(s) ({profit_pct:.1f}%), "
        f"{loss_count} LOSS month(s) ({loss_pct:.1f}%), out of {total_months} total month(s)"
    )

    cost_loss_count = (df["Cost ROI"] == "LOSS").sum()
    cost_profit_count = (df["Cost ROI"] == "PROFIT").sum()
    cost_profit_pct = (cost_profit_count / total_months * 100) if total_months > 0 else 0.0
    cost_loss_pct = (cost_loss_count / total_months * 100) if total_months > 0 else 0.0

    total_vrs_cost = pd.to_numeric(df["VRS Cost ($)"], errors="coerce").sum()
    total_convo_cost = pd.to_numeric(df["Convo Now Cost ($)"], errors="coerce").sum()

    st.write(
        f"**Cost-based Summary (VRS @ ${VRS_RATE_PER_MINUTE}/min, Convo Now @ ${CONVO_NOW_RATE_PER_MINUTE}/min):** "
        f"{cost_profit_count} PROFIT month(s) ({cost_profit_pct:.1f}%), "
        f"{cost_loss_count} LOSS month(s) ({cost_loss_pct:.1f}%) — "
        f"Total VRS Cost: ${total_vrs_cost:,.2f}, Total Convo Now Cost: ${total_convo_cost:,.2f}"
    )

def render_profit_loss_summary(df):
    month_rows = df[df["Month"] != "-"]

    if month_rows.empty:
        st.info("No monthly data available for this search.")
        return

    minutes_diff = pd.to_numeric(month_rows["VRS - Convo Now"], errors="coerce")
    cost_diff = pd.to_numeric(month_rows["Cost Diff ($)"], errors="coerce")

    profit_months = month_rows[month_rows["ROI"] == "PROFIT"]
    loss_months = month_rows[month_rows["ROI"] == "LOSS"]
    cost_profit_months = month_rows[month_rows["Cost ROI"] == "PROFIT"]
    cost_loss_months = month_rows[month_rows["Cost ROI"] == "LOSS"]

    st.markdown("#### Minutes-based")
    c1, c2, c3 = st.columns(3)
    c1.metric("PROFIT months", len(profit_months), f"+{minutes_diff[minutes_diff > 0].sum():.1f} min")
    c2.metric("LOSS months", len(loss_months), f"{minutes_diff[minutes_diff < 0].sum():.1f} min")
    c3.metric("Net minutes (VRS - Convo Now)", f"{minutes_diff.sum():.1f}")

    st.markdown("#### Cost-based")
    d1, d2, d3 = st.columns(3)
    d1.metric("PROFIT months", len(cost_profit_months), f"+${cost_diff[cost_diff > 0].sum():,.2f}")
    d2.metric("LOSS months", len(cost_loss_months), f"-${abs(cost_diff[cost_diff < 0].sum()):,.2f}")
    d3.metric("Net cost (VRS - Convo Now)", f"${cost_diff.sum():,.2f}")

    st.markdown("#### All LOSS months")
    if loss_months.empty:
        st.write("No LOSS months found.")
    else:
        st.dataframe(loss_months, use_container_width=True)

def render_charts(person_numbers, person_month_values, person_email_display):
    st.subheader("VRS vs Convo Now — Month-over-Month Comparison")

    for person_key in sorted(person_numbers.keys()):
        months = person_month_values.get(person_key)
        if not months:
            continue

        sorted_months = sorted(months.keys(), key=month_sort_key)
        chart_df = pd.DataFrame({
            "Month": sorted_months,
            "VRS Minutes": [sum(months[m]["vrs"]) if months[m]["vrs"] else 0 for m in sorted_months],
            "CFZ Minutes": [sum(months[m]["cfz"]) if months[m]["cfz"] else 0 for m in sorted_months],
            "Convo Now Minutes": [sum(months[m]["convo"]) if months[m]["convo"] else 0 for m in sorted_months],
        })

        long_df = chart_df.melt(id_vars="Month", var_name="Metric", value_name="Minutes")

        chart = alt.Chart(long_df).mark_line(point=True).encode(
            x=alt.X("Month", sort=sorted_months),
            y="Minutes",
            color=alt.Color("Metric", scale=alt.Scale(
                domain=list(COLOR_MAP.keys()),
                range=list(COLOR_MAP.values())
            ))
        )

        label = person_email_display.get(person_key, person_key)
        st.markdown(f"**{label}** (numbers: {', '.join(sorted(person_numbers[person_key]))})")
        st.altair_chart(chart, use_container_width=True)

def render_vrs_zero_convo_active(df, person_numbers, person_month_values, person_email_display):
    st.subheader("Months where VRS ≤ 0 min and Convo Now > 1 min")

    month_rows = df[df["Month"] != "-"].copy()
    vrs_num = pd.to_numeric(month_rows["VRS Minutes"], errors="coerce").fillna(0)
    convo_num = pd.to_numeric(month_rows["Convo Now Minutes"], errors="coerce").fillna(0)
    mask = (vrs_num <= 0) & (convo_num > 1)
    filtered = month_rows[mask]

    if filtered.empty:
        st.info("No months found where VRS ≤ 0 and Convo Now > 1.")
        return

    st.write(f"**{len(filtered)} month row(s)** across **{filtered['Email'].nunique()} person(s)**")

    render_table_and_summary(filtered)

    # Charts only for persons present in the filtered results
    matched_emails = set(filtered["Email"].str.lower().str.strip())
    filtered_person_numbers = {
        k: v for k, v in person_numbers.items()
        if norm(person_email_display.get(k, k)) in matched_emails or k in matched_emails
    }
    render_charts(filtered_person_numbers, person_month_values, person_email_display)


col1, col2, col3 = st.columns(3)
with col1:
    search_input = st.text_input("Number(s) or email(s) — comma-separated:")
with col2:
    first_name_input = st.text_input("First name:")
with col3:
    last_name_input = st.text_input("Last name:")

if st.button("Search") and (search_input.strip() or first_name_input.strip() or last_name_input.strip()):
    search_terms = [t.strip() for t in search_input.split(",") if t.strip()]
    first_name_input = first_name_input.strip()
    last_name_input = last_name_input.strip()

    filter_groups = []
    for i in range(0, len(search_terms), 100):
        chunk = search_terms[i:i + 100]
        filter_groups.append({"filters": [
            {"propertyName": "number", "operator": "IN", "values": chunk},
            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
        ]})
        filter_groups.append({"filters": [
            {"propertyName": "email", "operator": "IN", "values": chunk},
            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
        ]})
    if first_name_input:
        filter_groups.append({"filters": [
            {"propertyName": "first_name", "operator": "CONTAINS_TOKEN", "value": first_name_input},
            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
        ]})
    if last_name_input:
        filter_groups.append({"filters": [
            {"propertyName": "last_name", "operator": "CONTAINS_TOKEN", "value": last_name_input},
            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
        ]})

    with st.spinner("Searching number object..."):
        matched_numbers = fetch_all(
            "2-40974683",
            ["number", "email", "credit_type", "first_name", "last_name", "number_status", "usage_type"],
            filter_groups=filter_groups
        )

    if not matched_numbers:
        st.warning("No number object record found for that search.")
    else:
        with st.spinner("Fetching monthly value data..."):
            df, person_numbers, person_month_values, person_email_display = build_report(matched_numbers)

        st.write(f"Merged into {len(person_numbers)} person(s) by email")

        report_tab, summary_tab, vrs_zero_tab = st.tabs(["Detailed Report", "Profit/Loss Summary", "VRS ≤0 & Convo Now >1"])
        with report_tab:
            render_table_and_summary(df)
            render_charts(person_numbers, person_month_values, person_email_display)
        with summary_tab:
            render_profit_loss_summary(df)
        with vrs_zero_tab:
            render_vrs_zero_convo_active(df, person_numbers, person_month_values, person_email_display)
