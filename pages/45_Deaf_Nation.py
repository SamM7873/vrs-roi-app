import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Deaf Nation", layout="wide", page_icon="🎪")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Deaf Nation")

report_header("Deaf Nation Events",
              "Event submissions → Contact → VRS Number → Monthly Values usage",
              section="Customers")

SUB_OBJECT = "2-49942763"   # submission form records
NUM_OBJECT = "2-40974683"   # Number object
MV_OBJECT = "2-46246179"    # Monthly Values
CACHE_VERSION = 1
_key = f"deaf_nation_v{CACHE_VERSION}"

EVENTS_DEFAULT = ["DeafNationAtlanta_May_2026", "DeafNationDallas_April_2026"]
WINDOWS = {"Last 3 months": 3, "Last 6 months": 6, "Last 9 months": 9, "Last 12 months": 12}


def _months_ago_ms(n):
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12; y -= 1
    return str(int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000))


@st.cache_data(ttl=3600, show_spinner=False)
def _list_props(obj):
    """Discover the object's property names/labels/types at runtime (uses the app token)."""
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{obj}", headers=_H, timeout=30)
        if r.status_code == 200:
            return [(p.get("name"), p.get("label") or p.get("name"), p.get("type"))
                    for p in r.json().get("results", [])]
    except Exception:
        pass
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def _prop_options(obj):
    """{prop_name: [option values]} for enumeration properties (their defined choices)."""
    out = {}
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{obj}", headers=_H, timeout=30)
        if r.status_code == 200:
            for p in r.json().get("results", []):
                opts = [o.get("value") for o in (p.get("options") or []) if o.get("value")]
                if opts:
                    out[p.get("name")] = opts
    except Exception:
        pass
    return out


def _count_matches(prop, values):
    """How many submission records have `prop` IN the given event values (server-side total)."""
    try:
        r = requests.post(f"{_B}/crm/v3/objects/{SUB_OBJECT}/search", headers=_H,
                          json={"limit": 1, "properties": ["hs_object_id"],
                                "filterGroups": [{"filters": [
                                    {"propertyName": prop, "operator": "IN", "values": values}]}]},
                          timeout=20)
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return -1  # error / not filterable


def _assoc(from_obj, to_obj, from_ids):
    """from_obj record IDs → associated to_obj IDs (v4 batch read). {from_id: [to_ids]}."""
    out = defaultdict(list)
    for i in range(0, len(from_ids), 100):
        chunk = [str(x) for x in from_ids[i:i + 100]]
        try:
            r = requests.post(f"{_B}/crm/v4/associations/{from_obj}/{to_obj}/batch/read",
                              headers=_H, json={"inputs": [{"id": s} for s in chunk]}, timeout=60)
            if r.status_code in (200, 207):
                for res in r.json().get("results", []):
                    fid = str(res.get("from", {}).get("id", ""))
                    for a in res.get("to", []):
                        tid = str(a.get("toObjectId") or a.get("id") or "")
                        if tid:
                            out[fid].append(tid)
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.08)
    return out


def _batch_read(obj, ids, props):
    """Read objects by ID (batch). Returns {id: properties}."""
    out = {}
    ids = [str(x) for x in ids]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{obj}/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk], "properties": props},
                              timeout=60)
            if r.status_code in (200, 207):
                for o in r.json().get("results", []):
                    out[str(o["id"])] = o.get("properties", {})
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.06)
    return out


# ── discover submission properties (used to auto-find the event field at run time) ──
props = _list_props(SUB_OBJECT)
prop_names = [n for n, _, _ in props]
# fields worth testing for the event name: text / enumeration / string-ish, event-ish first
_str_types = {"string", "enumeration", None}
_cands = [n for n, l, t in props if t in _str_types]
_cands.sort(key=lambda n: (0 if "event" in n.lower() else 1 if any(
    k in n.lower() for k in ("form", "submission", "campaign", "name", "title")) else 2, n))


def _find_event_field(values):
    """Return the property name that actually contains the given event values (or None)."""
    for n in _cands:
        if _count_matches(n, values) > 0:
            return n
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _event_catalog():
    """Discover the event field and every distinct event name (with submission counts).

    Prefer an enumeration property whose defined options include the known event
    names — that gives the full list cheaply. Fall back to scanning records.
    """
    opt_map = _prop_options(SUB_OBJECT)
    # 1) enumeration property that contains our known Deaf Nation events
    field = next((n for n, opts in opt_map.items()
                  if any(e in opts for e in EVENTS_DEFAULT)), None)
    if field:
        vals = opt_map[field]
        counts = _count_by_value(field, vals) if len(vals) <= 200 else {}
        return field, {v: counts.get(v, 0) for v in vals}
    # 2) otherwise locate the field by value match, then scan records for distinct values
    field = _find_event_field(EVENTS_DEFAULT) or (_cands[0] if _cands else None)
    counts = {}
    if field:
        for s in fetch_all(SUB_OBJECT, [field]):
            v = (s.get("properties", {}).get(field) or "").strip()
            if v:
                counts[v] = counts.get(v, 0) + 1
    return field, counts


