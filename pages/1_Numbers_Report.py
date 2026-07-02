import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Numbers Report", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Numbers Report", "Live VRS numbers by usage type and created date")

if st.button("Load Numbers Report", key="load_numbers_report"):
    with st.spinner("Fetching all number records (45k+, may take a moment)..."):
        all_number_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type", "number_created_at", "credit_type"]
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
            rows.append({
                "Number": num,
                "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                "Email": p.get("email") or "",
                "Service Type": p.get("service_type") or "-",
                "Number Status": p.get("number_status") or "-",
                "Usage Type": p.get("usage_type") or "-",
                "Credit Type": p.get("credit_type") or "-",
                "Number Created At": created_full,
                "_week": week_start,
            })

        if not rows:
            st.info("No Live VRS numbers found.")
        else:
            report_df = pd.DataFrame(rows)

            total = len(report_df)
            personal = (report_df["Usage Type"].str.lower() == "personal").sum()
            org = (report_df["Usage Type"].str.lower() == "organization").sum()
            m1, m2, m3 = st.columns(3)
            m1.metric("Live VRS Numbers", total)
            m2.metric("Personal", personal)
            m3.metric("Organization", org)

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

            st.markdown("#### Detail Table")
            st.dataframe(report_df.drop(columns=["_week"]), use_container_width=True)

report_header_close()
