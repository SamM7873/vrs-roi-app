import streamlit as st
import pandas as pd
import altair as alt
import requests
import copy
import time
from datetime import datetime, timezone
from utils import require_auth, list_all, get_secret, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Data Explorer", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Data Explorer", "Browse, filter, join and chart your custom objects", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

OBJECTS = {
    "VRS Numbers": {
        "id": "2-40974683", "tag": "Numbers",
        "defaults": ["number", "email", "first_name", "last_name", "account_status",
                     "service_type", "credit_type", "credit_plan_name", "city",
                     "account_created_at", "number_status", "usage_type"],
    },
    "Registrations": {
        "id": "2-58833629", "tag": "Registrations",
        "defaults": ["registration_id", "registration_type", "email", "first_name",
                     "last_name", "number", "submitted_at", "registered_at"],
    },
    "Monthly Values": {
        "id": "2-46246179", "tag": "Monthly",
        "defaults": ["number", "month_date", "service_type", "usage_minutes",
                     "ursa_minutes", "cfz_minutes", "credit_type", "usage_total_value"],
    },
    "Tickets": {
        "id": "tickets", "tag": "Tickets",
        "defaults": ["subject", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority",
                     "hs_ticket_category", "source_type", "createdate", "hs_lastmodifieddate"],
    },
}

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


def _search_over_cap(object_id, properties, filter_groups):
    """Page through a filtered HubSpot search with no 10k limit."""
    url = f"{BASE_URL}/crm/v3/objects/{object_id}/search"
    props = list(properties)
    if "hs_object_id" not in props:
        props = props + ["hs_object_id"]

    all_results = []
    last_id = None
    loader = st.empty()
    WINDOW = 9900

    while True:
        groups = copy.deepcopy(filter_groups) if filter_groups else [{"filters": []}]
        if last_id is not None:
            for g in groups:
                g["filters"].append({"propertyName": "hs_object_id", "operator": "GT", "value": str(last_id)})

        after = None
        window_count = 0
        hit_cap = False
        while True:
            payload = {
                "limit": 100, "properties": props, "filterGroups": groups,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
            }
            if after:
                payload["after"] = after
            resp = requests.post(url, headers=_headers, json=payload, timeout=60)
            if resp.status_code == 429:
                time.sleep(1.5)
                continue
            if resp.status_code != 200:
                loader.empty()
                if not all_results:
                    st.error(f"Error {resp.status_code}: {resp.text}")
                return all_results
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            all_results.extend(results)
            window_count += len(results)
            last_id = results[-1].get("properties", {}).get("hs_object_id") or results[-1].get("id")
            loader.markdown(
                f"<div style='padding:0.6rem 1rem;background:#F4F1E8;border:1px solid #DDD9CC;"
                f"border-radius:10px;'>Fetching… <strong>{len(all_results):,}</strong> records</div>",
                unsafe_allow_html=True,
            )
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            if window_count >= WINDOW:
                hit_cap = True
                break
            time.sleep(0.1)

        if not hit_cap:
            break

    loader.empty()
    return all_results


def fetch_object(object_id, properties, applicable_filters):
    """Fetch one object's records, applying only the filters that exist on it."""
    try:
        if applicable_filters:
            return _search_over_cap(object_id, properties, [{"filters": applicable_filters}])
        return list_all(object_id, properties, progress_label="Fetching records")
    except Exception as e:
        st.error(f"Fetch failed: {e}")
        return []


def _to_epoch_ms(d, end_of_day=False):
    t = datetime(d.year, d.month, d.day, 23 if end_of_day else 0,
                 59 if end_of_day else 0, 59 if end_of_day else 0, tzinfo=timezone.utc)
    return str(int(t.timestamp() * 1000))


# ── object selection (one or more) ──────────────────────────────────────────
object_types = st.multiselect(
    "Select Custom Object(s)", list(OBJECTS.keys()), default=["VRS Numbers"],
    help="Pick more than one to match/join them on a shared key.",
)
if not object_types:
    st.info("Select at least one custom object to begin.")
    st.stop()

