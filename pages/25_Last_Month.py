import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from utils import require_auth, get_secret, COMMON_CSS, log_report_view

st.set_page_config(page_title="Last Month", layout="wide", page_icon="🗓️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Last Month")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
NUM_OBJECT = "2-40974683"
TTL = 300
BLUE, GREEN, CYAN, AMBER, PURPLE, RED = "#206BC4", "#2FB344", "#0EA5E9", "#F59F00", "#8B5CF6", "#D63939"

VRS = {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}
CN = {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}
_DEACT = ["Deactivated", "deactivated", "Deleted", "deleted", "Inactive", "inactive", "Cancelled", "cancelled"]


def _count(filters):
    try:
        r = requests.post(f"{BASE_URL}/crm/v3/objects/{NUM_OBJECT}/search", headers=_headers,
                          json={"filterGroups": [{"filters": filters}], "properties": ["number_status"], "limit": 1},
                          timeout=10)
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return None


def _ms(dt):
    return str(int(dt.timestamp() * 1000))


def _between(prop, lo, hi):
    return [{"propertyName": prop, "operator": "GTE", "value": _ms(lo)},
            {"propertyName": prop, "operator": "LT", "value": _ms(hi)}]


def _load():
    now = datetime.now(timezone.utc)
    _this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m0 = _this - relativedelta(months=1)   # LAST month start
    m1 = _this                             # this month start (= last month end)
    pm0 = m0 - relativedelta(months=1)     # month before last (for the delta)
    created = "number_created_at"
    deleted = "number_deleted_at"
    return {
        "new_vrs":       _count([VRS] + _between(created, m0, m1)),
        "new_vrs_prev":  _count([VRS] + _between(created, pm0, m0)),
        "new_convo":     _count([CN] + _between(created, m0, m1)),
        "new_convo_prev": _count([CN] + _between(created, pm0, m0)),
        "reg":           _count([VRS] + _between("registered_at", m0, m1)),
        "reg_prev":      _count([VRS] + _between("registered_at", pm0, m0)),
        "deact":         _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]
                                + _between(deleted, m0, m1)),
        "portout":       _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portouts"}]
                                + _between(deleted, m0, m1)),
        "month_label":   m0.strftime("%B %Y"),
        "ts": time.time(),
    }


# ── page head ─────────────────────────────────────────────────────────────────
h1, h2 = st.columns([6, 1.4])
with h1:
    st.markdown(
        "<div style='font-size:0.7rem;font-weight:800;letter-spacing:0.12em;color:#8792A2;text-transform:uppercase;'>Dashboard</div>"
        "<div style='font-size:1.7rem;font-weight:800;color:#1A2234;letter-spacing:-0.5px;margin-top:-2px;'>Last Month</div>",
        unsafe_allow_html=True)
with h2:
    st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
    refresh = st.button("↻ Refresh", use_container_width=True)

_c = st.session_state.get("_lm")
if refresh or (not _c) or (time.time() - _c.get("ts", 0)) > TTL:
    with st.spinner("Loading last month's metrics…"):
        _c = _load()
        st.session_state["_lm"] = _c

_age = int(time.time() - _c["ts"])
st.caption(f"📡 Live from HubSpot · **{_c['month_label']}** · updated {'just now' if _age < 60 else f'{_age//60} min ago'} · auto-refreshes every 5 min")


def _v(x):
    return x or 0


def _delta(cur, prev):
    cur, prev = _v(cur), _v(prev)
    if not prev:
        return "<span style='color:#8792A2;font-weight:700;'>—</span>"
    d = (cur - prev) / prev * 100
    if d > 0:
        return f"<span style='color:{GREEN};font-weight:700;'>+{d:.0f}% ↗</span>"
    if d < 0:
        return f"<span style='color:{RED};font-weight:700;'>{d:.0f}% ↘</span>"
    return "<span style='color:#8792A2;font-weight:700;'>0% —</span>"


def card(col, label, value, sub, color, icon, chip, delta_html=""):
    with col:
        st.markdown(f"""
<div class="tblr-card">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.5rem;">
    <div>
      <div class="tblr-label">{label}</div>
      <div class="tblr-value" style="color:{color};">{_v(value):,}</div>
      <div class="tblr-sub">{sub} {delta_html}</div>
    </div>
    <div class="tblr-chip" style="background:{chip};">{icon}</div>
  </div>
</div>""", unsafe_allow_html=True)


st.markdown("<div style='height:0.4rem;'></div>", unsafe_allow_html=True)
r1 = st.columns(3)
card(r1[0], "New VRS numbers", _c["new_vrs"], f"vs {_v(_c['new_vrs_prev']):,} last month", BLUE, "📇", "#E8F1FE", _delta(_c["new_vrs"], _c["new_vrs_prev"]))
card(r1[1], "New Convo Now", _c["new_convo"], f"vs {_v(_c['new_convo_prev']):,} last month", GREEN, "📞", "#E7F6EC", _delta(_c["new_convo"], _c["new_convo_prev"]))
card(r1[2], "New Registrations", _c["reg"], f"vs {_v(_c['reg_prev']):,} last month", PURPLE, "✅", "#F1EBFC", _delta(_c["reg"], _c["reg_prev"]))

st.markdown("<div style='height:0.7rem;'></div>", unsafe_allow_html=True)
r2 = st.columns(3)
card(r2[0], "Deactivated", _c["deact"], "closed / deleted that month", RED, "🚫", "#FDECEC")
card(r2[1], "Port-Out", _c["portout"], "ported to another provider", AMBER, "📤", "#FEF3E2")
_net = _v(_c["new_vrs"]) + _v(_c["new_convo"]) - _v(_c["deact"])
card(r2[2], "Net new (VRS+Convo − deact)", _net, "net growth that month", GREEN if _net >= 0 else RED, "📈", "#E7F6EC")

# ── this month vs last month comparison ───────────────────────────────────────
st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)
st.markdown("<div class='tblr-label' style='margin-bottom:0.4rem;'>This month vs last month · new numbers</div>",
            unsafe_allow_html=True)
_cmp = pd.DataFrame({
    "Service": ["VRS", "VRS", "Convo Now", "Convo Now"],
    "Period": ["Last month", "This month", "Last month", "This month"],
    "Count": [_v(_c["new_vrs_prev"]), _v(_c["new_vrs"]), _v(_c["new_convo_prev"]), _v(_c["new_convo"])],
})
chart = alt.Chart(_cmp).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
    x=alt.X("Service:N", axis=alt.Axis(title=None, labelAngle=0)),
    xOffset="Period:N",
    y=alt.Y("Count:Q", title=None),
    color=alt.Color("Period:N", scale=alt.Scale(domain=["Last month", "This month"], range=["#C7D2E0", BLUE]),
                    legend=alt.Legend(orient="top", title=None)),
    tooltip=["Service", "Period", "Count"]).properties(height=300)
st.altair_chart(chart, use_container_width=True)

st.caption("This dashboard is scoped to the previous calendar month. Use **Overview** for all-time totals or **This Month** for the current month.")
