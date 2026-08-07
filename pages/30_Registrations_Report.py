import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import date, datetime, timedelta, timezone
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Registrations Report", layout="wide", page_icon="📝")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Registrations Report")

report_header("Registrations Report",
              "New registrations over time — daily, weekly, monthly, quarterly, yearly",
              section="Numbers")

REG_OBJECT = "2-58833629"


# ── central-time helpers (match HubSpot report boundaries) ──────────────────
def _ct_offset(m):
    return -5 if 3 <= m <= 11 else -6  # CDT Mar–Nov else CST


def _ms(d, end=False):
    off = _ct_offset(d.month)
    t = datetime(d.year, d.month, d.day, 23, 59, 59 if end else 0,
                 0 if end else 0, tzinfo=timezone(timedelta(hours=off)))
    if not end:
        t = t.replace(hour=0, minute=0, second=0)
    return str(int(t.timestamp() * 1000))


@st.cache_data(ttl=1800, show_spinner=False)
def _date_prop():
    """Pick the registration date field that exists (registered_at, else create date)."""
    for cand in ("registered_at", "hs_createdate", "createdate"):
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{REG_OBJECT}/search", headers=_H,
                              json={"limit": 1, "properties": [cand],
                                    "sorts": [{"propertyName": cand, "direction": "DESCENDING"}]},
                              timeout=20)
            if r.status_code == 200:
                return cand
        except Exception:
            pass
    return "hs_createdate"


@st.cache_data(ttl=1800, show_spinner=False)
def _extra_props(date_prop):
    """Which optional descriptive fields exist on the Registrations object."""
    out = []
    for cand in ("number", "email", "type_of_registration", "service_type", "usage_type"):
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{REG_OBJECT}/search", headers=_H,
                              json={"limit": 1, "properties": [cand]}, timeout=15)
            if r.status_code == 200:
                out.append(cand)
        except Exception:
            pass
    return out