multi = len(object_types) > 1

# reset filters when the selected set changes
sel_key = tuple(sorted(object_types))
if st.session_state.get("de_last_sel") != sel_key:
    st.session_state.de_last_sel = sel_key
    st.session_state.de_filter_ids = []
    st.session_state.de_next_id = 0

# load + merge schemas across the selected objects
per_obj_available = {}
meta = {}
label_by_name = {}
for otype in object_types:
    sch = fetch_property_schema(OBJECTS[otype]["id"])
    if not sch:
        st.error(f"Could not load properties for {otype}. Check the API token / permissions.")
        st.stop()
    per_obj_available[otype] = {p["name"] for p in sch}
    for p in sch:
        meta.setdefault(p["name"], p)          # first object wins on shared names
        label_by_name.setdefault(p["name"], p["label"])

available = sorted(set().union(*per_obj_available.values()))

# union of each object's defaults, restricted to what exists
default_cols = []
for otype in object_types:
    default_cols += [c for c in OBJECTS[otype]["defaults"] if c in per_obj_available[otype]]
default_cols = list(dict.fromkeys(default_cols)) or available[:10]

# ── combine mode (only when >1 object) ──────────────────────────────────────
# Joining is optional: multiple objects can be joined on a shared key, or just
# stacked together (listed one after another, no join required).
join_key = None
join_how = "inner"
combine_mode = "single"
if multi:
    common = sorted(set.intersection(*per_obj_available.values()))
    # Tickets link to VRS Numbers via association (not a shared column). If Tickets
    # is selected alongside a number-bearing object, offer a "number via association" key.
    tickets_number_join = ("Tickets" in object_types
                           and any(o != "Tickets" and "number" in per_obj_available[o] for o in object_types))
    join_options = list(common)
    if tickets_number_join and "number" not in join_options:
        join_options = ["number"] + join_options

    mode_options = ["Stack (list all, no join)"]
    if join_options:
        mode_options = ["Join on shared key", "Stack (list all, no join)"]
    combine_mode = st.radio("With multiple objects", mode_options, horizontal=True, key="de_combine")

    if combine_mode == "Join on shared key":
        jk_default = "number" if "number" in join_options else join_options[0]

        def _jk_label(n):
            if n == "number" and tickets_number_join and "number" not in common:
                return "VRS Number (via ticket association)  ·  number"
            return f"{label_by_name.get(n, n)}  ·  {n}"

        jc1, jc2 = st.columns([2, 2])
        join_key = jc1.selectbox("Match (join) on", join_options,
                                 index=join_options.index(jk_default), format_func=_jk_label)
        match_mode = jc2.radio("Include", ["Only matched (in all)", "All records"], horizontal=True)
        join_how = "inner" if match_mode.startswith("Only") else "outer"

do_join = multi and combine_mode == "Join on shared key"

st.caption(f"{len(available)} properties available across **{', '.join(object_types)}**.")

selected = st.multiselect(
    "Columns to show", options=available, default=default_cols,
    format_func=lambda n: f"{label_by_name.get(n, n)}  ·  {n}",
)
load_all_cols = st.checkbox("Show ALL properties as columns", value=False,
                            help="Slower — pulls every property for every record.")
display_props = available if load_all_cols else selected

resolve_numbers = False
if "Tickets" in object_types:
    resolve_numbers = st.checkbox(
        "🔗 Show associated VRS Numbers for tickets", value=False,
        help="Looks up each ticket's directly-associated VRS Number(s) and adds "
             "“Associated Numbers” + “Number Count” columns.")

