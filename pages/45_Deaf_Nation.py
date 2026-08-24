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


def _digits(v):
    """Normalize a phone number to its last 10 digits for reliable matching."""
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


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


def _assoc_contacts(sub_ids):
    """Submission record → associated Contact IDs (v4 batch read)."""
    out = defaultdict(list)
    for i in range(0, len(sub_ids), 100):
        chunk = sub_ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v4/associations/{SUB_OBJECT}/contacts/batch/read",
                              headers=_H, json={"inputs": [{"id": s} for s in chunk]}, timeout=60)
            if r.status_code in (200, 207):
                for res in r.json().get("results", []):
                    sid = str(res.get("from", {}).get("id", ""))
                    for a in res.get("to", []):
                        cid = str(a.get("toObjectId") or a.get("id") or "")
                        if cid:
                            out[sid].append(cid)
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.1)
    return out


def _seek(obj, props, filters):
    """Seek-paginated search over `obj` (walks past HubSpot's 10k search cap)."""
    url = f"{_B}/crm/v3/objects/{obj}/search"
    out, last = [], "0"
    ph = st.empty()
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(6):
            r = requests.post(url, headers=_H, json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            ph.empty(); st.error(f"HubSpot error {getattr(r,'status_code','?')}"); break
        batch = r.json().get("results", [])
        out.extend(batch)
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.06)
    ph.empty()
    return out


st.markdown("Reads **event submission** records, follows them to the **Contact**, matches the "
            "contact's email to a **VRS Number** (with its status), then pulls **Monthly Values** "
            "usage over the chosen window. Flow: **Submission → Contact → VRS Number → Monthly Values**.")

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


st.markdown("Reads **event submission** records, follows them to the **Contact**, matches the "
            "contact's email to a **VRS Number** (with its status), then pulls **Monthly Values** "
            "usage over the chosen window. Flow: **Submission → Contact → VRS Number → Monthly Values**.")

c1, c2 = st.columns([1.8, 1.2])
with c1:
    events = st.multiselect("Event(s)", EVENTS_DEFAULT, default=EVENTS_DEFAULT,
                            accept_new_options=True,
                            help="Add another event name if it isn't listed.")
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

    # 2) submission → contact → email/name (fills in emails not on the submission record)
    with dash_spinner(f"Linking {len(sub_ids):,} submissions to Contacts…"):
        sub_to_cids = _assoc_contacts(sub_ids)
        all_cids = sorted({c for cids in sub_to_cids.values() for c in cids})
        contact_of = {}
        for i in range(0, len(all_cids), 100):
            chunk = all_cids[i:i + 100]
            r = requests.post(f"{_B}/crm/v3/objects/contacts/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk],
                                    "properties": ["email", "firstname", "lastname"]}, timeout=60)
            if r.status_code in (200, 207):
                for c in r.json().get("results", []):
                    cp = c.get("properties", {})
                    contact_of[str(c["id"])] = {
                        "email": (cp.get("email") or "").strip().lower(),
                        "name": f"{(cp.get('firstname') or '').strip()} {(cp.get('lastname') or '').strip()}".strip()}

    # resolve one email + name per submission (contact first, else the record's own email)
    people = []
    for sid, meta in sub_meta.items():
        email, name = meta["email"], meta["name"]
        for cid in sub_to_cids.get(sid, []):
            cm = contact_of.get(cid, {})
            if cm.get("email"):
                email = cm["email"]; name = name or cm.get("name", ""); break
        people.append({"sub": sid, "event": meta["event"], "email": email, "name": name})

    # 3) email → VRS Number (with status)
    #    Pull ALL VRS numbers once and match email case-insensitively in Python — the
    #    Number object's `email` isn't guaranteed lowercase, so a server-side IN filter
    #    on lowercased emails silently misses. (Same approach as Convo Now Only page.)
    want = {p["email"] for p in people if p["email"]}   # already lowercased
    vrs_by_email = defaultdict(list)   # email(lower) -> list of (number, status)
    if want:
        with dash_spinner("Loading VRS numbers to match by email…"):
            for rr in _seek(NUM_OBJECT, ["number", "email", "account_status", "number_status"],
                            [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]):
                pp = rr.get("properties", {})
                em = (pp.get("email") or "").strip().lower()
                if em not in want:
                    continue
                num = str(pp.get("number") or "").strip()
                stt = (pp.get("account_status") or pp.get("number_status") or "").strip()
                if em and num and (not status_filter or stt in status_filter):
                    vrs_by_email[em].append((num, stt))

    # 4) Monthly Values usage for those VRS numbers in the window
    all_nums = sorted({n for lst in vrs_by_email.values() for n, _ in lst})
    num_usage = defaultdict(float)
    if all_nums:
        with dash_spinner(f"Pulling Monthly Values for {len(all_nums):,} VRS numbers…"):
            for i in range(0, len(all_nums), 100):
                chunk = all_nums[i:i + 100]
                for o in _seek(MV_OBJECT, ["number", "usage_minutes", "service_type", "month_date"],
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                   {"propertyName": "month_date", "operator": "GTE", "value": floor_ms}]):
                    op = o.get("properties", {})
                    # key by last-10 digits so +1 / dashes / spaces don't break the join
                    num_usage[_digits(op.get("number"))] += to_float(op.get("usage_minutes")) or 0.0

    rows = []
    for p in people:
        nums = vrs_by_email.get(p["email"], [])
        mins = round(sum(num_usage.get(_digits(n), 0.0) for n, _ in nums), 1)
        rows.append({
            "Event": p["event"] or "—",
            "Name": p["name"] or "—",
            "Email": p["email"] or "—",
            "VRS Number(s)": ", ".join(n for n, _ in nums) or "—",
            "VRS Status": ", ".join(sorted({s for _, s in nums if s})) or "—",
            "Has VRS": "Yes" if nums else "No",
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
