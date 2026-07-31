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
    """Seconds from a Wait Time value. Handles 'HH:MM:SS' and 'Missed · Wait: HH:MM:SS'.
    Old-format bare 'Missed' (no duration) returns None."""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "n/a", "none"):
        return None
    low = s.lower()
    if "wait:" in low:                       # 'Missed · Wait: 00:02:21'
        s = s[low.index("wait:") + 5:].strip()
    elif low.startswith("missed"):           # old format: flagged missed, no duration
        return None
    try:
        parts = [int(x) for x in s.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        return None


def _is_missed(v):
    return str(v).strip().lower().startswith("missed")


def _fmt_wait(sec):
    """Friendly wait label: '—' for none/zero, '9s', '2m 21s' otherwise (matches CONVO360)."""
    if sec is None or (isinstance(sec, float) and pd.isna(sec)):
        return "—"
    sec = int(round(sec))
    if sec <= 0:
        return "—"
    m, s = divmod(sec, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _agent_label(v):
    """Blank/placeholder agent = the interaction never connected to a rep."""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "-", "—", "unassigned", "n/a", "na", "unknown"):
        return "Missed"
    return s


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
st.caption("Build: v6 (overall KPIs: handled/missed/AHT/call time)")

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
if wait_col:
    k["_missed"] = k[wait_col].map(_is_missed)
    _wsec = k[wait_col].map(_wait_secs)            # seconds for answered AND new-format missed
    k["_wait"] = _wsec.where(~k["_missed"])        # answered speed-of-answer
    k["_wait_missed"] = _wsec.where(k["_missed"])  # how long missed callers waited (if recorded)
else:
    k["_missed"] = False
    k["_wait"] = None
    k["_wait_missed"] = None
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
m3.metric("Total handle time", f"{total_hrs:,.1f} hrs" if total_hrs is not None else "—",
          help=f"{k['_dur'].sum():,.0f} total minutes" if dur_col else None)
m4.metric("Avg wait (answered)", f"{avg_wait:.0f} sec" if avg_wait is not None else "—",
          help="Speed of answer — averaged over answered interactions only. "
               "Missed calls carry no wait duration in the source, so they're excluded.")

# ── Overall KPIs: Handled · Missed · AHT · Avg call time with consumers ──────────
_iscall_all = k["_type"].str.contains("call", case=False, na=False) if type_col else pd.Series(False, index=k.index)
_missed_all = k["_missed"] if "_missed" in k.columns else pd.Series(False, index=k.index)
handled = int((~_missed_all).sum())
missed_all = int(_missed_all.sum())
call_time = k.loc[_iscall_all & ~_missed_all, "_dur"].mean() if dur_col else None

st.markdown("##### Overall")
o1, o2, o3, o4 = st.columns(4)
o1.metric("🎧 Handled", f"{handled:,}", help="Interactions that connected to a rep (all types).")
o2.metric("📵 Missed", f"{missed_all:,}",
          help="Calls flagged Missed in the export (no rep connected).")
o3.metric("⏱️ Overall AHT", f"{aht:.1f} min" if aht is not None else "—",
          help="Average handle time across all interactions.")
o4.metric("📞 Avg call time (consumers)", f"{call_time:.1f} min" if call_time and call_time == call_time else "—",
          help="Average talk time on answered Call / Video calls — actual time spent with consumers.")

# ── Missed calls (Wait Time = 'Missed' flag in the CONVO360 export) ──────────
# The source flags a missed CALL by writing 'Missed' in the Wait Time column.
# Only calls can be missed; chats/queries are async and never 'missed'.
if wait_col:
    is_call = k["_type"].str.contains("call", case=False, na=False)
    call_attempts = int(is_call.sum())
    missed = int(k["_missed"].sum())
    answered = call_attempts - missed
    ans_rate = answered / call_attempts * 100 if call_attempts else 0
    miss_rate = missed / call_attempts * 100 if call_attempts else 0

    mwait = k.loc[k["_missed"], "_wait_missed"].dropna()

    st.markdown("##### Call answer performance")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("✅ Answered calls", f"{answered:,}")
    cc1.caption(f"{ans_rate:.1f}% of {call_attempts:,} call attempts")
    cc2.metric("📵 Missed calls", f"{missed:,}")
    cc2.caption(f"{miss_rate:.1f}% of call attempts")
    cc3.metric("Answer rate", f"{ans_rate:.1f}%")
    cc3.caption(f"{answered:,} of {call_attempts:,} calls answered")
    if len(mwait):
        cc4.metric("⏱️ Missed avg wait", _fmt_wait(mwait.mean()))
        cc4.caption(f"median {_fmt_wait(mwait.median())} · {len(mwait):,} missed")
    else:
        cc4.metric("⏱️ Missed avg wait", "—")
        cc4.caption("no wait logged in this export")

    # Missed-call wait time (only present in exports that log 'Missed · Wait: HH:MM:SS')
    if len(mwait):
        st.markdown("###### How long missed callers waited")
        mw1, mw2, mw3, mw4 = st.columns(4)
        mw1.metric("Avg wait — missed", _fmt_wait(mwait.mean()),
                   help=f"across {len(mwait):,} missed calls with a logged wait. "
                        "Skewed by long outliers — prefer the median.")
        mw2.metric("Median wait — missed", _fmt_wait(mwait.median()),
                   help="Half of missed callers waited less than this — the representative number.")
        mw3.metric("Longest missed wait", _fmt_wait(mwait.max()))
        mw4.metric("Total wait abandoned", f"{mwait.sum()/60:.1f} min")

    # Missed calls detail
    md = k[k["_missed"]].copy()
    if not md.empty:
        st.markdown("###### Missed calls")
        _mcols, _mren = ["_type"], {"_type": "Type"}
        _cust_m = next((c for c in df.columns if c.lower() in ("customer name", "name")), None)
        _date_m = next((c for c in df.columns if "date" in c.lower()), None)
        for _c in (_cust_m, _date_m):
            if _c and _c not in _mcols:
                _mcols.append(_c)
        md["Wait"] = md["_wait_missed"].map(_fmt_wait)
        _mcols.append("Wait")
        if md["_wait_missed"].notna().any():
            md = md.sort_values("_wait_missed", ascending=False)
        elif _date_m:
            md = md.sort_values(_date_m)
        st.dataframe(md[_mcols].rename(columns=_mren),
                     use_container_width=True, hide_index=True, height=300)
        if not md["_wait_missed"].notna().any():
            st.caption("This export flags missed calls without a wait duration, so the wait columns are blank.")

    _agent_m = next((c for c in df.columns if c.lower() == "agent"), None)
    _date_m2 = next((c for c in df.columns if "date" in c.lower()), None)
    _hour_m = next((c for c in df.columns if c.lower() == "hour"), None)
    calls_df = k[is_call].copy()

    # Answered vs missed by call type
    st.markdown("###### Answered vs missed by call type")
    _bt = (calls_df.assign(_status=calls_df["_missed"].map({True: "Missed", False: "Answered"}))
           .groupby(["_type", "_status"]).size().reset_index(name="n"))
    piv = _bt.pivot(index="_type", columns="_status", values="n").fillna(0).reset_index()
    for _c in ("Answered", "Missed"):
        if _c not in piv.columns:
            piv[_c] = 0
    piv["Attempts"] = piv["Answered"] + piv["Missed"]
    piv["Answer rate %"] = (piv["Answered"] / piv["Attempts"] * 100).round(1)
    piv = piv.rename(columns={"_type": "Type"}).sort_values("Attempts", ascending=False)
    st.dataframe(piv[["Type", "Attempts", "Answered", "Missed", "Answer rate %"]],
                 use_container_width=True, hide_index=True)

    # Speed of answer by rep (answered calls only)
    if _agent_m:
        st.markdown("###### Speed of answer by rep (answered calls)")
        ans = calls_df[~calls_df["_missed"]].copy()
        ans["_ag"] = ans[_agent_m].map(_agent_label)
        rr = (ans.dropna(subset=["_wait"]).groupby("_ag")
              .agg(**{"Answered calls": ("_wait", "size"),
                      "Avg wait (sec)": ("_wait", "mean"),
                      "Max wait (sec)": ("_wait", "max")})
              .reset_index().rename(columns={"_ag": "Agent"}))
        rr["Avg wait (sec)"] = rr["Avg wait (sec)"].round(0)
        rr["Max wait (sec)"] = rr["Max wait (sec)"].round(0)
        rr = rr.sort_values("Avg wait (sec)", ascending=False)
        st.dataframe(rr, use_container_width=True, hide_index=True)

    # Missed calls & answer rate by day
    if _date_m2:
        st.markdown("###### Missed calls & answer rate by day")
        cc = calls_df.copy()
        cc["_day"] = pd.to_datetime(cc[_date_m2], errors="coerce").dt.date
        g = (cc.dropna(subset=["_day"]).groupby("_day")
             .agg(Attempts=("_missed", "size"), Missed=("_missed", "sum")).reset_index())
        g["Answered"] = g["Attempts"] - g["Missed"]
        g["Answer rate %"] = (g["Answered"] / g["Attempts"] * 100).round(1)
        ch = (alt.Chart(g).mark_bar(color="#C0392B")
              .encode(x=alt.X("_day:T", title="Day"),
                      y=alt.Y("Missed:Q", title="Missed calls"),
                      tooltip=["_day:T", "Attempts:Q", "Missed:Q", "Answer rate %:Q"])
              .properties(height=220))
        st.altair_chart(ch, use_container_width=True)

    # Missed by agent + by hour
    mc = calls_df[calls_df["_missed"]].copy()
    _c_a, _c_h = st.columns(2)
    if _agent_m and not mc.empty:
        with _c_a:
            st.markdown("###### Missed calls by agent")
            ga = (mc.assign(_ag=mc[_agent_m].map(_agent_label))
                  .groupby("_ag").size().reset_index(name="Missed")
                  .rename(columns={"_ag": "Agent"}).sort_values("Missed", ascending=False))
            st.dataframe(ga, use_container_width=True, hide_index=True)
    if _hour_m and not mc.empty:
        with _c_h:
            st.markdown("###### Missed calls by hour")
            gh = mc.groupby(_hour_m).size().reset_index(name="Missed").rename(columns={_hour_m: "Hour"})
            ch2 = (alt.Chart(gh).mark_bar(color="#C0392B")
                   .encode(x=alt.X("Hour:O", title="Hour of day"), y="Missed:Q",
                           tooltip=["Hour", "Missed"])
                   .properties(height=220))
            st.altair_chart(ch2, use_container_width=True)

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

# Wait time by interaction type — how long customers wait before each type
if wait_col:
    st.markdown("##### Wait time by interaction type")
    wt = (k.dropna(subset=["_wait"]).groupby("_type")
            .agg(Interactions=("_wait", "size"),
                 **{"Avg wait (sec)": ("_wait", "mean"),
                    "Max wait (sec)": ("_wait", "max")})
            .reset_index().rename(columns={"_type": "Type"}))
    wt["Avg wait (sec)"] = wt["Avg wait (sec)"].round(0)
    wt["Max wait (sec)"] = wt["Max wait (sec)"].round(0)
    wt = wt.sort_values("Avg wait (sec)", ascending=False)
    w1, w2 = st.columns([2, 3])
    with w1:
        st.dataframe(wt, use_container_width=True, hide_index=True)
    with w2:
        wchart = (alt.Chart(wt).mark_bar(color="#C8792B", cornerRadiusEnd=4)
                  .encode(
                      x=alt.X("Avg wait (sec):Q", title="Avg wait (sec)"),
                      y=alt.Y("Type:N", sort="-x", title=None),
                      tooltip=["Type", "Interactions", "Avg wait (sec)", "Max wait (sec)"])
                  .properties(height=200))
        st.altair_chart(wchart, use_container_width=True)

    # Longest-wait interactions
    st.markdown("##### Longest-wait interactions")
    lw = k.dropna(subset=["_wait"]).copy()
    lw["Wait"] = lw["_wait"].map(_fmt_wait)
    _agent_c0 = next((c for c in df.columns if c.lower() == "agent"), None)
    if _agent_c0:
        lw[_agent_c0] = lw[_agent_c0].map(_agent_label)
    _cols = ["Wait", "_type"]
    _rename = {"_type": "Type"}
    _agent0 = next((c for c in df.columns if c.lower() == "agent"), None)
    _cust0 = next((c for c in df.columns if c.lower() in ("customer name", "name")), None)
    _date0 = next((c for c in df.columns if "date" in c.lower()), None)
    for _c in (_agent0, _cust0, _date0):
        if _c and _c not in _cols:
            _cols.append(_c)
    lw = lw.sort_values("_wait", ascending=False).head(15)[_cols].rename(columns=_rename)
    st.dataframe(lw, use_container_width=True, hide_index=True)

# Per-rep — how long each rep works
agent_c = next((c for c in df.columns if c.lower() == "agent"), None)
if agent_c and dur_col:
    st.markdown("##### Per-rep handle time")
    rep = (k.assign(_agent=k[agent_c].map(_agent_label))
             .groupby("_agent")
             .agg(Interactions=("_agent", "size"),
                  **{"Total minutes": ("_dur", "sum"),
                     "Avg handle (min)": ("_dur", "mean"),
                     "Avg wait (sec)": ("_wait", "mean")})
             .reset_index().rename(columns={"_agent": "Agent"}))
    rep["Total hours"] = (rep["Total minutes"] / 60).round(1)
    rep["Total minutes"] = rep["Total minutes"].round(0)
    rep["Avg handle (min)"] = rep["Avg handle (min)"].round(1)
    rep["Avg wait (sec)"] = rep["Avg wait (sec)"].round(0)
    rep = rep.sort_values("Total minutes", ascending=False)[
        ["Agent", "Interactions", "Total hours", "Total minutes", "Avg handle (min)", "Avg wait (sec)"]]
    st.dataframe(rep, use_container_width=True, hide_index=True)

    # ── Top / lowest performing agents (by interactions handled) ──
    perf = rep[~rep["Agent"].eq("Missed")].copy()
    if len(perf) >= 2:
        st.markdown("##### Agent performance")
        st.caption("Ranked by interactions handled (volume). Avg handle & avg speed-of-answer shown for context.")
        top = perf.sort_values("Interactions", ascending=False).head(3)
        low = perf.sort_values("Interactions", ascending=True).head(3)
        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown("**🏆 Top performers**")
            for _, r in top.iterrows():
                st.metric(r["Agent"], f"{int(r['Interactions']):,} handled",
                          help=f"{r['Total hours']} hrs · avg handle {r['Avg handle (min)']} min "
                               f"· speed of answer {int(r['Avg wait (sec)']) if pd.notna(r['Avg wait (sec)']) else '—'} sec")
        with pc2:
            st.markdown("**🐢 Lowest volume**")
            for _, r in low.iterrows():
                st.metric(r["Agent"], f"{int(r['Interactions']):,} handled",
                          help=f"{r['Total hours']} hrs · avg handle {r['Avg handle (min)']} min "
                               f"· speed of answer {int(r['Avg wait (sec)']) if pd.notna(r['Avg wait (sec)']) else '—'} sec")

st.download_button("📥 Export interactions CSV", df.to_csv(index=False),
                   f"convo360_{datetime.now():%Y%m%d}.csv", "text/csv")

report_header_close()
