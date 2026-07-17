import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Numbers Report", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Numbers Report", "Live VRS numbers by usage type and created date with monthly usage metrics")

if st.button("Load Numbers Report", key="load_numbers_report"):
    all_number_records = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type", "number_created_at", "credit_type", "usage_minutes"],
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
                usage_mins = float(p.get("usage_minutes") or 0)
            except:
                usage_mins = 0

            status = "Active" if usage_mins > 1 else "Live"

            rows.append({
                "Number": num,
                "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                "Email": p.get("email") or "",
                "Service Type": p.get("service_type") or "-",
                "Number Status": p.get("number_status") or "-",
                "Usage Type": p.get("usage_type") or "-",
                "Credit Type": p.get("credit_type") or "-",
                "Number Created At": created_full,
                "Usage Minutes": usage_mins,
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

        st.markdown("#### Monthly Usage Metrics")
        st.info("Loading monthly usage values...")
        try:
            monthly_values = list_all(
                "2-46246179",
                ["number", "month_date", "usage_minutes", "service_type"],
                progress_label="Fetching monthly usage values"
            )

            if monthly_values:
                mv_rows = []
                for r in monthly_values:
                    p = r.get("properties", {})
                    if norm(p.get("service_type") or "") != "vrs":
                        continue
                    try:
                        usage_mins = float(p.get("usage_minutes") or 0)
                    except:
                        usage_mins = 0

                    status = "Active" if usage_mins > 1 else "Live"

                    month_str = p.get("month_date") or ""
                    mv_rows.append({
                        "Number": p.get("number") or "",
                        "Month": month_str,
                        "Usage Minutes": usage_mins,
                        "Status": status,
                    })

                if mv_rows:
                    mv_df = pd.DataFrame(mv_rows)
                    monthly_summary = (
                        mv_df.groupby(["Month", "Status"]).size().reset_index(name="Count")
                        .sort_values("Month")
                    )

                    if not monthly_summary.empty:
                        chart = alt.Chart(monthly_summary).mark_bar().encode(
                            x=alt.X("Month:N", title="Month"),
                            y=alt.Y("Count:Q", title="Number of Records"),
                            color=alt.Color("Status:N", scale=alt.Scale(domain=["Active", "Live"], range=["#2DB84B", "#FFA500"]), legend=alt.Legend(title="Status")),
                            tooltip=["Month:N", "Status:N", "Count:Q"]
                        ).properties(height=320)
                        st.altair_chart(chart, use_container_width=True)

                        st.markdown("##### Monthly Usage Detail")
                        st.dataframe(mv_df[["Number", "Month", "Usage Minutes", "Status"]], use_container_width=True)
                    else:
                        st.info("No monthly usage data available.")
                else:
                    st.info("No VRS monthly usage values found.")
            else:
                st.info("No monthly values records found.")
        except Exception as e:
            st.error(f"Error loading monthly usage values: {str(e)}")

            st.markdown("#### Detail Table")
            display_cols = ["Number", "Name", "Email", "Usage Type", "Status", "Usage Minutes", "Number Created At", "Number Status"]
            st.dataframe(report_df[display_cols], use_container_width=True)

report_header_close()
