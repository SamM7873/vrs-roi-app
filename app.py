import streamlit as st
import requests
import os
import time
from datetime import datetime, timezone, timedelta

st.set_page_config(page_title="VRS Lookup", layout="wide", page_icon="🔍")

from utils import get_secret
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
BASE_URL = "https://api.hubapi.com"
SYNC_TTL = 300  # refresh every 5 minutes

def _hs_count(filters):
    try:
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={"filterGroups": [{"filters": filters}], "properties": ["number_status"], "limit": 1},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return None

def render_sync_widget():
    now = time.time()
    cached = st.session_state.get("_sync_widget")
    if not cached or (now - cached["ts"]) > SYNC_TTL:
        live      = _hs_count([{"propertyName": "number_status", "operator": "EQ", "value": "Live"},
                                {"propertyName": "service_type",  "operator": "EQ", "value": "VRS"}])
        suspended = _hs_count([{"propertyName": "number_status", "operator": "EQ", "value": "Suspended"},
                                {"propertyName": "service_type",  "operator": "EQ", "value": "VRS"}])
        healthy = live is not None
        st.session_state["_sync_widget"] = {
            "ts": now,
            "healthy": healthy,
            "live": live if live is not None else "—",
            "suspended": suspended if suspended is not None else "—",
        }
    d = st.session_state["_sync_widget"]
    dot  = "#2DB84B" if d["healthy"] else "#EF4444"
    label = "Healthy" if d["healthy"] else "Error"

    # Retention from last lookup (if available)
    seg = st.session_state.get("_retention_summary", {})

    with st.sidebar:
        st.markdown("""<div style="border-top:1px solid #E6E9F0;margin:0.5rem 0;"></div>""",
                    unsafe_allow_html=True)
        age_mins = int((time.time() - d["ts"]) / 60)
        # Format in Central Time (UTC-5 CDT / UTC-6 CST) — auto pick offset by month
        _now = datetime.fromtimestamp(d["ts"], tz=timezone.utc)
        _ct_offset = -5 if 3 <= _now.month <= 11 else -6  # CDT Mar–Nov, CST otherwise
        _ct_label  = "CDT" if _ct_offset == -5 else "CST"
        last_sync  = _now.astimezone(timezone(timedelta(hours=_ct_offset))).strftime(f"%b %d at %I:%M %p {_ct_label}")

        st.markdown(f"""
<div style="padding:0.6rem 0.25rem 0;">
  <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.25rem;">
    <div style="width:8px;height:8px;border-radius:50%;background:{dot};
                box-shadow:0 0 6px {dot};flex-shrink:0;"></div>
    <span style="font-size:0.78rem;font-weight:700;color:#1A2234;">HubSpot {label}</span>
  </div>
  <div style="font-size:0.68rem;color:#667085;margin-bottom:0.75rem;">
    Last sync: {last_sync}
  </div>
  <div style="font-size:0.68rem;color:#8792A2;">
    Data refreshes every 5 min · {age_mins}m ago
  </div>
</div>
""", unsafe_allow_html=True)

        with st.expander("View Details"):
            st.caption(f"Data refreshes every 5 min · {age_mins}m ago")
            st.caption(f"Last sync: {last_sync}")

