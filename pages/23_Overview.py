import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from utils import require_auth, get_secret, COMMON_CSS, log_report_view

st.set_page_config(page_title="Dashboard", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Overview")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
NUM_OBJECT = "2-40974683"
TTL = 300

BLUE, GREEN, CYAN, AMBER, PURPLE, RED, INK = "#206BC4", "#2FB344", "#4299E1", "#F59F00", "#8B5CF6", "#D63939", "#1A2234"


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


VRS = {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}


_LIVE_VALS = ["Live", "live", "LIVE", "Active", "active"]


def _status(vals):
    # HubSpot's canonical status field is account_status ("Account Status").
    return _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": vals}])


_DEACT = ["Deactivated", "deactivated", "Deleted", "deleted", "Inactive", "inactive", "Cancelled", "cancelled"]


def _convo_minus_guest():
    """Convo Now numbers with Account Status = Live, credit type is none of Guest.
    Counts all-live minus guest-live so blank credit types are kept (matches
    HubSpot's 'is none of' semantics)."""
    _cn = {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}
    _live = {"propertyName": "account_status", "operator": "IN", "values": ["Live", "live", "LIVE"]}
    _all = _count([_cn, _live])
    _guest = _count([_cn, _live, {"propertyName": "credit_type", "operator": "IN",
                                  "values": ["guest", "Guest", "GUEST"]}])
    if _all is None:
        return None
    return _all - (_guest or 0)


def _ms(dt):
    return str(int(dt.timestamp() * 1000))


def _load():
    now = datetime.now(timezone.utc)
    mo_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # last 12 months of new-number counts (real trend)
    _CN = {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}
    trend = []
    for i in range(11, -1, -1):
        s = (mo_start - relativedelta(months=i))
        e = (s + relativedelta(months=1))
        _lo = {"propertyName": "number_created_at", "operator": "GTE", "value": _ms(s)}
        _hi = {"propertyName": "number_created_at", "operator": "LT", "value": _ms(e)}
        c = _count([VRS, _lo, _hi])
        cn = _count([_CN, _lo, _hi])
        trend.append({"Month": s.strftime("%b"), "MonthKey": s.strftime("%Y-%m"),
                      "New": c or 0, "ConvoNow": cn or 0})
    return {
        "total": _count([VRS]),
        "live": _status(["Live", "live", "LIVE", "Active", "active"]),
        "suspended": _status(["Suspended", "suspended", "SUSPENDED"]),
        "registered": _count([VRS, {"propertyName": "registered_at", "operator": "HAS_PROPERTY"}]),
        "deactivated": _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]),
        "port_out": _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portouts"}]),
        # Convo Now Live, "Credit Type is none of Guest" — HubSpot's "is none of"
        # KEEPS blank credit types, so compute all-live minus guest-live (NOT_IN
        # would wrongly drop the blanks).
        "convo_live": _convo_minus_guest(),
        "new_month": trend[-1]["New"] if trend else 0,
        "prev_month": trend[-2]["New"] if len(trend) > 1 else 0,
        "trend": trend,
        "ts": time.time(),
    }


# ── page head (Tabler style) ──────────────────────────────────────────────────
h1, h2 = st.columns([6, 1.4])
with h1:
    st.markdown(
        "<div style='font-size:0.7rem;font-weight:800;letter-spacing:0.12em;color:#8792A2;text-transform:uppercase;'>Overview</div>"
        "<div style='font-size:1.7rem;font-weight:800;color:#1A2234;letter-spacing:-0.5px;margin-top:-2px;'>Dashboard</div>",
        unsafe_allow_html=True)
with h2:
    st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
    refresh = st.button("↻ Refresh", use_container_width=True)

_c = st.session_state.get("_ov")
if refresh or (not _c) or (time.time() - _c.get("ts", 0)) > TTL:
    with st.spinner("Loading live metrics from HubSpot…"):
        _c = _load()
        st.session_state["_ov"] = _c

_age = int(time.time() - _c["ts"])
st.caption(f"📡 Live from HubSpot · updated {'just now' if _age < 60 else f'{_age//60} min ago'} · auto-refreshes every 5 min")

total = _c["total"] or 0
live = _c["live"] or 0
reg = _c["registered"] or 0
susp = _c["suspended"] or 0
new_m = _c["new_month"] or 0
prev_m = _c["prev_month"] or 0
deact = _c.get("deactivated") or 0
port_out = _c.get("port_out") or 0
convo_live = _c.get("convo_live") or 0
reg_pct = (reg / total * 100) if total else 0
live_pct = (live / total * 100) if total else 0
mom = ((new_m - prev_m) / prev_m * 100) if prev_m else 0
trend_df = pd.DataFrame(_c["trend"])
_order = list(trend_df["Month"])


