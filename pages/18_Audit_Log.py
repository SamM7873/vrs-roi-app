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

report_header("Audit Log", "Login activity — who signed in, when, and from where", section="Admin")

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

# friendly local-ish timestamp
def _fmt(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d, %Y  %I:%M %p UTC")
    except Exception:
        return ts

df["When"] = df["ts"].map(_fmt)
df = df.rename(columns={"username": "User", "action": "Action", "ip": "IP",
                        "location": "Location", "device": "Device", "ua": "User Agent"})

# ── summary tiles ──
logins = int((df["Action"] == "login").sum())
failed = int((df["Action"] == "login_failed").sum())
users_n = df.loc[df["Action"] == "login", "User"].nunique()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total events", f"{len(df):,}")
c2.metric("Successful logins", f"{logins:,}")
c3.metric("Failed logins", f"{failed:,}")
c4.metric("Distinct users", f"{users_n:,}")

# ── filters ──
f1, f2 = st.columns([2, 2])
who = f1.selectbox("User", ["(all)"] + sorted(df["User"].dropna().unique().tolist()))
act = f2.multiselect("Action", sorted(df["Action"].unique().tolist()),
                     default=sorted(df["Action"].unique().tolist()))

view = df.copy()
if who != "(all)":
    view = view[view["User"] == who]
if act:
    view = view[view["Action"].isin(act)]

cols = ["When", "User", "Action", "Location", "IP", "Device", "User Agent"]
st.dataframe(view[cols], use_container_width=True, hide_index=True, height=520)

st.download_button(
    "📥 Download CSV",
    view[cols].to_csv(index=False),
    f"audit_log_{datetime.now().strftime('%Y%m%d')}.csv",
    "text/csv",
)

report_header_close()
