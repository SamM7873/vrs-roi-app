import streamlit as st
import requests
import pandas as pd
import os
import time
from datetime import datetime
from collections import defaultdict

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
APP_PASSWORD = st.secrets.get("APP_PASSWORD", os.environ.get("APP_PASSWORD", ""))

BASE_URL = "https://api.hubapi.com"
headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def require_auth():
    """Show login gate if APP_PASSWORD is set. Call at the top of every page."""
    if not HUBSPOT_TOKEN:
        st.error("HUBSPOT_TOKEN is not set.")
        st.stop()
    if not APP_PASSWORD:
        return
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
                text-align: center; margin-bottom: 0;
            }
            .login-logo { font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-1px;margin-bottom:1.5rem; }
            .login-hero h2 { color:#fff;font-size:2rem;font-weight:900;letter-spacing:-0.5px;margin-bottom:0.5rem; }
            .login-hero p { color:rgba(255,255,255,0.85);font-size:0.97rem;margin:0; }
            .login-card { background:#fff;border-radius:24px;padding:2rem 1.75rem;margin-top:-1.5rem;box-shadow:0 2px 16px rgba(0,0,0,0.06); }
            .stTextInput > div > div > input { border-radius:999px !important;border:1.5px solid #D8D8D2 !important;padding:0.6rem 1.1rem !important;font-size:0.93rem !important; }
            .stTextInput > div > div > input:focus { border-color:#2DB84B !important;box-shadow:0 0 0 3px rgba(45,184,75,0.15) !important; }
            div.stButton > button { background-color:#2DB84B;color:#fff;border-radius:999px;border:none;padding:0.6rem 2.2rem;font-weight:700;font-size:0.95rem;width:100%;box-shadow:0 2px 8px rgba(45,184,75,0.25); }
            div.stButton > button:hover { background-color:#25A340;color:#fff; }
        </style>
        <div class="login-hero">
            <div class="login-logo">convo</div>
            <h2>VRS / Convo Now Lookup</h2>
            <p>Please enter the password to continue</p>
        </div>
        <div class="login-card">
        """, unsafe_allow_html=True)
        entered = st.text_input("Password", type="password", placeholder="Enter password")
        if st.button("Login"):
            if entered == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()


def list_all(object_type_id, properties, progress_label="Loading..."):
    """Fetch all records with a green progress bar."""
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}"
    all_results = []
    after = None
    # Use HubSpot total count for accurate progress
    total_estimate = None

    status_text = st.empty()
    bar = st.progress(0, text="")

    # Apply green styling to the progress bar
    st.markdown("""
    <style>
    div[data-testid="stProgress"] > div > div > div {
        background-color: #2DB84B !important;
    }
    </style>
    """, unsafe_allow_html=True)

    page_num = 0
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
        page_num += 1

        # Estimate total from first page paging info
        if total_estimate is None:
            # HubSpot doesn't give total in list endpoint; estimate from pages seen
            total_estimate = max(data.get("total", 0), len(all_results))

        after = data.get("paging", {}).get("next", {}).get("after")
        fetched = len(all_results)

        if after and total_estimate and total_estimate > 0:
            pct = min(int(fetched / total_estimate * 100), 95)
        elif not after:
            pct = 100
        else:
            # Unknown total — pulse based on pages (cap at 90%)
            pct = min(page_num * 5, 90)

        bar.progress(pct, text=f"{progress_label} — {fetched:,} records ({pct}%)")

        if not after:
            break
        time.sleep(0.26)

    bar.progress(100, text=f"✅ Done — {len(all_results):,} records loaded")
    status_text.empty()
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


COMMON_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: #F2F2EE; }
    section[data-testid="stSidebar"] { background-color: #fff; border-right: 1px solid #E5E7EB; }
    div.stButton > button {
        background-color: #2DB84B; color: #fff;
        border-radius: 999px; border: none;
        padding: 0.55rem 1.4rem; font-weight: 700;
        font-size: 0.93rem;
        box-shadow: 0 2px 8px rgba(45,184,75,0.2);
    }
    div.stButton > button:hover { background-color: #25A340; }
    .stTabs [data-baseweb="tab-list"] { background-color:#F0F0EB;border-radius:999px;padding:4px 6px;border:1px solid #E0E0DA; }
    .stTabs [data-baseweb="tab"] { border-radius:999px;color:#666660;font-weight:600;font-size:0.88rem;padding:0.4rem 1.1rem; }
    .stTabs [aria-selected="true"] { background-color:#2DB84B !important;color:#FFFFFF !important;font-weight:700 !important; }
    .stTabs [data-baseweb="tab-border"] { display:none; }
    .stTabs [data-baseweb="tab-panel"] { padding-top:1.25rem; }
</style>
"""


def report_header(title, subtitle, section="Analytics"):
    st.markdown(f"""
<div style="margin-top:2rem;">
<div style="background:#2DB84B;border-radius:20px 20px 0 0;padding:1.5rem 1.75rem 1rem;">
    <div style="font-size:0.72rem;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;
                color:rgba(255,255,255,0.7);margin-bottom:0.4rem;">{section}</div>
    <div style="font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-0.5px;">{title}</div>
    <div style="color:rgba(255,255,255,0.8);font-size:0.93rem;margin-top:0.3rem;">{subtitle}</div>
</div>
<div style="background:#fff;border-radius:0 0 20px 20px;padding:1.75rem 2rem 2rem;
            box-shadow:0 2px 16px rgba(0,0,0,0.06);margin-bottom:2rem;">
""", unsafe_allow_html=True)


def report_header_close():
    st.markdown("</div></div>", unsafe_allow_html=True)
