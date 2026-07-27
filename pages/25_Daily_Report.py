import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from utils import require_auth, get_secret, COMMON_CSS, log_report_view

st.set_page_config(page_title="Daily Report", layout="wide", page_icon="📆")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Daily Report")

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


def _parse(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _search_records(filters, props, cap=2000):
    """Fetch matching records (not just count) with the given properties."""
    out, after = [], None
    for _ in range(cap // 100 + 1):
        payload = {"filterGroups": [{"filters": filters}], "properties": props, "limit": 100}
        if after:
            payload["after"] = after
        try:
            r = requests.post(f"{BASE_URL}/crm/v3/objects/{NUM_OBJECT}/search",
                              headers=_headers, json=payload, timeout=20)
        except Exception:
            break
        if r.status_code != 200:
            break
        d = r.json()
        out.extend(d.get("results", []))
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


def _portout_detail(m0, m1):
    """Returns (avg_age_days, rows) for numbers ported out this month.
    Age = number_deleted_at − number_created_at."""
    recs = _search_records(
        [VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portouts"}]
        + _btw("number_deleted_at", m0, m1),
        ["number", "email", "first_name", "last_name", "number_created_at", "number_deleted_at"])
    days, rows = [], []
    for r in recs:
        p = r.get("properties", {})
        c, d = _parse(p.get("number_created_at")), _parse(p.get("number_deleted_at"))
        age = (d - c).total_seconds() / 86400 if (c and d and d > c) else None
        if age is not None:
            days.append(age)
        if c and d and d > c:
            _rd = relativedelta(d, c)
            _parts = [f"{_rd.years}y" if _rd.years else "", f"{_rd.months}m" if _rd.months else "",
                      f"{_rd.days}d" if _rd.days else ""]
            age_ymd = " ".join(x for x in _parts if x) or "0d"
        else:
            age_ymd = "—"
        rows.append({
            "Number": p.get("number") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "Created": c.strftime("%b %d, %Y") if c else "—",
            "Ported Out": d.strftime("%b %d, %Y") if d else "—",
            "Age": age_ymd,
            "Age (days)": round(age) if age is not None else None,
        })
    rows.sort(key=lambda x: (x["Age (days)"] is None, -(x["Age (days)"] or 0)))
    return ((sum(days) / len(days)) if days else None), rows


def _deact_detail(m0, m1):
    """All VRS numbers deactivated in the window, with lifespan age."""
    recs = _search_records(
        [VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]
        + _btw("number_deleted_at", m0, m1),
        ["number", "email", "first_name", "last_name", "account_status",
         "deleted_reason", "number_created_at", "number_deleted_at"])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        c, d = _parse(p.get("number_created_at")), _parse(p.get("number_deleted_at"))
        if c and d and d > c:
            _rd = relativedelta(d, c)
            age_ymd = " ".join(x for x in [f"{_rd.years}y" if _rd.years else "",
                                           f"{_rd.months}m" if _rd.months else "",
                                           f"{_rd.days}d" if _rd.days else ""] if x) or "0d"
            age_days = round((d - c).total_seconds() / 86400)
        else:
            age_ymd, age_days = "—", None
        _reason = (p.get("deleted_reason") or "").replace("_", " ").strip().title() or "—"
        rows.append({
            "Number": p.get("number") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "Status": (p.get("account_status") or "—").title(),
            "Delete Reason": _reason,
            "Created": c.strftime("%b %d, %Y") if c else "—",
            "Deactivated": d.strftime("%b %d, %Y") if d else "—",
            "Age": age_ymd,
            "Age (days)": age_days,
        })
    rows.sort(key=lambda x: (x["Age (days)"] is None, -(x["Age (days)"] or 0)))
    return rows


def _unreg_detail(m0, m1):
    """Numbers created this month with NO registration timestamp — typically
    manually-added by CS (number changes, new Spanish numbers)."""
    recs = _search_records(
        [VRS, {"propertyName": "registered_at", "operator": "NOT_HAS_PROPERTY"}]
        + _btw("number_created_at", m0, m1),
        ["number", "email", "first_name", "last_name", "usage_type",
         "language_preference", "number_created_at"])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        c = _parse(p.get("number_created_at"))
        _lang = (p.get("language_preference") or "").strip()
        _lang = "English" if _lang.lower() in ("en", "english") else (
            "Spanish" if _lang.lower() in ("es", "spanish", "español", "espanol") else (_lang.title() or "—"))
        rows.append({
            "Number": p.get("number") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "Usage": (p.get("usage_type") or "—").title(),
            "Language": _lang,
            "Created": c.strftime("%b %d, %Y") if c else "—",
        })
    rows.sort(key=lambda x: x["Created"])
    return rows


def _btw(prop, lo, hi):
    return [{"propertyName": prop, "operator": "GTE", "value": _ms(lo)},
            {"propertyName": prop, "operator": "LT", "value": _ms(hi)}]


def _load():
    # Define DAYS in Central time (to match HubSpot's filters), then the
    # tz-aware datetimes convert to correct UTC epoch ms in _ms().
    _ct = timezone(timedelta(hours=-5 if 3 <= datetime.now(timezone.utc).month <= 11 else -6))
    now = datetime.now(_ct)
    m0 = now.replace(hour=0, minute=0, second=0, microsecond=0)   # today start
    m1 = m0 + timedelta(days=1)                                    # tomorrow start
    pm0 = m0 - timedelta(days=1)                                   # yesterday start
    _po_avg, _po_rows = _portout_detail(m0, m1)
    _unreg_rows = _unreg_detail(m0, m1)
    _deact_rows = _deact_detail(m0, m1)
    # last 12 days trend (for sparklines)
    trend = []
    for i in range(11, -1, -1):
        s = m0 - timedelta(days=i)
        e = s + timedelta(days=1)
        trend.append({"Month": s.strftime("%m/%d"),
                      "VRS": _count([VRS] + _btw("number_created_at", s, e)) or 0,
                      "ConvoNow": _count([CN] + _btw("number_created_at", s, e)) or 0})
    return {
        "trend": trend,
        "reg": _count([VRS] + _btw("registered_at", m0, m1)),
        "reg_prev": _count([VRS] + _btw("registered_at", pm0, m0)),
        # created today but with NO registration timestamp (unregistered)
        "unreg": _count([VRS, {"propertyName": "registered_at", "operator": "NOT_HAS_PROPERTY"}]
                        + _btw("number_created_at", m0, m1)),
        "deact": _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]
                        + _btw("number_deleted_at", m0, m1)),
        "deact_prev": _count([VRS, {"propertyName": "account_status", "operator": "IN", "values": _DEACT}]
                             + _btw("number_deleted_at", pm0, m0)),
        "portout": _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portouts"}]
                          + _btw("number_deleted_at", m0, m1)),
        "portin": _count([VRS, {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portins"}]
                         + _btw("number_created_at", m0, m1)),
        "portout_age": _po_avg,
        "portout_rows": _po_rows,
        "unreg_rows": _unreg_rows,
        "deact_rows": _deact_rows,
        "month_label": m0.strftime("%A, %b %d, %Y"),
        "ts": time.time(),
    }


# ── head ──────────────────────────────────────────────────────────────────────
h1, h2 = st.columns([6, 1.4])
with h1:
    st.markdown(
        "<div style='font-size:0.7rem;font-weight:800;letter-spacing:0.12em;color:#8792A2;text-transform:uppercase;'>Dashboard</div>"
        "<div style='font-size:1.7rem;font-weight:800;color:#1A2234;letter-spacing:-0.5px;margin-top:-2px;'>Daily Report</div>",
        unsafe_allow_html=True)
with h2:
    st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
    refresh = st.button("↻ Refresh", use_container_width=True)

_c = st.session_state.get("_dr")
if refresh or (not _c) or (time.time() - _c.get("ts", 0)) > TTL:
    with st.spinner("Loading today's metrics…"):
        _c = _load()
        st.session_state["_dr"] = _c

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
unreg = _c.get("unreg") or 0
_unreg_pct = (unreg / vrs_m * 100) if vrs_m else 0
_net = vrs_m - deact
_net_prev = vrs_p - (_c.get("deact_prev") or 0)
_net_pct = (_net / vrs_m * 100) if vrs_m else 0
_deact_pct = (deact / vrs_m * 100) if vrs_m else 0
_net_pct_html = (f"<span style='color:{GREEN if _net >= 0 else RED};font-weight:800;'>"
                 f"{_net_pct:.0f}% net {'↗' if _net >= 0 else '↘'}</span>")


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
    cur, prev = cur or 0, prev or 0
    diff = cur - prev
    if diff == 0:
        return "<span style='color:#8792A2;font-weight:700;'>0 —</span>"
    color = GREEN if diff > 0 else RED
    arrow = "↗" if diff > 0 else "↘"
    # % only makes sense vs a positive baseline; otherwise show absolute change
    txt = f"{diff / prev * 100:+.0f}%" if prev > 0 else f"{diff:+,}"
    return f"<span style='color:{color};font-weight:700;'>{txt} {arrow}</span>"


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
k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    _kpi("New VRS numbers", vrs_m, f"vs {vrs_p:,} yesterday", BLUE, _delta(vrs_m, vrs_p))
    st.altair_chart(_spark(BLUE, "bar", "VRS"), use_container_width=True)
with k2:
    _kpi("New Convo Now", cn_m, f"vs {cn_p:,} yesterday", GREEN, _delta(cn_m, cn_p))
    st.altair_chart(_spark(GREEN, "line", "ConvoNow"), use_container_width=True)
with k3:
    _kpi("Total new numbers", tot_m, f"vs {tot_p:,} yesterday", CYAN, _delta(tot_m, tot_p))
    st.altair_chart(_spark(CYAN, "area", "Total"), use_container_width=True)
with k4:
    _kpi("New registrations", reg, f"vs {reg_p:,} yesterday", PURPLE, _delta(reg, reg_p))
    st.markdown(f"""<div style="height:6px;background:#EEF1F6;border-radius:4px;margin-top:0.55rem;overflow:hidden;">
      <div style="width:{min(reg/(reg_p or 1)*50,100):.0f}%;height:100%;background:{PURPLE};"></div></div>""",
                unsafe_allow_html=True)
with k5:
    _kpi("Net new", _net, f"vs {_net_prev:,} yesterday {_delta(_net, _net_prev)}",
         GREEN if _net >= 0 else RED, _net_pct_html)
    st.markdown(f"""<div style="height:6px;background:#EEF1F6;border-radius:4px;margin-top:0.55rem;overflow:hidden;">
      <div style="width:{min(max(_net_pct,0),100):.0f}%;height:100%;background:{GREEN if _net >= 0 else RED};"></div></div>""",
                unsafe_allow_html=True)

# ── small stat cards ──────────────────────────────────────────────────────────
st.markdown("<div style='height:0.9rem;'></div>", unsafe_allow_html=True)
def _age_str(days):
    if days is None:
        return "avg age —"
    y = int(days // 365.25)
    rem = days - y * 365.25
    mo = int(rem // 30.44)
    d = int(rem - mo * 30.44)
    parts = [f"{y}y" if y else "", f"{mo}m" if mo else "", f"{d}d" if (d or not (y or mo)) else ""]
    return "avg age " + " ".join(x for x in parts if x)

_po_age = _c.get("portout_age")
_registered_of_new = vrs_m - unreg
stat = [
    ("📥", f"{portin:,} Port-In", "ported in from another provider", BLUE),
    ("📤", f"{portout:,} Port-Out", f"ported to another provider · {_age_str(_po_age)}", AMBER),
    ("🚫", f"{deact:,} Deactivated", f"= {_deact_pct:.0f}% of new adds · deleted today", RED),
    ("⏳", f"{unreg:,} Unregistered", f"of {vrs_m:,} new · {_registered_of_new:,} registered, {unreg:,} not", "#8B5CF6"),
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

# ── port-out detail table ─────────────────────────────────────────────────────
_po_rows = _c.get("portout_rows") or []
if _po_rows:
    st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)
    st.markdown(f"##### Port-Out numbers today · {len(_po_rows):,}")
    st.caption("Age = Ported-Out date − Created date (how long the number was active). Sorted oldest first.")
    _pdf = pd.DataFrame(_po_rows)
    st.dataframe(_pdf, use_container_width=True, hide_index=True,
                 column_config={"Age (days)": st.column_config.NumberColumn("Age (days)", format="%d")})
    st.download_button("📥 Download Port-Out CSV", _pdf.to_csv(index=False),
                       f"port_out_{_c['month_label'].replace(' ', '_')}.csv", "text/csv", key="dl_po")

# ── unregistered (manual) detail table ────────────────────────────────────────
_ur_rows = _c.get("unreg_rows") or []
if _ur_rows:
    st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)
    st.markdown(f"##### Unregistered numbers today · {len(_ur_rows):,}")
    st.caption("Created today with no registration timestamp — typically added manually by "
               "Customer Support (number changes, new Spanish numbers).")
    _urdf = pd.DataFrame(_ur_rows)
    st.dataframe(_urdf, use_container_width=True, hide_index=True)
    st.download_button("📥 Download Unregistered CSV", _urdf.to_csv(index=False),
                       f"unregistered_{_c['month_label'].replace(' ', '_')}.csv", "text/csv", key="dl_ur")

# ── deactivated detail table ──────────────────────────────────────────────────
_de_rows = _c.get("deact_rows") or []
if _de_rows:
    st.markdown("<div style='height:1.2rem;'></div>", unsafe_allow_html=True)
    st.markdown(f"##### Deactivated numbers today · {len(_de_rows):,}")
    st.caption("Numbers deactivated today, with lifespan age (Deactivated − Created). Sorted oldest first.")
    _dedf = pd.DataFrame(_de_rows)
    st.dataframe(_dedf, use_container_width=True, hide_index=True,
                 column_config={"Age (days)": st.column_config.NumberColumn("Age (days)", format="%d")})
    st.download_button("📥 Download Deactivated CSV", _dedf.to_csv(index=False),
                       f"deactivated_{_c['month_label'].replace(' ', '_')}.csv", "text/csv", key="dl_de")

st.caption("Scoped to **today**. Use **This Month** for the month or **Overview** for all-time totals.")
