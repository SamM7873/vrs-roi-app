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

    for r in matched_numbers:
        props = r.get("properties", {})
        num = str(props.get("number") or "").strip()
        email_raw = str(props.get("email") or "")
        credit_type = str(props.get("credit_type") or "")
        person_key = norm(email_raw) or f"num:{num}"

        num_to_person[num] = person_key
        person_numbers[person_key].add(num)
        if email_raw and person_key not in person_email_display:
            person_email_display[person_key] = email_raw
        if credit_type:
            person_credit_types[person_key].add(credit_type)

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
        email_display = person_email_display.get(person_key, "")
        credit_display = ", ".join(sorted(person_credit_types.get(person_key, [])))
        numbers_display = ", ".join(sorted(person_numbers[person_key]))

        months = person_month_values.get(person_key)
        if not months:
            rows.append({"Email": email_display, "Numbers": numbers_display, "Credit Type": credit_display,
                         "Month": "-", "VRS Minutes": "-", "CFZ Minutes": "-",
                         "Convo Now Minutes": "-", "VRS - Convo Now": "-", "ROI %": "-", "ROI": "-"})
            continue

        for mkey in sorted(months.keys(), key=month_sort_key):
            vrs_list = months[mkey]["vrs"]
            cfz_list = months[mkey]["cfz"]
            convo_list = months[mkey]["convo"]
            vrs_merged = sum(vrs_list) if vrs_list else None
            cfz_merged = sum(cfz_list) if cfz_list else None
            convo_merged = sum(convo_list) if convo_list else None
            roi, diff, roi_pct = classify_roi(vrs_merged, convo_merged)

            rows.append({"Email": email_display, "Numbers": numbers_display, "Credit Type": credit_display,
                         "Month": mkey, "VRS Minutes": vrs_merged, "CFZ Minutes": cfz_merged,
                         "Convo Now Minutes": convo_merged, "VRS - Convo Now": round(diff, 1),
                         "ROI %": round(roi_pct, 1), "ROI": roi})

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
        styler = styler.map(highlight_roi, subset=["ROI"])
    else:
        styler = styler.applymap(highlight_roi, subset=["ROI"])

    st.dataframe(styler, use_container_width=True)

    total_months = len(df[df["Month"] != "-"])
    loss_count = (df["ROI"] == "LOSS").sum()
    profit_count = (df["ROI"] == "PROFIT").sum()

    profit_pct = (profit_count / total_months * 100) if total_months > 0 else 0.0
    loss_pct = (loss_count / total_months * 100) if total_months > 0 else 0.0

    st.write(
        f"**Summary:** {profit_count} PROFIT month(s) ({profit_pct:.1f}%), "
        f"{loss_count} LOSS month(s) ({loss_pct:.1f}%), out of {total_months} total month(s)"
    )

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

tab_search, tab_all = st.tabs(["Search", "All Numbers (Export CSV)"])

with tab_search:
    search_input = st.text_input("Enter a number or email:")

    if st.button("Search") and search_input.strip():
        search_input = search_input.strip()

        with st.spinner("Searching number object..."):
            matched_numbers = fetch_all(
                "2-40974683",
                ["number", "email", "credit_type"],
                filter_groups=[
                    {"filters": [
                        {"propertyName": "number", "operator": "EQ", "value": search_input},
                        {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
                    ]},
                    {"filters": [
                        {"propertyName": "email", "operator": "EQ", "value": search_input},
                        {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
                    ]}
                ]
            )

        if not matched_numbers:
            st.warning(f"No number object record found for '{search_input}'.")
        else:
            with st.spinner("Fetching monthly value data..."):
                df, person_numbers, person_month_values, person_email_display = build_report(matched_numbers)

            st.write(f"Merged into {len(person_numbers)} person(s) by email")
            render_table_and_summary(df)
            render_charts(person_numbers, person_month_values, person_email_display)

with tab_all:
    st.write("Loads every number object record (excluding Guest credit type) and lets you export the full report as CSV.")

    if st.button("Load All Numbers"):
        with st.spinner("Fetching all number object records..."):
            all_numbers = fetch_all(
                "2-40974683",
                ["number", "email", "credit_type"],
                filter_groups=[
                    {"filters": [{"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}]}
                ]
            )

        if not all_numbers:
            st.warning("No number object records found.")
        else:
            with st.spinner(f"Fetching monthly value data for {len(all_numbers)} number(s)..."):
                df, person_numbers, person_month_values, person_email_display = build_report(all_numbers)

            st.write(f"Loaded {len(all_numbers)} number record(s), merged into {len(person_numbers)} person(s) by email")
            render_table_and_summary(df)

            csv_data = df.to_csv(index=False)
            st.download_button(
                label="Download as CSV",
                data=csv_data,
                file_name="vrs_convo_now_report.csv",
                mime="text/csv"
            )
