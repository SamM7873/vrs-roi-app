import streamlit as st
import pandas as pd
import requests
from datetime import datetime
from utils import require_auth, list_all, get_secret, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse every property across VRS Numbers, Registrations, and Monthly Values", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

# object type id + a sensible default set of properties per object
OBJECTS = {
    "VRS Numbers": {
        "id": "2-40974683",
        "defaults": ["number", "email", "first_name", "last_name", "account_status",
                     "service_type", "credit_type", "credit_plan_name", "city",
                     "account_created_at", "number_status", "usage_type"],
    },
    "Registrations": {
        "id": "2-58833629",
        "defaults": ["registration_id", "registration_type", "email", "first_name",
                     "last_name", "number", "submitted_at", "registered_at"],
    },
    "Monthly Values": {
        "id": "2-46246179",
        "defaults": ["number", "month_date", "service_type", "usage_minutes",
                     "ursa_minutes", "cfz_minutes", "credit_type", "usage_total_value"],
    },
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_property_schema(object_id):
    """Return list of {name, label, group} for every property on the object."""
    url = f"{BASE_URL}/crm/v3/properties/{object_id}"
    resp = requests.get(url, headers=_headers, timeout=30)
    if resp.status_code != 200:
        return []
    out = []
    for p in resp.json().get("results", []):
        out.append({
            "name": p.get("name"),
            "label": p.get("label") or p.get("name"),
            "group": p.get("groupName") or "",
        })
    return sorted(out, key=lambda x: (x["group"], x["label"].lower()))


@st.cache_data(ttl=600, show_spinner=False)
def fetch_records(object_id, properties):
    return list_all(object_id, properties, progress_label="Fetching records")


object_type = st.selectbox("Select Custom Object", list(OBJECTS.keys()), key="explorer_object")
obj = OBJECTS[object_type]

schema = fetch_property_schema(obj["id"])
if not schema:
    st.error("Could not load property list from HubSpot. Check the API token / object permissions.")
    st.stop()

available = [p["name"] for p in schema]
label_by_name = {p["name"]: p["label"] for p in schema}
group_by_name = {p["name"]: p["group"] for p in schema}

# defaults, filtered to only those that actually exist on the object
defaults = [n for n in obj["defaults"] if n in available] or available[:10]

st.caption(f"{len(available)} properties available on **{object_type}**. Pick which to load (fewer = faster).")

selected = st.multiselect(
    "Properties to display",
    options=available,
    default=defaults,
    format_func=lambda n: f"{label_by_name.get(n, n)}  ·  {n}",
)

col_a, col_b = st.columns([1, 3])
with col_a:
    load_all = st.checkbox("Load ALL properties", value=False,
                           help="Slower — pulls every property for every record.")

props_to_fetch = available if load_all else selected

if not props_to_fetch:
    st.info("Select at least one property (or tick “Load ALL properties”) to load data.")
    st.stop()

records = fetch_records(obj["id"], props_to_fetch)

if not records:
    st.warning(f"No records found in {object_type}")
    st.stop()

df = pd.DataFrame([r.get("properties", {}) for r in records])
# keep only requested columns, in the order chosen
cols = [c for c in props_to_fetch if c in df.columns]
df = df[cols] if cols else df
# friendly column headers: "Label (internal_name)"
df = df.rename(columns={c: f"{label_by_name.get(c, c)}" for c in df.columns})

m1, m2, m3 = st.columns(3)
m1.metric("Total Records", f"{len(df):,}")
m2.metric("Columns", len(df.columns))
m3.metric("Fetched", datetime.now().strftime("%b %d, %I:%M %p"))

search = st.text_input("🔍 Search all fields", "")
if search:
    mask = df.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
    df = df[mask]
    st.write(f"**Found {len(df):,} records matching '{search}'**")

st.dataframe(df, use_container_width=True, height=520)

st.download_button(
    "📥 Download CSV",
    df.to_csv(index=False),
    f"{object_type.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
    "text/csv",
)

report_header_close()
