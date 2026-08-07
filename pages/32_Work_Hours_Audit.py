import streamlit as st
import pandas as pd
from datetime import datetime
import time
from utils import (require_auth, is_app_admin, COMMON_CSS,
                   report_header, report_header_close, log_report_view,
                   save_report, load_report, saved_at_label)

SAVE_KEY = "work_hours_audit"
RETENTION_OPTS = {"24 hours": 24, "48 hours": 48, "7 days": 168}   # label -> hours

st.set_page_config(page_title="Work-Hours Audit", layout="wide", page_icon="⏱️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Work-Hours Audit")

report_header("Work-Hours Audit",
              "Daily working span per user from the HubSpot audit log export",
              section="Admin")

if not is_app_admin():
    st.warning("This page is restricted to administrators.")
    report_header_close(); st.stop()

st.markdown(
    "Export your HubSpot **user audit log** "
    "(Settings → Account Defaults → **Review account's audit log** → Export) and upload the CSV. "
    "This measures each person's **first-to-last recorded action per day** — a proxy for workday "
    "length. Breaks inside that window are included; work outside HubSpot is not counted.")

c_up, c_ret = st.columns([3, 1])
with c_up:
    up = st.file_uploader("HubSpot audit log CSV", type=["csv"], key="wh_csv")
with c_ret:
    _ret_label = st.selectbox("Keep report for", list(RETENTION_OPTS.keys()),
                              index=1, key="wh_ret",
                              help="How long the uploaded report stays saved before it expires.")
TTL_SECONDS = RETENTION_OPTS[_ret_label] * 3600


def _hm(h):
    return f"{int(h)}h {int(round((h - int(h)) * 60)):02d}m"


def _parse(file):
    """Return (day-span df `g`, event_count, first_day, last_day) or an error string."""
    raw = pd.read_csv(file, dtype=str).fillna("")
    _user_col = next((c for c in raw.columns if c.strip().lower() in
                      ("modified by", "user", "performed by")), None)
    _date_col = next((c for c in raw.columns if "date" in c.strip().lower()), None)
    if not _user_col or not _date_col:
        return f"Couldn't find user / date columns. Found: {list(raw.columns)}"
    raw["_u"] = raw[_user_col].str.strip()
    raw["_t"] = pd.to_datetime(raw[_date_col].str.strip(), errors="coerce")
    raw = raw[(raw["_u"] != "") & raw["_t"].notna()].copy()
    raw["_day"] = raw["_t"].dt.date
    g = raw.groupby(["_u", "_day"]).agg(
        start=("_t", "min"), end=("_t", "max"), events=("_t", "size")).reset_index()
    g["span_h"] = (g["end"] - g["start"]).dt.total_seconds() / 3600.0
    return g, len(raw), raw["_day"].min(), raw["_day"].max()


# On upload: parse + persist for 48h. Otherwise fall back to a saved report if fresh.
if up is not None:
    res = _parse(up)
    if isinstance(res, str):
        st.error(res); report_header_close(); st.stop()
    g, n_events, d0, d1 = res
    save_report(SAVE_KEY, {"g": g, "n_events": n_events, "d0": d0, "d1": d1})
    saved = load_report(SAVE_KEY)
else:
    saved = load_report(SAVE_KEY)
    if saved is None:
        st.info("Upload the audit CSV to see the report.")
        report_header_close(); st.stop()
    age = time.time() - (saved.get("saved_at") or 0)
    if age > TTL_SECONDS:
        st.warning(f"The saved report is older than {_ret_label} — please upload a fresh export.")
        report_header_close(); st.stop()
    g, n_events, d0, d1 = saved["g"], saved["n_events"], saved["d0"], saved["d1"]

# saved-state banner (retention window)
if saved and saved.get("saved_at"):
    _rem = max(0, TTL_SECONDS - (time.time() - saved["saved_at"]))
    _left = f"~{int(_rem // 3600)}h left" if _rem >= 3600 else f"~{int(_rem // 60)}m left"
    st.caption(f"📌 Saved {saved_at_label(saved)} · kept for {_ret_label} "
               f"({_left}) · upload a new CSV to replace it.")

MIN_DAY = st.slider("Ignore days shorter than (hours) — filters out quick check-ins",
                    0.0, 3.0, 1.0, 0.5, key="wh_min")
clean = g[g["span_h"] >= MIN_DAY].copy()
if clean.empty:
    st.warning("No qualifying working days after the filter.")
    report_header_close(); st.stop()

st.caption(f"Window **{d0:%b %d}–{d1:%b %d, %Y}** · {n_events:,} events · "
           f"{clean['_u'].nunique()} users · times as exported (account timezone).")

# ── per-user summary ─────────────────────────────────────────────────────────
summ = (clean.groupby("_u")
        .agg(Days=("span_h", "size"), Avg=("span_h", "mean"),
             Median=("span_h", "median"), Longest=("span_h", "max"),
             Total=("span_h", "sum"))
        .reset_index().sort_values("Avg", ascending=False))


def _tag(h):
    return "🟢 Full" if h >= 8 else ("🟡 Steady" if h >= 6.5 else "🔴 Short")


st.markdown("##### Average workday per user")
cols = st.columns(len(summ))
for col, (_, r) in zip(cols, summ.iterrows()):
    col.metric(r["_u"].split("@")[0], _hm(r["Avg"]), _tag(r["Avg"]))

show = summ.copy()
show["User"] = show["_u"].str.split("@").str[0]
for c in ("Avg", "Median", "Longest"):
    show[c] = show[c].map(_hm)
show["Total"] = show["Total"].map(lambda h: f"{h:.0f}h")
show = show[["User", "Days", "Avg", "Median", "Longest", "Total"]]
st.dataframe(show, use_container_width=True, hide_index=True)

st.caption("🟢 ≥ 8h · 🟡 6.5–8h · 🔴 < 6.5h average day. "
           "Median is a better guide than average when someone has a few very long or short days.")

# ── day-by-day detail ────────────────────────────────────────────────────────
st.markdown("##### Day-by-day detail")
who = st.selectbox("User", sorted(clean["_u"].unique()), key="wh_who")
det = g[g["_u"] == who].sort_values("_day").copy()
det["Date"] = det["_day"].map(lambda d: d.strftime("%a %b %d"))
det["Start"] = det["start"].dt.strftime("%H:%M")
det["End"] = det["end"].dt.strftime("%H:%M")
det["Span"] = det["span_h"].map(_hm)
det["Type"] = det["span_h"].map(lambda h: "check-in" if h < MIN_DAY else _tag(h)[2:])
det["Events"] = det["events"]
st.dataframe(det[["Date", "Start", "End", "Span", "Type", "Events"]],
             use_container_width=True, hide_index=True, height=460)

# ── exports ──────────────────────────────────────────────────────────────────
csv_all = g.assign(user=g["_u"], day=g["_day"].astype(str),
                   span_hours=g["span_h"].round(2))[
    ["user", "day", "start", "end", "span_hours", "events"]]
st.download_button("📥 Download all day-spans (CSV)", csv_all.to_csv(index=False),
                   "work_hours_by_day.csv", "text/csv")

report_header_close()
