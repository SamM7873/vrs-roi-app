import streamlit as st
import pandas as pd
from datetime import datetime
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, log_report_view, save_report, load_report, saved_at_label)

st.set_page_config(page_title="Data Health Audit", layout="wide", page_icon="🧹")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Data Health Audit")

report_header("Data Health & Email Association Audit",
              "Find Numbers with missing data, and review primary vs secondary email matches",
              section="Tools")

NUM_OBJECT = "2-40974683"


def _norm(v):
    return str(v or "").strip().lower()


st.markdown("Reviews the **Number** object for data-quality gaps and checks each Number's email "
            "against **Contact primary email** and **secondary emails** (`hs_additional_emails`) "
            "to flag numbers that should be re-associated or cleaned up.")

c1, c2 = st.columns(2)
with c1:
    svc = st.selectbox("Service type", ["VRS", "Convo Now", "All"], index=0)
with c2:
    only_live = st.checkbox("Live numbers only (account_status = Live)", value=True)

run = st.button("Run audit", type="primary")

if run:
    filters = []
    if svc != "All":
        filters.append({"propertyName": "service_type", "operator": "EQ", "value": svc})
    if only_live:
        filters.append({"propertyName": "account_status", "operator": "EQ", "value": "Live"})

    with st.spinner("Fetching Number objects…"):
        num_recs = fetch_all(
            NUM_OBJECT,
            ["number", "email", "service_type", "account_status", "number_status",
             "usage_type", "number_created_at"],
            filter_groups=[{"filters": filters}] if filters else None)

    nums = []
    for r in num_recs:
        p = r.get("properties", {})
        nums.append({
            "Number": str(p.get("number") or "").strip(),
            "Email": _norm(p.get("email")),
            "Service": (p.get("service_type") or "").strip() or "—",
            "Account Status": (p.get("account_status") or "").strip(),
            "Number Status": (p.get("number_status") or "").strip(),
            "Usage Type": (p.get("usage_type") or "").strip(),
        })
    ndf = pd.DataFrame(nums)
    if ndf.empty:
        st.warning("No Number objects matched the filters.")
        report_header_close(); st.stop()

    # ── contact email maps (primary + secondary) for the number emails ──────────
    number_emails = sorted({e for e in ndf["Email"].tolist() if e})
    primary_map = {}     # primary email -> "First Last"
    secondary_map = {}   # secondary email -> primary email of the contact holding it
    with st.spinner(f"Matching {len(number_emails):,} emails against Contacts…"):
        # primary matches: email IN chunks
        for i in range(0, len(number_emails), 100):
            chunk = number_emails[i:i + 100]
            cs = fetch_all("contacts", ["email", "firstname", "lastname", "hs_additional_emails"],
                           filter_groups=[{"filters": [
                               {"propertyName": "email", "operator": "IN", "values": chunk}]}])
            for c in cs:
                p = c.get("properties", {})
                em = _norm(p.get("email"))
                nm = f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip()
                if em:
                    primary_map[em] = nm or em
                for se in str(p.get("hs_additional_emails") or "").replace(",", ";").split(";"):
                    se = _norm(se)
                    if se:
                        secondary_map.setdefault(se, em)
        # secondary matches for emails still unmatched: search hs_additional_emails (5 per call)
        unmatched = [e for e in number_emails if e not in primary_map and e not in secondary_map]
        for i in range(0, len(unmatched), 5):
            grp = unmatched[i:i + 5]
            cs = fetch_all("contacts", ["email", "firstname", "lastname", "hs_additional_emails"],
                           filter_groups=[{"filters": [
                               {"propertyName": "hs_additional_emails",
                                "operator": "CONTAINS_TOKEN", "value": e}]} for e in grp])
            for c in cs:
                p = c.get("properties", {})
                em = _norm(p.get("email"))
                for se in str(p.get("hs_additional_emails") or "").replace(",", ";").split(";"):
                    se = _norm(se)
                    if se:
                        secondary_map.setdefault(se, em)

    # ── classify each number ────────────────────────────────────────────────────
    def _email_class(row):
        e = row["Email"]
        if not e:
            return "❌ No email", ""
        if e in primary_map:
            return "✅ Primary match", primary_map[e]
        if e in secondary_map:
            return "🟠 Secondary email", f"primary: {secondary_map[e] or '—'}"
        return "🔴 Orphan (no contact)", ""

    ndf[["Email match", "Contact / note"]] = ndf.apply(
        lambda r: pd.Series(_email_class(r)), axis=1)

    # data-quality flags
    ndf["Missing email"] = ndf["Email"] == ""
    ndf["Missing account_status"] = ndf["Account Status"] == ""
    ndf["Missing usage_type"] = ndf["Usage Type"] == ""
    ndf["Status mismatch"] = (ndf["Account Status"].str.lower() != ndf["Number Status"].str.lower()) & \
                             (ndf["Number Status"] != "")

    total = len(ndf)
    st.session_state["dha"] = {"ndf": ndf, "total": total}