# ── filter builder ──────────────────────────────────────────────────────────
st.markdown("#### Filters")
st.caption("All conditions are combined with **AND**. Each filter applies to whichever object has that property. Leave empty to return every record.")

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
    prop = c_prop.selectbox("Property", available, key=f"deprop_{fid}",
                            format_func=lambda n: label_by_name.get(n, n))
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

    elif op["values"] == 2:
        v1 = c_val.number_input("From", key=f"deval_{fid}_a", value=0.0, format="%g")
        v2 = c_val.number_input("To", key=f"deval_{fid}_b", value=0.0, format="%g")
        hs_filter = {"propertyName": prop, "operator": "BETWEEN", "value": str(v1), "highValue": str(v2)}

    else:
        if ptype == "enumeration" and meta[prop]["options"]:
            v = c_val.selectbox("Value", meta[prop]["options"], key=f"deval_{fid}")
        elif ptype == "number":
            v = str(c_val.number_input("Value", key=f"deval_{fid}", value=0.0, format="%g"))
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


def fetch_object_constrained(oid, props, base_filters, key_prop, key_values, label):
    """Fetch an object restricted to key_prop IN key_values (batched)."""
    vals = [str(v) for v in key_values if v not in (None, "")]
    if not vals:
        return []
    all_recs = []
    loader = st.empty()
    for i in range(0, len(vals), 100):  # HubSpot IN operator allows max 100 values
        chunk = vals[i:i + 100]
        fg = [{"filters": base_filters + [{"propertyName": key_prop, "operator": "IN", "values": chunk}]}]
        all_recs.extend(_search_over_cap(oid, props, fg))
        loader.markdown(
            f"<div style='padding:0.6rem 1rem;background:#F4F1E8;border:1px solid #DDD9CC;"
            f"border-radius:10px;'>Fetching {label} for matched keys… <strong>{len(all_recs):,}</strong></div>",
            unsafe_allow_html=True,
        )
    loader.empty()
    return all_recs


def resolve_ticket_numbers(ticket_ids):
    """Map ticket id -> list of associated VRS Number values (direct association)."""
    NUM_OBJ = "2-40974683"
    tid_to_nids = {}
    all_nids = set()
    loader = st.empty()
    ids = [str(t) for t in ticket_ids if t]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            ar = requests.post(f"{BASE_URL}/crm/v4/associations/tickets/{NUM_OBJ}/batch/read",
                               headers=_headers, json={"inputs": [{"id": t} for t in chunk]}, timeout=60)
        except requests.exceptions.RequestException:
            continue
        if ar.status_code in (200, 207):
            for result in ar.json().get("results", []):
                tid = str(result.get("from", {}).get("id", ""))
                nids = [str(a.get("toObjectId") or a.get("id") or "") for a in result.get("to", [])]
                nids = [n for n in nids if n]
                if nids:
                    tid_to_nids[tid] = nids
                    all_nids.update(nids)
        loader.markdown(
            f"<div style='padding:0.6rem 1rem;background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;'>"
            f"Resolving ticket → number associations… {min(i + 100, len(ids)):,}/{len(ids):,}</div>",
            unsafe_allow_html=True)

    # number object id -> number value
    nid_to_num = {}
    nids = list(all_nids)
    for i in range(0, len(nids), 100):
        chunk = nids[i:i + 100]
        try:
            br = requests.post(f"{BASE_URL}/crm/v3/objects/{NUM_OBJ}/batch/read", headers=_headers,
                               json={"properties": ["number"], "inputs": [{"id": n} for n in chunk]}, timeout=60)
        except requests.exceptions.RequestException:
            continue
        if br.status_code in (200, 207):
            for r in br.json().get("results", []):
                nid_to_num[str(r.get("id"))] = r.get("properties", {}).get("number") or str(r.get("id"))
    loader.empty()
    return {tid: [nid_to_num.get(n, n) for n in ns] for tid, ns in tid_to_nids.items()}


