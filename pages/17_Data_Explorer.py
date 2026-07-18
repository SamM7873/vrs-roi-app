import streamlit as st
import pandas as pd
from datetime import datetime
from utils import require_auth, list_all, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse all properties across VRS Numbers, Registrations, and Monthly Values", section="Analytics")

object_type = st.selectbox(
    "Select Custom Object",
    ["VRS Numbers", "Registrations", "Monthly Values"],
    key="explorer_object"
)

@st.cache_data(ttl=600, show_spinner=False)
def fetch_explorer_data(obj_type):
    if obj_type == "VRS Numbers":
        return list_all("2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type",
             "usage_type", "number_created_at", "credit_type", "ursa_sum_of_total_billable_inbound_minutes"],
            progress_label="Fetching VRS Numbers")
    elif obj_type == "Registrations":
        return list_all("2-58833629",
            ["registration_id", "registration_type", "email", "first_name", "last_name",
             "number", "submitted_at", "registered_at"],
            progress_label="Fetching Registrations")
    else:
        return list_all("2-46246179",
            ["number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"],
            progress_label="Fetching Monthly Values")

records = fetch_explorer_data(object_type)

if not records:
    st.warning(f"No records found in {object_type}")
else:
    df = pd.DataFrame([r.get("properties", {}) for r in records])

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Records", len(df))
    col2.metric("Columns", len(df.columns))
    col3.metric("Fetched", "Just now")

    search = st.text_input("🔍 Search all fields", "")
    if search:
        mask = df.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
        df = df[mask]
        st.write(f"**Found {len(df)} records matching '{search}'**")

    st.dataframe(df, use_container_width=True, height=500)

    csv = df.to_csv(index=False)
    st.download_button(
        "📥 Download CSV",
        csv,
        f"{object_type.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
        "text/csv"
    )

report_header_close()