def _spark(df, color, kind="area", ycol="New"):
    base = alt.Chart(df).encode(
        x=alt.X("Month:N", sort=_order, axis=None),
        y=alt.Y(f"{ycol}:Q", axis=None))
    if kind == "bar":
        ch = base.mark_bar(color=color, size=6, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
    elif kind == "line":
        ch = base.mark_line(color=color, strokeWidth=2)
    else:
        ch = base.mark_area(line={"color": color, "strokeWidth": 2},
                            color=alt.Gradient(gradient="linear",
                                               stops=[alt.GradientStop(color=color + "05", offset=0),
                                                      alt.GradientStop(color=color + "55", offset=1)],
                                               x1=1, x2=1, y1=1, y2=0))
    return ch.properties(height=46).configure_view(strokeWidth=0)


def _delta(v):
    if v > 0:
        return f"<span style='color:{GREEN};font-weight:700;'>{v:.0f}% ↗</span>"
    if v < 0:
        return f"<span style='color:{RED};font-weight:700;'>{v:.0f}% ↘</span>"
    return "<span style='color:#8792A2;font-weight:700;'>0% —</span>"


# ── KPI cards row ─────────────────────────────────────────────────────────────
st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.6rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">Registered rate</span>
    <span style="font-size:0.68rem;color:#B0B7C3;">of {total:,}</span></div>
  <div class="tblr-value">{reg_pct:.0f}%</div>
  <div style="height:6px;background:#EEF1F6;border-radius:4px;margin-top:0.6rem;overflow:hidden;">
    <div style="width:{reg_pct:.0f}%;height:100%;background:{BLUE};"></div></div>
  <div class="tblr-sub" style="margin-top:0.5rem;">{reg:,} registered numbers</div>
</div>""", unsafe_allow_html=True)

with k2:
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.2rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">Total VRS numbers</span></div>
  <div class="tblr-value">{total:,}</div>
  <div class="tblr-sub">all service numbers</div>
</div>""", unsafe_allow_html=True)
    st.altair_chart(_spark(trend_df.assign(New=trend_df["New"].cumsum() + (total - trend_df["New"].sum())),
                           BLUE, "area"), use_container_width=True)

with k3:
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.2rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">New this month</span>
    <span style="font-size:0.72rem;">{_delta(mom)}</span></div>
  <div class="tblr-value">{new_m:,}</div>
  <div class="tblr-sub">vs {prev_m:,} last month</div>
</div>""", unsafe_allow_html=True)
    st.altair_chart(_spark(trend_df, GREEN, "bar"), use_container_width=True)

with k4:
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.2rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">Live numbers</span>
    <span style="font-size:0.72rem;color:#8792A2;font-weight:700;">{live_pct:.0f}%</span></div>
  <div class="tblr-value" style="color:{GREEN};">{live:,}</div>
  <div class="tblr-sub">{susp:,} suspended</div>
</div>""", unsafe_allow_html=True)
    st.altair_chart(_spark(trend_df, CYAN, "line"), use_container_width=True)

with k5:
    st.markdown(f"""
<div class="tblr-card" style="padding-bottom:0.2rem;">
  <div style="display:flex;justify-content:space-between;"><span class="tblr-label">Convo Now live</span>
    <span style="font-size:0.72rem;">📞</span></div>
  <div class="tblr-value" style="color:{GREEN};">{convo_live:,}</div>
  <div class="tblr-sub">new/mo trend →</div>
</div>""", unsafe_allow_html=True)
    st.altair_chart(_spark(trend_df, GREEN, "line", ycol="ConvoNow"), use_container_width=True)

# ── small stat cards row ──────────────────────────────────────────────────────
st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
stat = [
    ("🟢", f"{live:,} Live VRS", "currently active", GREEN),
    ("📞", f"{convo_live:,} Convo Now Live", "live Convo Now numbers", "#0EA5E9"),
    ("⏸️", f"{susp:,} Suspended", "not active", AMBER),
    ("🚫", f"{deact:,} Deactivated", "closed / inactive", RED),
    ("📤", f"{port_out:,} Port-Out", "ported to another provider", "#8B5CF6"),
    ("✨", f"{new_m:,} New", "this month", CYAN),
]
sc = st.columns(6)
for col, (icon, title, sub, color) in zip(sc, stat):
    with col:
        st.markdown(f"""
<div class="tblr-card" style="display:flex;align-items:center;gap:0.85rem;padding:0.9rem 1.1rem;">
  <div class="tblr-chip" style="background:{color}18;">{icon}</div>
  <div><div style="font-weight:800;color:#1A2234;font-size:1.02rem;">{title}</div>
       <div style="font-size:0.75rem;color:#9AA5B1;">{sub}</div></div>
</div>""", unsafe_allow_html=True)