def _frame_for(otype, key_values=None):
    """Fetch one object's records as a tagged DataFrame. If key_values is given,
    restrict the fetch to join_key IN key_values (used to fetch the secondary
    object only for keys found in the primary)."""
    oid = OBJECTS[otype]["id"]
    oavail = per_obj_available[otype]
    oprops = [p for p in display_props if p in oavail]
    if do_join and join_key in oavail and join_key not in oprops:
        oprops.append(join_key)
    ofilters = [f for f in built_filters if f["propertyName"] in oavail]

    if key_values is not None and join_key in oavail:
        recs = fetch_object_constrained(oid, oprops, ofilters, join_key, key_values, otype)
    else:
        recs = fetch_object(oid, oprops, ofilters)

    fdf = pd.DataFrame([r.get("properties", {}) for r in recs])
    keep = [c for c in oprops if c in fdf.columns]
    fdf = fdf[keep] if keep else fdf

    # tickets joined to VRS Numbers via association: attach a real "number" column
    # (one row per ticket↔number) so the merge on "number" works
    if otype == "Tickets" and do_join and join_key == "number" and "number" not in fdf.columns and recs:
        tid_map = resolve_ticket_numbers([r.get("id") for r in recs])
        fdf = fdf.copy()
        fdf["number"] = [tid_map.get(str(r.get("id")), []) for r in recs]
        fdf = fdf.explode("number").dropna(subset=["number"])

    # enrich tickets with their associated VRS Numbers (display columns)
    if otype == "Tickets" and resolve_numbers and recs and "Associated Numbers" not in fdf.columns:
        tid_map = resolve_ticket_numbers([r.get("id") for r in recs])
        per_row = [tid_map.get(str(r.get("id")), []) for r in recs]
        fdf = fdf.copy()
        fdf["Associated Numbers"] = [", ".join(v) for v in per_row]
        fdf["Number Count"] = [len(v) for v in per_row]

    return fdf


def build_dataframe():
    """Fetch objects — single, stacked (no join), or joined on a shared key."""
    if not do_join:
        # single object, or multiple objects stacked (listed together)
        frames = []
        for otype in object_types:
            fdf = _frame_for(otype)
            fdf = fdf.rename(columns={c: label_by_name.get(c, c) for c in fdf.columns})
            if multi:
                fdf.insert(0, "Object", otype)
            frames.append(fdf)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    # Order: objects that carry a filter come first (smaller, drives the join),
    # otherwise keep the user's selection order. This makes e.g. filtered
    # Numbers fetch first, then Monthly Values only for those numbers.
    def has_filter(otype):
        return any(f["propertyName"] in per_obj_available[otype] for f in built_filters)
    ordered = sorted(object_types, key=lambda o: (not has_filter(o), object_types.index(o)))

    frames = []
    key_values = None  # keys found so far — constrain later fetches (inner join only)
    for otype in ordered:
        fdf = _frame_for(otype, key_values if join_how == "inner" else None)
        if join_key not in fdf.columns:
            st.warning(f"{otype} returned no “{label_by_name.get(join_key, join_key)}” values — skipped from the join.")
            continue
        tag = OBJECTS[otype]["tag"]
        frames.append(fdf.rename(columns={c: f"{tag}::{c}" for c in fdf.columns if c != join_key}))

        found = set(fdf[join_key].dropna().astype(str))
        if join_how == "inner":
            key_values = found if key_values is None else (key_values & found)
            if not key_values:
                break  # nothing can match — stop early

    if not frames:
        return pd.DataFrame()

    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on=join_key, how=join_how)

    def friendly(col):
        if col == join_key:
            return label_by_name.get(join_key, join_key)
        tag, _, internal = col.partition("::")
        return f"{tag} · {label_by_name.get(internal, internal)}"

    return merged.rename(columns={c: friendly(c) for c in merged.columns})


if run:
    with st.status("Running report…", expanded=True) as status:
        st.write(f"Objects: {', '.join(object_types)}")
        st.write(f"Columns: {len(display_props)} · Filters: {len(built_filters)}")
        try:
            result_df = build_dataframe()
        except Exception as e:
            status.update(label="Report failed", state="error")
            st.exception(e)
            st.stop()
        st.session_state.de_df = result_df
        st.session_state.de_info = {
            "objects": object_types,
            "filters": len(built_filters),
            "joined": do_join,
            "stacked": multi and not do_join,
            "join_how": join_how,
            "join_label": label_by_name.get(join_key, join_key) if do_join else None,
        }
        status.update(label=f"Done — {len(result_df):,} records", state="complete")

