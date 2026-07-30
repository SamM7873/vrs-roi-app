import streamlit as st
import pandas as pd
import requests
from datetime import datetime
from collections import defaultdict
from utils import (require_auth, get_secret, COMMON_CSS, report_header,
                   report_header_close, log_report_view)

st.set_page_config(page_title="CONVO360 Import", layout="wide", page_icon="📥")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("CONVO360 Import")

report_header("CONVO360 Import & Match",
              "Upload the CONVO360 interaction CSV, match rows to T1/T2 tickets, audit and export",
              section="Tools")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def _norm(s):
    return " ".join(str(s or "").strip().lower().split())


@st.cache_data(ttl=3600, show_spinner=False)
def _pipelines():
    try:
        r = requests.get(f"{BASE_URL}/crm/v3/pipelines/tickets", headers=_headers, timeout=20)
        if r.status_code == 200:
            return {p["label"]: p["id"] for p in r.json().get("results", [])}
    except Exception:
        pass
    return {}


@st.cache_data(ttl=600, show_spinner=False)
def _owners():
    """id -> {'full': 'First Last', 'first': 'First'}."""
    out = {}
    for _arch in (False, True):
        after = None
        for _ in range(30):
            url = (f"{BASE_URL}/crm/v3/owners?limit=100" + ("&archived=true" if _arch else "")
                   + (f"&after={after}" if after else ""))
            r = requests.get(url, headers=_headers, timeout=20)
            if r.status_code != 200:
                break
            d = r.json()
            for o in d.get("results", []):
                fn = (o.get("firstName") or "").strip()
                ln = (o.get("lastName") or "").strip()
                out[str(o.get("id"))] = {"full": f"{fn} {ln}".strip() or o.get("email", ""), "first": fn}
            after = d.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
    return out


