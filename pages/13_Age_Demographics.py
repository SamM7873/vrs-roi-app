import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict
from datetime import date, datetime, timezone
from utils import require_auth, fetch_all, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Age Demographics", layout="wide", page_icon="👥")
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

AGE_BUCKET_ORDER = [
    "Under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+",
    "18 - 35", "36 - 50", "51 - 64", "65 and Over",
]

def to_abbr(s):
    s = (s or "").strip()
    if len(s) == 2:
        return s.upper()
    return US_STATE_ABBR.get(s.title(), s.upper()[:2] if s else "")

def _f(v):
    try:
        return float(v) if v not in (None, "", "null") else 0.0
    except Exception:
        return 0.0

# ── UI ──────────────────────────────────────────────────────────────────────
report_header("Age Demographics", "Usage minutes by age group and state")

fc1, fc2, fc3 = st.columns([1, 1, 1])
with fc1:
    RANGE_OPTIONS = ["This Month", "This Year", "Last 3 Months", "Last 6 Months", "Last 12 Months", "All Time"]
    range_label = st.selectbox("Date range (month_date)", RANGE_OPTIONS, key="age_range")
with fc2:
    service_type = st.selectbox("Service Type", ["VRS", "Convo Now", "Both"], key="age_svc")
with fc3:
    number_status = st.selectbox("Number Status", ["All", "Live", "Suspended"], key="age_status")

# Resolve date floor for monthly value records
today = date.today()
if range_label == "This Month":
    floor = date(today.year, today.month, 1)
elif range_label == "This Year":
    floor = date(today.year, 1, 1)
elif range_label == "Last 3 Months":
    m, y = today.month - 3, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 6 Months":
    m, y = today.month - 6, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 12 Months":
    floor = date(today.year - 1, today.month, 1)
else:
    floor = date(2000, 1, 1)