df = st.session_state.get("de_df")
info = st.session_state.get("de_info")
if df is None:
    st.info("Set your objects, columns and filters, then press **Run report**.")
    st.stop()
if df.empty:
    st.warning("No records matched your filters. Try loosening a condition — "
               "e.g. check the exact value/spelling, or use **contains** instead of **is**.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Records", f"{len(df):,}")
m2.metric("Columns", len(df.columns))
m3.metric("Filters applied", info["filters"])

if info.get("joined"):
    st.caption(f"Matched **{', '.join(info['objects'])}** on **{info['join_label']}** "
               f"({'only records present in all' if info.get('join_how') == 'inner' else 'all records, outer join'}).")
elif info.get("stacked"):
    st.caption(f"Stacked **{', '.join(info['objects'])}** (listed together, no join). "
               f"See the **Object** column for each row's source.")

search = st.text_input("🔍 Search results", "")
view = df
if search:
    mask = view.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
    view = view[mask]
    st.write(f"**{len(view):,} rows matching '{search}'**")

st.dataframe(view, use_container_width=True, height=520)

st.download_button(
    "📥 Download CSV",
    view.to_csv(index=False),
    f"data_explorer_report_{datetime.now().strftime('%Y%m%d')}.csv",
    "text/csv",
)

# ── Visualize ───────────────────────────────────────────────────────────────
st.markdown("#### Visualize")

PRIMARY = "#C9A876"     # beige
SECONDARY = "#0D3B26"   # deep green
DONUT_SCHEME = ["#C9A876", "#0D3B26", "#8FA998", "#B59467", "#6B7280",
                "#3B7A57", "#DDBD8E", "#5A6A5A", "#A9CBB7", "#8C6A3F"]


def _agg_by(frame, gcol, measure_choice, num_cols):
    """Return (grouped_df with Value, label) for the chosen measure."""
    w = frame.copy()
    w[gcol] = w[gcol].fillna("—").replace("", "—")
    if measure_choice == "Count of records":
        g = w.groupby(gcol).size().reset_index(name="Value")
        return g, "Records"
    agg, _, col = measure_choice.partition(" of ")
    w["_num"] = pd.to_numeric(w[col], errors="coerce")
    gb = w.groupby(gcol)["_num"]
    g = (gb.sum() if agg == "Sum" else gb.mean()).reset_index(name="Value")
    return g, measure_choice