if "dha" in st.session_state:
    ndf = st.session_state["dha"]["ndf"]
    total = st.session_state["dha"]["total"]

    st.markdown("##### Summary")
    m = st.columns(4)
    m[0].metric("Numbers audited", f"{total:,}")
    m[1].metric("❌ Missing email", f"{int(ndf['Missing email'].sum()):,}")
    m[2].metric("🟠 Secondary-email matches", f"{int((ndf['Email match']=='🟠 Secondary email').sum()):,}")
    m[3].metric("🔴 Orphan (no contact)", f"{int((ndf['Email match']=='🔴 Orphan (no contact)').sum()):,}")
    m2 = st.columns(4)
    m2[0].metric("✅ Primary matches", f"{int((ndf['Email match']=='✅ Primary match').sum()):,}")
    m2[1].metric("Missing account_status", f"{int(ndf['Missing account_status'].sum()):,}")
    m2[2].metric("Missing usage_type", f"{int(ndf['Missing usage_type'].sum()):,}")
    m2[3].metric("Status field mismatch", f"{int(ndf['Status mismatch'].sum()):,}")

    st.markdown("##### Review email association")
    st.caption("**🟠 Secondary** = the Number's email is a *secondary* email on a contact whose "
               "*primary* is different → consider re-associating to the primary contact or updating "
               "the Number's email. **🔴 Orphan** = email matches no contact at all.")
    _view = st.radio("Show", ["All issues", "🟠 Secondary email", "🔴 Orphan", "❌ No email",
                              "Missing account_status", "Status mismatch"], horizontal=True)
    show = ndf
    if _view == "🟠 Secondary email":
        show = ndf[ndf["Email match"] == "🟠 Secondary email"]
    elif _view == "🔴 Orphan":
        show = ndf[ndf["Email match"] == "🔴 Orphan (no contact)"]
    elif _view == "❌ No email":
        show = ndf[ndf["Missing email"]]
    elif _view == "Missing account_status":
        show = ndf[ndf["Missing account_status"]]
    elif _view == "Status mismatch":
        show = ndf[ndf["Status mismatch"]]
    elif _view == "All issues":
        show = ndf[(ndf["Email match"] != "✅ Primary match") | ndf["Missing account_status"] |
                   ndf["Missing usage_type"] | ndf["Status mismatch"]]

    _cols = ["Number", "Email", "Service", "Account Status", "Number Status", "Usage Type",
             "Email match", "Contact / note"]
    st.caption(f"Showing {len(show):,} of {total:,}.")
    st.dataframe(show[_cols], use_container_width=True, hide_index=True, height=460)
    st.download_button("📥 Export audit CSV", ndf[_cols].to_csv(index=False),
                       f"data_health_audit_{datetime.now():%Y%m%d}.csv", "text/csv")

report_header_close()