def _seek(date_prop, props, start_ms, end_ms, label):
    url = f"{_B}/crm/v3/objects/{REG_OBJECT}/search"
    results, last = [], "0"
    ph = st.empty()
    allprops = list(dict.fromkeys(props + [date_prop]))
    while True:
        body = {"limit": 100, "properties": allprops,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": [
                    {"propertyName": date_prop, "operator": "GTE", "value": start_ms},
                    {"propertyName": date_prop, "operator": "LTE", "value": end_ms},
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = requests.post(url, headers=_H, json=body, timeout=30)
        if r.status_code != 200:
            ph.empty()
            st.error(f"HubSpot error {r.status_code}: {r.text[:200]}")
            break
        batch = r.json().get("results", [])
        results.extend(batch)
        ph.caption(f"{label} {len(results):,} registrations…")
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"])
    ph.empty()
    return results


def _parse(v):
    if not v:
        return None
    try:
        s = str(v)
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ── period presets ──────────────────────────────────────────────────────────
def _range(preset, today=None):
    t = today or date.today()
    q = (t.month - 1) // 3
    if preset == "Today":
        return t, t
    if preset == "Yesterday":
        y = t - timedelta(days=1); return y, y
    if preset == "This Week":
        s = t - timedelta(days=t.weekday()); return s, t
    if preset == "This Month":
        return t.replace(day=1), t
    if preset == "This Quarter":
        return date(t.year, q * 3 + 1, 1), t
    if preset == "This Year":
        return date(t.year, 1, 1), t
    if preset == "Last Month":
        first = t.replace(day=1); le = first - timedelta(days=1)
        return le.replace(day=1), le
    if preset == "Last Quarter":
        ly, lq = (t.year, q - 1) if q > 0 else (t.year - 1, 3)
        s = date(ly, lq * 3 + 1, 1)
        em, ey = (s.month + 3, s.year)
        if em > 12: em, ey = em - 12, ey + 1
        return s, date(ey, em, 1) - timedelta(days=1)
    if preset == "Last Year":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    return t.replace(day=1), t


PRESETS = ["Today", "Yesterday", "This Week", "This Month", "This Quarter", "This Year",
           "Last Month", "Last Quarter", "Last Year", "Custom"]

c1, c2 = st.columns([2, 1])
with c1:
    preset = st.selectbox("Period", PRESETS, index=3)
    start_d, end_d = _range(preset)
    if preset == "Custom":
        dr = st.date_input("Custom range", value=(start_d, end_d))
        if isinstance(dr, tuple) and len(dr) == 2:
            start_d, end_d = dr
with c2:
    st.markdown("<div style='margin-top:1.7rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run", type="primary", use_container_width=True)

st.caption(f"Showing registrations from **{start_d:%b %d, %Y}** to **{end_d:%b %d, %Y}**.")

_key = f"registrations_{start_d:%Y%m%d}_{end_d:%Y%m%d}"

if run:
    date_prop = _date_prop()
    props = _extra_props(date_prop)
    with dash_spinner("Fetching registrations…"):
        recs = _seek(date_prop, props, _ms(start_d), _ms(end_d, end=True), "Loaded")
    rows = []
    for r in recs:
        p = r.get("properties", {})
        dt = _parse(p.get(date_prop))
        rows.append({
            "Registered": dt.strftime("%Y-%m-%d") if dt else "",
            "Number": p.get("number", "") if "number" in props else "",
            "Email": (p.get("email") or "") if "email" in props else "",
            "Type": (p.get("type_of_registration") or "—") if "type_of_registration" in props else "—",
            "Service": (p.get("service_type") or "—") if "service_type" in props else "—",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "start": str(start_d), "end": str(end_d), "date_prop": date_prop})

saved = load_report(_key)
if saved is None:
    st.info("Pick a period and click **Run**. Results are saved and reload automatically.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · date field: `{saved.get('date_prop','')}`")

if df.empty:
    st.warning("No registrations in this period.")
    report_header_close(); st.stop()

# ── KPIs ──
total = len(df)
_d = pd.to_datetime(df["Registered"], errors="coerce")
span_days = (end_d - start_d).days + 1
per_day = total / span_days if span_days else total
k1, k2, k3 = st.columns(3)
k1.metric("Total registrations", f"{total:,}")
k2.metric("Avg per day", f"{per_day:,.1f}")
k3.metric("Days in period", f"{span_days:,}")

# ── trend (bucket by span) ──
st.markdown("##### Registrations over time")
tmp = df.assign(_dt=_d).dropna(subset=["_dt"])
if span_days <= 45:
    tmp["Bucket"] = tmp["_dt"].dt.date.astype(str); _title = "By day"
elif span_days <= 210:
    tmp["Bucket"] = tmp["_dt"].dt.to_period("W").apply(lambda p: p.start_time.date().isoformat()); _title = "By week"
else:
    tmp["Bucket"] = tmp["_dt"].dt.to_period("M").astype(str); _title = "By month"
trend = tmp.groupby("Bucket").size().reset_index(name="Registrations")
ch = (alt.Chart(trend).mark_bar(color="#0D3B26", cornerRadiusEnd=3)
      .encode(x=alt.X("Bucket:N", title=_title, sort=None),
              y=alt.Y("Registrations:Q"),
              tooltip=["Bucket", "Registrations"])
      .properties(height=260))
st.altair_chart(ch, use_container_width=True)

# ── by type ──
if (df["Type"] != "—").any():
    st.markdown("##### By registration type")
    bt = df.groupby("Type").size().reset_index(name="Registrations").sort_values("Registrations", ascending=False)
    st.dataframe(bt, use_container_width=True, hide_index=True)

# ── detail ──
st.markdown("##### Registration detail")
_cols = [c for c in ["Registered", "Number", "Email", "Type", "Service"] if c in df.columns]
st.dataframe(df.sort_values("Registered", ascending=False)[_cols],
             use_container_width=True, hide_index=True, height=420)
st.download_button("📥 Export CSV", df.to_csv(index=False),
                   f"registrations_{start_d:%Y%m%d}_{end_d:%Y%m%d}.csv", "text/csv")

report_header_close()
