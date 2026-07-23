import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timezone, date
from utils import (
    require_auth, list_all, get_secret, COMMON_CSS,
    report_header, report_header_close, save_report, load_report,
)

st.set_page_config(page_title="Survey", layout="wide", page_icon="📝")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Survey Feedback", "HubSpot feedback survey submissions — CSAT / NPS / CES responses",
              section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
OBJECT = "feedback_submissions"

PRIMARY = "#C9A876"
GROUP_COLORS = {"PROMOTER": "#15803D", "PASSIVE": "#C9A876", "DETRACTOR": "#EF4444"}

# candidate feedback properties (only those present in the schema are used)
CANDIDATES = [
    "hs_survey_name", "hs_survey_type", "hs_survey_channel", "hs_value",
    "hs_response_group", "hs_sentiment", "hs_content", "hs_submission_timestamp",
    "hs_createdate", "hs_survey_id", "hs_form_guid",
]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_schema(object_type):
    try:
        r = requests.get(f"{BASE_URL}/crm/v3/properties/{object_type}", headers=_headers, timeout=30)
    except requests.exceptions.RequestException:
        return None, "network error"
    if r.status_code != 200:
        return None, f"{r.status_code}: {r.text[:200]}"
    out = {}
    for p in r.json().get("results", []):
        out[p.get("name")] = p.get("label") or p.get("name")
    return out, None


schema, err = fetch_schema(OBJECT)
if schema is None:
    st.error(
        "Couldn't load the feedback survey object. This usually means the HubSpot "
        "private-app token is missing the **crm.objects.feedback_submissions.read** scope. "
        f"\n\nDetails: {err}"
    )
    report_header_close()
    st.stop()

label_of = {n: schema.get(n, n) for n in schema}
props = [p for p in CANDIDATES if p in schema]
ts_prop = "hs_submission_timestamp" if "hs_submission_timestamp" in schema else (
    "hs_createdate" if "hs_createdate" in schema else None)

def _owner_name_from(o):
    return f"{o.get('firstName','')} {o.get('lastName','')}".strip() or o.get("email") or ""


def _fetch_owners():
    """owner id -> 'First Last' (or email) for all HubSpot owners."""
    out, after = {}, None
    for _ in range(50):
        url = f"{BASE_URL}/crm/v3/owners?limit=100" + (f"&after={after}" if after else "")
        try:
            r = requests.get(url, headers=_headers, timeout=30)
        except requests.exceptions.RequestException:
            break
        if r.status_code != 200:
            break
        data = r.json()
        for o in data.get("results", []):
            nm = _owner_name_from(o)
            if nm:
                out[str(o.get("id"))] = nm
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


def _owner_names(owner_ids):
    """Resolve owner id -> name; bulk list first, then per-id fallback.
    Never returns a raw id — unresolved owners map to '—'."""
    bulk = _fetch_owners()
    names = {}
    for oid in {str(o) for o in owner_ids if o}:
        if oid in bulk:
            names[oid] = bulk[oid]
            continue
        nm = ""
        try:
            r = requests.get(f"{BASE_URL}/crm/v3/owners/{oid}", headers=_headers, timeout=15)
            if r.status_code == 200:
                nm = _owner_name_from(r.json())
        except requests.exceptions.RequestException:
            pass
        names[oid] = nm or "—"
    return names


def _resolve_ticket_info(submission_ids):
    """submission id -> {'owner': name, 'ticket': subject} via feedback→ticket."""
    ids = [str(s) for s in submission_ids if s]
    sid_to_tids, all_tids = {}, set()
    loader = st.empty()
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            ar = requests.post(f"{BASE_URL}/crm/v4/associations/{OBJECT}/tickets/batch/read",
                               headers=_headers, json={"inputs": [{"id": s} for s in chunk]}, timeout=60)
        except requests.exceptions.RequestException:
            continue
        if ar.status_code in (200, 207):
            for res in ar.json().get("results", []):
                sid = str(res.get("from", {}).get("id", ""))
                tids = [str(a.get("toObjectId") or a.get("id") or "") for a in res.get("to", [])]
                tids = [t for t in tids if t]
                if tids:
                    sid_to_tids[sid] = tids
                    all_tids.update(tids)
        loader.markdown(
            f"<div style='padding:0.5rem 1rem;background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;'>"
            f"Resolving ticket info… {min(i+100, len(ids)):,}/{len(ids):,}</div>", unsafe_allow_html=True)

    tid_to_owner, tid_to_subject = {}, {}
    tids = list(all_tids)
    for i in range(0, len(tids), 100):
        chunk = tids[i:i + 100]
        try:
            br = requests.post(f"{BASE_URL}/crm/v3/objects/tickets/batch/read", headers=_headers,
                               json={"properties": ["hubspot_owner_id", "subject"],
                                     "inputs": [{"id": t} for t in chunk]}, timeout=60)
        except requests.exceptions.RequestException:
            continue
        if br.status_code in (200, 207):
            for t in br.json().get("results", []):
                p = t.get("properties", {}) or {}
                tid = str(t.get("id"))
                if p.get("hubspot_owner_id"):
                    tid_to_owner[tid] = str(p["hubspot_owner_id"])
                if p.get("subject"):
                    tid_to_subject[tid] = p["subject"]
    owners = _owner_names(tid_to_owner.values())
    loader.empty()

    result = {}
    for sid, tlist in sid_to_tids.items():
        info = {"owner": "—", "ticket": "—", "ticket_id": tlist[0] if tlist else "—"}
        for tid in tlist:
            oid = tid_to_owner.get(tid)
            if oid and info["owner"] == "—":
                info["owner"] = owners.get(oid, "—")
            if tid_to_subject.get(tid) and info["ticket"] == "—":
                info["ticket"] = tid_to_subject[tid]
        result[sid] = info
    return result


