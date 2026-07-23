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
        ["number", "email", "first_name", "last_name", "number_status", "service_type",
         "city", "state", "zip_code",
         "emerg_city", "emerg_state", "emerg_zip_code"],
        progress_label="Fetching geographic records"
    )

    rows = []
    for r in geo_records:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        primary_state = (p.get("state") or "").strip()
        emerg_state = (p.get("emerg_state") or "").strip()
        rows.append({
            "Number": p.get("number") or "",
            "Email": p.get("email") or "",
            "First Name": p.get("first_name") or "",
            "Last Name": p.get("last_name") or "",
            "City": (p.get("city") or "").strip(),
            "State": primary_state,
            "E911 City": (p.get("emerg_city") or "").strip(),
            "E911 State": emerg_state,
            "Address Mismatch": (
                "Mismatch" if primary_state and emerg_state and
                to_abbr(primary_state) != to_abbr(emerg_state) else
                "Match" if primary_state and emerg_state else "—"
            ),
        })

    if not rows:
        st.warning("No live VRS numbers found.")
    else:
        geo_df = pd.DataFrame(rows)

        tab_primary, tab_e911, tab_mismatch = st.tabs(["Primary Address", "E911 Address", "Mismatch Report"])

        with tab_primary:
            prim_df = geo_df[geo_df["State"] != ""].copy()
            prim_df["State Code"] = prim_df["State"].apply(to_abbr)
            state_counts = prim_df.groupby(["State", "State Code"]).size().reset_index(name="Count")
            city_counts = (
                prim_df[prim_df["City"] != ""]
                .groupby(["City", "State", "State Code"]).size()
                .reset_index(name="Count")
                .sort_values("Count", ascending=False)
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Total Live VRS", len(geo_df))
            col2.metric("States Covered", state_counts["State Code"].nunique())
            col3.metric("Cities Covered", prim_df[prim_df["City"] != ""]["City"].nunique())

            st.markdown("#### Numbers by State")
            fig_state = px.choropleth(
                state_counts, locations="State Code", locationmode="USA-states",
                color="Count", scope="usa", hover_name="State",
                hover_data={"State Code": False, "Count": True},
                color_continuous_scale=[[0, "#D1FAE5"], [0.5, "#2DB84B"], [1, "#1A4D2E"]],
                labels={"Count": "Live VRS Numbers"},
            )
            fig_state.update_layout(
                geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="#F2F2EE", landcolor="#F2F2EE"),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                coloraxis_colorbar=dict(title="Count", thickness=14),
            )
            st.plotly_chart(fig_state, use_container_width=True)

            st.markdown("#### Top 20 Cities")
            top_cities = city_counts.head(20)
            fig_city = go.Figure(go.Bar(
                x=top_cities["City"] + ", " + top_cities["State Code"],
                y=top_cities["Count"], marker_color="#2DB84B", marker_line_width=0,
                text=top_cities["Count"], textposition="outside",
                hovertemplate="<b>%{x}</b><br>Count: %{y}<extra></extra>",
            ))
            fig_city.update_layout(
                xaxis=dict(tickangle=-45, title=None), yaxis=dict(title="Live VRS Numbers"),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=120), height=420,
            )
            st.plotly_chart(fig_city, use_container_width=True)

            st.markdown("#### State Breakdown")
            st.dataframe(state_counts.sort_values("Count", ascending=False).reset_index(drop=True), use_container_width=True)
            st.markdown("#### City Detail")
            st.dataframe(city_counts.reset_index(drop=True), use_container_width=True)

        with tab_e911:
            e911_df = geo_df[geo_df["E911 State"] != ""].copy()
            e911_df["State Code"] = e911_df["E911 State"].apply(to_abbr)
            e911_state = e911_df.groupby(["E911 State", "State Code"]).size().reset_index(name="Count")
            e911_city = (
                e911_df[e911_df["E911 City"] != ""]
                .groupby(["E911 City", "E911 State", "State Code"]).size()
                .reset_index(name="Count")
                .sort_values("Count", ascending=False)
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Numbers with E911 Address", len(e911_df))
            col2.metric("E911 States", e911_state["State Code"].nunique())
            col3.metric("E911 Cities", e911_df[e911_df["E911 City"] != ""]["E911 City"].nunique())

            st.markdown("#### E911 Numbers by State")
            if not e911_state.empty:
                fig_e911 = px.choropleth(
                    e911_state, locations="State Code", locationmode="USA-states",
                    color="Count", scope="usa", hover_name="E911 State",
                    hover_data={"State Code": False, "Count": True},
                    color_continuous_scale=[[0, "#FEE2E2"], [0.5, "#EF4444"], [1, "#7F1D1D"]],
                    labels={"Count": "E911 Numbers"},
                )
                fig_e911.update_layout(
                    geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="#F2F2EE", landcolor="#F2F2EE"),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0),
                    coloraxis_colorbar=dict(title="Count", thickness=14),
                )
                st.plotly_chart(fig_e911, use_container_width=True)
            st.markdown("#### E911 State Breakdown")
            st.dataframe(e911_state.sort_values("Count", ascending=False).reset_index(drop=True), use_container_width=True)

        with tab_mismatch:
            mismatch_df = geo_df[geo_df["Address Mismatch"] == "Mismatch"].copy()
            match_df    = geo_df[geo_df["Address Mismatch"] == "Match"].copy()
            no_e911_df  = geo_df[geo_df["Address Mismatch"] == "—"].copy()

            c1, c2, c3 = st.columns(3)
            c1.metric("Address Match", len(match_df), help="Primary state = E911 state")
            c2.metric("Mismatch", len(mismatch_df), help="Primary state ≠ E911 state")
            c3.metric("No E911 on File", len(no_e911_df))

            if not mismatch_df.empty:
                st.markdown("#### Mismatched Records")
                st.markdown("Primary address state differs from E911 address state — may indicate a moved consumer whose emergency address hasn't been updated.")
                st.dataframe(
                    mismatch_df[["Number", "First Name", "Last Name", "Email", "City", "State", "E911 City", "E911 State"]].reset_index(drop=True),
                    use_container_width=True, hide_index=True,
                )
                mismatch_csv = mismatch_df.to_csv(index=False)
                st.download_button("Download Mismatch CSV", mismatch_csv, "address_mismatch.csv", "text/csv")
                from utils import pdf_download_button
                pdf_download_button(mismatch_df, "address_mismatch.pdf", "Geographic — Address Mismatch", key="geo_mm")
            else:
                st.success("No address mismatches found — all E911 addresses match primary state.")

report_header_close()
