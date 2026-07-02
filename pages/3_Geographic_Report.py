import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Geographic Report", layout="wide", page_icon="🗺️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

US_STATE_ABBR = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA",
    "Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD",
    "Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS",
    "Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH",
    "New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC",
    "North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA",
    "Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD","Tennessee":"TN",
    "Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA",
    "West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY","District of Columbia":"DC",
}

def to_abbr(s):
    s = s.strip()
    if len(s) == 2:
        return s.upper()
    return US_STATE_ABBR.get(s.title(), s.upper()[:2])

report_header("Geographic Report", "Live VRS numbers by city and state")

if st.button("Load Geographic Report", key="load_geo_report"):
    geo_records = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name", "number_status", "service_type", "city", "state"],
        progress_label="Fetching geographic records"
    )

    rows = []
    for r in geo_records:
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
            "City": (p.get("city") or "").strip(),
            "State": (p.get("state") or "").strip(),
        })

    if not rows:
        st.warning("No live VRS numbers found.")
    else:
        geo_df = pd.DataFrame(rows)
        geo_df = geo_df[geo_df["State"] != ""]
        geo_df["State Code"] = geo_df["State"].apply(to_abbr)

        state_counts = geo_df.groupby(["State", "State Code"]).size().reset_index(name="Count")
        city_counts = (
            geo_df[geo_df["City"] != ""]
            .groupby(["City", "State", "State Code"]).size()
            .reset_index(name="Count")
            .sort_values("Count", ascending=False)
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Live VRS", len(geo_df))
        col2.metric("States Covered", state_counts["State Code"].nunique())
        col3.metric("Cities Covered", geo_df[geo_df["City"] != ""]["City"].nunique())

        st.markdown("#### Numbers by State")
        fig_state = px.choropleth(
            state_counts,
            locations="State Code",
            locationmode="USA-states",
            color="Count",
            scope="usa",
            hover_name="State",
            hover_data={"State Code": False, "Count": True},
            color_continuous_scale=[[0, "#D1FAE5"], [0.5, "#2DB84B"], [1, "#1A4D2E"]],
            labels={"Count": "Live VRS Numbers"},
        )
        fig_state.update_layout(
            geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="#F2F2EE", landcolor="#F2F2EE"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            coloraxis_colorbar=dict(title="Count", thickness=14),
        )
        st.plotly_chart(fig_state, use_container_width=True)

        st.markdown("#### Top 20 Cities")
        top_cities = city_counts.head(20)
        fig_city = go.Figure(go.Bar(
            x=top_cities["City"] + ", " + top_cities["State Code"],
            y=top_cities["Count"],
            marker_color="#2DB84B",
            marker_line_width=0,
            text=top_cities["Count"],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Count: %{y}<extra></extra>",
        ))
        fig_city.update_layout(
            xaxis=dict(tickangle=-45, title=None),
            yaxis=dict(title="Live VRS Numbers"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=120),
            height=420,
        )
        st.plotly_chart(fig_city, use_container_width=True)

        st.markdown("#### State Breakdown")
        st.dataframe(
            state_counts.sort_values("Count", ascending=False).reset_index(drop=True),
            use_container_width=True,
        )

        st.markdown("#### City Detail")
        st.dataframe(city_counts.reset_index(drop=True), use_container_width=True)

report_header_close()