def _count_by_value(field, values):
    """Per-value submission counts via cheap server-side totals (limit-1 searches)."""
    out = {}
    for v in values:
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{SUB_OBJECT}/search", headers=_H,
                              json={"limit": 1, "properties": ["hs_object_id"],
                                    "filterGroups": [{"filters": [
                                        {"propertyName": field, "operator": "EQ", "value": v}]}]},
                              timeout=20)
            if r.status_code == 200:
                out[v] = r.json().get("total", 0)
        except Exception:
            pass
    return out


st.markdown("Reads **event submission** records and follows their **associations**: "
            "**Submission → Contact → Number → Monthly Values**. VRS numbers are filtered by "
            "status, and Monthly Values usage is summed over the chosen window.")

# ── all event names from the submission object ────────────────────────────────────
cat_field, cat_counts = _event_catalog()
_all_event_names = sorted(cat_counts, key=lambda k: (-cat_counts[k], k.lower()))
with st.expander(f"📋 All event names in the submission object ({len(_all_event_names):,})", expanded=True):
    if cat_field:
        st.caption(f"From field `{cat_field}` · {sum(cat_counts.values()):,} submissions total")
        st.dataframe(pd.DataFrame({"Event name": _all_event_names,
                                   "Submissions": [cat_counts[e] for e in _all_event_names]}),
                     use_container_width=True, hide_index=True, height=320)
    else:
        st.warning("Couldn't identify the event-name field on the submission object.")

# default the picker to the two Deaf Nation events if present, else nothing
_opts = _all_event_names or EVENTS_DEFAULT
_default = [e for e in EVENTS_DEFAULT if e in _opts] or []

c1, c2 = st.columns([1.8, 1.2])
with c1:
    events = st.multiselect("Event(s)", _opts, default=_default,
                            accept_new_options=True,
                            help="Pick from the events found above, or type another name.")
with c2:
    window_label = st.selectbox("Usage window", list(WINDOWS.keys()), index=0)

status_filter = st.multiselect("Number status (VRS)", ["Live", "Suspended", "Cancelled", "Ported Out"],
                               default=["Live"], help="Which VRS number statuses to include.")
run = st.button("▶ Run", type="primary", disabled=(not events))

