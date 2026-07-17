import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime as _dt
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="URSA Login Report", layout="wide", page_icon="👤")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("URSA Login Report", "First login, first outbound, and second outbound timestamps")

@st.cache_data(ttl=300)
def fetch_ursa_data():
    """Fetch URSA login data and cache for 5 minutes"""
    ursa_records = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name", "number_status", "service_type",
         "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call",
         "ursa_last_outbound_call", "number_created_at"],
        progress_label="Fetching URSA records"
    )

    rows = []
    for r in ursa_records:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        rows.append({
            "Number": p.get("number") or "",
            "Email": p.get("email") or "",
            "First Name": p.get("first_name") or "",
            "Last Name": p.get("last_name") or "",
            "Number Created": p.get("number_created_at") or "",
            "URSA First Login": p.get("ursa_first_login") or "",
            "URSA First Outbound Call": p.get("ursa_first_outbound_call") or "",
            "URSA Second Outbound Call": p.get("ursa_second_outbound_call") or "",
            "URSA Last Outbound Call": p.get("ursa_last_outbound_call") or "",
        })

    if not rows:
        return None
    return pd.DataFrame(rows)

# Auto-load data
ursa_df = fetch_ursa_data()

if ursa_df is None or ursa_df.empty:
    st.warning("No live VRS numbers found.")
else:
    has_login    = ursa_df["URSA First Login"] != ""
    has_outbound = ursa_df["URSA First Outbound Call"] != ""
    never_called = has_login & ~has_outbound

    count_logged_in      = has_login.sum()
    count_not_logged_in  = (~has_login).sum()
    count_never_called   = never_called.sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Live VRS", len(ursa_df))
    col2.metric("Has First Login", int(count_logged_in))
    col3.metric("No Login Yet", int(count_not_logged_in))
    col4.metric("Logged In, Never Called", int(count_never_called))

    # Refresh button
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    tab_overview, tab_never_called, tab_no_login, tab_full = st.tabs([
        "Overview", "Never Made a Call", "Never Logged In", "Full Detail"
    ])

    def ursa_bar(df, col_name, label):
        has = (df[col_name] != "").sum()
        missing = (df[col_name] == "").sum()
        chart_data = pd.DataFrame({
            "Status": ["Has Value", "No Value"],
            "Count": [int(has), int(missing)],
            "Color": ["#C9A876", "#EF4444"],
        })
        chart = alt.Chart(chart_data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Status:N", title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Count:Q", title="Count"),
            color=alt.Color("Color:N", scale=None, legend=None),
            tooltip=["Status", "Count"],
        ).properties(height=220)
        st.markdown(f"##### {label}")
        st.altair_chart(chart, use_container_width=True)

    with tab_overview:
        c1, c2 = st.columns(2)
        with c1:
            ursa_bar(ursa_df, "URSA First Login", "First Login")
        with c2:
            ursa_bar(ursa_df, "URSA First Outbound Call", "First Outbound Call")

    with tab_never_called:
        st.markdown("**Consumers who have logged in to URSA but have never made an outbound VRS call.**")
        st.markdown("These users may need onboarding support or a check-in call to get activated.")
        nc_df = ursa_df[never_called][["Number", "Email", "First Name", "Last Name", "Number Created", "URSA First Login", "URSA Last Outbound Call"]].copy()

        def _fmt(v):
            if not v:
                return "—"
            try:
                return _dt.fromisoformat(v.replace("Z", "+00:00")).strftime("%b %d, %Y")
            except Exception:
                return v

        nc_df["Number Created"] = nc_df["Number Created"].apply(_fmt)
        nc_df["URSA First Login"] = nc_df["URSA First Login"].apply(_fmt)
        nc_df.rename(columns={"URSA Last Outbound Call": "Last Outbound (none)"}, inplace=True)
        nc_df = nc_df.reset_index(drop=True)
        st.dataframe(nc_df, use_container_width=True, hide_index=True)
        if not nc_df.empty:
            st.download_button("Download CSV", nc_df.to_csv(index=False), "never_called.csv", "text/csv")

    with tab_no_login:
        not_logged_in_df = ursa_df[~has_login][["Number", "Email", "First Name", "Last Name"]].reset_index(drop=True)
        st.dataframe(not_logged_in_df, use_container_width=True, hide_index=True)

    with tab_full:
        st.dataframe(ursa_df, use_container_width=True, hide_index=True)

report_header_close()