overview_page = st.Page("pages/23_Overview.py",           title="Overview",             icon="📊", default=True)
lookup_page   = st.Page("pages/0_Lookup.py",              title="VRS Lookup",           icon="🔍")
numbers_page  = st.Page("pages/1_Numbers_Report.py",      title="Numbers Report",        icon="📊")
ursa_page     = st.Page("pages/2_URSA_Login_Report.py",   title="URSA Login Report",     icon="👤")
geo_page      = st.Page("pages/3_Geographic_Report.py",   title="Geographic Report",     icon="🗺️")
bulk_page     = st.Page("pages/4_Bulk_Search.py",         title="Bulk Search",           icon="🔎")
churn_page    = st.Page("pages/5_Churn_Risk.py",          title="Churn Risk Report",     icon="🚨")
funnel_page   = st.Page("pages/6_Registration_Funnel.py", title="Registration Funnel",   icon="📋")
portin_page   = st.Page("pages/7_Port_In_Report.py",      title="Port-In Report",        icon="📲")
journey_page  = st.Page("pages/8_Signup_Journey.py",      title="Sign-Up Journey",        icon="🗺️")
numfunnel_page = st.Page("pages/9_Number_Funnel.py",      title="Number Funnel",          icon="🔢")
winback_page    = st.Page("pages/10_Port_Out_Winback.py",          title="Port-Out Winback",         icon="🔄")
cs_tickets_page  = st.Page("pages/11_Consumer_Success_Tickets.py",  title="Consumer Success Tickets",  icon="🎫")
vrs_zero_page    = st.Page("pages/12_VRS_Zero_ConvoNow_Active.py",   title="VRS Zero / Convo Now Active", icon="🔄")
age_demo_page    = st.Page("pages/13_Age_Demographics.py",           title="Age Demographics",             icon="👥")
pendo_page       = st.Page("pages/14_Pendo_Report.py",               title="Pendo Report",                 icon="📱")
dq_page          = st.Page("pages/15_Data_Quality.py",               title="Data Quality",                 icon="🧹")
yoy_page         = st.Page("pages/16_YoY_Comparison.py",             title="Year-over-Year",               icon="📆")
explorer_page    = st.Page("pages/17_Data_Explorer.py",               title="Data Explorer",                icon="📊")
audit_page       = st.Page("pages/18_Audit_Log.py",                   title="Audit Log",                    icon="🛡️")
survey_page      = st.Page("pages/19_Survey.py",                      title="Survey",                       icon="📝")
ticket_rpt_page  = st.Page("pages/20_Ticket_Report.py",               title="Ticket Report",                icon="🎫")
jira_rpt_page    = st.Page("pages/21_Jira_Report.py",                  title="Jira Ticket Report",           icon="🧩")
retention_page   = st.Page("pages/22_Retention_Report.py",             title="Retention Report",             icon="🔁")

_nav_groups = {
    "Home": [overview_page, lookup_page],
    "Numbers": [numbers_page, numfunnel_page, funnel_page, portin_page, winback_page, geo_page, yoy_page],
    "Customers": [ursa_page, journey_page, age_demo_page, churn_page, vrs_zero_page, retention_page],
    "Support": [cs_tickets_page, ticket_rpt_page, jira_rpt_page, survey_page],
    "Tools": [bulk_page, explorer_page, pendo_page, dq_page],
    "Admin": [audit_page],
}
# Hide the default sidebar menu; render our own Tabler-style horizontal top nav.
pg = st.navigation(_nav_groups, position="hidden")

st.markdown("""
<style>
  /* Tabler-style top navigation bar */
  .topnav-wrap { background:#FFFFFF; border:1px solid #E6E9F0; border-radius:14px;
                 padding:0.6rem 0.9rem 0.3rem; margin-bottom:1.1rem;
                 box-shadow:0 1px 2px rgba(24,36,51,0.04),0 4px 12px rgba(24,36,51,0.05); }
  .topnav-brand { display:flex;align-items:center;gap:0.55rem;font-weight:800;
                  font-size:1.05rem;color:#1A2234;padding:0.15rem 0.4rem 0.5rem; }
  .topnav-brand .dot { width:26px;height:26px;border-radius:8px;background:#0D3B26;
                       display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.85rem; }
  div[data-testid="stPageLink"] a {
      border-radius:8px !important; padding:0.3rem 0.7rem !important;
      font-size:0.86rem !important; font-weight:600 !important; color:#495057 !important;
      transition:background 0.12s; }
  div[data-testid="stPageLink"] a:hover { background:#F1F3F8 !important; }
  div[data-testid="stPageLink"] a[aria-current="page"],
  div[data-testid="stPageLink"] a.active {
      background:rgba(13,59,38,0.10) !important; color:#0D3B26 !important; }
  section[data-testid="stSidebar"] [data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='topnav-brand'><span class='dot'>▸</span> VRS Analytics</div>", unsafe_allow_html=True)
_all_pages = [overview_page, lookup_page, numbers_page, numfunnel_page, funnel_page, portin_page,
              winback_page, geo_page, yoy_page, ursa_page, journey_page, age_demo_page, churn_page,
              vrs_zero_page, retention_page, cs_tickets_page, ticket_rpt_page, jira_rpt_page,
              survey_page, bulk_page, explorer_page, pendo_page, dq_page, audit_page]
_PER_ROW = 6
for _i in range(0, len(_all_pages), _PER_ROW):
    _row = st.columns(_PER_ROW)
    for _col, _p in zip(_row, _all_pages[_i:_i + _PER_ROW]):
        _col.page_link(_p)
st.divider()

render_sync_widget()
pg.run()