def _fetch_tickets(pipe_ids):
    """Tickets in the given pipelines with owner + create date + email."""
    tickets = []
    for pid in pipe_ids:
        after = None
        while True:
            body = {"filterGroups": [{"filters": [
                        {"propertyName": "hs_pipeline", "operator": "EQ", "value": pid}]}],
                    "properties": ["subject", "createdate", "closed_date", "hubspot_owner_id",
                                   "email", "hs_pipeline"],
                    "limit": 100, "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}]}
            if after:
                body["after"] = after
            r = requests.post(f"{BASE_URL}/crm/v3/objects/tickets/search", headers=_headers, json=body, timeout=30)
            if r.status_code != 200:
                break
            d = r.json()
            tickets.extend(d.get("results", []))
            after = d.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
    return tickets


def _ticket_contacts(ticket_ids):
    """ticket_id -> {'email', 'name'} from associated contacts (batched)."""
    tid_to_cids = defaultdict(list)
    for i in range(0, len(ticket_ids), 100):
        chunk = ticket_ids[i:i + 100]
        body = {"inputs": [{"id": t} for t in chunk]}
        try:
            r = requests.post(f"{BASE_URL}/crm/v4/associations/tickets/contacts/batch/read",
                              headers=_headers, json=body, timeout=30)
            if r.status_code == 200:
                for res in r.json().get("results", []):
                    tid = str(res.get("from", {}).get("id"))
                    for to in res.get("to", []):
                        tid_to_cids[tid].append(str(to.get("toObjectId") or to.get("id")))
        except Exception:
            pass
    # fetch contact details
    all_cids = list({c for cids in tid_to_cids.values() for c in cids})
    cinfo = {}
    for i in range(0, len(all_cids), 100):
        chunk = all_cids[i:i + 100]
        body = {"inputs": [{"id": c} for c in chunk], "properties": ["email", "firstname", "lastname"]}
        try:
            r = requests.post(f"{BASE_URL}/crm/v3/objects/contacts/batch/read", headers=_headers, json=body, timeout=30)
            if r.status_code == 200:
                for obj in r.json().get("results", []):
                    p = obj.get("properties", {})
                    cinfo[str(obj["id"])] = {
                        "email": (p.get("email") or "").strip().lower(),
                        "name": f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip()}
        except Exception:
            pass
    out = {}
    for tid, cids in tid_to_cids.items():
        for c in cids:
            info = cinfo.get(c)
            if info and (info["email"] or info["name"]):
                out[tid] = info
                break
    return out


# ── UI ────────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Upload CONVO360 interaction CSV", type=["csv"])
pipes = _pipelines()
if not pipes:
    st.warning("Could not load ticket pipelines from HubSpot.")
pick = st.multiselect("Focus pipelines (choose your **T1** and **T2**)", list(pipes.keys()),
                      help="Pick the Tier-1 and Tier-2 support pipelines to match against.")
run = st.button("Run import & match", type="primary", disabled=not (uploaded and pick))

if run:
    df = pd.read_csv(uploaded)
    df.columns = [c.strip() for c in df.columns]
    _email_col = next((c for c in df.columns if "email" in c.lower()), None)
    name_col = next((c for c in df.columns if c.lower() in ("customer name", "name")), None)
    agent_col = next((c for c in df.columns if c.lower() == "agent"), None)
    date_col = next((c for c in df.columns if "date" in c.lower()), None)

    with st.spinner("Fetching T1/T2 tickets from HubSpot…"):
        owners = _owners()
        tickets = _fetch_tickets([pipes[p] for p in pick])
        tconts = _ticket_contacts([t["id"] for t in tickets])

    # build ticket index for matching
    by_email, by_name, by_agent_day = {}, {}, defaultdict(list)
    pipe_label = {v: k for k, v in pipes.items()}
    tinfo = {}
    for t in tickets:
        p = t.get("properties", {})
        tid = t["id"]
        cont = tconts.get(tid, {})
        email = (cont.get("email") or (p.get("email") or "")).strip().lower()
        cname = cont.get("name") or ""
        owner = owners.get(str(p.get("hubspot_owner_id") or ""), {})
        created = (p.get("createdate") or "")[:10]
        tinfo[tid] = {"email": email, "name": cname, "owner": owner.get("full", ""),
                      "pipeline": pipe_label.get(p.get("hs_pipeline"), ""), "created": created,
                      "subject": p.get("subject") or ""}
        if email:
            by_email.setdefault(email, tid)
        if cname:
            by_name.setdefault(_norm(cname), tid)
        if owner.get("first") and created:
            by_agent_day[(_norm(owner["first"]), created)].append(tid)

    # match each CSV row — try all keys in order: email, name, agent+date
    results = []
    for _, row in df.iterrows():
        matched_tid, method = None, "—"
        if _email_col and _norm(row.get(_email_col)):
            matched_tid = by_email.get(_norm(row.get(_email_col)))
            if matched_tid:
                method = "email"
        if not matched_tid and name_col and _norm(row.get(name_col)):
            matched_tid = by_name.get(_norm(row.get(name_col)))
            if matched_tid:
                method = "name"
        if not matched_tid and agent_col and date_col:
            try:
                _day = pd.to_datetime(row.get(date_col), errors="coerce")
                _day = _day.strftime("%Y-%m-%d") if pd.notna(_day) else None
            except Exception:
                _day = None
            cands = by_agent_day.get((_norm(row.get(agent_col)), _day)) if _day else None
            if cands:
                matched_tid = cands[0]
                method = "agent+date"
        ti = tinfo.get(matched_tid, {})
        results.append({
            "Type": row.get("Type", ""),
            "Customer Name": row.get(name_col, "") if name_col else "",
            "Date": row.get(date_col, "") if date_col else "",
            "Agent": row.get(agent_col, "") if agent_col else "",
            "Matched": "✅" if matched_tid else "❌",
            "Match Method": method,
            "Ticket Email": ti.get("email", ""),
            "Ticket ID": matched_tid or "",
            "Pipeline": ti.get("pipeline", ""),
            "Ticket Subject": ti.get("subject", ""),
        })
    res_df = pd.DataFrame(results)

    # ── audit ──
    total = len(res_df)
    matched = int((res_df["Matched"] == "✅").sum())
    st.markdown("##### Import audit")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("CSV rows", f"{total:,}")
    a2.metric("Matched to ticket", f"{matched:,}", f"{matched/total*100:.0f}%" if total else "—")
    a3.metric("Unmatched", f"{total - matched:,}")
    a4.metric("T1/T2 tickets", f"{len(tickets):,}")

    _mm = res_df[res_df["Matched"] == "✅"]["Match Method"].value_counts().rename_axis("Method").reset_index(name="Count")
    if not _mm.empty:
        st.caption("Matched by:")
        st.dataframe(_mm, use_container_width=True, hide_index=True)

    st.markdown("##### Matched / unmatched rows")
    _view = st.radio("Show", ["All", "Matched only", "Unmatched only"], horizontal=True)
    show = res_df
    if _view == "Matched only":
        show = res_df[res_df["Matched"] == "✅"]
    elif _view == "Unmatched only":
        show = res_df[res_df["Matched"] == "❌"]
    st.dataframe(show, use_container_width=True, hide_index=True, height=460)

    st.download_button("📥 Export merged CSV", res_df.to_csv(index=False),
                       f"convo360_matched_{datetime.now():%Y%m%d}.csv", "text/csv")

report_header_close()