# ── data (persisted so it doesn't re-fetch every visit) ─────────────────────
_KEY = "survey_feedback_v5"  # v5: adds ticket id
top = st.columns([1, 3])
refresh = top[0].button("🔄 Refresh data")

disk = None if refresh else load_report(_KEY)
if disk is not None and disk.get("records") is not None:
    records = disk["records"]
    ticket_by_sid = disk.get("ticket_by_sid", {})
    _saved_at = disk.get("saved_at")
else:
    records = list_all(OBJECT, props, progress_label="Fetching survey submissions")
    ticket_by_sid = _resolve_ticket_info([r.get("id") for r in records]) if records else {}
    save_report(_KEY, {"records": records, "ticket_by_sid": ticket_by_sid})
    _saved_at = time.time()

if not records:
    st.info("No survey submissions found.")
    report_header_close()
    st.stop()

_sids = [str(r.get("id")) for r in records]
df = pd.DataFrame([r.get("properties", {}) for r in records])
for c in props:
    if c not in df.columns:
        df[c] = None
df["Ticket ID"] = [ticket_by_sid.get(s, {}).get("ticket_id", "—") for s in _sids]
df["Ticket Owner"] = [ticket_by_sid.get(s, {}).get("owner", "—") for s in _sids]
df["Ticket Name"] = [ticket_by_sid.get(s, {}).get("ticket", "—") for s in _sids]

# parse timestamp
if ts_prop:
    df["_ts"] = pd.to_datetime(df[ts_prop], errors="coerce", utc=True)
else:
    df["_ts"] = pd.NaT
# numeric score / rating
if "hs_value" in df.columns:
    df["_score"] = pd.to_numeric(df["hs_value"], errors="coerce")
    df["Rating"] = df["hs_value"]
else:
    df["_score"] = pd.NA
    df["Rating"] = "—"

if _saved_at:
    _age = int(time.time() - _saved_at)
    _ago = "just now" if _age < 90 else (f"{_age//60} min ago" if _age < 3600 else f"{_age//3600} h ago")
    st.caption(f"📌 Saved data · last refreshed **{_ago}** · click **Refresh data** to reload from HubSpot.")

# ── filters ─────────────────────────────────────────────────────────────────
f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1.5])

def _opts(col):
    return sorted([v for v in df[col].dropna().unique().tolist() if str(v).strip()]) if col in df.columns else []

sel_type = f1.multiselect("Survey type", _opts("hs_survey_type")) if "hs_survey_type" in df.columns else []
sel_owner = f2.multiselect("Ticket owner", _opts("Ticket Owner"))

if df["_ts"].notna().any():
    min_d = df["_ts"].min().date()
    max_d = df["_ts"].max().date()
    d_from = f3.date_input("From", value=min_d, min_value=min_d, max_value=max_d)
    d_to = f4.date_input("To", value=max_d, min_value=min_d, max_value=max_d)
else:
    d_from = d_to = None
sel_name = []

view = df.copy()
if sel_type:
    view = view[view["hs_survey_type"].isin(sel_type)]
if sel_owner:
    view = view[view["Ticket Owner"].isin(sel_owner)]
if d_from and d_to:
    m = (view["_ts"].dt.date >= d_from) & (view["_ts"].dt.date <= d_to)
    view = view[m | view["_ts"].isna()]

# ── summary tiles ───────────────────────────────────────────────────────────
total = len(view)
avg_score = view["_score"].mean() if view["_score"].notna().any() else None
with_comment = int(view["hs_content"].fillna("").astype(str).str.strip().ne("").sum()) if "hs_content" in view.columns else 0

