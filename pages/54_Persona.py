import streamlit as st
import pandas as pd
import time
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner, log_report_view)

st.set_page_config(page_title="Persona", layout="wide", page_icon="🧑")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Persona")

report_header("Persona 360",
              "One email → Contact · Tickets · Registration · Number (VRS)",
              section="Tools")

REG_OBJECT = "2-58833629"   # registration
NUM_OBJECT = "2-40974683"   # Number object

DEFAULT_EMAILS = """lorena.navarrete+chile@convorelay.com
lorena.navarrete+govermentidnotpassed@convorelay.com
lorena.navarrete+expired@convorelay.com"""


def _norm(v):
    return str(v or "").strip().lower()


def _assoc(from_obj, to_obj, from_ids):
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
        time.sleep(0.05)
    return out


def _batch_read(obj, ids, props):
    out = {}
    ids = [str(x) for x in ids]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{obj}/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk], "properties": props}, timeout=60)
            if r.status_code in (200, 207):
                for o in r.json().get("results", []):
                    out[str(o["id"])] = o.get("properties", {})
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.05)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _owner_names():
    out = {}
    try:
        r = requests.get(f"{_B}/crm/v3/owners?limit=500", headers=_H, timeout=15)
        if r.status_code == 200:
            for o in r.json().get("results", []):
                nm = f"{(o.get('firstName') or '').strip()} {(o.get('lastName') or '').strip()}".strip()
                out[str(o["id"])] = nm or o.get("email", str(o["id"]))
    except Exception:
        pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _pipelines():
    sl = {}
    try:
        r = requests.get(f"{_B}/crm/v3/pipelines/tickets", headers=_H, timeout=15)
        if r.status_code == 200:
            for pipe in r.json().get("results", []):
                for stg in pipe.get("stages", []):
                    sl[stg["id"]] = stg.get("label", stg["id"])
    except Exception:
        pass
    return sl


st.markdown("For each email, checks the **Contact**, its **Tickets**, the **Registration** object, "
            "and the **Number** object (via contact association, the number's own email, or the "
            "registration's number).")

emails_raw = st.text_area("Emails (one per line)", value=DEFAULT_EMAILS, height=110)
_emails = sorted({_norm(e) for e in emails_raw.replace(",", "\n").splitlines() if e.strip()})
run = st.button("▶ Run", type="primary")

