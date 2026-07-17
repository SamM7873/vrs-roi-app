import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Numbers Report", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Numbers Report", "Live VRS numbers by URSA billable minutes (active vs live)")

if st.button("Load Numbers Report", key="load_numbers_report"):
    all_number_records = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type", "number_created_at", "credit_type", "ursa_sum_of_total_billable_inbound_minutes", "ursa_sum_of_total_billable_outbound_minutes"],
        progress_label="Fetching number records"
    )

    if not all_number_records:
        st.info("No number records found.")
    else:
        rows = []
        for r in all_number_records:
            p = r.get("properties", {})
            if norm(p.get("service_type") or "") != "vrs":
                continue
            if norm(p.get("number_status") or "") != "live":
                continue
            num = str(p.get("number") or "").strip()
            created_raw = p.get("number_created_at") or ""
            try:
                dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                created_full = dt.strftime("%m/%d/%Y")
                week_start = (dt - pd.Timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
            except Exception:
                created_full = "-"
                week_start = "-"

            try:
                inbound_mins = float(p.get("ursa_sum_of_total_billable_inbound_minutes") or 0)
                outbound_mins = float(p.get("ursa_sum_of_total_billable_outbound_minutes") or 0)
            except:
                inbound_mins = 0
                outbound_mins = 0

            total_ursa_mins = inbound_mins + outbound_mins
            status = "Active" if total_ursa_mins > 1 else "Live"

            rows.append({
                "Number": num,
                "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                "Email": p.get("email") or "",
                "Service Type": p.get("service_type") or "-",
                "Number Status": p.get("number_status") or "-",
                "Usage Type": p.get("usage_type") or "-",
                "Credit Type": p.get("credit_type") or "-",
                "Number Created At": created_full,
                "Inbound Minutes": inbound_mins,
                "Outbound Minutes": outbound_mins,
                "Total URSA Minutes": total_ursa_mins,
                "Status": status,
                "_week": week_start,
            })

        if not rows:
            st.info("No Live VRS numbers found.")
        else:
            report_df = pd.DataFrame(rows)

            total = len(report_df)
            personal_active = ((report_df["Usage Type"].str.lower() == "personal") & (report_df["Status"] == "Active")).sum()
            personal_live = ((report_df["Usage Type"].str.lower() == "personal") & (report_df["Status"] == "Live")).sum()
            org_active = ((report_df["Usage Type"].str.lower() == "organization") & (report_df["Status"] == "Active")).sum()
            org_live = ((report_df["Usage Type"].str.lower() == "organization") & (report_df["Status"] == "Live")).sum()

            st.markdown("### Numbers Breakdown")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Total", total)
            col2.metric("Personal Active", personal_active)
            col3.metric("Personal Live", personal_live)
            col4.metric("Org Active", org_active)
            col5.metric("Org Live", org_live)

            df_dated = report_df[report_df["Number Created At"] != "-"].copy()
            df_dated["_dt"] = pd.to_datetime(df_dated["Number Created At"], format="%m/%d/%Y", errors="coerce")
            df_dated = df_dated.dropna(subset=["_dt"])

            latest_dt = df_dated["_dt"].max()
            this_month_df = df_dated[
                (df_dated["_dt"].dt.year == latest_dt.year) &
                (df_dated["_dt"].dt.month == latest_dt.month)
            ]

            def bar_chart(data, x_col, x_title, sort_order):
                if data.empty:
                    st.info("No data for this period.")
                    return
                chart = alt.Chart(data).mark_bar(color="#2DB84B", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    x=alt.X(f"{x_col}:N", sort=sort_order, title=x_title, axis=alt.Axis(labelAngle=-45)),
                    y=alt.Y("Count:Q", title="Numbers Created"),
                    tooltip=[alt.Tooltip(f"{x_col}:N", title=x_title), "Count"]
                ).properties(height=320)
                st.altair_chart(chart, use_container_width=True)

            st.markdown("#### Numbers Created At")
            tab_daily, tab_weekly, tab_monthly = st.tabs(["Daily", "Weekly", "Monthly"])

            with tab_daily:
                daily = (
                    this_month_df.assign(_day=this_month_df["_dt"].dt.strftime("%m/%d"))
                    .groupby("_day").size().reset_index(name="Count").sort_values("_day")
                )
                bar_chart(daily, "_day", "Day", daily["_day"].tolist())

            with tab_weekly:
                weekly = (
                    this_month_df.assign(_week=this_month_df["_week"])
                    .groupby("_week").size().reset_index(name="Count").sort_values("_week")
                )
                bar_chart(weekly, "_week", "Week Starting", weekly["_week"].tolist())

            with tab_monthly:
                monthly = (
                    df_dated.assign(_month=df_dated["_dt"].dt.strftime("%Y-%m"))
                    .groupby("_month").size().reset_index(name="Count").sort_values("_month")
                )
                bar_chart(monthly, "_month", "Month", monthly["_month"].tolist())

            st.markdown("#### Monthly Breakdown by Status (Active vs Live)")
            monthly_status = (
                df_dated.assign(_month=df_dated["_dt"].dt.strftime("%Y-%m"))
                .groupby(["_month", "Status"]).size().reset_index(name="Count")
                .sort_values("_month")
            )
            if not monthly_status.empty:
                chart = alt.Chart(monthly_status).mark_bar().encode(
                    x=alt.X("_month:N", title="Month", sort=sorted(monthly_status["_month"].unique())),
                    y=alt.Y("Count:Q", title="Numbers Created"),
                    color=alt.Color("Status:N", scale=alt.Scale(domain=["Active", "Live"], range=["#2DB84B", "#FFA500"]), legend=alt.Legend(title="Status")),
                    tooltip=["_month:N", "Status:N", "Count:Q"]
                ).properties(height=320)
                st.altair_chart(chart, use_container_width=True)
            else:
                st.info("No data available for monthly breakdown.")

            st.markdown("#### Detail Tables")
            display_cols = ["Number", "Name", "Email", "Usage Type", "Inbound Minutes", "Outbound Minutes", "Total URSA Minutes", "Number Created At"]

            # Filter by Usage Type
            filter_type = st.radio("Filter by Usage Type:", ["All", "Personal", "Organization"], horizontal=True, key="usage_type_filter")

            if filter_type == "Personal":
                filtered_df = report_df[report_df["Usage Type"].str.lower() == "personal"]
            elif filter_type == "Organization":
                filtered_df = report_df[report_df["Usage Type"].str.lower() == "organization"]
            else:
                filtered_df = report_df

            tab_active, tab_live = st.tabs(["Active Numbers", "Live Numbers"])

            with tab_active:
                active_df = filtered_df[filtered_df["Status"] == "Active"][display_cols]
                if active_df.empty:
                    st.info("No active numbers found.")
                else:
                    st.dataframe(active_df, use_container_width=True)

            with tab_live:
                live_df = filtered_df[filtered_df["Status"] == "Live"][display_cols]
                if live_df.empty:
                    st.info("No live numbers found.")
                else:
                    st.dataframe(live_df, use_container_width=True)

report_header_close()
