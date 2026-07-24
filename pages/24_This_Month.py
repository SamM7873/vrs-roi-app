import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from utils import require_auth, get_secret, COMMON_CSS, log_report_view

st.set_page_config(page_title="This Month", layout="wide", page_icon="🗓️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("This Month")

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


def _btw(prop, lo, hi):
    return [{"propertyName": prop, "operator": "GTE", "value": _ms(lo)},
            {"propertyName": prop, "operator": "LT", "value": _ms(hi)}]


def _load():
    now = datetime.now(timezone.utc)
    m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m1 = m0 + relativedelta(months=1)
    pm0 = m0 - relativedelta(months=1)
    # 12-month trends (for sparklines)
    trend = []
    for i in range(11, -1, -1):
        s = m0 - relativedelta(months=i)
        e = s + relativedelta(months=1)
        trend.append({"Month": s.strftime("%b"),
                      "VRS": _count([VRS] + _btw("number_created_at", s, e)) or 0,
                      "ConvoNow": _count([CN] + _btw("number_created_at", s, e)) or 0})
    return {
        "trend": trend,
        "reg": _count([VRS] + _btw("registered_at", m0, m1)),
        "reg_prev": _count([VRS] + _btw("registered_at", pm0, m0)),
        "deact": _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]
                        + _btw("number_deleted_at", m0, m1)),
        "portout": _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portouts"}]
                          + _btw("number_deleted_at", m0, m1)),
        "portin": _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portins"}]
                         + _btw("number_created_at", m0, m1)),
        "month_label": m0.strftime("%B %Y"),
        "ts": time.time(),
    }


# ── head ──────────────────────────────────────────────────────────────────────
h1, h2 = st.columns([6, 1.4])
with h1:
    st.markdown(
        "<div style='font-size:0.7rem;font-weight:800;letter-spacing:0.12em;color:#8792A2;text-transform:uppercase;'>Dashboard</div>"
        "<div style='font-size:1.7rem;font-weight:800;color:#1A2234;letter-spacing:-0.5px;margin-top:-2px;'>This Month</div>",
        unsafe_allow_html=True)
with h2:
    st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
    refresh = st.button("↻ Refresh", use_container_width=True)

_c = st.session_state.get("_tm")
if refresh or (not _c) or (time.time() - _c.get("ts", 0)) > TTL:
    with st.spinner("Loading this month's metrics…"):
        _c = _load()
        st.session_state["_tm"] = _c

_age = int(time.time() - _c["ts"])
st.caption(f"📡 Live from HubSpot · **{_c['month_label']}** · updated {'just now' if _age < 60 else f'{_age//60} min ago'} · auto-refreshes every 5 min")

df = pd.DataFrame(_c["trend"])
df["Total"] = df["VRS"] + df["ConvoNow"]
_order = list(df["Month"])
vrs_m, vrs_p = int(df["VRS"].iloc[-1]), int(df["VRS"].iloc[-2])
cn_m, cn_p = int(df["ConvoNow"].iloc[-1]), int(df["ConvoNow"].iloc[-2])
tot_m, tot_p = int(df["Total"].iloc[-1]), int(df["Total"].iloc[-2])
reg = _c["reg"] or 0
reg_p = _c["reg_prev"] or 0
deact = _c["deact"] or 0
portout = _c["portout"] or 0
portin = _c.get("portin") or 0


def _spark(color, kind, ycol):
    base = alt.Chart(df).encode(x=alt.X("Month:N", sort=_order, axis=None), y=alt.Y(f"{ycol}:Q", axis=None))
    if kind == "bar":
        ch = base.mark_bar(color=color, size=6, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
    elif kind == "line":
        ch = base.mark_line(color=color, strokeWidth=2, point=alt.OverlayMarkDef(color=color, size=18))
    else:
        ch = base.mark_area(line={"color": color, "strokeWidth": 2},
                            color=alt.Gradient(gradient="linear",
                                               stops=[alt.GradientStop(color=color + "05", offset=0),
                                                      alt.GradientStop(color=color + "55", offset=1)],
                                               x1=1, x2=1, y1=1, y2=0))
    return ch.properties(height=46).configure_view(strokeWidth=0)


def _delta(cur, prev):
    if not prev:
        return "<span style='color:#8792A2;font-weight:700;'>—</span>"
    d = (cur - prev) / prev * 100
    if d > 0:
        return f"<span style='color:{GREEN};font-weight:700;'>+{d:.0f}% ↗</span>"
    if d < 0:
        return f"<span style='color:{RED};font-weight:700;'>{d:.0f}% ↘</span>"
    return "<span style='color:#8792A2;font-weight:700;'>0% —</span>"


def _kpi(label, value, sub, color, extra=""):
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.2rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">{label}</span>
    <span style="font-size:0.72rem;">{extra}</span></div>
  <div class="tblr-value" style="color:{color};">{value:,}</div>
  <div class="tblr-sub">{sub}</div>
</div>""", unsafe_allow_html=True)


# ── KPI cards with sparklines (Overview style) ────────────────────────────────
st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
with k1:
    _kpi("New VRS numbers", vrs_m, f"vs {vrs_p:,} last month", BLUE, _delta(vrs_m, vrs_p))
    st.altair_chart(_spark(BLUE, "bar", "VRS"), use_container_width=True)
with k2:
    _kpi("New Convo Now", cn_m, f"vs {cn_p:,} last month", GREEN, _delta(cn_m, cn_p))
    st.altair_chart(_spark(GREEN, "line", "ConvoNow"), use_container_width=True)
with k3:
    _kpi("Total new numbers", tot_m, f"vs {tot_p:,} last month", CYAN, _delta(tot_m, tot_p))
    st.altair_chart(_spark(CYAN, "area", "Total"), use_container_width=True)
with k4:
    _kpi("New registrations", reg, f"vs {reg_p:,} last month", PURPLE, _delta(reg, reg_p))
    st.markdown(f"""<div style="height:6px;background:#EEF1F6;border-radius:4px;margin-top:0.55rem;overflow:hidden;">
      <div style="width:{min(reg/(reg_p or 1)*50,100):.0f}%;height:100%;background:{PURPLE};"></div></div>""",
                unsafe_allow_html=True)

# ── small stat cards ──────────────────────────────────────────────────────────
st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
_net = vrs_m + cn_m - deact
stat = [
    ("📥", f"{portin:,} Port-In", "ported in from another provider", BLUE),
    ("📤", f"{portout:,} Port-Out", "ported to another provider", AMBER),
    ("🚫", f"{deact:,} Deactivated", "closed / deleted this month", RED),
    ("📈", f"{_net:,} Net new", "VRS + Convo − deactivated", GREEN if _net >= 0 else RED),
]
sc = st.columns(4)
for col, (icon, title, sub, color) in zip(sc, stat):
    with col:
        st.markdown(f"""
<div class="tblr-card" style="display:flex;align-items:center;gap:0.85rem;padding:0.9rem 1.1rem;">
  <div class="tblr-chip" style="background:{color}18;">{icon}</div>
  <div><div style="font-weight:800;color:#1A2234;font-size:1.02rem;">{title}</div>
       <div style="font-size:0.75rem;color:#9AA5B1;">{sub}</div></div>
</div>""", unsafe_allow_html=True)

st.caption("Scoped to the current calendar month. Use **Overview** for all-time totals.")
