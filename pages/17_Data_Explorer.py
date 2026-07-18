import streamlit as st
import pandas as pd
import time
from datetime import datetime
from utils import require_auth, list_all, COMMON_CSS, report_header, report_header_close, persistent_cache

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="🔍")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse and filter all properties from custom objects")

# Auto-refresh every 10 minutes for real-time updates
if "last_refresh_explorer" not in st.session_state:
    st.session_state.last_refresh_explorer = time.time()

current_time = time.time()
if current_time - st.session_state.last_refresh_explorer > 600:  # 10 minutes
    st.session_state.last_refresh_explorer = current_time
    st.rerun()

# Define custom objects
CUSTOM_OBJECTS = {
    "VRS Numbers": {
        "id": "2-40974683",
        "properties": [
            "number", "email", "first_name", "last_name", "number_status", "service_type",
            "usage_type", "number_created_at", "credit_type", "city", "state",
            "ursa_sum_of_total_billable_inbound_minutes", "ursa_sum_of_total_billable_outbound_minutes",
            "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call",
            "registered_at", "registration_created_at", "registration_updated_at",
            "bandwidth_order_type", "deleted_reason", "number_deleted_at",
            "emerg_city", "emerg_state", "emerg_zip_code", "zip_code",
            "portin_status", "lex_verification_status", "urd_status"
        ]
    },
    "Registrations": {
        "id": "2-58833629",
        "properties": [
            "registration_id", "registration_type", "usage_type", "email", "first_name", "last_name",
            "number", "submitted_at", "registered_at", "lex_verification_status", "lex_verified_at",
            "urd_status", "urd_registration_created_at", "is_cancelled", "registration_created_at",
            "portin_status", "state"
        ]
    },
    "Monthly Values": {
        "id": "2-46246179",
        "properties": [
            "number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"
        ]
    }
}

# Sidebar filters
st.sidebar.markdown("### Filter Options")
selected_object = st.sidebar.selectbox("Select Custom Object", list(CUSTOM_OBJECTS.keys()))
object_config = CUSTOM_OBJECTS[selected_object]

# Search filter
search_term = st.sidebar.text_input("Search (searches all text fields)", "")

# Fetch data decorator
@persistent_cache(ttl_seconds=600)
def fetch_explorer_data(object_id, object_name):
    """Fetch all records from custom object"""
    records = list_all(
        object_id,
        CUSTOM_OBJECTS[object_name]["properties"],
        progress_label=f"Fetching {object_name} records"
    )
    return records

# Load data
records = fetch_explorer_data(object_config["id"], selected_object)

if not records:
    st.warning(f"No records found in {selected_object}")
else:
    # Convert to DataFrame
    rows = []
    for r in records:
        p = r.get("properties", {})
        rows.append(p)

    df = pd.DataFrame(rows)

    # Display stats
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Records", len(df))
    col2.metric("Total Columns", len(df.columns))
    col3.metric("Fetched At", datetime.now().strftime("%H:%M:%S"))

    # Column selection
    st.markdown("### Columns")
    all_cols = st.checkbox("Select All Columns", value=True)

    if all_cols:
        display_cols = df.columns.tolist()
    else:
        display_cols = st.multiselect(
            "Choose columns to display",
            df.columns.tolist(),
            default=df.columns.tolist()[:5]
        )

    if not display_cols:
        st.warning("Please select at least one column")
    else:
        display_df = df[display_cols].copy()

        # Apply search filter
        if search_term:
            mask = display_df.astype(str).apply(lambda x: x.str.contains(search_term, case=False, na=False)).any(axis=1)
            display_df = display_df[mask]
            st.markdown(f"**Filtered to {len(display_df)} records** matching '{search_term}'")

        # Sort options
        st.markdown("### Sorting")
        col1, col2 = st.columns(2)
        with col1:
            sort_col = st.selectbox("Sort by", display_cols)
        with col2:
            sort_order = st.radio("Order", ["Ascending", "Descending"], horizontal=True)

        if sort_col:
            ascending = sort_order == "Ascending"
            display_df = display_df.sort_values(sort_col, ascending=ascending)

        # Display data
        st.markdown(f"### Data ({len(display_df)} records)")
        st.dataframe(display_df, use_container_width=True, height=500)

        # Download options
        st.markdown("### Export")
        col1, col2, col3 = st.columns(3)

        with col1:
            csv = display_df.to_csv(index=False)
            st.download_button(
                "📥 Download CSV",
                csv,
                f"{selected_object.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                "text/csv"
            )

        with col2:
            st.metric("Columns", len(display_cols))

        with col3:
            st.metric("Rows", len(display_df))

report_header_close()