floor_ms = str(int(datetime(floor.year, floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))
MV_DATE_FILTER = {"propertyName": "month_date", "operator": "GTE", "value": floor_ms}

run = st.button("Load Age Demographics", type="primary")

cached = st.session_state.get("_age_demo_cache")
if cached and not run:
    if (cached.get("service_type") != service_type
            or cached.get("number_status") != number_status
            or cached.get("range_label") != range_label):
        cached = None  # filters changed — require a fresh load

if run:

    # ── Step 1: Fetch Number objects ─────────────────────────────────────────
    NUM_PROPS = ["number", "email", "first_name", "last_name",
                 "age_bucket", "state", "service_type", "number_status"]

    num_recs = list_all("2-40974683", NUM_PROPS, progress_label="Fetching Number records")

    # index: phone_number → {age_bucket, state, service_type, email}
    phone_meta = {}
    for obj in num_recs:
        p = obj.get("properties", {})
        if service_type != "Both" and norm(p.get("service_type") or "") != norm(service_type):
            continue
        if number_status != "All" and norm(p.get("number_status") or "") != norm(number_status):
            continue
        num = str(p.get("number") or "").strip()
        if not num:
            continue
        phone_meta[num] = {
            "age_bucket":    (p.get("age_bucket") or "Unknown").strip() or "Unknown",
            "state":         (p.get("state") or "").strip(),
            "service_type":  (p.get("service_type") or "").strip(),
            "email":         (p.get("email") or "").strip(),
            "first_name":    (p.get("first_name") or "").strip(),
            "last_name":     (p.get("last_name") or "").strip(),
        }

    all_phones = list(phone_meta.keys())
    total_numbers = len(all_phones)

    if not all_phones:
        st.warning("No numbers found with the selected filters.")
        st.stop()

    # ── Step 2: Fetch Monthly Values by phone number ─────────────────────────
    MV_PROPS = [
        "number", "service_type", "month_date",
        "ursa_ios_minutes", "ursa_android_minutes", "ursa_web_minutes",
        "ursa_minutes", "cfz_minutes", "usage_minutes",
    ]

    mv_svc_filters = []
    if service_type != "Both":
        mv_svc_filters.append({"propertyName": "service_type", "operator": "EQ", "value": service_type})

    # aggregate: phone → {ursa_ios, ursa_android, ursa_web, ursa, cfz, usage}
    phone_usage = defaultdict(lambda: dict(ursa_ios=0.0, ursa_android=0.0, ursa_web=0.0,
                                            ursa=0.0, cfz=0.0, usage=0.0, mv_count=0))

    progress = st.progress(0, text="Fetching monthly values...")
    chunks = [all_phones[i:i+100] for i in range(0, len(all_phones), 100)]

    for idx, chunk in enumerate(chunks):
        filters = [{"propertyName": "number", "operator": "IN", "values": chunk}, MV_DATE_FILTER]
        if mv_svc_filters:
            filters += mv_svc_filters

        mv_recs = fetch_all("2-46246179", MV_PROPS,
                             filter_groups=[{"filters": filters}])
        for obj in mv_recs:
            p2 = obj.get("properties", {})
            ph = str(p2.get("number") or "").strip()
            if ph not in phone_meta:
                continue
            phone_usage[ph]["ursa_ios"]     += _f(p2.get("ursa_ios_minutes"))
            phone_usage[ph]["ursa_android"] += _f(p2.get("ursa_android_minutes"))
            phone_usage[ph]["ursa_web"]     += _f(p2.get("ursa_web_minutes"))
            phone_usage[ph]["ursa"]         += _f(p2.get("ursa_minutes"))
            phone_usage[ph]["cfz"]          += _f(p2.get("cfz_minutes"))
            phone_usage[ph]["usage"]        += _f(p2.get("usage_minutes"))
            phone_usage[ph]["mv_count"]     += 1

        progress.progress(min(int((idx + 1) / len(chunks) * 100), 100),
                          text=f"Fetching monthly values... {idx+1}/{len(chunks)} batches")

    progress.empty()

    # ── Step 3: Build detail rows ────────────────────────────────────────────
    detail_rows = []
    for ph, meta in phone_meta.items():
        u = phone_usage.get(ph, {})
        detail_rows.append({
            "Phone":          ph,
            "Email":          meta["email"],
            "First Name":     meta["first_name"],
            "Last Name":      meta["last_name"],
            "Age Bucket":     meta["age_bucket"],
            "State":          meta["state"],
            "Service Type":   meta["service_type"],
            "URSA iOS Min":   u.get("ursa_ios", 0.0),
            "URSA Android Min": u.get("ursa_android", 0.0),
            "URSA Web Min":   u.get("ursa_web", 0.0),
            "URSA Min":       u.get("ursa", 0.0),
            "CfZ Min":        u.get("cfz", 0.0),
            "Usage Min":      u.get("usage", 0.0),
            "MV Records":     u.get("mv_count", 0),
        })

    df_detail = pd.DataFrame(detail_rows)

    # ── Step 4: Aggregate by age bucket ─────────────────────────────────────
    agg_age = (
        df_detail.groupby("Age Bucket", as_index=False)
        .agg(
            Numbers=("Phone", "count"),
            URSA_iOS=("URSA iOS Min", "sum"),
            URSA_Android=("URSA Android Min", "sum"),
            URSA_Web=("URSA Web Min", "sum"),
            URSA_Total=("URSA Min", "sum"),
            CfZ=("CfZ Min", "sum"),
            Usage=("Usage Min", "sum"),
        )
    )
    # sort by known order if possible, otherwise alpha
    order_map = {b: i for i, b in enumerate(AGE_BUCKET_ORDER)}
    agg_age["_ord"] = agg_age["Age Bucket"].map(lambda x: order_map.get(x, 999))
    agg_age = agg_age.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)

    # ── Step 5: Aggregate by state ───────────────────────────────────────────
    df_with_state = df_detail[df_detail["State"] != ""].copy()
    df_with_state["State Code"] = df_with_state["State"].apply(to_abbr)
    df_with_state = df_with_state[df_with_state["State Code"].str.len() == 2]

    agg_state = (
        df_with_state.groupby(["State", "State Code"], as_index=False)
        .agg(
            Numbers=("Phone", "count"),
            Usage=("Usage Min", "sum"),
            URSA_Total=("URSA Min", "sum"),
            CfZ=("CfZ Min", "sum"),
        )
        .sort_values("Usage", ascending=False)
        .reset_index(drop=True)
    )

    summary = {
        "total_numbers": total_numbers,
        "with_mv": int((df_detail["MV Records"] > 0).sum()),
        "states": df_with_state["State Code"].nunique() if not df_with_state.empty else 0,
        "age_buckets": agg_age[agg_age["Age Bucket"] != "Unknown"].shape[0],
        "total_ursa": df_detail["URSA Min"].sum(),
        "total_cfz": df_detail["CfZ Min"].sum(),
        "total_usage": df_detail["Usage Min"].sum(),
    }

    df_age   = agg_age
    df_state = agg_state

    st.session_state["_age_demo_cache"] = {
        "service_type": service_type,
        "number_status": number_status,
        "range_label": range_label,
        "df_age": df_age,
        "df_state": df_state,
        "df_detail": df_detail,
        "summary": summary,
    }
    cached = st.session_state["_age_demo_cache"]

