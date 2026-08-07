import streamlit as st
import pandas as pd
from datetime import datetime
from utils import (require_auth, is_app_admin, COMMON_CSS,
                   report_header, report_header_close, log_report_view)

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

up = st.file_uploader("HubSpot audit log CSV", type=["csv"], key="wh_csv")
if up is None:
    st.info("Upload the audit CSV to see the report.")
    report_header_close(); st.stop()

raw = pd.read_csv(up, dtype=str).fillna("")
# expected columns: "Modified by", "Date of change" (YYYY-MM-DD HH:MM)
_user_col = next((c for c in raw.columns if c.strip().lower() in
                  ("modified by", "user", "performed by")), None)
_date_col = next((c for c in raw.columns if "date" in c.strip().lower()), None)
if not _user_col or not _date_col:
    st.error(f"Couldn't find user / date columns. Found: {list(raw.columns)}")
    report_header_close(); st.stop()

raw["_u"] = raw[_user_col].str.strip()
raw["_t"] = pd.to_datetime(raw[_date_col].str.strip(), errors="coerce")
raw = raw[(raw["_u"] != "") & raw["_t"].notna()].copy()
raw["_day"] = raw["_t"].dt.date

# ── per user / per day span ──────────────────────────────────────────────────
g = raw.groupby(["_u", "_day"]).agg(
    start=("_t", "min"), end=("_t", "max"), events=("_t", "size")).reset_index()
g["span_h"] = (g["end"] - g["start"]).dt.total_seconds() / 3600.0

MIN_DAY = st.slider("Ignore days shorter than (hours) — filters out quick check-ins",
                    0.0, 3.0, 1.0, 0.5, key="wh_min")
clean = g[g["span_h"] >= MIN_DAY].copy()
if clean.empty:
    st.warning("No qualifying working days after the filter.")
    report_header_close(); st.stop()


def _hm(h):
    return f"{int(h)}h {int(round((h - int(h)) * 60)):02d}m"


d0, d1 = raw["_day"].min(), raw["_day"].max()
st.caption(f"Window **{d0:%b %d}–{d1:%b %d, %Y}** · {len(raw):,} events · "
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