if run and _emails:
    _own, _stages = _owner_names(), _pipelines()

    # 1) contacts by email
    with dash_spinner("Reading contacts…"):
        email_cids = defaultdict(list)
        con_of = {}
        for i in range(0, len(_emails), 100):
            chunk = _emails[i:i + 100]
            for c in fetch_all("contacts", ["email", "firstname", "lastname", "phone", "lifecyclestage",
                                            "hs_lead_status", "createdate"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                em = _norm(c.get("properties", {}).get("email"))
                if em:
                    email_cids[em].append(str(c["id"])); con_of[str(c["id"])] = c.get("properties", {})
    all_cids = sorted(con_of)

    # 2) contact → tickets
    with dash_spinner("Reading tickets…"):
        cid_tids = _assoc("contacts", "tickets", all_cids)
        all_tids = sorted({t for v in cid_tids.values() for t in v})
        tk_of = _batch_read("tickets", all_tids,
                            ["subject", "hs_pipeline_stage", "createdate", "hubspot_owner_id",
                             "hs_ticket_category", "subcategory"])

    # 3) registrations by email
    with dash_spinner("Reading registrations…"):
        email_regs = defaultdict(list)
        for i in range(0, len(_emails), 100):
            chunk = _emails[i:i + 100]
            for r in fetch_all(REG_OBJECT, ["email", "number", "first_name", "last_name",
                                            "credit_type", "registered_at"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                em = _norm(r.get("properties", {}).get("email"))
                if em:
                    email_regs[em].append(r.get("properties", {}))

    # 4) numbers: contact assoc ∪ number.email match ∪ reg number
    nprops = ["number", "email", "service_type", "number_status", "usage_type", "registration_type",
              "registered_at", "number_created_at"]
    with dash_spinner("Reading numbers…"):
        cid_nids = _assoc("contacts", NUM_OBJECT, all_cids)
        num_of = _batch_read(NUM_OBJECT, sorted({n for v in cid_nids.values() for n in v}), nprops)
        # number.email match
        for i in range(0, len(_emails), 100):
            chunk = _emails[i:i + 100]
            for o in fetch_all(NUM_OBJECT, nprops, filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                num_of[str(o["id"])] = o.get("properties", {})
        # reg numbers
        _rnums = sorted({str(p.get("number") or "").strip()
                         for ps in email_regs.values() for p in ps if str(p.get("number") or "").strip()})
        for i in range(0, len(_rnums), 100):
            chunk = _rnums[i:i + 100]
            for o in fetch_all(NUM_OBJECT, nprops, filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk}]}]):
                num_of[str(o["id"])] = o.get("properties", {})

    # summary + per-persona detail
    summ = []
    for em in _emails:
        cids = email_cids.get(em, [])
        tids = sorted({t for c in cids for t in cid_tids.get(c, [])})
        regs = email_regs.get(em, [])
        # numbers for this email: via contact assoc, number.email, reg number
        _rn = {str(p.get("number") or "").strip() for p in regs}
        nums = []
        for nid, p in num_of.items():
            if (_norm(p.get("email")) == em
                    or any(nid in cid_nids.get(c, []) for c in cids)
                    or str(p.get("number") or "").strip() in _rn):
                nums.append(p)
        vrs_live = [p for p in nums if "vrs" in _norm(p.get("service_type"))
                    and _norm(p.get("number_status")) == "live"]
        summ.append({
            "Email": em,
            "Contact": "Yes" if cids else "No",
            "Tickets": len(tids),
            "Registration": "Yes" if regs else "No",
            "Numbers": len(nums),
            "VRS Live": "Yes" if vrs_live else "No",
        })
        st.session_state.setdefault("_persona_detail", {})[em] = {
            "contact": [con_of[c] for c in cids], "tickets": [tk_of.get(t, {}) for t in tids],
            "regs": regs, "nums": nums}
    st.session_state["_persona_summary"] = summ

summ = st.session_state.get("_persona_summary")
if not summ:
    st.info("Enter emails and click **▶ Run**.")
    report_header_close(); st.stop()

st.markdown("##### Summary")
st.dataframe(pd.DataFrame(summ), use_container_width=True, hide_index=True)

detail = st.session_state.get("_persona_detail", {})
_own, _stages = _owner_names(), _pipelines()
for em in [s["Email"] for s in summ]:
    d = detail.get(em, {})
    with st.expander(f"🧑 {em}", expanded=len(summ) <= 3):
        cc = d.get("contact", [])
        st.markdown("**Contact**")
        if cc:
            st.dataframe(pd.DataFrame([{
                "Name": f"{(c.get('firstname') or '').strip()} {(c.get('lastname') or '').strip()}".strip() or "—",
                "Email": c.get("email") or "—", "Phone": c.get("phone") or "—",
                "Lifecycle": c.get("lifecyclestage") or "—", "Lead status": c.get("hs_lead_status") or "—",
            } for c in cc]), use_container_width=True, hide_index=True)
        else:
            st.caption("No contact found.")

        st.markdown("**Registration**")
        rr = d.get("regs", [])
        if rr:
            st.dataframe(pd.DataFrame([{
                "Name": f"{(r.get('first_name') or '').strip()} {(r.get('last_name') or '').strip()}".strip() or "—",
                "Email": r.get("email") or "—", "Number": r.get("number") or "—",
                "Credit type": r.get("credit_type") or "—", "Registered at": str(r.get("registered_at") or "")[:10] or "—",
            } for r in rr]), use_container_width=True, hide_index=True)
        else:
            st.caption("No registration found.")

        st.markdown("**Number(s)**")
        nn = d.get("nums", [])
        if nn:
            st.dataframe(pd.DataFrame([{
                "Number": p.get("number") or "—", "Service": p.get("service_type") or "—",
                "Status": p.get("number_status") or "—", "Usage": p.get("usage_type") or "—",
                "Reg type": p.get("registration_type") or "—",
                "Created": str(p.get("number_created_at") or "")[:10] or "—",
            } for p in nn]), use_container_width=True, hide_index=True)
        else:
            st.caption("No number found.")

        st.markdown("**Tickets**")
        tt = d.get("tickets", [])
        if tt:
            st.dataframe(pd.DataFrame([{
                "Subject": t.get("subject") or "—",
                "Stage": _stages.get(t.get("hs_pipeline_stage"), t.get("hs_pipeline_stage") or "—"),
                "Category": t.get("hs_ticket_category") or "—", "Subcategory": t.get("subcategory") or "—",
                "Owner": _own.get(str(t.get("hubspot_owner_id") or ""), "—"),
                "Created": str(t.get("createdate") or "")[:10],
            } for t in tt]), use_container_width=True, hide_index=True)
        else:
            st.caption("No tickets found.")

report_header_close()