# ── traffic-style chart + status donut ────────────────────────────────────────
st.markdown("<div style='height:1.3rem;'></div>", unsafe_allow_html=True)
g1, g2 = st.columns([2, 1])
with g1:
    st.markdown("<div class='tblr-label' style='margin-bottom:0.4rem;'>New numbers · last 12 months "
                "<span style='color:#206BC4;'>▬ VRS</span> · <span style='color:#2FB344;'>▬ Convo Now</span></div>",
                unsafe_allow_html=True)
    _tbase = alt.Chart(trend_df).encode(x=alt.X("Month:N", sort=_order, axis=alt.Axis(title=None, labelAngle=0)))
    bars = _tbase.mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3, size=22).encode(
        y=alt.Y("New:Q", title=None),
        tooltip=[alt.Tooltip("Month"), alt.Tooltip("New:Q", title="New VRS"),
                 alt.Tooltip("ConvoNow:Q", title="New Convo Now")])
    cn_line = _tbase.mark_line(color=GREEN, strokeWidth=3,
                               point=alt.OverlayMarkDef(color=GREEN, size=45)).encode(
        y=alt.Y("ConvoNow:Q", title=None),
        tooltip=[alt.Tooltip("Month"), alt.Tooltip("ConvoNow:Q", title="New Convo Now")])
    st.altair_chart((bars + cn_line).resolve_scale(y="independent").properties(height=300),
                    use_container_width=True)
