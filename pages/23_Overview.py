import streamlit as st
import requests
import time
from datetime import datetime, timezone
from utils import require_auth, get_secret, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Overview", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
NUM_OBJECT = "2-40974683"
TTL = 300  # refresh KPIs every 5 min

GREEN, BLUE, AMBER, PURPLE, RED, TEAL = "#0D3B26", "#3B82F6", "#F59E0B", "#8B5CF6", "#EF4444", "#0EA5E9"


def _count(filters):
    """Return the total matching a filter set (cheap — limit 1, read total)."""
    try:
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/{NUM_OBJECT}/search",
            headers=_headers,
            json={"filterGroups": [{"filters": filters}], "properties": ["number_status"], "limit": 1},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return None


def _count_status(status_values):
    """Count VRS numbers whose status is any of the given values (OR across
    values via IN, so casing/label variants all count)."""
    return _count([VRS, {"propertyName": "number_status", "operator": "IN", "values": status_values}])


VRS = {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}


def _load_kpis():
    _mo_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _mo_ms = str(int(_mo_start.timestamp() * 1000))
    return {
        "total":      _count([VRS]),
        "live":       _count_status(["Live", "live", "LIVE", "Active", "active"]),
        "suspended":  _count_status(["Suspended", "suspended", "SUSPENDED"]),
        "registered": _count([VRS, {"propertyName": "registered_at", "operator": "HAS_PROPERTY"}]),
        "new_month":  _count([VRS, {"propertyName": "number_created_at", "operator": "GTE", "value": _mo_ms}]),
        "ts": time.time(),
    }


report_header("Overview", "Live snapshot of VRS numbers and activity", section="Dashboard")

# cached KPI snapshot (refresh button or 5-min TTL)
_c = st.session_state.get("_overview_kpis")
_stale = (not _c) or (time.time() - _c.get("ts", 0)) > TTL
top = st.columns([6, 1])
with top[1]:
    if st.button("↻ Refresh", use_container_width=True):
        _stale = True
if _stale:
    with st.spinner("Loading live metrics…"):
        _c = _load_kpis()
        st.session_state["_overview_kpis"] = _c

_age = int(time.time() - _c.get("ts", time.time()))
_ago = "just now" if _age < 60 else f"{_age // 60} min ago"
st.caption(f"📡 Live from HubSpot · updated {_ago} · auto-refreshes every 5 min")


def _fmt(v):
    return f"{v:,}" if isinstance(v, (int, float)) else "—"


live, total = _c.get("live"), _c.get("total")
reg = _c.get("registered")
_reg_pct = f"{reg / total * 100:.0f}% of all numbers" if (reg is not None and total) else ""
_susp = _c.get("suspended")
_susp_pct = f"{_susp / total * 100:.0f}% of all numbers" if (_susp is not None and total) else ""

cards = [
    ("Total VRS Numbers", _fmt(total),        "all service numbers",  GREEN,  "📇", "#E7F0EB"),
    ("Live Numbers",      _fmt(live),          "currently active",     BLUE,   "🟢", "#E8F1FE"),
    ("Registered",        _fmt(reg),           _reg_pct,               PURPLE, "✅", "#F1EBFC"),
    ("New This Month",    _fmt(_c.get("new_month")), "created this month", TEAL, "✨", "#E4F5FC"),
    ("Suspended",         _fmt(_susp),         _susp_pct,               AMBER,  "⏸️", "#FEF3E2"),
]

st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
cols = st.columns(len(cards))
for col, (label, value, sub, color, icon, chip_bg) in zip(cols, cards):
    with col:
        st.markdown(f"""
<div class="tblr-card">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;">
    <div>
      <div class="tblr-label">{label}</div>
      <div class="tblr-value" style="color:{color};">{value}</div>
      <div class="tblr-sub">{sub or '&nbsp;'}</div>
    </div>
    <div class="tblr-chip" style="background:{chip_bg};">{icon}</div>
  </div>
</div>""", unsafe_allow_html=True)

# ── quick links into the reports ─────────────────────────────────────────────
st.markdown("<div style='height:1.6rem;'></div>", unsafe_allow_html=True)
st.markdown("##### Jump to a report")

links = [
    ("🔍", "VRS Lookup", "Look up any number or customer", GREEN),
    ("🔢", "Number Funnel", "Registration → activation funnel", BLUE),
    ("🔁", "Retention Report", "Cohort retention over time", PURPLE),
    ("🎫", "Ticket Report", "Support ticket KPIs & trends", AMBER),
    ("📝", "Survey", "CSAT & feedback submissions", TEAL),
    ("📊", "Data Explorer", "Build your own query & chart", RED),
]
lc = st.columns(3)
for i, (icon, title, desc, color) in enumerate(links):
    with lc[i % 3]:
        st.markdown(f"""
<div class="tblr-card" style="margin-bottom:1rem;display:flex;align-items:center;gap:0.85rem;">
  <div class="tblr-chip" style="background:{color}18;font-size:1.25rem;">{icon}</div>
  <div>
    <div style="font-weight:700;color:#1F2937;font-size:0.98rem;">{title}</div>
    <div style="font-size:0.78rem;color:#9AA5B1;">{desc}</div>
  </div>
</div>""", unsafe_allow_html=True)
st.caption("Use the sidebar to open any report — this overview is your at-a-glance home.")

report_header_close()
