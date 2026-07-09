import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from utils import require_auth, fetch_all, COMMON_CSS, report_header, report_header_close, norm, vrs_rate_for_month

CONVO_NOW_RATE = 2.60

def _to_float(v):
    try:
        return float(v) if v not in (None, "", "—") else 0.0
    except Exception:
        return 0.0

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Consumer Success Tickets",
    "Support tickets in the Consumer Success pipeline — monthly trends and detail",
    section="Analytics",
)

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(v):
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _fmt(v):
    dt = _parse_dt(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

def _month_key(v):
    dt = _parse_dt(v)
    return dt.strftime("%m/01/%Y") if dt else None

def _month_sort(k):
    try:
        return datetime.strptime(k, "%m/01/%Y")
    except Exception:
        return datetime.min

# ── date presets ──────────────────────────────────────────────────────────────

PRESETS = [
    "Jun 2026–Present",
    "All Time", "Today", "Yesterday",
    "Last 7 Days", "Last 30 Days",
    "This Week (Mon–Sun)", "Last Week",
    "This Month", "Last Month", "Last 3 Months",
    "This Quarter", "Last Quarter",
    "This Year", "Last Year",
    "Custom Range",
]

TICKET_NAME_OPTIONS = [
    "All",
    "VRS Registration",
    "🧊 AT RISK",
    "⚠️ DECLINING",
    "Churn | Non-Migrated",
    "VRS Registration + Churn",
]

TICKET_NAME_MULTI = {
    "VRS Registration + Churn": ["vrs registration", "churn | non-migrated"],
}

def _date_range(preset):
    today = date.today()
    if preset == "Jun 2026–Present":  return date(2026, 6, 1), today
    if preset == "Today":             return today, today
    if preset == "Yesterday":         d = today - timedelta(days=1); return d, d
    if preset == "Last 7 Days":       return today - timedelta(days=6), today
    if preset == "Last 30 Days":      return today - timedelta(days=29), today
    if preset == "This Week (Mon–Sun)": return today - timedelta(days=today.weekday()), today
    if preset == "Last Week":
        s = today - timedelta(days=today.weekday() + 7); return s, s + timedelta(days=6)
    if preset == "This Month":        return today.replace(day=1), today
    if preset == "Last Month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if preset == "Last 3 Months":     return today - timedelta(days=89), today
    if preset == "This Quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q, day=1), today
    if preset == "Last Quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        end = today.replace(month=q, day=1) - timedelta(days=1)
        start = end.replace(month=((end.month - 1) // 3) * 3 + 1, day=1)
        return start, end
    if preset == "This Year":         return today.replace(month=1, day=1), today
    if preset == "Last Year":         return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None, None

# ── filter UI ─────────────────────────────────────────────────────────────────

col_preset, col_from, col_to, col_field, col_status = st.columns([2, 1, 1, 1.5, 1.5])

with col_preset:
    preset = st.selectbox("Date range", PRESETS, index=0)  # default Jun 2026–Present
with col_field:
    date_field_label = st.selectbox("Filter date by", ["Close Date", "Create Date"], index=0)
    date_field = "closed_date" if date_field_label == "Close Date" else "createdate"
with col_status:
    status_filter = st.selectbox("Status", ["All", "Open", "Closed"], index=0)

if preset == "Custom Range":
    with col_from:
        custom_from = st.date_input("From", value=date(2026, 6, 1))
    with col_to:
        custom_to = st.date_input("To", value=date.today())
    filter_start, filter_end = custom_from, custom_to
else:
    filter_start, filter_end = _date_range(preset)
    if filter_start:
        with col_from:
            st.markdown(f"<div style='padding-top:1.85rem;font-size:0.82rem;color:#9dc8b0;'>{filter_start.strftime('%b %d, %Y')}</div>", unsafe_allow_html=True)
        with col_to:
            st.markdown(f"<div style='padding-top:1.85rem;font-size:0.82rem;color:#9dc8b0;'>{filter_end.strftime('%b %d, %Y')}</div>", unsafe_allow_html=True)

# ── ticket name filter ─────────────────────────────────────────────────────────
ticket_name_filter = st.selectbox("Ticket name filter", TICKET_NAME_OPTIONS, index=0)

mv_all_months = st.checkbox(
    "Include all months of usage (match HubSpot report)",
    value=False,
    help="HubSpot's report has no month filter on Monthly Values — it sums every month of "
         "usage for contacts whose tickets closed in the period. Unchecked, only usage that "
         "occurred within the report period is counted.",
)

st.markdown("<div style='margin-bottom:0.75rem;'></div>", unsafe_allow_html=True)

CLOSED_KEYWORDS = {"closed", "resolved", "done", "completed"}

def _is_closed(status_label):
    return any(k in (status_label or "").lower() for k in CLOSED_KEYWORDS)

# ── run ───────────────────────────────────────────────────────────────────────

if st.button("Run Consumer Success Tickets", use_container_width=False):

    with st.spinner("Loading pipeline configuration..."):
        # Fetch pipeline and stage metadata
        stage_labels = {}
        pipeline_names = {}
        cs_pipeline_id = None
        closed_stage_ids = set()
        try:
            pr = requests.get(f"{BASE_URL}/crm/v3/pipelines/tickets", headers=_headers, timeout=15)
            if pr.status_code == 200:
                for pipeline in pr.json().get("results", []):
                    pid = pipeline["id"]
                    plabel = pipeline.get("label", pid)
                    pipeline_names[pid] = plabel
                    if "consumer success" in plabel.lower():
                        cs_pipeline_id = pid
                    for stage in pipeline.get("stages", []):
                        sid = stage["id"]
                        slabel = stage.get("label", sid)
                        stage_labels[sid] = slabel
                        if "consumer success" in plabel.lower() and _is_closed(slabel):
                            closed_stage_ids.add(sid)
        except Exception as e:
            st.error(f"Failed to load pipelines: {e}")
            st.stop()

    if not cs_pipeline_id:
        st.warning("Could not find a 'Consumer Success' pipeline in HubSpot.")
        st.stop()

    # Build search filters
    TICKET_PROPS = [
        "subject", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority",
        "createdate", "hs_lastmodifieddate", "closed_date", "content",
        "hs_ticket_category", "hs_ticket_subcategory",
        "hubspot_owner_id", "email", "phone", "hs_resolution_time",
    ]

    # Fetch all Consumer Success tickets
    with st.spinner("Fetching Consumer Success tickets..."):
        owner_names = {}
        try:
            or_ = requests.get(f"{BASE_URL}/crm/v3/owners", headers=_headers, timeout=15)
            if or_.status_code == 200:
                for o in or_.json().get("results", []):
                    fn = o.get("firstName") or ""
                    ln = o.get("lastName") or ""
                    owner_names[str(o["id"])] = f"{fn} {ln}".strip() or o.get("email", str(o["id"]))
        except Exception:
            pass

        all_tickets = []
        after = None
        while True:
            body = {
                "filterGroups": [{"filters": [
                    {"propertyName": "hs_pipeline", "operator": "EQ", "value": cs_pipeline_id}
                ]}],
                "properties": TICKET_PROPS,
                "limit": 100,
                "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            }
            if after:
                body["after"] = after
            resp = requests.post(f"{BASE_URL}/crm/v3/objects/tickets/search",
                                 headers=_headers, json=body, timeout=30)
            if resp.status_code == 429:
                time.sleep(1.0)
                continue
            if resp.status_code != 200:
                st.error(f"Ticket search error {resp.status_code}: {resp.text[:300]}")
                break
            data = resp.json()
            all_tickets.extend(data.get("results", []))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            time.sleep(0.25)

    # Use v4 batch associations API to get contact IDs for each ticket
    ticket_contact_email = {}   # ticket_id → email
    ticket_ids = [t["id"] for t in all_tickets]

    with st.spinner(f"Looking up contact emails for {len(ticket_ids)} tickets..."):
        tid_to_cids = defaultdict(list)  # ticket_id → [contact_id, ...]
        for i in range(0, len(ticket_ids), 100):
            chunk = ticket_ids[i:i+100]
            ar = requests.post(
                f"{BASE_URL}/crm/v4/associations/tickets/contacts/batch/read",
                headers=_headers,
                json={"inputs": [{"id": tid} for tid in chunk]},
                timeout=30,
            )
            if ar.status_code == 200:
                for result in ar.json().get("results", []):
                    tid = str(result.get("from", {}).get("id", ""))
                    for assoc in result.get("to", []):
                        cid = str(assoc.get("toObjectId") or assoc.get("id") or "")
                        if cid:
                            tid_to_cids[tid].append(cid)
            time.sleep(0.1)

        unique_cids = list({cid for cids in tid_to_cids.values() for cid in cids})
        if unique_cids:
            contact_email_map = {}
            for i in range(0, len(unique_cids), 100):
                chunk = unique_cids[i:i+100]
                br = requests.post(
                    f"{BASE_URL}/crm/v3/objects/contacts/batch/read",
                    headers=_headers,
                    json={"inputs": [{"id": c} for c in chunk], "properties": ["email"]},
                    timeout=30,
                )
                if br.status_code == 200:
                    for c in br.json().get("results", []):
                        cid = str(c["id"])
                        email = (c.get("properties", {}).get("email") or "").strip().lower()
                        if email:
                            contact_email_map[cid] = email
            for tid, cids in tid_to_cids.items():
                for cid in cids:
                    if cid in contact_email_map:
                        ticket_contact_email[tid] = contact_email_map[cid]
                        break

    if not all_tickets:
        st.warning("No Consumer Success tickets found.")
        st.stop()

    # Build rows
    rows = []
    for t in all_tickets:
        tp = t.get("properties", {})
        raw_stage = tp.get("hs_pipeline_stage") or ""
        stage_label = stage_labels.get(raw_stage, raw_stage)
        is_closed = _is_closed(stage_label)
        res_ms = tp.get("hs_resolution_time")
        res_days = None
        if res_ms:
            try:
                res_days = round(int(res_ms) / 86400000, 1)
            except Exception:
                pass
        rows.append({
            "ID":            t["id"],
            "Subject":       tp.get("subject") or "—",
            "Status":        stage_label or "—",
            "Priority":      (tp.get("hs_ticket_priority") or "—").title(),
            "Category":      tp.get("hs_ticket_category") or "—",
            "Subcategory":   tp.get("hs_ticket_subcategory") or "—",
            "Owner":         owner_names.get(tp.get("hubspot_owner_id") or "", "—"),
            "Email":         ticket_contact_email.get(t["id"]) or tp.get("email") or "—",
            "Created":       tp.get("createdate") or "",
            "Closed":        tp.get("closed_date") or "",
            "Description":   tp.get("content") or "—",
            "Resolution Days": res_days,
            "Is Closed":     is_closed,
            "Create Month":  _month_key(tp.get("createdate")),
            "Close Month":   _month_key(tp.get("closed_date")),
        })

    # ── Apply date + status filters ────────────────────────────────────────────
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, 0, 0, 0, tzinfo=timezone.utc)
        fe = datetime(filter_end.year, filter_end.month, filter_end.day, 23, 59, 59, tzinfo=timezone.utc)
        def in_range(v):
            dt = _parse_dt(v)
            return dt is not None and fs <= dt <= fe
        field_key = "Closed" if date_field == "closed_date" else "Created"
        rows = [r for r in rows if in_range(r[field_key])]
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
    else:
        range_label = "All Time"

    if status_filter == "Open":
        rows = [r for r in rows if not r["Is Closed"]]
    elif status_filter == "Closed":
        rows = [r for r in rows if r["Is Closed"]]

    if ticket_name_filter != "All":
        if ticket_name_filter in TICKET_NAME_MULTI:
            keywords = TICKET_NAME_MULTI[ticket_name_filter]
            rows = [r for r in rows if any(kw in (r["Subject"] or "").lower() for kw in keywords)]
        else:
            rows = [r for r in rows if ticket_name_filter.lower() in (r["Subject"] or "").lower()]

    if not rows:
        st.warning(f"No tickets found for the selected filters ({range_label}).")
        st.stop()

    # ── Step 2: ticket → contact → number object → monthly values ────────────────
    # Path through contacts gives the consumer's personal numbers only.
    filtered_ticket_ids = [r["ID"] for r in rows]
    num_monthly = defaultdict(list)

    # Build ticket → close month map for filtered rows
    tid_to_close_month = {}
    for r in rows:
        close_dt = _parse_dt(r["Closed"])
        if close_dt:
            tid_to_close_month[r["ID"]] = close_dt.strftime("%Y-%m")

    # Collect contact IDs for filtered tickets, tracking which close months each contact belongs to
    cid_to_close_months = defaultdict(set)
    for tid in filtered_ticket_ids:
        cm = tid_to_close_month.get(tid)
        for cid in tid_to_cids.get(tid, []):
            if cm:
                cid_to_close_months[cid].add(cm)
    filtered_cids = list(cid_to_close_months.keys())

    # Contact → number object IDs (v4 association)
    cid_to_nids = defaultdict(list)
    if filtered_cids:
        with st.spinner(f"Looking up number objects for {len(filtered_cids)} contact(s)..."):
            for i in range(0, len(filtered_cids), 100):
                chunk = filtered_cids[i:i+100]
                ar = requests.post(
                    f"{BASE_URL}/crm/v4/associations/contacts/2-40974683/batch/read",
                    headers=_headers,
                    json={"inputs": [{"id": cid} for cid in chunk]},
                    timeout=30,
                )
                if ar.status_code == 200:
                    for result in ar.json().get("results", []):
                        cid = str(result.get("from", {}).get("id", ""))
                        for assoc in result.get("to", []):
                            nid = str(assoc.get("toObjectId") or assoc.get("id") or "")
                            if nid:
                                cid_to_nids[cid].append(nid)
                time.sleep(0.1)

    # Propagate close months from contact to number IDs
    nid_to_close_months = defaultdict(set)
    for cid, nids in cid_to_nids.items():
        for nid in nids:
            nid_to_close_months[nid].update(cid_to_close_months.get(cid, set()))

    all_num_ids = list({nid for nids in cid_to_nids.values() for nid in nids})

    # Batch-read number objects to get the phone number string
    num_id_to_number = {}
    if all_num_ids:
        with st.spinner(f"Reading {len(all_num_ids)} number objects..."):
            for i in range(0, len(all_num_ids), 100):
                chunk = all_num_ids[i:i+100]
                br = requests.post(
                    f"{BASE_URL}/crm/v3/objects/2-40974683/batch/read",
                    headers=_headers,
                    json={"inputs": [{"id": n} for n in chunk],
                          "properties": ["number", "service_type", "number_status"]},
                    timeout=30,
                )
                if br.status_code == 200:
                    for obj in br.json().get("results", []):
                        p = obj.get("properties", {})
                        # No service_type filter here — HubSpot's report filters
                        # service type on Monthly Values only, and so does our
                        # monthly value search below. Filtering numbers too drops
                        # usage for numbers whose type is blank or was changed.
                        num = str(p.get("number") or "").strip()
                        if num:
                            num_id_to_number[str(obj["id"])] = num

    vrs_num_ids = list(num_id_to_number.keys())  # all associated number object IDs

    # Step 3: search monthly values directly by phone number string (more reliable
    # than v4 association → MV ID lookup which can time out at scale).
    MV_PROPS = ["number", "month_date", "service_type",
                "usage_minutes", "ursa_minutes", "cfz_minutes",
                "fcc_cost_based_on_vrs_usage", "fcc_cost_based_on_cfz_usage",
                "fcc_rate_1"]

    if mv_all_months:
        mv_floor = date(2000, 1, 1)  # no floor — sum every month, like HubSpot's report
    elif filter_start:
        mv_floor = date(filter_start.year, filter_start.month, 1)
    else:
        today_d = date.today()
        mv_floor = date(today_d.year - 2, today_d.month, 1)
    mv_floor_ms = str(int(datetime(mv_floor.year, mv_floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))
    MV_DATE_FILTER = {"propertyName": "month_date", "operator": "GTE", "value": mv_floor_ms}

    vrs_numbers = list(num_id_to_number.values())  # phone number strings
    num_to_nid  = {v: k for k, v in num_id_to_number.items()}  # phone → object ID

    # Monthly value records collected from both paths, deduped by record ID.
    mv_objects = {}          # mv_id → (number_object_id, properties)
    seen_mv_ids = set()

    if vrs_numbers:
        with st.spinner(f"Fetching monthly values for {len(vrs_numbers):,} numbers (from {mv_floor.strftime('%b %Y')})..."):
            # Path 1: direct phone-string search (fast, but misses rows whose
            # number text is formatted differently than the Number object's).
            for i in range(0, len(vrs_numbers), 100):
                chunk_nums = vrs_numbers[i:i+100]
                mv_recs = fetch_all(
                    "2-46246179",
                    MV_PROPS,
                    filter_groups=[{"filters": [
                        {"propertyName": "number",       "operator": "IN", "values": chunk_nums},
                        MV_DATE_FILTER,
                        {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                    ]}]
                )
                for obj in mv_recs:
                    mv_id = str(obj.get("id") or "")
                    if not mv_id or mv_id in seen_mv_ids:
                        continue
                    seen_mv_ids.add(mv_id)
                    p2  = obj.get("properties", {})
                    num = str(p2.get("number") or "").strip()
                    mv_objects[mv_id] = (num_to_nid.get(num, num), p2)

            # Path 2: number → monthly value associations (matches how HubSpot's
            # report joins the objects; catches rows the string search missed).
            nid_to_mv_ids = defaultdict(list)
            for i in range(0, len(vrs_num_ids), 100):
                chunk_ids = vrs_num_ids[i:i+100]
                ar = requests.post(
                    f"{BASE_URL}/crm/v4/associations/2-40974683/2-46246179/batch/read",
                    headers=_headers,
                    json={"inputs": [{"id": n} for n in chunk_ids]},
                    timeout=30,
                )
                if ar.status_code == 200:
                    for result in ar.json().get("results", []):
                        nid = str(result.get("from", {}).get("id", ""))
                        for assoc in result.get("to", []):
                            mv_id = str(assoc.get("toObjectId") or assoc.get("id") or "")
                            if mv_id and mv_id not in seen_mv_ids:
                                nid_to_mv_ids[nid].append(mv_id)
                time.sleep(0.1)

            missing_mv = [(mid, nid) for nid, mids in nid_to_mv_ids.items() for mid in mids]
            for i in range(0, len(missing_mv), 100):
                chunk = missing_mv[i:i+100]
                br = requests.post(
                    f"{BASE_URL}/crm/v3/objects/2-46246179/batch/read",
                    headers=_headers,
                    json={"inputs": [{"id": mid} for mid, _ in chunk], "properties": MV_PROPS},
                    timeout=30,
                )
                if br.status_code == 200:
                    owner = dict(chunk)  # mv_id → number object id
                    for obj in br.json().get("results", []):
                        mv_id = str(obj["id"])
                        if mv_id in seen_mv_ids:
                            continue
                        p2 = obj.get("properties", {})
                        # apply the same filters the search path used
                        if norm(p2.get("service_type") or "") != "vrs":
                            continue
                        md = p2.get("month_date") or ""
                        if md and md[:10] < mv_floor.isoformat():
                            continue
                        seen_mv_ids.add(mv_id)
                        mv_objects[mv_id] = (owner.get(mv_id, ""), p2)
                time.sleep(0.1)

    for mv_id, (nid, p2) in mv_objects.items():
        num_monthly[nid].append({
            "month":    p2.get("month_date") or "",
            "ursa_min": _to_float(p2.get("ursa_minutes")),
            "cfz_min":  _to_float(p2.get("cfz_minutes")),
            "fcc_vrs":  _to_float(p2.get("fcc_cost_based_on_vrs_usage")),
            "fcc_cfz":  _to_float(p2.get("fcc_cost_based_on_cfz_usage")),
        })

    # Aggregate all June 2026+ monthly values for matched numbers.
    # HubSpot's report shows June-closed tickets with July monthly values
    # (usage recorded the following month), so we do not restrict by close month.
    month_agg = defaultdict(lambda: {"ursa_min": 0.0, "cfz_min": 0.0, "fcc_vrs": 0.0, "fcc_cfz": 0.0})
    for nid, mv_list in num_monthly.items():
        for mv in mv_list:
            mk = mv["month"][:7] if mv["month"] else None  # YYYY-MM
            if not mk:
                continue
            month_agg[mk]["ursa_min"] += mv["ursa_min"]
            month_agg[mk]["cfz_min"]  += mv["cfz_min"]
            month_agg[mk]["fcc_vrs"]  += mv["fcc_vrs"]
            month_agg[mk]["fcc_cfz"]  += mv["fcc_cfz"]

    # Usage Total = URSA + CfZ (derived, avoids double-counting usage_minutes field)
    for mk in month_agg:
        month_agg[mk]["usage_min"] = month_agg[mk]["ursa_min"] + month_agg[mk]["cfz_min"]

    total_ursa_min   = sum(v["ursa_min"]  for v in month_agg.values())
    total_cfz_min    = sum(v["cfz_min"]   for v in month_agg.values())
    total_usage_min  = sum(v["usage_min"] for v in month_agg.values())
    # Use HubSpot's pre-calculated FCC costs (fcc_cost_based_on_vrs_usage + fcc_cost_based_on_cfz_usage)
    total_vrs_fcc    = sum(v["fcc_vrs"] + v["fcc_cfz"] for v in month_agg.values())

    # ── Summary tiles ──────────────────────────────────────────────────────────
    total    = len(rows)
    closed_n = sum(1 for r in rows if r["Is Closed"])
    open_n   = total - closed_n
    high_n   = sum(1 for r in rows if r["Priority"].lower() == "high")
    res_vals = [r["Resolution Days"] for r in rows if r["Resolution Days"] is not None]
    avg_res  = f"{sum(res_vals)/len(res_vals):.1f}d" if res_vals else "—"

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Filtered by <strong style="color:#E6F2EC;">{date_field_label}</strong>
  &nbsp;·&nbsp; {total:,} ticket{'s' if total != 1 else ''}
</div>""", unsafe_allow_html=True)

    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:0.5rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total Tickets</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Open</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{open_n:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Closed</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;">{closed_n:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #FEE2E2;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#EF4444;margin-bottom:0.25rem;">High Priority</div>
    <div style="font-size:1.4rem;font-weight:800;color:#EF4444;">{high_n:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Avg Resolution</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{avg_res}</div>
  </div>
</div>""", unsafe_allow_html=True)

    # ── Monthly Values section ─────────────────────────────────────────────────
    if vrs_numbers or num_monthly:
        st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#9dc8b0;margin:1.5rem 0 0.75rem;'>Monthly Values — VRS Numbers</div>", unsafe_allow_html=True)
        st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin-bottom:1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Numbers Matched</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{len(vrs_numbers):,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">URSA Minutes</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;font-variant-numeric:tabular-nums;">{total_ursa_min:,.0f}</div>
    <div style="font-size:0.72rem;color:#6aab85;">VRS sub-type</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">CfZ Minutes</div>
    <div style="font-size:1.4rem;font-weight:800;color:#8B5CF6;font-variant-numeric:tabular-nums;">{total_cfz_min:,.0f}</div>
    <div style="font-size:0.72rem;color:#a78bfa;">VRS sub-type</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Usage Minutes (Total)</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;font-variant-numeric:tabular-nums;">{total_usage_min:,.0f}</div>
    <div style="font-size:0.72rem;color:#9CA3AF;">URSA + CfZ</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">VRS FCC Cost</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;font-variant-numeric:tabular-nums;">${total_vrs_fcc:,.0f}</div>
    <div style="font-size:0.72rem;color:#6aab85;">VRS + CfZ FCC (HubSpot)</div>
  </div>
</div>""", unsafe_allow_html=True)

        if month_agg:
            sorted_mk = sorted(month_agg.keys())

            # ── All-months grouped bar chart ───────────────────────────────────
            mv_chart_df = pd.DataFrame([
                {"Month": mk, "Type": "URSA",         "Minutes": round(month_agg[mk]["ursa_min"],  1)}
                for mk in sorted_mk
            ] + [
                {"Month": mk, "Type": "CfZ",          "Minutes": round(month_agg[mk]["cfz_min"],   1)}
                for mk in sorted_mk
            ] + [
                {"Month": mk, "Type": "Usage (Total)", "Minutes": round(month_agg[mk]["usage_min"],  1)}
                for mk in sorted_mk
            ])
            mv_bar = (
                alt.Chart(mv_chart_df)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("Month:N", sort=sorted_mk, axis=alt.Axis(title=None, labelAngle=-20)),
                    y=alt.Y("Minutes:Q", title="Minutes"),
                    xOffset=alt.XOffset("Type:N", sort=["CfZ", "URSA", "Usage (Total)"]),
                    color=alt.Color("Type:N", scale=alt.Scale(
                        domain=["URSA", "CfZ", "Usage (Total)"],
                        range=["#00A651", "#8B5CF6", "#D1D5DB"]
                    ), legend=alt.Legend(orient="top", title=None)),
                    tooltip=["Month", "Type", alt.Tooltip("Minutes:Q", format=",.1f")],
                )
                .properties(height=240, title="Monthly URSA / CfZ / Usage Minutes")
            )
            st.altair_chart(mv_bar, use_container_width=True)

            # ── Grouped bar chart: CfZ + URSA minutes with FCC cost labels ───────
            jul26_mks = [mk for mk in sorted_mk if mk >= "2026-06"]
            if jul26_mks:
                st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#9dc8b0;margin:1.25rem 0 0.5rem;'>CfZ & URSA Minutes — June 2026 Onward</div>", unsafe_allow_html=True)
                bar_rows = []
                fcc_rows = []  # one row per month for FCC label
                for mk in jul26_mks:
                    label = datetime.strptime(mk, "%Y-%m").strftime("%b %Y")
                    fcc   = round(month_agg[mk]["fcc_vrs"] + month_agg[mk]["fcc_cfz"], 0)
                    ursa_m = round(month_agg[mk]["ursa_min"], 1)
                    cfz_m  = round(month_agg[mk]["cfz_min"],  1)
                    bar_rows.append({"Month": label, "Type": "URSA Minutes", "Minutes": ursa_m, "FCC Cost ($)": fcc})
                    bar_rows.append({"Month": label, "Type": "CfZ Minutes",  "Minutes": cfz_m,  "FCC Cost ($)": fcc})
                    fcc_rows.append({"Month": label, "Minutes": max(ursa_m, cfz_m), "FCC Cost ($)": fcc})

                bar_df = pd.DataFrame(bar_rows)
                fcc_df = pd.DataFrame(fcc_rows)
                m_order = [datetime.strptime(mk, "%Y-%m").strftime("%b %Y") for mk in jul26_mks]

                grouped_bar = (
                    alt.Chart(bar_df)
                    .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                    .encode(
                        x=alt.X("Month:N", sort=m_order, axis=alt.Axis(title=None, labelAngle=0)),
                        y=alt.Y("Minutes:Q", title="Minutes"),
                        xOffset=alt.XOffset("Type:N", sort=["CfZ Minutes", "URSA Minutes"]),
                        color=alt.Color("Type:N", scale=alt.Scale(
                            domain=["CfZ Minutes", "URSA Minutes"],
                            range=["#8B5CF6", "#00A651"]
                        ), legend=alt.Legend(orient="top", title=None)),
                        tooltip=[
                            alt.Tooltip("Month:N"),
                            alt.Tooltip("Type:N"),
                            alt.Tooltip("Minutes:Q", format=",.0f"),
                            alt.Tooltip("FCC Cost ($):Q", format="$,.0f"),
                        ],
                    )
                )
                fcc_text = (
                    alt.Chart(fcc_df)
                    .mark_text(dy=-14, fontSize=11, fontWeight=700, color="#00A651")
                    .encode(
                        x=alt.X("Month:N", sort=m_order),
                        y=alt.Y("Minutes:Q"),
                        text=alt.Text("FCC Cost ($):Q", format="$,.0f"),
                    )
                )
                st.altair_chart(
                    (grouped_bar + fcc_text).properties(height=280, width="container"),
                    use_container_width=True,
                )

    # ── Monthly trend chart ────────────────────────────────────────────────────
    month_field_key = "Close Month" if date_field == "closed_date" else "Create Month"
    month_counts = {}
    for r in rows:
        mk = r[month_field_key]
        if mk:
            month_counts[mk] = month_counts.get(mk, 0) + 1

    if month_counts:
        sorted_months = sorted(month_counts.keys(), key=_month_sort)
        month_labels = [datetime.strptime(m, "%m/01/%Y").strftime("%b %Y") for m in sorted_months]
        chart_df = pd.DataFrame({
            "Month":   month_labels,
            "Tickets": [month_counts[m] for m in sorted_months],
        })
        n_months = len(month_labels)
        bar_width = max(20, min(60, 800 // max(n_months, 1)))
        chart_width = max(400, n_months * (bar_width + 10))
        bar = (
            alt.Chart(chart_df)
            .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=bar_width)
            .encode(
                x=alt.X("Month:N", sort=month_labels, axis=alt.Axis(title=None, labelAngle=-20)),
                y=alt.Y("Tickets:Q", title="Ticket Count"),
                tooltip=["Month", "Tickets"],
            )
            .properties(height=220, width=chart_width, title=f"Tickets by {date_field_label} — Monthly")
        )
        st.altair_chart(bar, use_container_width=True)

    # ── Status breakdown chart ─────────────────────────────────────────────────
    status_counts = {}
    for r in rows:
        s = r["Status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    if status_counts:
        st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin:1rem 0 0.5rem;'>Status Breakdown</div>", unsafe_allow_html=True)
        sc_df = pd.DataFrame([{"Status": k, "Count": v} for k, v in status_counts.items()]).sort_values("Count", ascending=False)
        bar2 = (
            alt.Chart(sc_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Count:Q", title="Count"),
                y=alt.Y("Status:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                color=alt.condition(
                    alt.datum.Status == "Closed (Consumer Success)",
                    alt.value("#00A651"), alt.value("#3B82F6")
                ),
                tooltip=["Status", "Count"],
            )
            .properties(height=max(100, len(status_counts) * 32))
        )
        st.altair_chart(bar2, use_container_width=True)

    # ── Priority breakdown ─────────────────────────────────────────────────────
    pri_counts = {}
    for r in rows:
        p = r["Priority"]
        pri_counts[p] = pri_counts.get(p, 0) + 1
    PRIORITY_COLOR = {"High": "#EF4444", "Medium": "#F59E0B", "Low": "#3B82F6", "—": "#9CA3AF"}

    # ── Owner breakdown ────────────────────────────────────────────────────────
    owner_counts = {}
    for r in rows:
        o = r["Owner"]
        if o != "—":
            owner_counts[o] = owner_counts.get(o, 0) + 1

    ch1, ch2 = st.columns(2)
    with ch1:
        if pri_counts:
            st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin-bottom:0.5rem;'>By Priority</div>", unsafe_allow_html=True)
            for lbl, cnt in sorted(pri_counts.items(), key=lambda x: -x[1]):
                color = PRIORITY_COLOR.get(lbl, "#9CA3AF")
                pct = cnt / total * 100
                st.markdown(f"""<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">
  <div style="width:90px;font-size:0.83rem;font-weight:600;color:#374151;">{lbl}</div>
  <div style="flex:1;background:#E5E7EB;border-radius:4px;height:10px;overflow:hidden;">
    <div style="width:{pct:.0f}%;background:{color};height:100%;border-radius:4px;"></div>
  </div>
  <div style="width:40px;text-align:right;font-size:0.83rem;font-weight:700;color:{color};font-variant-numeric:tabular-nums;">{cnt}</div>
</div>""", unsafe_allow_html=True)
    with ch2:
        if owner_counts:
            st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin-bottom:0.5rem;'>By Owner</div>", unsafe_allow_html=True)
            max_count = max(owner_counts.values())
            for lbl, cnt in sorted(owner_counts.items(), key=lambda x: -x[1])[:10]:
                pct = cnt / max_count * 100
                st.markdown(f"""<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">
  <div style="width:130px;font-size:0.83rem;font-weight:600;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{lbl}</div>
  <div style="flex:1;background:#E5E7EB;border-radius:4px;height:10px;overflow:hidden;">
    <div style="width:{pct:.0f}%;background:#3B82F6;height:100%;border-radius:4px;"></div>
  </div>
  <div style="width:40px;text-align:right;font-size:0.83rem;font-weight:700;color:#374151;font-variant-numeric:tabular-nums;">{cnt}</div>
</div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin:1rem 0;'></div>", unsafe_allow_html=True)

    # ── Ticket cards / detail ──────────────────────────────────────────────────
    PRIORITY_PILL_COLOR = {"High": "#EF4444", "Medium": "#F59E0B", "Low": "#3B82F6", "—": "#9CA3AF"}

    def pri_badge(p):
        c = PRIORITY_PILL_COLOR.get((p or "").title(), "#9CA3AF")
        return f'<span style="background:{c};color:#fff;font-size:0.68rem;font-weight:700;padding:2px 8px;border-radius:99px;letter-spacing:0.5px;">{(p or "—").upper()}</span>'

    def status_pill(s):
        closed = _is_closed(s)
        c = "#9CA3AF" if closed else "#3B82F6"
        return f'<span style="background:{c}22;color:{c};border:1px solid {c}55;font-size:0.68rem;font-weight:700;padding:2px 9px;border-radius:99px;">{(s or "—").upper()}</span>'

    tab_open, tab_closed, tab_all = st.tabs([f"Open ({open_n})", f"Closed ({closed_n})", f"All ({total})"])

    def _render_cards(ticket_list):
        if not ticket_list:
            st.info("No tickets in this view.")
            return
        cards = '<div style="display:flex;flex-direction:column;gap:0.75rem;">'
        for r in ticket_list:
            res_str = f"{r['Resolution Days']}d" if r["Resolution Days"] is not None else "—"
            cards += f"""
<div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:1.1rem 1.4rem;">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
    <div style="flex:1;min-width:0;">
      <div style="font-size:0.7rem;font-weight:600;color:#9CA3AF;margin-bottom:0.18rem;">#{r['ID']}</div>
      <div style="font-size:0.97rem;font-weight:700;color:#111827;margin-bottom:0.35rem;word-break:break-word;">{r['Subject']}</div>
      <div style="font-size:0.82rem;color:#6B7280;line-height:1.5;">{r['Description'][:200]}{'…' if len(r['Description']) > 200 else ''}</div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.35rem;white-space:nowrap;">
      {pri_badge(r['Priority'])}
      {status_pill(r['Status'])}
    </div>
  </div>
  <div style="display:flex;gap:1.25rem;margin-top:0.75rem;padding-top:0.65rem;border-top:1px solid #F3F4F6;flex-wrap:wrap;">
    <span style="font-size:0.76rem;color:#6B7280;">👤 <b>{r['Owner']}</b></span>
    <span style="font-size:0.76rem;color:#6B7280;">📂 <b>{r['Category']}</b></span>
    <span style="font-size:0.76rem;color:#6B7280;">✉️ <b>{r['Email']}</b></span>
    <span style="font-size:0.76rem;color:#6B7280;">📅 Created: <b>{_fmt(r['Created'])}</b></span>
    <span style="font-size:0.76rem;color:#6B7280;">🔒 Closed: <b>{_fmt(r['Closed']) if r['Closed'] else '—'}</b></span>
    <span style="font-size:0.76rem;color:#6B7280;">⏱ Resolution: <b>{res_str}</b></span>
  </div>
</div>"""
        cards += "</div>"
        st.markdown(cards, unsafe_allow_html=True)

    with tab_open:
        _render_cards([r for r in rows if not r["Is Closed"]])
    with tab_closed:
        _render_cards([r for r in rows if r["Is Closed"]])
    with tab_all:
        _render_cards(rows)
        st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
        export_cols = ["ID", "Subject", "Status", "Priority", "Category", "Subcategory",
                       "Owner", "Email", "Created", "Closed", "Resolution Days", "Description"]
        export_df = pd.DataFrame([{c: _fmt(r[c]) if c in ("Created", "Closed") else r[c] for c in export_cols} for r in rows])
        st.download_button(
            "Download CSV",
            export_df.to_csv(index=False),
            f"consumer_success_tickets_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
