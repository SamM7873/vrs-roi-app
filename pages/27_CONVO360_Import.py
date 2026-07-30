import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, date, timedelta

TYPE_LABEL = {
    "CALL": "Call",
    "CHAT": "Chat",
    "QUERY": "Query (text)",
    "SIP_VIDEO_CALL": "Video call (SIP)",
    "SIP_AUDIO_CALL": "Audio call (SIP)",
    "SIP_VOICE_CALL": "Voice call (SIP)",
    "SIP_CALL": "SIP call",
    "SIP": "SIP call",
}


def _type_label(v):
    key = str(v).strip().upper()
    if key in TYPE_LABEL:
        return TYPE_LABEL[key]
    if key.startswith("SIP"):  # any other SIP_* variant stays readable + tagged
        rest = key.replace("SIP_", "").replace("_", " ").title()
        return f"{rest} (SIP)" if rest else "SIP call"
    return str(v).strip() or "—"


def _wait_secs(v):
    try:
        parts = [int(x) for x in str(v).split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        return None


def _preset_range(preset, today=None):
    """Return (start_date, end_date) inclusive for a named preset, or (None, None) for all time."""
    today = today or date.today()
    if preset == "Daily (today)":
        return today, today
    if preset == "Weekly (this week)":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if preset == "This month":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1)
    if preset == "Last month":
        first = today.replace(day=1)
        last_end = first - timedelta(days=1)
        return last_end.replace(day=1), last_end
    if preset == "Last quarter":
        q = (today.month - 1) // 3
        y, lq = today.year, q - 1
        if lq < 0:
            lq, y = 3, y - 1
        start = date(y, lq * 3 + 1, 1)
        em, ey = start.month + 3, start.year
        if em > 12:
            em, ey = em - 12, ey + 1
        return start, date(ey, em, 1) - timedelta(days=1)
    return None, None  # All time / Custom


from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   log_report_view, save_report, load_report, saved_at_label)

st.set_page_config(page_title="CONVO360 Import", layout="wide", page_icon="📥")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("CONVO360 Import")

report_header("CONVO360 Import & AHT",
              "Upload the CONVO360 interaction CSV, then review AHT / KPI audit by date range",
              section="Tools")

# ── UI ────────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Upload CONVO360 interaction CSV", type=["csv"])
run = st.button("Import & save", type="primary", disabled=not uploaded)

if run:
    with st.spinner("Importing CSV…"):
        df = pd.read_csv(uploaded)
        df.columns = [c.strip() for c in df.columns]
        save_report("convo360", {"df": df})
    st.success("Imported and saved — the audit below now reloads automatically without re-running.")

# ── render saved import (survives reloads; no need to click again) ──────────────
saved = load_report("convo360")
if saved is None:
    st.info("Upload the CONVO360 CSV and click **Import & save** once. "
            "Results are saved and reload automatically on every visit.")
    report_header_close()
    st.stop()

df = saved["df"]
_c1, _c2 = st.columns([4, 1])
_c1.caption(f"Showing saved import from {saved_at_label(saved)} · {len(df):,} interactions")
if _c2.button("🔄 Re-run / clear", help="Clear the saved import so you can upload a fresh CSV"):
    import os as _os
    from utils import REPORT_CACHE_DIR
    try:
        _os.remove(_os.path.join(REPORT_CACHE_DIR, "convo360.pkl"))
    except Exception:
        pass
    st.rerun()

# ── date-range preset ──
date_col = next((c for c in df.columns if "date" in c.lower()), None)
PRESETS = ["All time", "Daily (today)", "Weekly (this week)", "This month", "Last month",
           "Last quarter", "Custom"]
preset = st.radio("Date range", PRESETS, horizontal=True)
start_d, end_d = _preset_range(preset)
if preset == "Custom":
    _dr = st.date_input("Pick a custom range", value=())
    if isinstance(_dr, tuple) and len(_dr) == 2:
        start_d, end_d = _dr

