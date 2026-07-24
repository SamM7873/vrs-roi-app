import streamlit as st
import pandas as pd
from datetime import datetime
from utils import (
    require_auth, is_app_admin, read_audit,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="Audit Log", layout="wide", page_icon="🛡️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Audit Log", "Activity — who signed in and which reports they ran, when", section="Admin")

if not is_app_admin():
    st.warning("This page is restricted to administrators.")
    report_header_close()
    st.stop()

events = read_audit(limit=5000)
if not events:
    st.info("No audit events recorded yet. Events are captured as users log in and out.")
    report_header_close()
    st.stop()

df = pd.DataFrame(events)
if "report" not in df.columns:      # older events predate report tracking
    df["report"] = ""
df["report"] = df["report"].fillna("")

# friendly local-ish timestamp
def _fmt(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d, %Y  %I:%M %p UTC")
    except Exception:
        return ts

df["When"] = df["ts"].map(_fmt)
df = df.rename(columns={"username": "User", "action": "Action", "report": "Report",
                        "ip": "IP", "location": "Location", "device": "Device", "ua": "User Agent"})

# ── summary tiles ──
logins = int((df["Action"] == "login").sum())
report_views = int((df["Action"] == "report_view").sum())
users_n = df.loc[df["Action"] == "login", "User"].nunique()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total events", f"{len(df):,}")
c2.metric("Successful logins", f"{logins:,}")
c3.metric("Report runs", f"{report_views:,}")
c4.metric("Distinct users", f"{users_n:,}")

# ── report usage summary ──
_rv = df[df["Action"] == "report_view"]
if not _rv.empty:
    st.markdown("##### Report usage — how often each report was run")
    usage = (_rv.groupby("Report")
             .agg(Runs=("Report", "size"), Users=("User", "nunique"),
                  Last_run=("When", "first"))
             .reset_index().rename(columns={"Last_run": "Last run"})
             .sort_values("Runs", ascending=False))
    st.dataframe(usage, use_container_width=True, hide_index=True)

# ── filters ──
st.markdown("##### Activity log")
f1, f2, f3 = st.columns([2, 2, 2])
who = f1.selectbox("User", ["(all)"] + sorted(df["User"].dropna().unique().tolist()))
act = f2.multiselect("Action", sorted(df["Action"].unique().tolist()),
                     default=sorted(df["Action"].unique().tolist()))
_report_opts = sorted([r for r in df["Report"].unique().tolist() if r])
rep = f3.selectbox("Report", ["(all)"] + _report_opts)

view = df.copy()
if who != "(all)":
    view = view[view["User"] == who]
if act:
    view = view[view["Action"].isin(act)]
if rep != "(all)":
    view = view[view["Report"] == rep]

cols = ["When", "User", "Action", "Report", "Location", "IP", "Device", "User Agent"]
st.dataframe(view[cols], use_container_width=True, hide_index=True, height=520)

st.download_button(
    "📥 Download CSV",
    view[cols].to_csv(index=False),
    f"audit_log_{datetime.now().strftime('%Y%m%d')}.csv",
    "text/csv",
)
from utils import pdf_download_button
pdf_download_button(view[cols], "audit_log.pdf", "Audit Log", key="audit")

report_header_close()
