import streamlit as st
import requests
import pandas as pd
import altair as alt
import plotly.express as px
import plotly.graph_objects as go
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
                # Capture IP, location, device at login time
                try:
                    hdrs = st.context.headers
                    ip = hdrs.get("X-Forwarded-For", hdrs.get("X-Real-Ip", "Unknown"))
                    if ip and "," in ip:
                        ip = ip.split(",")[0].strip()
                    ua = hdrs.get("User-Agent", "Unknown")
                except Exception:
                    ip, ua = "Unknown", "Unknown"
                # Geo lookup
                try:
                    geo = requests.get(f"http://ip-api.com/json/{ip}?fields=city,regionName,country,status", timeout=5).json()
                    if geo.get("status") == "success":
                        city = geo.get("city", "")
                        region = geo.get("regionName", "")
                        country = geo.get("country", "")
                        location = ", ".join(filter(None, [city, region, country])) or "Unknown"
                    else:
                        location = f"Lookup failed ({ip})"
                except Exception as e:
                    location = f"Error: {e}"
                # Parse device from user agent
                ua_lower = ua.lower()
                if "mobile" in ua_lower or "android" in ua_lower:
                    device = "Mobile"
                elif "tablet" in ua_lower or "ipad" in ua_lower:
                    device = "Tablet"
                else:
                    device = "Desktop"
                # Store in session
                login_time = datetime.now().strftime("%b %d, %Y at %I:%M %p")
                st.session_state.login_info = {
                    "ip": ip, "location": location, "device": device,
                    "ua": ua, "time": login_time
                }
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
    num_month_values = defaultdict(lambda: defaultdict(float))  # num -> month -> vrs minutes
    num_month_detail = {}  # num -> month -> {"cfz": value}

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
                num_month_values[num][mkey] = num_month_values[num].get(mkey, 0.0) + usage
            else:
                # Record the month even with 0 usage so retention sees it
                if mkey not in num_month_values[num]:
                    num_month_values[num][mkey] = 0.0
            if cfz is not None:
                person_month_values[person_key][mkey]["cfz"].append(cfz)
            if num not in num_month_detail:
                num_month_detail[num] = {}
            num_month_detail[num][mkey] = {"cfz": cfz}
        elif service == "convo now" and usage is not None:
            if norm(num_to_status.get(num) or "") != "suspended":
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
    return df, person_numbers, person_month_values, person_email_display, num_month_values, num_to_person, num_to_status, num_month_detail

st.set_page_config(page_title="VRS / Convo Now Lookup", layout="wide", page_icon="📊")

# Show login session info in sidebar
if "login_info" in st.session_state:
    li = st.session_state.login_info
    st.sidebar.markdown(f"""
<div style="background:#F9FAFB;border-radius:12px;padding:1rem;margin-bottom:1rem;border:1px solid #E5E7EB;">
  <div style="font-size:0.7rem;font-weight:700;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:0.6rem;">🔐 Session Info</div>
  <div style="font-size:0.8rem;color:#374151;margin-bottom:0.3rem;">🕐 {li['time']}</div>
  <div style="font-size:0.8rem;color:#374151;margin-bottom:0.3rem;">🌐 {li['ip']}</div>
  <div style="font-size:0.8rem;color:#374151;margin-bottom:0.3rem;">📍 {li['location']}</div>
  <div style="font-size:0.8rem;color:#374151;margin-bottom:0.3rem;">💻 {li['device']}</div>
</div>
""", unsafe_allow_html=True)

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

    vrs_mins   = pd.to_numeric(month_rows["VRS Minutes"],       errors="coerce").fillna(0)
    convo_mins = pd.to_numeric(month_rows["Convo Now Minutes"], errors="coerce").fillna(0)
    vrs_cost   = pd.to_numeric(month_rows["VRS Cost ($)"],      errors="coerce").fillna(0)
    convo_cost = pd.to_numeric(month_rows["Convo Now Cost ($)"],errors="coerce").fillna(0)
    cost_diff  = pd.to_numeric(month_rows["Cost Diff ($)"],     errors="coerce")

    profit_months     = month_rows[month_rows["ROI"] == "PROFIT"]
    loss_months       = month_rows[month_rows["ROI"] == "LOSS"]
    cost_profit_months = month_rows[month_rows["Cost ROI"] == "PROFIT"]
    cost_loss_months   = month_rows[month_rows["Cost ROI"] == "LOSS"]

    # ── VRS ────────────────────────────────────────────────────────────────────
    st.markdown("#### VRS")
    v1, v2, v3 = st.columns(3)
    v1.metric("Total Minutes", f"{vrs_mins.sum():,.1f} min")
    v2.metric("Total Cost", f"${vrs_cost.sum():,.2f}")
    v3.metric("Rate", f"${VRS_RATE_PER_MINUTE}/min")

    # ── Convo Now ──────────────────────────────────────────────────────────────
    st.markdown("#### Convo Now")
    n1, n2, n3 = st.columns(3)
    n1.metric("Total Minutes", f"{convo_mins.sum():,.1f} min")
    n2.metric("Total Cost", f"${convo_cost.sum():,.2f}")
    n3.metric("Rate", f"${CONVO_NOW_RATE_PER_MINUTE}/min")

    # ── Cost Summary ───────────────────────────────────────────────────────────
    st.markdown("#### Cost Summary")
    s1, s2, s3 = st.columns(3)
    net = cost_diff.sum()
    s1.metric("PROFIT months", len(cost_profit_months), f"+${cost_diff[cost_diff > 0].sum():,.2f}")
    s2.metric("LOSS months",   len(cost_loss_months),   f"-${abs(cost_diff[cost_diff < 0].sum()):,.2f}")
    s3.metric("Net Cost (VRS − Convo Now)", f"${net:,.2f}", delta_color="inverse")

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