if date_col:
    _dt = pd.to_datetime(df[date_col], errors="coerce")
    if start_d and end_d:
        mask = _dt.dt.date.between(start_d, end_d)
        df = df[mask].reset_index(drop=True)
        st.caption(f"Filtered to {start_d:%b %d, %Y} → {end_d:%b %d, %Y} · {len(df):,} interactions")

if df.empty:
    st.warning("No interactions in the selected date range.")
    report_header_close()
    st.stop()

# ── AHT / KPI audit ─────────────────────────────────────────────────────────
st.markdown("### ⏱️ AHT / KPI audit")

dur_col = next((c for c in df.columns if "duration" in c.lower()), None)
wait_col = next((c for c in df.columns if "wait" in c.lower()), None)
type_col = next((c for c in df.columns if c.lower() == "type"), None)

k = df.copy()
k["_dur"] = pd.to_numeric(k[dur_col], errors="coerce") if dur_col else None
k["_wait"] = k[wait_col].map(_wait_secs) if wait_col else None
if type_col:
    k["_type"] = k[type_col].map(_type_label)
else:
    k["_type"] = "—"

n_int = len(k)
aht = k["_dur"].mean() if dur_col else None
total_hrs = k["_dur"].sum() / 60 if dur_col else None
avg_wait = k["_wait"].mean() if wait_col else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total interactions", f"{n_int:,}")
m2.metric("AHT (avg handle)", f"{aht:.1f} min" if aht is not None else "—")
m3.metric("Total handle time", f"{total_hrs:,.0f} hrs" if total_hrs is not None else "—")
m4.metric("Avg wait time", f"{avg_wait/60:.1f} min" if avg_wait is not None else "—")

# Duration by interaction type — how long is a chat vs video vs call
if dur_col:
    st.markdown("##### Handle time by interaction type")
    bt = (k.groupby("_type")
            .agg(Interactions=("_type", "size"),
                 **{"Avg handle (min)": ("_dur", "mean"),
                    "Total minutes": ("_dur", "sum")})
            .reset_index().rename(columns={"_type": "Type"}))
    bt["Avg handle (min)"] = bt["Avg handle (min)"].round(1)
    bt["Total minutes"] = bt["Total minutes"].round(0)
    bt = bt.sort_values("Total minutes", ascending=False)
    c1, c2 = st.columns([2, 3])
    with c1:
        st.dataframe(bt, use_container_width=True, hide_index=True)
    with c2:
        chart = (alt.Chart(bt).mark_bar(color="#0D3B26", cornerRadiusEnd=4)
                 .encode(
                     x=alt.X("Avg handle (min):Q", title="Avg handle (min)"),
                     y=alt.Y("Type:N", sort="-x", title=None),
                     tooltip=["Type", "Interactions", "Avg handle (min)", "Total minutes"])
                 .properties(height=200))
        st.altair_chart(chart, use_container_width=True)

# Per-rep — how long each rep works
agent_c = next((c for c in df.columns if c.lower() == "agent"), None)
if agent_c and dur_col:
    st.markdown("##### Per-rep handle time")
    rep = (k.assign(_agent=k[agent_c].fillna("—"))
             .groupby("_agent")
             .agg(Interactions=("_agent", "size"),
                  **{"Total minutes": ("_dur", "sum"),
                     "Avg handle (min)": ("_dur", "mean"),
                     "Avg wait (min)": ("_wait", "mean")})
             .reset_index().rename(columns={"_agent": "Agent"}))
    rep["Total hours"] = (rep["Total minutes"] / 60).round(1)
    rep["Total minutes"] = rep["Total minutes"].round(0)
    rep["Avg handle (min)"] = rep["Avg handle (min)"].round(1)
    rep["Avg wait (min)"] = (rep["Avg wait (min)"] / 60).round(1)
    rep = rep.sort_values("Total minutes", ascending=False)[
        ["Agent", "Interactions", "Total hours", "Total minutes", "Avg handle (min)", "Avg wait (min)"]]
    st.dataframe(rep, use_container_width=True, hide_index=True)

st.download_button("📥 Export interactions CSV", df.to_csv(index=False),
                   f"convo360_{datetime.now():%Y%m%d}.csv", "text/csv")

report_header_close()
