import streamlit as st
import requests
import os
import time
from datetime import datetime

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
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
            "last_sync": datetime.now().strftime("%b %d at %I:%M %p"),
        }
    d = st.session_state["_sync_widget"]
    dot  = "#2DB84B" if d["healthy"] else "#EF4444"
    label = "Healthy" if d["healthy"] else "Error"

    # Retention from last lookup (if available)
    seg = st.session_state.get("_retention_summary", {})

    with st.sidebar:
        st.markdown("""<div style="border-top:1px solid rgba(255,255,255,0.1);margin:0.5rem 0;"></div>""",
                    unsafe_allow_html=True)
        st.markdown(f"""
<div style="padding:0.6rem 0.25rem 0;">
  <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.25rem;">
    <div style="width:8px;height:8px;border-radius:50%;background:{dot};
                box-shadow:0 0 6px {dot};flex-shrink:0;"></div>
    <span style="font-size:0.78rem;font-weight:700;color:rgba(255,255,255,0.9);">HubSpot {label}</span>
  </div>
  <div style="font-size:0.68rem;color:rgba(255,255,255,0.45);margin-bottom:0.75rem;">
    Last sync: {d['last_sync']}
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.4rem;margin-bottom:0.6rem;">
    <div style="background:rgba(255,255,255,0.07);border-radius:7px;padding:0.45rem 0.6rem;">
      <div style="font-size:0.58rem;color:rgba(255,255,255,0.45);text-transform:uppercase;
                  letter-spacing:0.8px;margin-bottom:2px;">Live VRS</div>
      <div style="font-size:1.05rem;font-weight:800;color:#2DB84B;">{d['live']:,}</div>
    </div>
    <div style="background:rgba(255,255,255,0.07);border-radius:7px;padding:0.45rem 0.6rem;">
      <div style="font-size:0.58rem;color:rgba(255,255,255,0.45);text-transform:uppercase;
                  letter-spacing:0.8px;margin-bottom:2px;">Suspended</div>
      <div style="font-size:1.05rem;font-weight:800;color:#EF4444;">{d['suspended']:,}</div>
    </div>
  </div>
""", unsafe_allow_html=True)

        if seg:
            st.markdown(f"""
<div style="background:rgba(255,255,255,0.07);border-radius:7px;padding:0.5rem 0.6rem;margin-bottom:0.6rem;">
  <div style="font-size:0.58rem;color:rgba(255,255,255,0.45);text-transform:uppercase;
              letter-spacing:0.8px;margin-bottom:0.4rem;">Retention</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;font-size:0.72rem;">
    <div style="color:#2DB84B;font-weight:700;">📈 A: {seg.get('A',0)}</div>
    <div style="color:#3B82F6;font-weight:700;">✅ B: {seg.get('B',0)}</div>
    <div style="color:#F59E0B;font-weight:700;">⚠️ C: {seg.get('C',0)}</div>
    <div style="color:#EF4444;font-weight:700;">🚨 D: {seg.get('D',0)}</div>
  </div>
</div>
""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        with st.expander("View Details"):
            age_mins = int((time.time() - d["ts"]) / 60)
            st.caption(f"Data refreshes every 5 min · {age_mins}m ago")
            st.markdown(f"**Live VRS:** {d['live']:,}")
            st.markdown(f"**Suspended:** {d['suspended']:,}")
            if seg:
                st.markdown("**Retention (last lookup):**")
                st.markdown(f"- 📈 Growth (A): {seg.get('A',0)}")
                st.markdown(f"- ✅ Stable (B): {seg.get('B',0)}")
                st.markdown(f"- ⚠️ Declining (C): {seg.get('C',0)}")
                st.markdown(f"- 🚨 At Risk (D): {seg.get('D',0)}")
            else:
                st.caption("Run a lookup to see retention segments")

lookup_page   = st.Page("pages/0_Lookup.py",              title="VRS Lookup",           icon="🔍", default=True)
numbers_page  = st.Page("pages/1_Numbers_Report.py",      title="Numbers Report",        icon="📊")
ursa_page     = st.Page("pages/2_URSA_Login_Report.py",   title="URSA Login Report",     icon="👤")
geo_page      = st.Page("pages/3_Geographic_Report.py",   title="Geographic Report",     icon="🗺️")
bulk_page     = st.Page("pages/4_Bulk_Search.py",         title="Bulk Search",           icon="🔎")
churn_page    = st.Page("pages/5_Churn_Risk.py",          title="Churn Risk Report",     icon="🚨")
funnel_page   = st.Page("pages/6_Registration_Funnel.py", title="Registration Funnel",   icon="📋")
portin_page   = st.Page("pages/7_Port_In_Report.py",      title="Port-In Report",        icon="📲")

pg = st.navigation([lookup_page, numbers_page, ursa_page, geo_page, bulk_page, churn_page, funnel_page, portin_page])
render_sync_widget()
pg.run()