st.markdown("""
<div class="search-card">
  <div class="search-card-title">🔍 Search</div>
  <div style="font-size:0.85rem;color:#6B7280;margin-bottom:1rem;">
    Search by phone number, email address, or name
  </div>
""", unsafe_allow_html=True)
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    search_input = st.text_input("Number(s) or email(s)", placeholder="e.g. 5551234567, user@email.com", label_visibility="collapsed")
    st.caption("📞 Number or ✉️ Email")
with col2:
    first_name_input = st.text_input("First name", placeholder="First name", label_visibility="collapsed")
    st.caption("👤 First name")
with col3:
    last_name_input = st.text_input("Last name", placeholder="Last name", label_visibility="collapsed")
    st.caption("👤 Last name")
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
            [
                "number", "email", "credit_type", "first_name", "last_name",
                "number_status", "usage_type", "service_type", "number_created_at",
                "phone", "street1", "street2", "city", "state", "zip_code",
                "emerg_street1", "emerg_street2", "emerg_city", "emerg_state", "emerg_zip_code",
                "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call",
                "ursa_last_outbound_call", "ursa_last_inbound_call",
                "ursa_ios_minutes", "ursa_android_minutes", "ursa_web_minutes", "cfz_minutes",
            ],
            filter_groups=filter_groups
        )

    if not matched_numbers:
        st.warning("No number object record found for that search.")
    else:
        with st.spinner("Fetching monthly value data..."):
            df, person_numbers, person_month_values, person_email_display, num_month_values, num_to_person, num_to_status, num_month_detail = build_report(matched_numbers)

        st.write(f"Merged into {len(person_numbers)} person(s) by email")

        contact_tab, tickets_tab, retention_tab, summary_tab, vrs_zero_tab, report_tab = st.tabs([
            "Contact Card", "Tickets", "Retention", "Profit/Loss Summary", "VRS ≤0 & Convo Now >1", "Detailed Report"
        ])
        with report_tab:
            render_table_and_summary(df)
            render_charts(person_numbers, person_month_values, person_email_display)
        with summary_tab:
            render_profit_loss_summary(df)
        with vrs_zero_tab:
            render_vrs_zero_convo_active(df, person_numbers, person_month_values, person_email_display)
        with retention_tab:
            SEG_META = {
                "A": {"label": "GROWTH", "emoji": "📈", "color": "#2DB84B", "desc": "Usage exceeded baseline"},
                "B": {"label": "STABLE", "emoji": "✅", "color": "#3B82F6", "desc": "Near baseline, healthy"},
                "C": {"label": "DECLINING", "emoji": "⚠️", "color": "#F59E0B", "desc": "Notable drop in usage"},
                "D": {"label": "AT RISK", "emoji": "🚨", "color": "#EF4444", "desc": "Severe drop, churn risk"},
            }

            NEXT_STEPS = {
                "A": ["No action required at this time.", "Continue monitoring monthly usage."],
                "B": ["Monitor usage trend over next 1–2 months.", "No immediate outreach needed."],
                "C": ["Reach out with personalized support.", "Identify pain points.", "Offer training or resources."],
                "D": ["URGENT: Schedule customer success call.", "Investigate reason for drop.", "Prepare retention strategy."],
            }

            INTERPRETATION = {
                "A": lambda rc: f"Last month usage was {rc['last_month_perf']:.1f}% of the historical baseline. Consumer is growing — usage exceeded baseline.",
                "B": lambda rc: f"Last month usage was {rc['last_month_perf']:.1f}% of the historical baseline. Consumer is stable — usage is near baseline.",
                "C": lambda rc: f"Last month usage was {rc['last_month_perf']:.1f}% of the historical baseline. Consumer is declining — requires outreach and support.",
                "D": lambda rc: f"Last month usage was {rc['last_month_perf']:.1f}% of the historical baseline. Consumer is at risk — immediate action needed to prevent churn.",
            }

            st.markdown("#### VRS Consumer Retention Analysis")
            analysis_date = datetime.now().strftime("%b %d, %Y at %I:%M %p")

            seg_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
            retention_cards = []

            # Build a lookup of num -> props from matched_numbers for VRS numbers only
            num_props_lookup = {}
            for r in matched_numbers:
                props = r.get("properties", {})
                if norm(props.get("service_type") or "") != "vrs":
                    continue
                num = str(props.get("number") or "").strip()
                if num:
                    num_props_lookup[num] = props

            # Analyze per VRS number — iterate num_month_values so no number is missed
            today_month_key = datetime.now().strftime("%m/01/%Y")
            for num, vrs_months in num_month_values.items():
                # Only analyze numbers that belong to this search result
                if num not in num_props_lookup:
                    continue

                props = num_props_lookup[num]
                if not vrs_months:
                    continue

                current_month = today_month_key
                current_usage = vrs_months.get(current_month, 0.0)

                # Baseline = all historical months excluding the current month
                history_pairs = sorted(
                    [(k, v) for k, v in vrs_months.items() if k != current_month],
                    key=lambda x: month_sort_key(x[0])
                )
                history = [v for _, v in history_pairs]

                if not history:
                    continue

                baseline = sum(history) / len(history)
                if baseline <= 0:
                    continue

                # Last month = most recent historical month
                last_month_key, last_month_usage = history_pairs[-1] if history_pairs else (None, None)
                last_month_perf = (last_month_usage / baseline * 100) if (last_month_usage is not None and baseline > 0) else None
                perf = (current_usage / baseline * 100) if current_usage > 0 else 0.0

                # Segment based on last month performance (current month is in-progress)
                seg_perf = last_month_perf if last_month_perf is not None else 0.0
                if seg_perf >= 100:  seg = "A"  # Growth
                elif seg_perf >= 75: seg = "B"  # Stable
                elif seg_perf >= 40: seg = "C"  # Declining
                else:                seg = "D"  # At Risk

                seg_counts[seg] += 1
                meta = SEG_META[seg]

                name = f"{props.get('first_name') or ''} {props.get('last_name') or ''}".strip() or "—"
                email = props.get("email") or "—"
                # Lifetime URSA/CFZ from number object
                ursa_ios = to_float(props.get("ursa_ios_minutes"))
                ursa_android = to_float(props.get("ursa_android_minutes"))
                ursa_web = to_float(props.get("ursa_web_minutes"))
                ursa_total = sum(x for x in [ursa_ios, ursa_android, ursa_web] if x is not None) or 0.0
                cfz_min = to_float(props.get("cfz_minutes")) or 0.0

                # Last month CFZ from monthly records
                lm_detail = num_month_detail.get(num, {}).get(last_month_key, {}) if last_month_key else {}
                lm_cfz = lm_detail.get("cfz")

                retention_cards.append({
                    "name": name, "email": email, "number": num,
                    "seg": seg, "meta": meta,
                    "vrs_months": vrs_months,
                    "baseline": baseline, "current_usage": current_usage,
                    "current_month": current_month, "perf": perf,
                    "history_months": len(history),
                    "last_month_key": last_month_key, "last_month_usage": last_month_usage, "last_month_perf": last_month_perf,
                    "ursa_ios": ursa_ios, "ursa_android": ursa_android, "ursa_web": ursa_web,
                    "ursa_total": ursa_total, "cfz_min": cfz_min,
                    "lm_cfz": lm_cfz,
                })

            # Summary metrics
            total_analyzed = sum(seg_counts.values())
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Analyzed", total_analyzed)
            c2.metric(f"📈 A — GROWTH",   seg_counts["A"])
            c3.metric(f"✅ B — STABLE",   seg_counts["B"])
            c4.metric(f"⚠️ C — DECLINING", seg_counts["C"])
            c5.metric(f"🚨 D — AT RISK",  seg_counts["D"])

            if not retention_cards:
                st.info("No retention data available — insufficient historical usage.")
            else:
                seg_order = {"D": 0, "C": 1, "B": 2, "A": 3}
                retention_cards.sort(key=lambda x: seg_order[x["seg"]])

                for rc in retention_cards:
                    meta = rc["meta"]
                    color = meta["color"]
                    interp = INTERPRETATION[rc["seg"]](rc)
                    steps_html = "".join(f"<li style='margin-bottom:3px;'>{s}</li>" for s in NEXT_STEPS[rc["seg"]])
                    st.markdown(f"""
<div style="background:#fff;border-radius:14px;box-shadow:0 1px 6px rgba(0,0,0,0.07);
            padding:1.25rem 1.5rem;margin-bottom:1rem;border-left:4px solid {color};">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.5rem;margin-bottom:1rem;">
    <div>
      <span style="font-size:1rem;font-weight:800;color:#111827;">{rc['name']}</span>
      <span style="font-size:0.82rem;color:#6B7280;margin-left:0.5rem;">{rc['email']} · #{rc['number']}</span>
    </div>
    <span style="background:{color};color:#fff;font-size:0.82rem;font-weight:800;padding:4px 14px;border-radius:99px;">
      {meta['emoji']} Segment {rc['seg']} — {meta['label']}
    </span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.75rem;margin-bottom:1rem;">
    <div style="background:#F9FAFB;border-radius:10px;padding:0.75rem 1rem;">
      <div style="font-size:0.7rem;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Historical Baseline</div>
      <div style="font-size:1.3rem;font-weight:800;color:#111827;">{rc['baseline']:.1f} <span style="font-size:0.75rem;font-weight:500;">min</span></div>
      <div style="font-size:0.72rem;color:#9CA3AF;">avg of {rc['history_months']} month(s)</div>
    </div>
    <div style="background:#F9FAFB;border-radius:10px;padding:0.75rem 1rem;">
      <div style="font-size:0.7rem;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Last Month</div>
      <div style="font-size:1.3rem;font-weight:800;color:#111827;">{f"{rc['last_month_usage']:.1f}" if rc['last_month_usage'] is not None else "—"} <span style="font-size:0.75rem;font-weight:500;">min</span></div>
      <div style="font-size:0.72rem;color:#9CA3AF;">{rc['last_month_key'] or "—"}</div>
    </div>
    <div style="background:#F9FAFB;border-radius:10px;padding:0.75rem 1rem;">
      <div style="font-size:0.7rem;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Last Month Perf</div>
      <div style="font-size:1.3rem;font-weight:800;color:#111827;">{f"{rc['last_month_perf']:.1f}%" if rc['last_month_perf'] is not None else "—"}</div>
      <div style="font-size:0.72rem;color:#9CA3AF;">vs baseline</div>
    </div>
    <div style="background:#F9FAFB;border-radius:10px;padding:0.75rem 1rem;">
      <div style="font-size:0.7rem;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Current Month</div>
      <div style="font-size:1.3rem;font-weight:800;color:#111827;">{rc['current_usage']:.1f} <span style="font-size:0.75rem;font-weight:500;">min</span></div>
      <div style="font-size:0.72rem;color:#9CA3AF;">{rc['current_month']}</div>
    </div>
    <div style="background:{color}11;border-radius:10px;padding:0.75rem 1rem;">
      <div style="font-size:0.7rem;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Performance</div>
      <div style="font-size:1.3rem;font-weight:800;color:{color};">{rc['perf']:.1f}%</div>
      <div style="font-size:0.72rem;color:#9CA3AF;">vs baseline</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;">
    <div style="background:#F9FAFB;border-radius:8px;padding:0.75rem 1rem;">
      <div style="font-size:0.72rem;font-weight:700;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:0.4rem;">📝 Interpretation</div>
      <div style="font-size:0.83rem;color:#374151;">{interp}</div>
    </div>
    <div style="background:#F9FAFB;border-radius:8px;padding:0.75rem 1rem;">
      <div style="font-size:0.72rem;font-weight:700;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:0.4rem;">⚡ Next Steps</div>
      <ul style="margin:0;padding-left:1.1rem;font-size:0.83rem;color:#374151;">{steps_html}</ul>
    </div>
  </div>
  <div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.75rem;">Analysis Date: {analysis_date}</div>
</div>""", unsafe_allow_html=True)

                    # Monthly usage history chart with per-month segment coloring
                    vrs_months = rc["vrs_months"]
                    baseline_val = rc["baseline"]
                    all_months_sorted = sorted(vrs_months.keys(), key=month_sort_key)

                    def month_seg(m, usage):
                        if m == rc["current_month"]:
                            return "Current Month"
                        p = (usage / baseline_val * 100) if baseline_val > 0 else 0
                        if p >= 100: return "📈 Growth"
                        elif p >= 75: return "✅ Stable"
                        elif p >= 40: return "⚠️ Declining"
                        else: return "🚨 At Risk"

                    chart_rows = []
                    for m in all_months_sorted:
                        usage = vrs_months[m]
                        seg_label = month_seg(m, usage)
                        perf_val = (usage / baseline_val * 100) if baseline_val > 0 and m != rc["current_month"] else None
                        chart_rows.append({
                            "Month": m,
                            "VRS Minutes": usage,
                            "Segment": seg_label,
                            "Performance": f"{perf_val:.1f}%" if perf_val is not None else "—",
                        })

                    chart_data = pd.DataFrame(chart_rows)
                    seg_domain = ["📈 Growth", "✅ Stable", "⚠️ Declining", "🚨 At Risk", "Current Month"]
                    seg_colors = ["#2DB84B", "#3B82F6", "#F59E0B", "#EF4444", "#D1D5DB"]

                    baseline_line = pd.DataFrame({
                        "Month": all_months_sorted,
                        "Baseline": [baseline_val] * len(all_months_sorted),
                    })
                    bars = alt.Chart(chart_data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                        x=alt.X("Month:N", sort=all_months_sorted, axis=alt.Axis(labelAngle=-35, title=None)),
                        y=alt.Y("VRS Minutes:Q", title="VRS Minutes"),
                        color=alt.Color("Segment:N", scale=alt.Scale(domain=seg_domain, range=seg_colors),
                                        legend=alt.Legend(title=None, orient="top")),
                        tooltip=["Month", "VRS Minutes", "Segment", "Performance"],
                    )
                    baseline_rule = alt.Chart(baseline_line).mark_rule(
                        color="#6B7280", strokeDash=[6, 3], strokeWidth=1.5
                    ).encode(
                        y="Baseline:Q",
                        tooltip=[alt.Tooltip("Baseline:Q", title="Baseline avg", format=".1f")],
                    )
                    st.altair_chart((bars + baseline_rule).properties(height=220), use_container_width=True)

        with contact_tab:
            def fmt(v):
                return v if v else "—"

            def status_badge(status):
                s = norm(status)
                color = "#2DB84B" if s == "live" else "#EF4444" if s == "suspended" else "#F59E0B" if s in ("inactive", "cancelled") else "#6B7280"
                return f'<span style="background:{color};color:#fff;padding:2px 10px;border-radius:999px;font-size:0.75rem;font-weight:700;">{status or "—"}</span>'

            def ursa_badge(v):
                if v:
                    return f'<span style="background:#DCFCE7;color:#15803D;padding:2px 10px;border-radius:999px;font-size:0.75rem;font-weight:600;">✓ {v}</span>'
                return '<span style="background:#F3F4F6;color:#9CA3AF;padding:2px 10px;border-radius:999px;font-size:0.75rem;">Not yet</span>'

            def row(label, value):
                return f"""
                <div style="display:flex;justify-content:space-between;align-items:flex-start;
                            padding:0.55rem 0;border-bottom:1px solid #F3F4F6;">
                  <span style="color:#6B7280;font-size:0.82rem;font-weight:500;min-width:160px;">{label}</span>
                  <span style="color:#111827;font-size:0.85rem;font-weight:500;text-align:right;">{value}</span>
                </div>"""

            sorted_numbers = sorted(
                matched_numbers,
                key=lambda r: 0 if norm(r.get("properties", {}).get("service_type") or "") == "vrs" else 1
            )
            last_label = None
            for r in sorted_numbers:
                p = r.get("properties", {})
                svc = norm(p.get("service_type") or "")
                section_label = "VRS" if svc == "vrs" else "Convo Now"
                if section_label != last_label:
                    color = "#2DB84B" if svc == "vrs" else "#3B82F6"
                    mt = "0" if last_label is None else "1.5rem"
                    st.markdown(
                        f'<div style="display:inline-flex;align-items:center;gap:0.5rem;'
                        f'background:{color};color:#fff;'
                        f'font-size:0.8rem;font-weight:800;letter-spacing:1.5px;'
                        f'text-transform:uppercase;padding:0.35rem 1rem;'
                        f'border-radius:999px;margin-top:{mt};margin-bottom:0.75rem;">'
                        f'{section_label}</div>',
                        unsafe_allow_html=True
                    )
                    last_label = section_label
                name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—"
                addr_street = " ".join(a for a in [p.get("street1"), p.get("street2")] if a)
                addr_csz = ", ".join(a for a in [p.get("city"), p.get("state"), p.get("zip_code")] if a)
                address = "<br>".join(a for a in [addr_street, addr_csz] if a) or "—"

                emerg_street = " ".join(a for a in [p.get("emerg_street1"), p.get("emerg_street2")] if a)
                emerg_csz = ", ".join(a for a in [p.get("emerg_city"), p.get("emerg_state"), p.get("emerg_zip_code")] if a)
                emergency = "<br>".join(a for a in [emerg_street, emerg_csz] if a) or "—"
                initials = "".join(n[0].upper() for n in name.split() if n)[:2] if name != "—" else "?"

                # Build convo now monthly usage for this record's person key
                email_key = norm(p.get("email") or "") or f"num:{p.get('number') or ''}"
                convo_monthly = person_month_values.get(email_key, {})

                html_card = (
                    '<div style="background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,0.07);'
                    'padding:1.5rem;margin-bottom:1.25rem;border:1px solid #F0F0EA;">'
                    '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;'
                    'padding-bottom:1rem;border-bottom:2px solid #F0F0EA;">'
                    f'<div style="width:52px;height:52px;border-radius:50%;background:#2DB84B;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'font-size:1.2rem;font-weight:800;color:#fff;flex-shrink:0;">{initials}</div>'
                    f'<div><div style="font-size:1.15rem;font-weight:800;color:#111827;">{name}</div>'
                    f'<div style="font-size:0.85rem;color:#6B7280;">{fmt(p.get("email"))}</div></div>'
                    f'<div style="margin-left:auto;">{status_badge(p.get("number_status"))}</div>'
                    '</div>'
                )
                is_vrs = norm(p.get("service_type") or "") == "vrs"
                is_suspended = norm(p.get("number_status") or "") == "suspended"
                show_monthly = not is_vrs and not is_suspended
                grid_cols = "1fr 1fr 1fr" if (is_vrs or show_monthly) else "1fr 1fr"
                col1 = (
                    '<div>'
                    '<div style="font-size:0.7rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#2DB84B;margin-bottom:0.6rem;">Contact</div>'
                    + row("📞 Phone", fmt(p.get("phone")))
                    + row("✉️ Email", fmt(p.get("email")))
                    + (row("🏠 Address", address) + row("🚨 Emergency", emergency) if is_vrs else "")
                    + '</div>'
                )
                col2 = (
                    '<div>'
                    '<div style="font-size:0.7rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#2DB84B;margin-bottom:0.6rem;">Number Details</div>'
                    + row("📱 Number", fmt(p.get("number")))
                    + row("📅 Created At", fmt(p.get("number_created_at")))
                    + row("🔧 Service Type", fmt(p.get("service_type")))
                    + row("👤 Usage Type", fmt(p.get("usage_type")))
                    + '</div>'
                )
                if is_vrs:
                    col3 = (
                        '<div>'
                        '<div style="font-size:0.7rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#2DB84B;margin-bottom:0.6rem;">URSA Activity</div>'
                        '<div style="padding:0.55rem 0;border-bottom:1px solid #F3F4F6;">'
                        '<div style="color:#6B7280;font-size:0.78rem;margin-bottom:3px;">First Login</div>'
                        + ursa_badge(p.get("ursa_first_login"))
                        + '</div>'
                        '<div style="padding:0.55rem 0;border-bottom:1px solid #F3F4F6;">'
                        '<div style="color:#6B7280;font-size:0.78rem;margin-bottom:3px;">1st Outbound Call</div>'
                        + ursa_badge(p.get("ursa_first_outbound_call"))
                        + '</div>'
                        '<div style="padding:0.55rem 0;border-bottom:1px solid #F3F4F6;">'
                        '<div style="color:#6B7280;font-size:0.78rem;margin-bottom:3px;">2nd Outbound Call</div>'
                        + ursa_badge(p.get("ursa_second_outbound_call"))
                        + '</div>'
                        '<div style="padding:0.55rem 0;border-bottom:1px solid #F3F4F6;">'
                        '<div style="color:#6B7280;font-size:0.78rem;margin-bottom:3px;">Last Outbound Call</div>'
                        + ursa_badge(p.get("ursa_last_outbound_call"))
                        + '</div>'
                        '<div style="padding:0.55rem 0;">'
                        '<div style="color:#6B7280;font-size:0.78rem;margin-bottom:3px;">Last Inbound Call</div>'
                        + ursa_badge(p.get("ursa_last_inbound_call"))
                        + '</div>'
                        '</div>'
                    )
                elif show_monthly:
                    convo_rows = "".join(
                        row(f"📆 {mk}", f"{sum(vals['convo']):.1f} min")
                        for mk, vals in sorted(convo_monthly.items(), reverse=True)
                        if vals.get("convo") and sum(vals["convo"]) > 0
                    ) or row("No data", "—")
                    col3 = (
                        '<div>'
                        '<div style="font-size:0.7rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#2DB84B;margin-bottom:0.6rem;">Monthly Usage (Convo Now)</div>'
                        + convo_rows
                        + '</div>'
                    )
                else:
                    col3 = ""

                html_card += (
                    f'<div style="display:grid;grid-template-columns:{grid_cols};gap:1.25rem;">'
                    + col1 + col2 + col3
                    + '</div></div>'
                )
                st.markdown(html_card, unsafe_allow_html=True)

        with tickets_tab:
            # Collect unique emails from matched numbers
            emails = list({
                (r.get("properties", {}).get("email") or "").strip().lower()
                for r in matched_numbers
                if (r.get("properties", {}).get("email") or "").strip()
            })

            if not emails:
                st.info("No email addresses found to look up tickets.")
            else:
                with st.spinner("Looking up contacts and tickets..."):
                    # Step 1: find HubSpot contact IDs by email
                    contact_ids = []
                    contact_errors = []
                    for email in emails:
                        resp = requests.post(
                            f"{BASE_URL}/crm/v3/objects/contacts/search",
                            headers=headers,
                            json={"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                                  "properties": ["email"], "limit": 10},
                            timeout=30,
                        )
                        if resp.status_code == 200:
                            for c in resp.json().get("results", []):
                                contact_ids.append(c["id"])
                        else:
                            contact_errors.append(f"Contact search error {resp.status_code}: {resp.text[:200]}")
                        time.sleep(0.26)

                    # Collect phone numbers from matched numbers
                    phones = list({
                        (r.get("properties", {}).get("phone") or "").strip()
                        for r in matched_numbers
                        if (r.get("properties", {}).get("phone") or "").strip()
                    })
                    vrs_numbers = list({
                        (r.get("properties", {}).get("number") or "").strip()
                        for r in matched_numbers
                        if (r.get("properties", {}).get("number") or "").strip()
                    })

                    # Fetch pipeline stage labels
                    stage_labels = {}
                    try:
                        pr = requests.get(f"{BASE_URL}/crm/v3/pipelines/tickets",
                                          headers=headers, timeout=15)
                        if pr.status_code == 200:
                            for pipeline in pr.json().get("results", []):
                                for stage in pipeline.get("stages", []):
                                    stage_labels[stage["id"]] = stage.get("label", stage["id"])
                    except Exception:
                        pass

                    ticket_rows = []
                    ticket_errors = []
                    seen_ids = set()

                    # Fetch owner name map
                    owner_names = {}
                    try:
                        or_ = requests.get(f"{BASE_URL}/crm/v3/owners",
                                           headers=headers, timeout=15)
                        if or_.status_code == 200:
                            for o in or_.json().get("results", []):
                                fn = o.get("firstName") or ""
                                ln = o.get("lastName") or ""
                                owner_names[str(o["id"])] = f"{fn} {ln}".strip() or o.get("email", str(o["id"]))
                    except Exception:
                        pass

                    TICKET_PROPS = ["subject", "hs_pipeline_stage", "hs_ticket_priority",
                                    "createdate", "hs_lastmodifieddate", "closed_date", "content",
                                    "hs_ticket_category", "hs_ticket_subcategory",
                                    "hubspot_owner_id", "email", "phone"]

                    def _collect_tickets(filter_groups):
                        after = None
                        while True:
                            body = {"filterGroups": filter_groups, "properties": TICKET_PROPS, "limit": 100}
                            if after:
                                body["after"] = after
                            r = requests.post(f"{BASE_URL}/crm/v3/objects/tickets/search",
                                              headers=headers, json=body, timeout=30)
                            if r.status_code == 200:
                                data = r.json()
                                for t in data.get("results", []):
                                    if t["id"] not in seen_ids:
                                        seen_ids.add(t["id"])
                                        tp = t.get("properties", {})
                                        raw_stage = tp.get("hs_pipeline_stage") or ""
                                        ticket_rows.append({
                                            "ID": t["id"],
                                            "Subject": tp.get("subject") or "—",
                                            "Status": stage_labels.get(raw_stage, raw_stage) or "—",
                                            "Priority": tp.get("hs_ticket_priority") or "—",
                                            "Category": tp.get("hs_ticket_category") or "—",
                                            "Subcategory": tp.get("hs_ticket_subcategory") or "—",
                                            "Owner": owner_names.get(tp.get("hubspot_owner_id") or "", "—"),
                                            "Created": (tp.get("createdate") or "")[:10],
                                            "Closed": (tp.get("closed_date") or "")[:10],
                                            "Description": tp.get("content") or "—",
                                        })
                                after = data.get("paging", {}).get("next", {}).get("after")
                                if not after:
                                    break
                            else:
                                ticket_errors.append(f"Ticket search error {r.status_code}: {r.text[:300]}")
                                break
                            time.sleep(0.26)

                    # Search by contact association
                    for cid in contact_ids:
                        _collect_tickets([{"filters": [{"propertyName": "associations.contact", "operator": "EQ", "value": cid}]}])
                        time.sleep(0.26)

                    # Search by email property on ticket
                    for email in emails:
                        _collect_tickets([{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}])
                        time.sleep(0.26)

                    # Search by phone property on ticket
                    for phone in phones:
                        _collect_tickets([{"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone}]}])
                        time.sleep(0.26)

                    # Search by VRS number in subject
                    for num in vrs_numbers:
                        _collect_tickets([{"filters": [{"propertyName": "subject", "operator": "CONTAINS_TOKEN", "value": num}]}])
                        time.sleep(0.26)

                for e in contact_errors + ticket_errors:
                    st.error(e)

                if not ticket_rows:
                    st.markdown("""
<div style="text-align:center;padding:3rem 1rem;">
  <div style="font-size:2.5rem;margin-bottom:0.5rem;">🎫</div>
  <div style="font-size:1.1rem;font-weight:600;color:#374151;margin-bottom:0.25rem;">No tickets found</div>
  <div style="font-size:0.85rem;color:#9CA3AF;">No support tickets are linked to this contact.</div>
</div>""", unsafe_allow_html=True)
                else:
                    tickets_df = pd.DataFrame(ticket_rows)

                    PRIORITY_COLOR = {"high": "#EF4444", "medium": "#F59E0B", "low": "#3B82F6", "—": "#9CA3AF"}
                    CLOSED_KEYWORDS = {"closed", "resolved", "done", "completed"}

                    def _status_color(label):
                        l = (label or "").lower()
                        if any(k in l for k in CLOSED_KEYWORDS):
                            return "#9CA3AF"
                        if "wait" in l or "hold" in l or "pending" in l:
                            return "#F59E0B"
                        if "new" in l or "open" in l:
                            return "#3B82F6"
                        return "#6B7280"

                    def pri_badge(p):
                        c = PRIORITY_COLOR.get((p or "").lower(), "#9CA3AF")
                        label = (p or "—").upper()
                        return f'<span style="background:{c};color:#fff;font-size:0.7rem;font-weight:700;padding:2px 8px;border-radius:99px;letter-spacing:0.5px;">{label}</span>'

                    def status_badge(s):
                        c = _status_color(s)
                        label = (s or "—").upper()
                        return f'<span style="background:{c}22;color:{c};border:1px solid {c}55;font-size:0.7rem;font-weight:700;padding:2px 9px;border-radius:99px;">{label}</span>'

                    total = len(tickets_df)
                    high  = (tickets_df["Priority"].str.lower() == "high").sum()
                    open_ = (~tickets_df["Status"].str.lower().apply(lambda x: any(k in x for k in CLOSED_KEYWORDS))).sum()

                    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:1.5rem;">
  <div style="background:linear-gradient(135deg,#2DB84B,#22a040);border-radius:14px;padding:1.25rem 1.5rem;color:#fff;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1px;opacity:0.8;text-transform:uppercase;margin-bottom:0.3rem;">Total Tickets</div>
    <div style="font-size:2rem;font-weight:900;">{total}</div>
  </div>
  <div style="background:linear-gradient(135deg,#EF4444,#dc2626);border-radius:14px;padding:1.25rem 1.5rem;color:#fff;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1px;opacity:0.8;text-transform:uppercase;margin-bottom:0.3rem;">High Priority</div>
    <div style="font-size:2rem;font-weight:900;">{high}</div>
  </div>
  <div style="background:linear-gradient(135deg,#3B82F6,#2563eb);border-radius:14px;padding:1.25rem 1.5rem;color:#fff;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1px;opacity:0.8;text-transform:uppercase;margin-bottom:0.3rem;">Open</div>
    <div style="font-size:2rem;font-weight:900;">{open_}</div>
  </div>
</div>""", unsafe_allow_html=True)

                    cards_html = '<div style="display:flex;flex-direction:column;gap:0.85rem;">'
                    for row in ticket_rows:
                        desc = row["Description"]
                        desc_snippet = desc
                        cards_html += f"""
<div style="background:#fff;border:1px solid #E5E7EB;border-radius:14px;padding:1.25rem 1.5rem;
            box-shadow:0 1px 4px rgba(0,0,0,0.05);transition:box-shadow 0.2s;">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
    <div style="flex:1;min-width:0;">
      <div style="font-size:0.72rem;font-weight:600;color:#9CA3AF;letter-spacing:0.5px;margin-bottom:0.2rem;">#{row['ID']}</div>
      <div style="font-size:1rem;font-weight:700;color:#111827;margin-bottom:0.5rem;word-break:break-word;">{row['Subject']}</div>
      <div style="font-size:0.83rem;color:#6B7280;line-height:1.5;">{desc_snippet}</div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.4rem;white-space:nowrap;">
      {pri_badge(row['Priority'])}
      {status_badge(row['Status'])}
    </div>
  </div>
  <div style="display:flex;gap:1.5rem;margin-top:0.85rem;padding-top:0.75rem;border-top:1px solid #F3F4F6;flex-wrap:wrap;">
    <span style="font-size:0.78rem;color:#6B7280;">👤 <b>{row['Owner']}</b></span>
    <span style="font-size:0.78rem;color:#6B7280;">📂 <b>{row['Category']}</b></span>
    <span style="font-size:0.78rem;color:#6B7280;">🔖 <b>{row['Subcategory']}</b></span>
    <span style="font-size:0.78rem;color:#6B7280;">📅 Created: <b>{row['Created']}</b></span>
    <span style="font-size:0.78rem;color:#6B7280;">🔒 Closed: <b>{row['Closed'] or '—'}</b></span>
  </div>
</div>"""
                    cards_html += "</div>"
                    st.markdown(cards_html, unsafe_allow_html=True)

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

            latest_dt = df_dated["_dt"].max()
            this_month_df = df_dated[
                (df_dated["_dt"].dt.year == latest_dt.year) &
                (df_dated["_dt"].dt.month == latest_dt.month)
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
            tab_daily, tab_weekly, tab_monthly = st.tabs(["Daily", "Weekly", "Monthly"])

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

# ── URSA Login Report ──────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top: 2.5rem;">
<div style="background:#2DB84B;border-radius:20px 20px 0 0;padding:1.5rem 1.75rem 1rem;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;
                color:rgba(255,255,255,0.7);margin-bottom:0.4rem;">Analytics</div>
    <div style="font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-0.5px;">URSA Login Report</div>
    <div style="color:rgba(255,255,255,0.8);font-size:0.93rem;margin-top:0.3rem;">
        First login, first outbound, and second outbound timestamps
    </div>
</div>
""", unsafe_allow_html=True)

if st.button("Load URSA Report", key="load_ursa_report"):
    with st.spinner("Fetching all number records for URSA report..."):
        ursa_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type",
             "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"]
        )

    rows = []
    for r in ursa_records:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        rows.append({
            "Number": p.get("number") or "",
            "Email": p.get("email") or "",
            "First Name": p.get("first_name") or "",
            "Last Name": p.get("last_name") or "",
            "URSA First Login": p.get("ursa_first_login") or "",
            "URSA First Outbound Call": p.get("ursa_first_outbound_call") or "",
            "URSA Second Outbound Call": p.get("ursa_second_outbound_call") or "",
        })

    if not rows:
        st.warning("No live VRS numbers found.")
    else:
        ursa_df = pd.DataFrame(rows)

        has_login = ursa_df["URSA First Login"] != ""
        count_logged_in = has_login.sum()
        count_not_logged_in = (~has_login).sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Live VRS", len(ursa_df))
        col2.metric("Has First Login", int(count_logged_in))
        col3.metric("No First Login Yet", int(count_not_logged_in))

        def ursa_bar(col_name, label):
            has = (ursa_df[col_name] != "").sum()
            missing = (ursa_df[col_name] == "").sum()
            chart_data = pd.DataFrame({
                "Status": ["Has Value", "No Value"],
                "Count": [int(has), int(missing)],
                "Color": ["#2DB84B", "#EF4444"],
            })
            chart = alt.Chart(chart_data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Status:N", title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("Count:Q", title="Count"),
                color=alt.Color("Color:N", scale=None, legend=None),
                tooltip=["Status", "Count"],
            ).properties(height=260)
            st.markdown(f"##### {label}")
            st.altair_chart(chart, use_container_width=True)

        ursa_bar("URSA First Login", "First Login")

        st.markdown("#### Who Has NOT Logged In Yet")
        not_logged_in_df = ursa_df[~has_login][["Number", "Email", "First Name", "Last Name"]].reset_index(drop=True)
        st.dataframe(not_logged_in_df, use_container_width=True)

        st.markdown("#### Full URSA Detail")
        st.dataframe(ursa_df, use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)

# ── Geographic Report ──────────────────────────────────────────────────────────
US_STATE_ABBR = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA",
    "Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD",
    "Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS",
    "Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH",
    "New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC",
    "North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA",
    "Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD","Tennessee":"TN",
    "Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA",
    "West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY","District of Columbia":"DC",
}