def _build_visual_pdf(chart_type, grouped, group_col, measure_label,
                      has_split, split_col, stack_mode, subtitle):
    """Render the current chart + its data table to a presentation-ready PDF."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    beige, green = "#C9A876", "#0D3B26"
    scheme = DONUT_SCHEME
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # ── page 1: chart ──
        fig, ax = plt.subplots(figsize=(11, 6.5))
        if has_split:
            piv = grouped.pivot_table(index=group_col, columns=split_col,
                                      values="Value", aggfunc="sum").fillna(0)
            kind = "barh" if chart_type == "Horizontal Bar" else "bar"
            piv.plot(kind=kind, stacked=(stack_mode == "Stacked"), ax=ax, color=scheme, width=0.8)
            ax.legend(title=split_col, fontsize=7)
        else:
            cats = grouped[group_col].astype(str).tolist()
            vals = pd.to_numeric(grouped["Value"], errors="coerce").fillna(0).tolist()
            if chart_type == "Horizontal Bar":
                ax.barh(cats, vals, color=beige); ax.invert_yaxis()
            elif chart_type == "Line":
                ax.plot(cats, vals, color=beige, marker="o", linewidth=2)
            elif chart_type == "Area":
                ax.fill_between(range(len(cats)), vals, color=beige, alpha=0.55)
                ax.plot(range(len(cats)), vals, color=green, linewidth=2)
                ax.set_xticks(range(len(cats))); ax.set_xticklabels(cats)
            elif chart_type == "Donut":
                ax.pie(vals, labels=cats, colors=scheme, autopct="%1.0f%%",
                       wedgeprops=dict(width=0.42), textprops=dict(fontsize=8))
            else:  # Vertical Bar / combo
                ax.bar(cats, vals, color=beige)
        if chart_type != "Donut":
            ax.set_ylabel(measure_label)
            if chart_type != "Horizontal Bar":
                plt.setp(ax.get_xticklabels(), rotation=40, ha="right", fontsize=8)
        ax.set_title(f"{measure_label} by {group_col}", fontsize=13, fontweight="bold")
        fig.text(0.02, 0.015, subtitle, fontsize=7, color="#666")
        fig.tight_layout(rect=[0, 0.03, 1, 1])
        pdf.savefig(fig); plt.close(fig)

        # ── table page(s): the numbers behind the chart ──
        if has_split:
            tdf = grouped.pivot_table(index=group_col, columns=split_col,
                                      values="Value", aggfunc="sum").fillna(0).round(1).reset_index()
        else:
            tdf = grouped[[group_col, "Value"]].copy()
            tdf.columns = [group_col, measure_label]
            tdf[measure_label] = pd.to_numeric(tdf[measure_label], errors="coerce").round(1)
        per_page = 26
        for start in range(0, max(len(tdf), 1), per_page):
            chunk = tdf.iloc[start:start + per_page]
            fig, ax = plt.subplots(figsize=(11, 8.5)); ax.axis("off")
            ax.set_title("Data table", fontsize=12, fontweight="bold", loc="left")
            tbl = ax.table(cellText=chunk.astype(str).values,
                           colLabels=[str(c) for c in chunk.columns],
                           loc="upper center", cellLoc="left")
            tbl.auto_set_font_size(False); tbl.set_fontsize(8); tbl.scale(1, 1.4)
            for (r, _c), cell in tbl.get_celld().items():
                if r == 0:
                    cell.set_facecolor(green)
                    cell.set_text_props(color="white", fontweight="bold")
            pdf.savefig(fig); plt.close(fig)
    return buf.getvalue()


if view.empty:
    st.info("No rows to chart.")
else:
    r1c1, r1c2, r1c3 = st.columns([2, 2, 2])
    chart_type = r1c1.selectbox(
        "Chart type",
        ["Vertical Bar", "Horizontal Bar", "Line", "Area", "Donut", "Bar + Line (combo)"],
        key="viz_type",
    )
    group_col = r1c2.selectbox("Group by (category / x-axis)", list(view.columns), key="viz_group")

    numeric_cols = [c for c in view.columns
                    if c != group_col and pd.to_numeric(view[c], errors="coerce").notna().any()]
    measure_opts = (["Count of records"]
                    + [f"Sum of {c}" for c in numeric_cols]
                    + [f"Average of {c}" for c in numeric_cols])
    measure = r1c3.selectbox("Measure", measure_opts, key="viz_measure")

    r2c1, r2c2, r2c3 = st.columns([2, 2, 2])
    top_n = int(r2c1.number_input("Top N", min_value=3, max_value=100, value=15, key="viz_topn"))
    sort_desc = r2c2.checkbox("Sort by value (largest first)", value=True, key="viz_sort")
    # combo needs a second measure (drawn as the line)
    measure2 = None
    if chart_type == "Bar + Line (combo)":
        measure2 = r2c3.selectbox("Line measure", measure_opts,
                                  index=min(1, len(measure_opts) - 1), key="viz_measure2")

    is_bar = chart_type in ("Vertical Bar", "Horizontal Bar")
    r3c1, r3c2, r3c3 = st.columns([2, 2, 2])
    split_col = "(none)"
    stack_mode = "Stacked"
    if is_bar:
        split_col = r3c1.selectbox("Split by (color)", ["(none)"] + [c for c in view.columns if c != group_col],
                                   key="viz_split")
        if split_col != "(none)":
            stack_mode = r3c2.radio("Bars", ["Stacked", "Grouped"], horizontal=True, key="viz_stack")
    data_labels = r3c3.checkbox("Show data labels", value=False, key="viz_labels")
    has_split = is_bar and split_col != "(none)"

    # detect a date-like x for line/area so trends read chronologically
    x_dt = pd.to_datetime(view[group_col], errors="coerce")
    is_temporal = x_dt.notna().mean() > 0.6 and chart_type in ("Line", "Area", "Bar + Line (combo)")

    # ── aggregate ───────────────────────────────────────────────────────────
    if has_split:
        w = view.copy()
        w[group_col] = w[group_col].fillna("—").replace("", "—")
        w[split_col] = w[split_col].fillna("—").replace("", "—")
        if measure == "Count of records":
            grouped = w.groupby([group_col, split_col]).size().reset_index(name="Value")
            measure_label = "Records"
        else:
            agg, _, col = measure.partition(" of ")
            w["_num"] = pd.to_numeric(w[col], errors="coerce")
            gb = w.groupby([group_col, split_col])["_num"]
            grouped = (gb.sum() if agg == "Sum" else gb.mean()).reset_index(name="Value")
            measure_label = measure
        totals = grouped.groupby(group_col)["Value"].sum().sort_values(ascending=not sort_desc)
        grouped = grouped[grouped[group_col].isin(totals.head(top_n).index)]
        group_totals = totals
    else:
        grouped, measure_label = _agg_by(view, group_col, measure, numeric_cols)
        if is_temporal:
            grouped["_sort"] = pd.to_datetime(grouped[group_col], errors="coerce")
            grouped = grouped.sort_values("_sort").drop(columns="_sort").head(top_n)
        else:
            grouped = grouped.sort_values("Value", ascending=not sort_desc).head(top_n)
        group_totals = grouped.set_index(group_col)["Value"]

    x_enc = (alt.X(f"{group_col}:T", title=group_col) if is_temporal
             else alt.X(f"{group_col}:N", sort=("-y" if sort_desc else "y"),
                        title=group_col, axis=alt.Axis(labelAngle=-40)))

    # summary cards
    cc1, cc2, cc3 = st.columns(3)
    cc1.metric("Total records", f"{len(view):,}")
    cc2.metric(f"Unique {group_col}", f"{view[group_col].fillna('—').replace('', '—').nunique():,}")
    if not group_totals.empty:
        gt = group_totals.sort_values(ascending=False)
        cc3.metric(f"Top {group_col}", str(gt.index[0])[:22], help=f"{measure_label}: {gt.iloc[0]:,.1f}")

    color_enc = alt.Color(f"{split_col}:N", scale=alt.Scale(range=DONUT_SCHEME),
                          legend=alt.Legend(title=split_col)) if has_split else alt.value(PRIMARY)
    tip = [alt.Tooltip(f"{group_col}:T" if is_temporal else f"{group_col}:N")]
    if has_split:
        tip.append(alt.Tooltip(f"{split_col}:N"))
    tip.append(alt.Tooltip("Value:Q", format=",.1f", title=measure_label))

    def _labels(base_enc, horizontal=False):
        """Data-label text layer (only when not split, to stay legible)."""
        t = alt.Chart(grouped).mark_text(
            color="#4B4B3A", fontSize=11, fontWeight=600,
            dx=6 if horizontal else 0, dy=0 if horizontal else -8,
            align="left" if horizontal else "center", baseline="middle",
        ).encode(**base_enc, text=alt.Text("Value:Q", format=",.0f"))
        return t

    if chart_type == "Horizontal Bar":
        y_enc = alt.Y(f"{group_col}:N", sort="-x", title=group_col)
        enc = dict(y=y_enc, x=alt.X("Value:Q", stack=(None if not has_split else ("zero" if stack_mode == "Stacked" else None)), title=measure_label), color=color_enc, tooltip=tip)
        if has_split and stack_mode == "Grouped":
            enc["yOffset"] = f"{split_col}:N"
        chart = alt.Chart(grouped).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(**enc) \
                   .properties(height=max(320, grouped[group_col].nunique() * (26 if not has_split else 40)))
        if data_labels and not has_split:
            chart = chart + _labels(dict(y=y_enc, x=alt.X("Value:Q")), horizontal=True)

    elif chart_type == "Line":
        chart = (alt.Chart(grouped)
                 .mark_line(color=PRIMARY, point=alt.OverlayMarkDef(color=SECONDARY, size=55), strokeWidth=3)
                 .encode(x=x_enc, y=alt.Y("Value:Q", title=measure_label), tooltip=tip)
                 .properties(height=380))
        if data_labels:
            chart = chart + _labels(dict(x=x_enc, y=alt.Y("Value:Q")))

    elif chart_type == "Area":
        chart = (alt.Chart(grouped)
                 .mark_area(line={"color": PRIMARY}, color=alt.Gradient(
                     gradient="linear",
                     stops=[alt.GradientStop(color="#F4F1E8", offset=0),
                            alt.GradientStop(color=PRIMARY, offset=1)],
                     x1=1, x2=1, y1=1, y2=0))
                 .encode(x=x_enc, y=alt.Y("Value:Q", title=measure_label), tooltip=tip)
                 .properties(height=380))

    elif chart_type == "Donut":
        chart = (alt.Chart(grouped)
                 .mark_arc(innerRadius=70, stroke="#fff", strokeWidth=2)
                 .encode(theta=alt.Theta("Value:Q", stack=True),
                         color=alt.Color(f"{group_col}:N", scale=alt.Scale(range=DONUT_SCHEME),
                                         legend=alt.Legend(title=group_col)),
                         tooltip=tip)
                 .properties(height=380))

    elif chart_type == "Bar + Line (combo)":
        grouped2, measure2_label = _agg_by(view, group_col, measure2, numeric_cols)
        grouped2 = grouped2.set_index(group_col).reindex(grouped[group_col]).reset_index()
        bars = alt.Chart(grouped).mark_bar(color=PRIMARY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=x_enc, y=alt.Y("Value:Q", title=measure_label), tooltip=tip)
        line = (alt.Chart(grouped2)
                .mark_line(color=SECONDARY, point=alt.OverlayMarkDef(color=SECONDARY, size=55), strokeWidth=3)
                .encode(x=x_enc, y=alt.Y("Value:Q", axis=alt.Axis(title=measure2_label, titleColor=SECONDARY)),
                        tooltip=[tip[0], alt.Tooltip("Value:Q", format=",.1f", title=measure2_label)]))
        chart = alt.layer(bars, line).resolve_scale(y="independent").properties(height=380)

    else:  # Vertical Bar
        enc = dict(x=x_enc, y=alt.Y("Value:Q", stack=(None if not has_split else ("zero" if stack_mode == "Stacked" else None)), title=measure_label), color=color_enc, tooltip=tip)
        if has_split and stack_mode == "Grouped":
            enc["xOffset"] = f"{split_col}:N"
        chart = alt.Chart(grouped).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(**enc).properties(height=380)
        if data_labels and not has_split:
            chart = chart + _labels(dict(x=x_enc, y=alt.Y("Value:Q")))

    st.altair_chart(chart, use_container_width=True)

    # ── PDF export (chart + table) ──────────────────────────────────────────
    _subtitle = (f"{', '.join(info['objects'])}  ·  {measure_label} by {group_col}"
                 f"  ·  {chart_type}  ·  generated {datetime.now().strftime('%b %d, %Y %I:%M %p')}")
    if st.button("📄 Prepare presentation PDF (chart + table)"):
        try:
            st.session_state._de_pdf = _build_visual_pdf(
                chart_type, grouped, group_col, measure_label,
                has_split, split_col, stack_mode, _subtitle)
        except Exception as e:
            st.session_state._de_pdf = None
            st.error(f"Couldn't build PDF: {e}")
    if st.session_state.get("_de_pdf"):
        st.download_button("📥 Download PDF", st.session_state._de_pdf,
                           f"data_explorer_{datetime.now().strftime('%Y%m%d')}.pdf",
                           "application/pdf")

report_header_close()