# ── Render ───────────────────────────────────────────────────────────────────
if cached:
    df_age    = cached["df_age"]
    df_state  = cached["df_state"]
    df_detail = cached["df_detail"]
    summary   = cached["summary"]

    # Summary tiles
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Numbers", f"{summary['total_numbers']:,}")
    c2.metric("With Usage Data", f"{summary['with_mv']:,}")
    c3.metric("Age Groups", f"{summary['age_buckets']}")
    c4.metric("States", f"{summary['states']}")
    c5.metric("Total URSA Min", f"{summary['total_ursa']:,.0f}")
    c6.metric("Total Usage Min", f"{summary['total_usage']:,.0f}")

    st.markdown("---")

    tab_age, tab_state, tab_detail = st.tabs(["Age Breakdown", "State Heatmap", "Detail Table"])

    # ── Age Breakdown Tab ────────────────────────────────────────────────────
    with tab_age:
        if df_age.empty:
            st.info("No age data available.")
        else:
            st.markdown("#### Usage Minutes by Age Group")

            # Grouped bar: one bar per metric per age bucket
            fig_age = go.Figure()
            colors = {
                "URSA iOS":    "#3B82F6",
                "URSA Android":"#8B5CF6",
                "URSA Web":    "#06B6D4",
                "CfZ":         "#F59E0B",
                "Total Usage": "#2DB84B",
            }
            for label, col, color in [
                ("URSA iOS",    "URSA_iOS",     "#3B82F6"),
                ("URSA Android","URSA_Android",  "#8B5CF6"),
                ("URSA Web",    "URSA_Web",      "#06B6D4"),
                ("CfZ",         "CfZ",           "#F59E0B"),
                ("Total Usage", "Usage",         "#2DB84B"),
            ]:
                fig_age.add_trace(go.Bar(
                    name=label,
                    x=df_age["Age Bucket"],
                    y=df_age[col].round(1),
                    marker_color=color,
                    hovertemplate=f"<b>%{{x}}</b><br>{label}: %{{y:,.1f}} min<extra></extra>",
                ))
            fig_age.update_layout(
                barmode="group",
                xaxis=dict(title=None),
                yaxis=dict(title="Minutes"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=40),
                height=420,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_age, use_container_width=True)

            st.markdown("#### Numbers per Age Group")
            fig_count = go.Figure(go.Bar(
                x=df_age["Age Bucket"],
                y=df_age["Numbers"],
                marker_color="#2DB84B",
                text=df_age["Numbers"],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Numbers: %{y:,}<extra></extra>",
            ))
            fig_count.update_layout(
                xaxis=dict(title=None),
                yaxis=dict(title="Number of Numbers"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=40),
                height=300,
            )
            st.plotly_chart(fig_count, use_container_width=True)

            st.markdown("#### Age Group Summary Table")
            display_age = df_age.copy()
            for col in ["URSA_iOS","URSA_Android","URSA_Web","URSA_Total","CfZ","Usage"]:
                display_age[col] = display_age[col].map(lambda x: f"{x:,.1f}")
            display_age.columns = [
                "Age Bucket","Numbers","URSA iOS Min","URSA Android Min","URSA Web Min",
                "URSA Total Min","CfZ Min","Usage Min"
            ]
            st.dataframe(display_age, use_container_width=True, hide_index=True)

    # ── State Heatmap Tab ─────────────────────────────────────────────────────
    with tab_state:
        if df_state.empty:
            st.info("No state data available.")
        else:
            metric_choice = st.selectbox(
                "Color heatmap by",
                ["Usage Min", "URSA Total Min", "CfZ Min", "Number Count"],
                key="age_state_metric"
            )
            col_map = {
                "Usage Min":      "Usage",
                "URSA Total Min": "URSA_Total",
                "CfZ Min":        "CfZ",
                "Number Count":   "Numbers",
            }
            hmap_col = col_map[metric_choice]

            fig_map = px.choropleth(
                df_state,
                locations="State Code",
                locationmode="USA-states",
                color=hmap_col,
                scope="usa",
                hover_name="State",
                hover_data={
                    "State Code": False,
                    "Numbers": True,
                    "Usage": ":.1f",
                    "URSA_Total": ":.1f",
                    "CfZ": ":.1f",
                },
                color_continuous_scale=[[0, "#D1FAE5"], [0.5, "#2DB84B"], [1, "#1A4D2E"]],
                labels={
                    "Usage": "Usage Min",
                    "URSA_Total": "URSA Min",
                    "CfZ": "CfZ Min",
                    "Numbers": "Numbers",
                },
            )
            fig_map.update_layout(
                geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="#F2F2EE", landcolor="#F2F2EE"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                coloraxis_colorbar=dict(title=metric_choice, thickness=14),
            )
            st.plotly_chart(fig_map, use_container_width=True)

            st.markdown("#### State Summary")
            display_state = df_state.copy()
            for col in ["Usage","URSA_Total","CfZ"]:
                display_state[col] = display_state[col].map(lambda x: f"{x:,.1f}")
            display_state.columns = ["State","State Code","Numbers","Usage Min","URSA Total Min","CfZ Min"]
            st.dataframe(display_state, use_container_width=True, hide_index=True)

            # Age bucket × state heatmap
            st.markdown("#### Age Group by State (Usage Minutes)")
            pivot_data = (
                df_detail[df_detail["State"] != ""].copy()
            )
            pivot_data["State Code"] = pivot_data["State"].apply(to_abbr)
            pivot_data = pivot_data[pivot_data["State Code"].str.len() == 2]

            if not pivot_data.empty:
                pivot = (
                    pivot_data.groupby(["Age Bucket", "State Code"])["Usage Min"]
                    .sum()
                    .reset_index()
                    .pivot(index="Age Bucket", columns="State Code", values="Usage Min")
                    .fillna(0)
                )
                # sort rows by known order
                order_map = {b: i for i, b in enumerate(AGE_BUCKET_ORDER)}
                pivot = pivot.loc[sorted(pivot.index, key=lambda x: order_map.get(x, 999))]

                fig_heat = px.imshow(
                    pivot,
                    color_continuous_scale=[[0, "#F0FDF4"], [0.5, "#2DB84B"], [1, "#1A4D2E"]],
                    aspect="auto",
                    labels=dict(x="State", y="Age Bucket", color="Usage Min"),
                )
                fig_heat.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=60),
                    height=max(300, len(pivot) * 50 + 80),
                    xaxis=dict(tickangle=-45),
                    coloraxis_colorbar=dict(title="Min", thickness=14),
                )
                st.plotly_chart(fig_heat, use_container_width=True)

    # ── Detail Table Tab ──────────────────────────────────────────────────────
    with tab_detail:
        search = st.text_input("Search by name, email, phone, age bucket, or state",
                               placeholder="e.g. Texas, 25-34, john@...", key="age_search")
        df_show = df_detail.copy()
        if search:
            q = search.strip().lower()
            mask = (
                df_show["Email"].str.lower().str.contains(q, na=False) |
                df_show["First Name"].str.lower().str.contains(q, na=False) |
                df_show["Last Name"].str.lower().str.contains(q, na=False) |
                df_show["Phone"].str.contains(q, na=False) |
                df_show["Age Bucket"].str.lower().str.contains(q, na=False) |
                df_show["State"].str.lower().str.contains(q, na=False)
            )
            df_show = df_show[mask]
            st.caption(f'{len(df_show):,} of {len(df_detail):,} records match "{search}"')

        for col in ["URSA iOS Min","URSA Android Min","URSA Web Min","URSA Min","CfZ Min","Usage Min"]:
            df_show[col] = df_show[col].map(lambda x: round(x, 1))

        st.dataframe(
            df_show.reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_order=["First Name","Last Name","Email","Phone","Age Bucket","State",
                          "Service Type","URSA iOS Min","URSA Android Min","URSA Web Min",
                          "URSA Min","CfZ Min","Usage Min","MV Records"],
        )

        csv = df_show.to_csv(index=False)
        st.download_button("Download CSV", csv, "age_demographics.csv", "text/csv")

report_header_close()