def to_abbr(s):
    s = s.strip()
    if len(s) == 2:
        return s.upper()
    return US_STATE_ABBR.get(s.title(), s.upper()[:2])

st.markdown("""
<div style="margin-top: 2.5rem;">
<div style="background:#2DB84B;border-radius:20px 20px 0 0;padding:1.5rem 1.75rem 1rem;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;
                color:rgba(255,255,255,0.7);margin-bottom:0.4rem;">Analytics</div>
    <div style="font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-0.5px;">Geographic Report</div>
    <div style="color:rgba(255,255,255,0.8);font-size:0.93rem;margin-top:0.3rem;">
        Live VRS numbers by city and state
    </div>
</div>
""", unsafe_allow_html=True)

if st.button("Load Geographic Report", key="load_geo_report"):
    with st.spinner("Fetching all number records for geographic report..."):
        geo_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type", "city", "state"]
        )

    rows = []
    for r in geo_records:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        rows.append({
            "Number": p.get("number") or "",
            "Email": p.get("email") or "",
            "First Name": p.get("first_name") or "",
            "Last Name": p.get("last_name") or "",
            "City": (p.get("city") or "").strip(),
            "State": (p.get("state") or "").strip(),
        })

    if not rows:
        st.warning("No live VRS numbers found.")
    else:
        geo_df = pd.DataFrame(rows)
        geo_df = geo_df[geo_df["State"] != ""]
        geo_df["State Code"] = geo_df["State"].apply(to_abbr)

        state_counts = geo_df.groupby(["State", "State Code"]).size().reset_index(name="Count")
        city_counts = (
            geo_df[geo_df["City"] != ""]
            .groupby(["City", "State", "State Code"]).size()
            .reset_index(name="Count")
            .sort_values("Count", ascending=False)
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Live VRS", len(geo_df))
        col2.metric("States Covered", state_counts["State Code"].nunique())
        col3.metric("Cities Covered", geo_df[geo_df["City"] != ""]["City"].nunique())

        st.markdown("#### Numbers by State")
        fig_state = px.choropleth(
            state_counts,
            locations="State Code",
            locationmode="USA-states",
            color="Count",
            scope="usa",
            hover_name="State",
            hover_data={"State Code": False, "Count": True},
            color_continuous_scale=[[0, "#D1FAE5"], [0.5, "#2DB84B"], [1, "#1A4D2E"]],
            labels={"Count": "Live VRS Numbers"},
        )
        fig_state.update_layout(
            geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="#F2F2EE", landcolor="#F2F2EE"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            coloraxis_colorbar=dict(title="Count", thickness=14),
        )
        st.plotly_chart(fig_state, use_container_width=True)

        # Top 20 cities bar chart
        st.markdown("#### Top 20 Cities")
        top_cities = city_counts.head(20)
        fig_city = go.Figure(go.Bar(
            x=top_cities["City"] + ", " + top_cities["State Code"],
            y=top_cities["Count"],
            marker_color="#2DB84B",
            marker_line_width=0,
            text=top_cities["Count"],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Count: %{y}<extra></extra>",
        ))
        fig_city.update_layout(
            xaxis=dict(tickangle=-45, title=None),
            yaxis=dict(title="Live VRS Numbers"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=120),
            height=420,
        )
        st.plotly_chart(fig_city, use_container_width=True)

        st.markdown("#### State Breakdown")
        st.dataframe(
            state_counts.sort_values("Count", ascending=False).reset_index(drop=True),
            use_container_width=True,
        )

        st.markdown("#### City Detail")
        st.dataframe(city_counts.reset_index(drop=True), use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)