with g2:
    st.markdown("<div class='tblr-label' style='margin-bottom:0.4rem;'>Status breakdown</div>", unsafe_allow_html=True)
    other = max(total - live - susp - deact, 0)
    donut_df = pd.DataFrame({"Status": ["Live", "Suspended", "Deactivated", "Other"],
                             "Count": [live, susp, deact, other]})
    donut = alt.Chart(donut_df).mark_arc(innerRadius=62, cornerRadius=3).encode(
        theta="Count:Q",
        color=alt.Color("Status:N", scale=alt.Scale(domain=["Live", "Suspended", "Deactivated", "Other"],
                                                     range=[GREEN, AMBER, RED, "#CBD5E1"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["Status", "Count"]).properties(height=300)
    st.caption("‘Other’ = numbers in any status besides Live, Suspended, or Deactivated.")
    st.altair_chart(donut, use_container_width=True)

st.caption("Use the sidebar to open any report — this dashboard is your at-a-glance home.")

# ── all reports (quick links) ─────────────────────────────────────────────────
st.markdown("<div style='height:1.6rem;'></div>", unsafe_allow_html=True)
st.markdown("<div class='tblr-label' style='font-size:0.72rem;margin-bottom:0.2rem;'>Browse all reports</div>",
            unsafe_allow_html=True)
st.markdown("""<style>
div[data-testid='stPageLink']{margin-bottom:0.7rem;}
div[data-testid='stPageLink'] a{
    border:1px solid #E6E9F0;border-radius:14px;
    padding:1rem 1.15rem !important;background:#FFFFFF;
    min-height:64px;display:flex;align-items:center;gap:0.6rem;
    font-weight:700 !important;font-size:0.95rem !important;color:#1A2234 !important;
    box-shadow:0 1px 2px rgba(24,36,51,0.04),0 4px 12px rgba(24,36,51,0.05);
    transition:box-shadow .14s, transform .14s, border-color .14s;}
div[data-testid='stPageLink'] a:hover{
    box-shadow:0 4px 16px rgba(24,36,51,0.10);
    transform:translateY(-2px);border-color:rgba(13,59,38,0.28);}
div[data-testid='stPageLink'] a span[data-testid='stIconMaterial'],
div[data-testid='stPageLink'] a > span:first-child{font-size:1.35rem !important;}
</style>""", unsafe_allow_html=True)

# (url_path, title, icon, description) — url_path is the page route (filename stem).
_SECTIONS = {
    ("Dashboards", GREEN): [
        ("This_Month", "This Month", "🗓️", "Current-month activity dashboard"),
        ("Last_Month", "Last Month", "📅", "Previous-month snapshot"),
    ],
    ("Numbers", BLUE): [
        ("Numbers_Report", "Numbers Report", "📊", "Live VRS numbers & billable minutes"),
        ("Number_Funnel", "Number Funnel", "🔢", "Registration → activation funnel"),
        ("Registration_Funnel", "Registration Funnel", "📋", "Sign-up completion stages"),
        ("Port_In_Report", "Port-In Report", "📲", "Numbers ported in"),
        ("Port_Out_Winback", "Port-Out Winback", "🔄", "Port-outs & win-back"),
        ("Geographic_Report", "Geographic Report", "🗺️", "Numbers by state & region"),
        ("YoY_Comparison", "Year-over-Year", "📆", "Year-over-year trends"),
    ],
    ("Customers", GREEN): [
        ("Lookup", "VRS Lookup", "🔍", "Look up any number or customer"),
        ("URSA_Login_Report", "URSA Login Report", "👤", "URSA login activity"),
        ("Signup_Journey", "Sign-Up Journey", "🧭", "Sign-up journey stages"),
        ("Age_Demographics", "Age Demographics", "👥", "Customer age breakdown"),
        ("Churn_Risk", "Churn Risk Report", "🚨", "At-risk customers"),
        ("VRS_Zero_ConvoNow_Active", "VRS Zero / Convo Now", "🔄", "Zero-usage & Convo Now active"),
        ("Retention_Report", "Retention Report", "🔁", "Cohort retention over time"),
    ],
    ("Support", AMBER): [
        ("Consumer_Success_Tickets", "Consumer Success Tickets", "🎫", "CS ticket overview"),
        ("Ticket_Report", "Ticket Report", "🎟️", "Support ticket KPIs & trends"),
        ("Jira_Report", "Jira Ticket Report", "🧩", "Jira engineering tickets"),
        ("Survey", "Survey", "📝", "CSAT & feedback submissions"),
    ],
    ("Tools", PURPLE): [
        ("Bulk_Search", "Bulk Search", "🔎", "Search many numbers at once"),
        ("Data_Explorer", "Data Explorer", "📊", "Build your own query & chart"),
        ("Pendo_Report", "Pendo Report", "📱", "App engagement analytics"),
        ("Data_Quality", "Data Quality", "🧹", "Data health & duplicates"),
        ("Audit_Log", "Audit Log", "🛡️", "Login & report-usage audit"),
    ],
}
# Live values pulled from the dashboard snapshot already loaded above (no extra
# queries) so relevant cards show real-time numbers.
_LIVE = {
    "Numbers_Report": f"{total:,} numbers",
    "Number_Funnel": f"{new_m:,} new",
    "Registration_Funnel": f"{reg:,} reg",
    "Retention_Report": f"{live:,} live",
    "VRS_Zero_ConvoNow_Active": f"{susp:,} susp",
    "Port_Out_Winback": f"{port_out:,} port-out",
}


def _stat(_url, _color):
    """Right-side live stat: big number + unit, or a subtle arrow if no data."""
    v = _LIVE.get(_url)
    if not v:
        return (f'<div style="margin-left:auto;flex-shrink:0;color:#C7CDD8;font-size:1.1rem;">›</div>')
    _num, _, _unit = v.partition(" ")
    return (f'<div style="margin-left:auto;flex-shrink:0;text-align:right;">'
            f'<div style="font-size:1.15rem;font-weight:800;color:{_color};line-height:1;'
            f'font-variant-numeric:tabular-nums;">{_num}</div>'
            f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;'
            f'color:#9AA5B1;margin-top:2px;">{_unit}</div></div>')


for (_sec, _color), _items in _SECTIONS.items():
    st.markdown(f"<div style='display:flex;align-items:center;gap:0.5rem;margin:1.15rem 0 0.55rem;'>"
                f"<span style='width:9px;height:9px;border-radius:3px;background:{_color};'></span>"
                f"<span style='font-weight:800;color:#1A2234;font-size:0.98rem;'>{_sec}</span></div>",
                unsafe_allow_html=True)
    _cards = "".join(
        f'<a href="{_url}" target="_self" style="text-decoration:none;">'
        f'<div style="background:#FFFFFF;border:1px solid #E6E9F0;border-left:4px solid {_color};'
        f'border-radius:14px;padding:0.95rem 1.1rem;display:flex;align-items:center;gap:0.8rem;height:100%;'
        f'box-shadow:0 1px 2px rgba(24,36,51,0.04),0 4px 12px rgba(24,36,51,0.05);transition:.14s;"'
        f' onmouseover="this.style.transform=\'translateY(-2px)\';this.style.boxShadow=\'0 6px 18px rgba(24,36,51,0.12)\';"'
        f' onmouseout="this.style.transform=\'none\';this.style.boxShadow=\'0 1px 2px rgba(24,36,51,0.04),0 4px 12px rgba(24,36,51,0.05)\';">'
        f'<div style="width:42px;height:42px;border-radius:11px;flex-shrink:0;background:{_color}1A;'
        f'display:flex;align-items:center;justify-content:center;font-size:1.25rem;">{_icon}</div>'
        f'<div style="min-width:0;"><div style="font-weight:800;color:#1A2234;font-size:0.92rem;line-height:1.15;">{_title}</div>'
        f'<div style="font-size:0.74rem;color:#9AA5B1;margin-top:2px;">{_desc}</div></div>'
        f'{_stat(_url, _color)}'
        f'</div></a>'
        for _url, _title, _icon, _desc in _items
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));'
        f'gap:0.75rem;margin-bottom:0.4rem;">{_cards}</div>', unsafe_allow_html=True)
