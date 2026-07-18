import streamlit as st
import pandas as pd
import time
from datetime import datetime
from utils import require_auth, list_all, COMMON_CSS, report_header, report_header_close, persistent_cache

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="search")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse and filter all properties from custom objects")

# Auto-refresh every 10 minutes
if "last_refresh_explorer" not in st.session_state:
    st.session_state.last_refresh_explorer = time.time()

current_time = time.time()
if current_time - st.session_state.last_refresh_explorer > 600:
    st.session_state.last_refresh_explorer = current_time
    st.rerun()

# Select object
st.sidebar.markdown("### Filter Options")
object_type = st.sidebar.selectbox(
    "Select Custom Object",
    ["VRS Numbers", "Registrations", "Monthly Values"]
)

# Fetch data
@persistent_cache(ttl_seconds=600)
def fetch_data(obj_type):
    if obj_type == "VRS Numbers":
        return list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type",
             "usage_type", "number_created_at"],
            progress_label="Fetching VRS Numbers"
        )
    elif obj_type == "Registrations":
        return list_all(
            "2-58833629",
            ["registration_id", "registration_type", "email", "first_name", "last_name",
             "number", "submitted_at"],
            progress_label="Fetching Registrations"
        )
    else:
        return list_all(
            "2-46246179",
            ["number", "month_date", "usage_minutes", "ursa_minutes", "service_type"],
            progress_label="Fetching Monthly Values"
        )

records = fetch_data(object_type)

if not records:
    st.warning(f"No records found in {object_type}")
else:
    rows = [r.get("properties", {}) for r in records]
    df = pd.DataFrame(rows)

    st.metric("Total Records", len(df))

    # Search
    search = st.text_input("Search all fields", "")
    if search:
        mask = df.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
        df = df[mask]
        st.write(f"**Found {len(df)} records**")

    st.dataframe(df, use_container_width=True, height=500)

    csv = df.to_csv(index=False)
    st.download_button(
        "📥 Download CSV",
        csv,
        f"{object_type.lower().replace(' ', '_')}.csv",
        "text/csv"
    )

report_header_close()
