import streamlit as st
import pandas as pd
import altair as alt
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="URSA Login Report", layout="wide", page_icon="👤")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("URSA Login Report", "First login, first outbound, and second outbound timestamps")

if st.button("Load URSA Report", key="load_ursa_report"):
    ursa_records = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name", "number_status", "service_type",
         "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
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
            "URSA First Login": p.get("ursa_first_login") or "",
            "URSA First Outbound Call": p.get("ursa_first_outbound_call") or "",
            "URSA Second Outbound Call": p.get("ursa_second_outbound_call") or "",
        })

    if not rows:
        st.warning("No live VRS numbers found.")
    else:
        ursa_df = pd.DataFrame(rows)

        has_login = ursa_df["URSA First Login"] != ""
        count_logged_in = has_login.sum()
        count_not_logged_in = (~has_login).sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Live VRS", len(ursa_df))
        col2.metric("Has First Login", int(count_logged_in))
        col3.metric("No First Login Yet", int(count_not_logged_in))

        def ursa_bar(col_name, label):
            has = (ursa_df[col_name] != "").sum()
            missing = (ursa_df[col_name] == "").sum()
            chart_data = pd.DataFrame({
                "Status": ["Has Value", "No Value"],
                "Count": [int(has), int(missing)],
                "Color": ["#2DB84B", "#EF4444"],
            })
            chart = alt.Chart(chart_data).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Status:N", title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("Count:Q", title="Count"),
                color=alt.Color("Color:N", scale=None, legend=None),
                tooltip=["Status", "Count"],
            ).properties(height=260)
            st.markdown(f"##### {label}")
            st.altair_chart(chart, use_container_width=True)

        ursa_bar("URSA First Login", "First Login")

        st.markdown("#### Who Has NOT Logged In Yet")
        not_logged_in_df = ursa_df[~has_login][["Number", "Email", "First Name", "Last Name"]].reset_index(drop=True)
        st.dataframe(not_logged_in_df, use_container_width=True)

        st.markdown("#### Full URSA Detail")
        st.dataframe(ursa_df, use_container_width=True)

report_header_close()
