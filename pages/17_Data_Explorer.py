import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone
from utils import require_auth, list_all, fetch_all, get_secret, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse every property and build filtered reports across your custom objects", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

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

# operator label -> HubSpot operator. Some take no value / two values.
OPS = {
    "is":            {"hs": "EQ",               "values": 1},
    "is not":        {"hs": "NEQ",              "values": 1},
    "contains":      {"hs": "CONTAINS_TOKEN",   "values": 1},
    "is any of":     {"hs": "IN",               "values": "list"},
    "greater than":  {"hs": "GT",               "values": 1},
    "less than":     {"hs": "LT",               "values": 1},
    "is between":    {"hs": "BETWEEN",          "values": 2},
    "is known":      {"hs": "HAS_PROPERTY",     "values": 0},
    "is unknown":    {"hs": "NOT_HAS_PROPERTY", "values": 0},
}

# which operators make sense for each property type
OPS_FOR_TYPE = {
    "date":        ["is", "is not", "is between", "greater than", "less than", "is known", "is unknown"],
    "datetime":    ["is between", "greater than", "less than", "is known", "is unknown"],
    "number":      ["is", "is not", "greater than", "less than", "is between", "is known", "is unknown"],
    "enumeration": ["is", "is not", "is any of", "is known", "is unknown"],
    "bool":        ["is", "is known", "is unknown"],
    "string":      ["is", "is not", "contains", "is any of", "is known", "is unknown"],
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_property_schema(object_id):
    """Return list of {name, label, group, type, options} for every property."""
    resp = requests.get(f"{BASE_URL}/crm/v3/properties/{object_id}", headers=_headers, timeout=30)
    if resp.status_code != 200:
        return []
    out = []
    for p in resp.json().get("results", []):
        out.append({
            "name": p.get("name"),
            "label": p.get("label") or p.get("name"),
            "group": p.get("groupName") or "",
            "type": p.get("type") or "string",
            "options": [o.get("value") for o in (p.get("options") or []) if o.get("value") not in (None, "")],
        })
    return sorted(out, key=lambda x: (x["group"], x["label"].lower()))


@st.cache_data(ttl=600, show_spinner=False)
def run_report(object_id, properties, filter_groups):
    if filter_groups:
        return fetch_all(object_id, properties, filter_groups=filter_groups)
    return list_all(object_id, properties, progress_label="Fetching records")


def _to_epoch_ms(d, end_of_day=False):
    t = datetime(d.year, d.month, d.day, 23 if end_of_day else 0,
                 59 if end_of_day else 0, 59 if end_of_day else 0, tzinfo=timezone.utc)
    return str(int(t.timestamp() * 1000))


# ── object + display columns ────────────────────────────────────────────────
object_type = st.selectbox("Select Custom Object", list(OBJECTS.keys()), key="explorer_object")
obj = OBJECTS[object_type]

# reset filters when the object changes
if st.session_state.get("de_last_object") != object_type:
    st.session_state.de_last_object = object_type
    st.session_state.de_filter_ids = []
    st.session_state.de_next_id = 0

schema = fetch_property_schema(obj["id"])
if not schema:
    st.error("Could not load property list from HubSpot. Check the API token / object permissions.")
    st.stop()

available = [p["name"] for p in schema]
meta = {p["name"]: p for p in schema}
label_by_name = {p["name"]: p["label"] for p in schema}
defaults = [n for n in obj["defaults"] if n in available] or available[:10]

st.caption(f"{len(available)} properties available on **{object_type}**.")

selected = st.multiselect(
    "Columns to show",
    options=available,
    default=defaults,
    format_func=lambda n: f"{label_by_name.get(n, n)}  ·  {n}",
)
load_all_cols = st.checkbox("Show ALL properties as columns", value=False,
                            help="Slower — pulls every property for every record.")
display_props = available if load_all_cols else selected

# ── filter builder ──────────────────────────────────────────────────────────
st.markdown("#### Filters")
st.caption("All conditions are combined with **AND**. Leave empty to return every record.")

if "de_filter_ids" not in st.session_state:
    st.session_state.de_filter_ids = []
    st.session_state.de_next_id = 0

add_col, clear_col, _ = st.columns([1, 1, 4])
if add_col.button("➕ Add filter"):
    st.session_state.de_filter_ids.append(st.session_state.de_next_id)
    st.session_state.de_next_id += 1
if clear_col.button("🗑 Clear all"):
    st.session_state.de_filter_ids = []

built_filters = []
for fid in list(st.session_state.de_filter_ids):
    c_prop, c_op, c_val, c_del = st.columns([3, 2, 3, 0.6])
    prop = c_prop.selectbox(
        "Property", available, key=f"deprop_{fid}",
        format_func=lambda n: label_by_name.get(n, n),
    )
    ptype = meta[prop]["type"]
    allowed_ops = OPS_FOR_TYPE.get(ptype, OPS_FOR_TYPE["string"])
    op_label = c_op.selectbox("Condition", allowed_ops, key=f"deop_{fid}")
    op = OPS[op_label]
    hs_filter = None

    if op["values"] == 0:
        c_val.markdown("<div style='padding-top:1.8rem;color:#9CA3AF;font-size:0.85rem;'>— no value —</div>",
                       unsafe_allow_html=True)
        hs_filter = {"propertyName": prop, "operator": op["hs"]}

    elif ptype in ("date", "datetime"):
        if op["values"] == 2:
            d1 = c_val.date_input("From", key=f"deval_{fid}_a")
            d2 = c_val.date_input("To", key=f"deval_{fid}_b")
            if d1 and d2:
                hs_filter = {"propertyName": prop, "operator": "BETWEEN",
                             "value": _to_epoch_ms(d1), "highValue": _to_epoch_ms(d2, end_of_day=True)}
        else:
            d = c_val.date_input("Date", key=f"deval_{fid}")
            if d:
                hs_filter = {"propertyName": prop, "operator": op["hs"], "value": _to_epoch_ms(d)}

    elif op["values"] == "list":
        if meta[prop]["options"]:
            vals = c_val.multiselect("Values", meta[prop]["options"], key=f"deval_{fid}")
        else:
            raw = c_val.text_input("Values (comma-separated)", key=f"deval_{fid}")
            vals = [v.strip() for v in raw.split(",") if v.strip()]
        if vals:
            hs_filter = {"propertyName": prop, "operator": "IN", "values": vals}

    elif op["values"] == 2:  # numeric between
        v1 = c_val.number_input("From", key=f"deval_{fid}_a", value=0.0, format="%g")
        v2 = c_val.number_input("To", key=f"deval_{fid}_b", value=0.0, format="%g")
        hs_filter = {"propertyName": prop, "operator": "BETWEEN", "value": str(v1), "highValue": str(v2)}

    else:  # single value
        if ptype == "enumeration" and meta[prop]["options"]:
            v = c_val.selectbox("Value", meta[prop]["options"], key=f"deval_{fid}")
        elif ptype == "number":
            v = c_val.number_input("Value", key=f"deval_{fid}", value=0.0, format="%g")
            v = str(v)
        else:
            v = c_val.text_input("Value", key=f"deval_{fid}")
        if v not in (None, ""):
            hs_filter = {"propertyName": prop, "operator": op["hs"], "value": v}

    if c_del.button("✕", key=f"dedel_{fid}"):
        st.session_state.de_filter_ids.remove(fid)
        st.rerun()

    if hs_filter:
        built_filters.append(hs_filter)

# ── run ─────────────────────────────────────────────────────────────────────
st.markdown("")
run = st.button("▶ Run report", type="primary")

if not display_props:
    st.info("Pick at least one column (or tick “Show ALL properties”) before running.")
    st.stop()

if run:
    st.session_state.de_run = {
        "props": display_props,
        "filter_groups": [{"filters": built_filters}] if built_filters else [],
        "obj_id": obj["id"],
        "object_type": object_type,
    }

cfg = st.session_state.get("de_run")
if not cfg:
    st.info("Set your columns and filters, then press **Run report**.")
    st.stop()

records = run_report(cfg["obj_id"], cfg["props"], cfg["filter_groups"])

if not records:
    st.warning("No records matched your filters.")
    st.stop()

df = pd.DataFrame([r.get("properties", {}) for r in records])
cols = [c for c in cfg["props"] if c in df.columns]
df = df[cols] if cols else df
df = df.rename(columns={c: label_by_name.get(c, c) for c in df.columns})

m1, m2, m3 = st.columns(3)
m1.metric("Records", f"{len(df):,}")
m2.metric("Columns", len(df.columns))
m3.metric("Filters applied", len(cfg["filter_groups"][0]["filters"]) if cfg["filter_groups"] else 0)

search = st.text_input("🔍 Search results", "")
if search:
    mask = df.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
    df = df[mask]
    st.write(f"**{len(df):,} rows matching '{search}'**")

st.dataframe(df, use_container_width=True, height=520)

st.download_button(
    "📥 Download CSV",
    df.to_csv(index=False),
    f"{cfg['object_type'].lower().replace(' ', '_')}_report_{datetime.now().strftime('%Y%m%d')}.csv",
    "text/csv",
)

report_header_close()