def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.4rem;font-weight:800;color:{color};line-height:1.15;">{value}</div>
  {f'<div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
</div>"""

tiles = [tile("Responses", f"{total:,}")]
if avg_score is not None:
    tiles.append(tile("Avg score", f"{avg_score:.2f}"))
tiles.append(tile("With comment", f"{with_comment:,}"))
if "hs_response_group" in view.columns and view["hs_response_group"].notna().any():
    promoters = int((view["hs_response_group"] == "PROMOTER").sum())
    detractors = int((view["hs_response_group"] == "DETRACTOR").sum())
    if promoters or detractors:
        nps = round((promoters - detractors) / total * 100) if total else 0
        tiles.append(tile("NPS", f"{nps}", "promoters − detractors"))
st.markdown(f'<div style="display:grid;grid-template-columns:repeat({len(tiles)},1fr);gap:0.85rem;margin:0.5rem 0 1.5rem;">{"".join(tiles)}</div>',
            unsafe_allow_html=True)

# ── charts ──────────────────────────────────────────────────────────────────
c_left, c_right = st.columns(2)

with c_left:
    if view["_ts"].notna().any():
        st.markdown("##### Responses over time")
        tdf = view.dropna(subset=["_ts"]).copy()
        tdf["Month"] = tdf["_ts"].dt.to_period("M").dt.to_timestamp()
        by_month = tdf.groupby("Month").size().reset_index(name="Responses")
        chart = (alt.Chart(by_month).mark_bar(color=PRIMARY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                 .encode(x=alt.X("Month:T", title=None),
                         y=alt.Y("Responses:Q", title="Responses"),
                         tooltip=["Month:T", "Responses:Q"])
                 .properties(height=280))
        st.altair_chart(chart, use_container_width=True)

with c_right:
    grp_col = "hs_response_group" if ("hs_response_group" in view.columns and view["hs_response_group"].notna().any()) \
        else ("hs_sentiment" if "hs_sentiment" in view.columns and view["hs_sentiment"].notna().any() else None)
    if grp_col:
        st.markdown(f"##### {label_of.get(grp_col, grp_col)} breakdown")
        gdf = view[grp_col].fillna("—").value_counts().reset_index()
        gdf.columns = ["Group", "Count"]
        chart = (alt.Chart(gdf).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                 .encode(x=alt.X("Group:N", sort="-y", title=None),
                         y=alt.Y("Count:Q", title="Responses"),
                         color=alt.Color("Group:N",
                                         scale=alt.Scale(domain=list(GROUP_COLORS.keys()),
                                                         range=list(GROUP_COLORS.values())),
                                         legend=None),
                         tooltip=["Group", "Count"])
                 .properties(height=280))
        st.altair_chart(chart, use_container_width=True)
    elif view["_score"].notna().any():
        st.markdown("##### Score distribution")
        sdf = view["_score"].dropna().round().astype(int).value_counts().reset_index()
        sdf.columns = ["Score", "Count"]
        sdf = sdf.sort_values("Score")
        chart = (alt.Chart(sdf).mark_bar(color=PRIMARY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                 .encode(x=alt.X("Score:O", title="Score"), y=alt.Y("Count:Q", title="Responses"),
                         tooltip=["Score", "Count"])
                 .properties(height=280))
        st.altair_chart(chart, use_container_width=True)

# ── detail table ────────────────────────────────────────────────────────────
st.markdown("##### Submissions")
search = st.text_input("Search responses", placeholder="Filter by comment, survey, value…",
                       label_visibility="collapsed")

show_cols = [c for c in props if c in view.columns]
tbl = view[show_cols].copy()
if ts_prop and ts_prop in tbl.columns:
    tbl[ts_prop] = view["_ts"].dt.strftime("%b %d, %Y %I:%M %p")
tbl = tbl.rename(columns={c: label_of.get(c, c) for c in tbl.columns})

# surface resolved Ticket ID / Name / Owner and a clear Rating column up front
tbl.insert(0, "Ticket ID", view.loc[tbl.index, "Ticket ID"])
tbl.insert(1, "Ticket Name", view.loc[tbl.index, "Ticket Name"])
tbl.insert(2, "Ticket Owner", view.loc[tbl.index, "Ticket Owner"])
if "Rating" not in tbl.columns:
    tbl.insert(3, "Rating", view.loc[tbl.index, "Rating"])

if search.strip():
    q = search.strip().lower()
    mask = tbl.astype(str).apply(lambda x: x.str.lower().str.contains(q, na=False)).any(axis=1)
    tbl = tbl[mask]
    st.caption(f"{len(tbl):,} of {total:,} responses match “{search}”")

st.dataframe(tbl, use_container_width=True, hide_index=True, height=460)

st.download_button("📥 Download CSV", tbl.to_csv(index=False),
                   f"survey_feedback_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
from utils import pdf_download_button
pdf_download_button(tbl, "survey_feedback.pdf", "Survey Feedback", key="survey")

report_header_close()