if run:
    floor_ms = _months_ago_ms(WINDOWS[window_label])

    # 0) auto-detect which submission field holds the event name
    with dash_spinner("Finding the event field…"):
        event_prop = _find_event_field(events)
    if not event_prop:
        st.warning(f"Couldn't find any submission field containing {', '.join(events)}. "
                   "Double-check the event name spelling.")
        report_header_close(); st.stop()

    # 1) submission records for the selected event(s)
    sub_props = [event_prop] + [p for p in ("email", "firstname", "lastname", "createdate") if p in prop_names]
    with dash_spinner("Reading event submissions…"):
        subs = fetch_all(SUB_OBJECT, sub_props, filter_groups=[{"filters": [
            {"propertyName": event_prop, "operator": "IN", "values": events}]}])
    if not subs:
        st.warning(f"No submission records found for {', '.join(events)}.")
        report_header_close(); st.stop()
    st.caption(f"Matched on submission field `{event_prop}`")

    sub_ids = [str(s["id"]) for s in subs]
    sub_meta = {}
    for s in subs:
        p = s.get("properties", {})
        sub_meta[str(s["id"])] = {
            "event": (p.get(event_prop) or "").strip(),
            "email": (p.get("email") or "").strip().lower(),
            "name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip()}

    # 2) submission → Contact (association) → email/name
    with dash_spinner(f"Linking {len(sub_ids):,} submissions to Contacts…"):
        sub_to_cids = _assoc(SUB_OBJECT, "contacts", sub_ids)
        all_cids = sorted({c for cids in sub_to_cids.values() for c in cids})
        contact_of = _batch_read("contacts", all_cids, ["email", "firstname", "lastname"])

    # 3) Contact → Number (association); keep VRS numbers matching the status filter
    with dash_spinner(f"Linking {len(all_cids):,} contacts to Number records…"):
        cid_to_nids = _assoc("contacts", NUM_OBJECT, all_cids)
        all_nids = sorted({n for nids in cid_to_nids.values() for n in nids})
        num_of = _batch_read(NUM_OBJECT, all_nids,
                             ["number", "service_type", "account_status", "number_status"])

    # 4) Number → Monthly Values (association); sum VRS usage in the window
    #    Pull MV for the VRS numbers we care about, keyed by MV record id via association.
    vrs_nids = [nid for nid in all_nids
                if (num_of.get(nid, {}).get("service_type") or "").strip().lower() == "vrs"
                and (not status_filter
                     or (num_of.get(nid, {}).get("account_status")
                         or num_of.get(nid, {}).get("number_status") or "").strip() in status_filter)]
    nid_usage = defaultdict(float)
    if vrs_nids:
        with dash_spinner(f"Linking {len(vrs_nids):,} VRS numbers to Monthly Values…"):
            nid_to_mvids = _assoc(NUM_OBJECT, MV_OBJECT, vrs_nids)
            all_mvids = sorted({m for mids in nid_to_mvids.values() for m in mids})
            mv_of = _batch_read(MV_OBJECT, all_mvids,
                                ["usage_minutes", "service_type", "month_date"])
            floor = int(floor_ms)
            for nid, mvids in nid_to_mvids.items():
                for mid in mvids:
                    mp = mv_of.get(mid, {})
                    if (mp.get("service_type") or "").strip().lower() != "vrs":
                        continue
                    md = mp.get("month_date")
                    try:
                        if md is not None and int(md) < floor:
                            continue
                    except (TypeError, ValueError):
                        pass
                    nid_usage[nid] += to_float(mp.get("usage_minutes")) or 0.0

    rows = []
    for sid, meta in sub_meta.items():
        cids = sub_to_cids.get(sid, [])
        email, name = meta["email"], meta["name"]
        for cid in cids:
            cm = contact_of.get(cid, {})
            if cm.get("email"):
                email = (cm.get("email") or "").strip().lower()
                name = name or f"{(cm.get('firstname') or '').strip()} {(cm.get('lastname') or '').strip()}".strip()
                break
        # VRS numbers reached via this submission's contacts
        nids = sorted({n for cid in cids for n in cid_to_nids.get(cid, [])})
        vnids = [n for n in nids if n in set(vrs_nids)]
        numbers = [str(num_of.get(n, {}).get("number") or "").strip() for n in vnids]
        numbers = [x for x in numbers if x]
        statuses = sorted({(num_of.get(n, {}).get("account_status")
                            or num_of.get(n, {}).get("number_status") or "").strip()
                           for n in vnids if num_of.get(n)})
        statuses = [s for s in statuses if s]
        mins = round(sum(nid_usage.get(n, 0.0) for n in vnids), 1)
        rows.append({
            "Event": meta["event"] or "—",
            "Name": name or "—",
            "Email": email or "—",
            "VRS Number(s)": ", ".join(numbers) or "—",
            "VRS Status": ", ".join(statuses) or "—",
            "Has VRS": "Yes" if vnids else "No",
            "VRS Min (window)": mins,
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "events": events, "window": window_label,
                       "status": status_filter, "event_prop": event_prop})

saved = load_report(_key)
if saved is None:
    st.info("Pick your event(s) and window, then click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · events: {', '.join(saved.get('events', []))} · "
               f"{saved.get('window','')} · statuses: {', '.join(saved.get('status', [])) or 'all'}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
n_vrs = int((df["Has VRS"] == "Yes").sum())
n_active = int((df["VRS Min (window)"] > 0).sum())
tot_min = int(round(df["VRS Min (window)"].sum()))
k = st.columns(4)
_card(k[0], "🎪 Event submissions", f"{N:,}", "records matched", "#7A5CFF")
_card(k[1], "📞 Have VRS number", f"{n_vrs:,}", f"{n_vrs/N*100:.0f}% of submissions" if N else "—", "#0FB5AE")
_card(k[2], "🚀 Generated VRS usage", f"{n_active:,}", f"in {saved.get('window','')}", "#2DB84B")
_card(k[3], "⏱️ Total VRS minutes", f"{tot_min:,}", saved.get('window', ''), "#4C8DFF")
st.markdown("")

# by-event breakdown
st.markdown("##### By event")
be = df.groupby("Event").agg(Submissions=("Event", "size"),
                             **{"Have VRS": ("Has VRS", lambda s: (s == "Yes").sum()),
                                "VRS Minutes": ("VRS Min (window)", "sum")}).reset_index()
st.dataframe(be, use_container_width=True, hide_index=True)

# records
st.markdown("##### Records")
f1, f2 = st.columns([1.4, 2])
evpick = f1.multiselect("Event", sorted(df["Event"].unique()), default=[])
search = f2.text_input("Search name / email / number").strip().lower()
view = df.copy()
if evpick:
    view = view[view["Event"].isin(evpick)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
view = view.sort_values("VRS Min (window)", ascending=False)
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "deaf_nation.csv", "text/csv")

report_header_close()
