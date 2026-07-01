import streamlit as st
import requests
import pandas as pd
import altair as alt
import os
import time
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
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap');
            html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
            .stApp { background-color: #F2F2EE; }
            .login-hero {
                background-color: #2DB84B;
                border-radius: 0 0 32px 32px;
                padding: 3rem 2rem 3.5rem;
                text-align: center;
                margin-bottom: 0;
            }
            .login-logo {
                font-size: 1.6rem; font-weight: 900; color: #fff;
                letter-spacing: -1px; margin-bottom: 1.5rem;
            }
            .login-hero h2 {
                color: #fff; font-size: 2rem; font-weight: 900;
                letter-spacing: -0.5px; margin-bottom: 0.5rem;
            }
            .login-hero p { color: rgba(255,255,255,0.85); font-size: 0.97rem; margin: 0; }
            .login-card {
                background: #fff; border-radius: 24px; padding: 2rem 1.75rem;
                margin-top: -1.5rem; box-shadow: 0 2px 16px rgba(0,0,0,0.06);
            }
            .stTextInput > div > div > input {
                border-radius: 999px !important;
                border: 1.5px solid #D8D8D2 !important;
                padding: 0.6rem 1.1rem !important;
                font-size: 0.93rem !important;
            }
            .stTextInput > div > div > input:focus {
                border-color: #2DB84B !important;
                box-shadow: 0 0 0 3px rgba(45,184,75,0.15) !important;
            }
            div.stButton > button {
                background-color: #2DB84B; color: #fff;
                border-radius: 999px; border: none;
                padding: 0.6rem 2.2rem; font-weight: 700;
                font-size: 0.95rem; width: 100%;
                box-shadow: 0 2px 8px rgba(45,184,75,0.25);
            }
            div.stButton > button:hover { background-color: #25A340; color: #fff; }
        </style>
        <div class="login-hero">
            <div class="login-logo">convo</div>
            <h2>VRS / Convo Now Lookup</h2>
            <p>Please enter the password to continue</p>
        </div>
        <div class="login-card">
        """, unsafe_allow_html=True)
        entered_password = st.text_input("Password", type="password", placeholder="Enter password")
        if st.button("Login"):
            if entered_password == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

BASE_URL = "https://api.hubapi.com"
headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

def list_all(object_type_id, properties):
    """Use GET /crm/v3/objects list endpoint — no filter required, no 10k cap."""
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}"
    all_results = []
    after = None
    while True:
        params = {"limit": 100, "properties": ",".join(properties)}
        if after:
            params["after"] = after
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(1.0)
            resp = requests.get(url, headers=headers, params=params, timeout=30)
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
        time.sleep(0.26)
    return all_results


def fetch_all(object_type_id, properties, filter_groups=None):
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}/search"
    all_results = []
    after = None
    while True:
        payload = {"limit": 100, "properties": properties, "filterGroups": filter_groups or []}
        if after:
            payload["after"] = after
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429:
            time.sleep(1.0)
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
        time.sleep(0.26)
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
        return "background-color: #FEE2E2; color: #B91C1C; font-weight: 600"
    if val == "PROFIT":
        return "background-color: #DCFCE7; color: #15803D; font-weight: 600"
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

st.set_page_config(page_title="VRS / Convo Now Lookup", layout="wide", page_icon="📊")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* ── Background ── */
    .stApp {
        background-color: #F2F2EE;
    }

    /* ── Hero ── */
    .hero-wrap {
        background-color: #2DB84B;
        border-radius: 0 0 32px 32px;
        padding: 2.5rem 2rem 3rem;
        margin-bottom: 0;
        position: relative;
        overflow: hidden;
    }
    .hero-logo {
        font-size: 1.6rem;
        font-weight: 900;
        color: #FFFFFF;
        letter-spacing: -1px;
        margin-bottom: 2rem;
    }
    .hero-wrap h1 {
        color: #FFFFFF;
        font-size: 2.4rem;
        font-weight: 900;
        margin-bottom: 0.5rem;
        letter-spacing: -1px;
        line-height: 1.1;
    }
    .hero-wrap p {
        color: rgba(255,255,255,0.85);
        font-size: 1rem;
        font-weight: 400;
        margin: 0;
    }

    /* ── Content card ── */
    .content-card {
        background: #FFFFFF;
        border-radius: 28px 28px 16px 16px;
        padding: 2rem 2rem 1.5rem;
        margin-top: -1.5rem;
        box-shadow: 0 2px 16px rgba(0,0,0,0.06);
    }

    /* ── Search card ── */
    .search-card {
        background: #F7F7F3;
        border: 1px solid #E4E4DE;
        border-radius: 16px;
        padding: 1.4rem 1.5rem 1.2rem;
        margin-bottom: 1.5rem;
    }
    .search-card-title {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 1.4px;
        text-transform: uppercase;
        color: #888880;
        margin-bottom: 0.9rem;
    }

    /* ── Inputs ── */
    .stTextInput label {
        color: #444440 !important;
        font-size: 0.83rem !important;
        font-weight: 600 !important;
    }
    .stTextInput > div > div > input {
        background-color: #FFFFFF !important;
        border: 1.5px solid #D8D8D2 !important;
        border-radius: 999px !important;
        color: #1A1A1A !important;
        padding: 0.6rem 1.1rem !important;
        font-size: 0.93rem !important;
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .stTextInput > div > div > input:focus {
        border-color: #2DB84B !important;
        box-shadow: 0 0 0 3px rgba(45,184,75,0.15) !important;
    }
    .stTextInput > div > div > input::placeholder {
        color: #AAAAA4 !important;
    }

    /* ── Buttons ── */
    div.stButton > button {
        background-color: #2DB84B;
        color: #FFFFFF;
        border-radius: 999px;
        border: none;
        padding: 0.6rem 2.2rem;
        font-weight: 700;
        font-size: 0.95rem;
        letter-spacing: 0.2px;
        transition: background-color 0.2s, transform 0.1s;
        box-shadow: 0 2px 8px rgba(45,184,75,0.25);
    }
    div.stButton > button:hover {
        background-color: #25A340;
        color: #FFFFFF;
        transform: translateY(-1px);
    }
    div.stButton > button:active {
        transform: translateY(0);
        background-color: #1E8C35;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background-color: #F0F0EB;
        border-radius: 999px;
        padding: 4px 6px;
        gap: 4px;
        border: 1px solid #E0E0DA;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 999px;
        color: #666660;
        font-weight: 500;
        font-size: 0.87rem;
        padding: 0.4rem 1.1rem;
    }
    .stTabs [aria-selected="true"] {
        background-color: #2DB84B !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }
    .stTabs [data-baseweb="tab-border"] {
        display: none;
    }
    .stTabs [data-baseweb="tab-panel"] {
        padding-top: 1.25rem;
    }

    /* ── Dataframe ── */
    div[data-testid="stDataFrame"] {
        border-radius: 14px;
        overflow: hidden;
        border: 1px solid #E4E4DE;
        box-shadow: 0 2px 12px rgba(0,0,0,0.05);
    }

    /* ── Metrics ── */
    div[data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid #E4E4DE;
        border-radius: 16px;
        padding: 1.1rem 1.3rem;
        box-shadow: 0 1px 6px rgba(0,0,0,0.04);
    }
    div[data-testid="stMetric"] label {
        color: #888880 !important;
        font-size: 0.75rem !important;
        font-weight: 700 !important;
        text-transform: uppercase;
        letter-spacing: 0.9px;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #1A1A1A !important;
        font-size: 1.9rem !important;
        font-weight: 800 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
        font-size: 0.84rem !important;
        font-weight: 500 !important;
    }

    /* ── Typography ── */
    h2, h3 {
        color: #1A1A1A !important;
        font-weight: 800 !important;
        letter-spacing: -0.3px;
    }

    /* ── Divider ── */
    hr {
        border-color: #E4E4DE;
    }

    /* ── Spinner ── */
    .stSpinner > div {
        border-top-color: #2DB84B !important;
    }

    /* ── Alerts ── */
    .stAlert {
        border-radius: 12px;
    }
</style>

<div class="hero-wrap">
    <div class="hero-logo">convo</div>
    <h1>VRS / Convo Now<br>Minutes Lookup</h1>
    <p>Search by number or email to compare usage and ROI by month</p>
</div>

<div class="content-card">
""", unsafe_allow_html=True)

COLOR_MAP = {
    "VRS Minutes": "#2DB84B",
    "CFZ Minutes": "#1A4D2E",
    "Convo Now Minutes": "#A3D9A5",
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

    matched_emails = set(filtered["Email"].str.lower().str.strip())
    filtered_person_numbers = {
        k: v for k, v in person_numbers.items()
        if norm(person_email_display.get(k, k)) in matched_emails or k in matched_emails
    }
    render_charts(filtered_person_numbers, person_month_values, person_email_display)


st.markdown('<div class="search-card"><div class="search-card-title">Search</div>', unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
with col1:
    search_input = st.text_input("Number(s) or email(s)", placeholder="e.g. 5551234567, user@email.com")
with col2:
    first_name_input = st.text_input("First name", placeholder="e.g. Jane")
with col3:
    last_name_input = st.text_input("Last name", placeholder="e.g. Smith")
st.markdown('</div>', unsafe_allow_html=True)

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

st.markdown('</div>', unsafe_allow_html=True)  # close content-card

# ── Numbers Report ─────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top: 2.5rem;">
<div style="background:#2DB84B; border-radius:20px 20px 0 0; padding:1.6rem 2rem 2.5rem;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;
                color:rgba(255,255,255,0.7);margin-bottom:0.4rem;">Analytics</div>
    <div style="font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-0.5px;">Numbers Report</div>
    <div style="color:rgba(255,255,255,0.8);font-size:0.93rem;margin-top:0.3rem;">
        Live VRS numbers by usage type and created date
    </div>
</div>
<div style="background:#fff;border-radius:0 0 20px 20px;padding:1.75rem 2rem 2rem;
            box-shadow:0 2px 16px rgba(0,0,0,0.06);margin-bottom:2rem;">
""", unsafe_allow_html=True)

if st.button("Load Numbers Report", key="load_numbers_report"):
    with st.spinner("Fetching all number records (45k+, may take a moment)..."):
        all_number_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type", "number_created_at", "credit_type"]
        )

    if not all_number_records:
        st.info("No number records found.")
    else:
        # Filter client-side: service_type = VRS AND number_status = Live
        rows = []
        for r in all_number_records:
            p = r.get("properties", {})
            if norm(p.get("service_type") or "") != "vrs":
                continue
            if norm(p.get("number_status") or "") != "live":
                continue
            num = str(p.get("number") or "").strip()
            created_raw = p.get("number_created_at") or ""
            try:
                dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                created_full = dt.strftime("%m/%d/%Y")
                # ISO week start (Monday)
                week_start = (dt - pd.Timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
            except Exception:
                created_full = "-"
                week_start = "-"
            rows.append({
                "Number": num,
                "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                "Email": p.get("email") or "",
                "Service Type": p.get("service_type") or "-",
                "Number Status": p.get("number_status") or "-",
                "Usage Type": p.get("usage_type") or "-",
                "Credit Type": p.get("credit_type") or "-",
                "Number Created At": created_full,
                "_week": week_start,
            })

        if not rows:
            st.info("No Live VRS numbers found.")
        else:
            report_df = pd.DataFrame(rows)

            # Metric cards
            total = len(report_df)
            personal = (report_df["Usage Type"].str.lower() == "personal").sum()
            org = (report_df["Usage Type"].str.lower() == "organization").sum()
            m1, m2, m3 = st.columns(3)
            m1.metric("Live VRS Numbers", total)
            m2.metric("Personal", personal)
            m3.metric("Organization", org)

            # Parse dates for grouping
            df_dated = report_df[report_df["Number Created At"] != "-"].copy()
            df_dated["_dt"] = pd.to_datetime(df_dated["Number Created At"], format="%m/%d/%Y", errors="coerce")
            df_dated = df_dated.dropna(subset=["_dt"])

            now = datetime.now()
            this_month_df = df_dated[
                (df_dated["_dt"].dt.year == now.year) &
                (df_dated["_dt"].dt.month == now.month)
            ]

            def bar_chart(data, x_col, x_title, sort_order):
                if data.empty:
                    st.info("No data for this period.")
                    return
                chart = alt.Chart(data).mark_bar(color="#2DB84B", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    x=alt.X(f"{x_col}:N", sort=sort_order, title=x_title, axis=alt.Axis(labelAngle=-45)),
                    y=alt.Y("Count:Q", title="Numbers Created"),
                    tooltip=[alt.Tooltip(f"{x_col}:N", title=x_title), "Count"]
                ).properties(height=320)
                st.altair_chart(chart, use_container_width=True)

            st.markdown("#### Numbers Created At")
            tab_daily, tab_weekly, tab_monthly = st.tabs(["Daily (This Month)", "Weekly (This Month)", "Monthly (All Time)"])

            with tab_daily:
                daily = (
                    this_month_df.assign(_day=this_month_df["_dt"].dt.strftime("%m/%d"))
                    .groupby("_day").size().reset_index(name="Count").sort_values("_day")
                )
                bar_chart(daily, "_day", "Day", daily["_day"].tolist())

            with tab_weekly:
                weekly = (
                    this_month_df.assign(_week=this_month_df["_week"])
                    .groupby("_week").size().reset_index(name="Count").sort_values("_week")
                )
                bar_chart(weekly, "_week", "Week Starting", weekly["_week"].tolist())

            with tab_monthly:
                monthly = (
                    df_dated.assign(_month=df_dated["_dt"].dt.strftime("%Y-%m"))
                    .groupby("_month").size().reset_index(name="Count").sort_values("_month")
                )
                bar_chart(monthly, "_month", "Month", monthly["_month"].tolist())

            # Detail table
            st.markdown("#### Detail Table")
            st.dataframe(report_df.drop(columns=["_week"]), use_container_width=True)

st.markdown("</div></div>", unsafe_allow_html=True)
