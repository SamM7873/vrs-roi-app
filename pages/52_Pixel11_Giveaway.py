import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, vrs_rate_for_month,
                   dash_spinner, save_report, load_report, saved_at_label, log_report_view,
                   pdf_download_button)

st.set_page_config(page_title="Pixel 11 Giveaway", layout="wide", page_icon="🎁")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Pixel 11 Giveaway")

report_header("Pixel 11 Giveaway — VRS ROI",
              "Emails → Contact → VRS Number → Monthly Values (this month → future)",
              section="Numbers")

NUM_OBJECT = "2-40974683"   # Number object
MV_OBJECT = "2-46246179"    # Monthly Values
_key = "pixel11_giveaway_v1"

# Permanent giveaway recipient list (pre-filled; editable in the box).
GIVEAWAY_EMAILS = """Domokidz03@gmail.com
milkakitty1995@yahoo.com
jazmynehuerta@gmail.com
jerrinfinite@gmail.com
kjerstinann26@gmail.com
luxboucle@gmail.com
jelitchfield@icloud.com
chrissylove33112@gmail.com
efraincasillas90@gmail.com
bluessrosez5@gmail.com
miletoca@optonline.net
cjosborn1997@icloud.com
danielaknicks@gmail.com
matrixrabbit13@gmail.com
frj1982@gmail.com
aebdc1984@gmail.com
misssjoie@gmail.com
herojcthe9@gmail.com
kevinagdovin@gmail.com
iluvblue4ever@gmail.com
Rebecca1asl@aol.com
sxybooty03@gmail.com
samrh8915@gmail.com
sbackus64@yahoo.com
Tanner32501.25@gmail.com"""


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


def _seek_mv(props, filters):
    url = f"{_B}/crm/v3/objects/{MV_OBJECT}/search"
    out, last = [], "0"
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(5):
            r = requests.post(url, headers=_H, json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            break
        batch = r.json().get("results", [])
        out.extend(batch)
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.06)
    return out


def _dig(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


st.markdown("Paste the **giveaway recipient emails** (one per line). Each email is matched to its "
            "**Contact → VRS Number**, then **Monthly Values** usage is summed from the chosen start "
            "month **forward** (past months are ignored). VRS numbers only.")

emails_raw = GIVEAWAY_EMAILS
_emails = sorted({e.strip().lower() for e in emails_raw.replace(",", "\n").splitlines() if e.strip()})

c1, c2 = st.columns([2, 1])
start_month = c1.text_input("Count from month (YYYY-MM)", value="2026-09",
                            help="Monthly Values on/after this month count; earlier months are ignored.")
c1.caption(f"{len(_emails)} giveaway recipients · from September 2026 → future.")
c2.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
run = c2.button("▶ Run", type="primary", use_container_width=True)
with st.expander("✏️ Edit recipient list", expanded=False):
    emails_raw = st.text_area("Recipient emails", value=GIVEAWAY_EMAILS, height=200,
                              label_visibility="collapsed")
    _emails = sorted({e.strip().lower() for e in emails_raw.replace(",", "\n").splitlines() if e.strip()})

if run:
    if not _emails:
        st.warning("Paste at least one email."); report_header_close(); st.stop()
    _cut = start_month.strip()[:7]

    # 1) emails → contacts
    with dash_spinner(f"Finding contacts for {len(_emails):,} emails…"):
        email_to_cids = defaultdict(list)
        for i in range(0, len(_emails), 100):
            chunk = _emails[i:i + 100]
            for c in fetch_all("contacts", ["email", "firstname", "lastname"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                em = (c.get("properties", {}).get("email") or "").strip().lower()
                if em:
                    email_to_cids[em].append(str(c["id"]))
        con_of = _batch_read("contacts", sorted({c for v in email_to_cids.values() for c in v}),
                             ["email", "firstname", "lastname"])

    # 2) contact → Number (assoc) ; keep VRS numbers
    all_cids = sorted({c for v in email_to_cids.values() for c in v})
    with dash_spinner("Linking contacts → Number objects…"):
        cid_to_nids = _assoc("contacts", NUM_OBJECT, all_cids)
    all_nids = sorted({n for v in cid_to_nids.values() for n in v})
    nprops = ["number", "email", "service_type", "number_status"]
    num_of = _batch_read(NUM_OBJECT, all_nids, nprops) if all_nids else {}

    def _is_vrs(nid):
        return "vrs" in (num_of.get(nid, {}).get("service_type") or "").lower()

    # email → VRS number ids
    email_to_vrs = {}
    for em, cids in email_to_cids.items():
        nids = sorted({n for c in cids for n in cid_to_nids.get(c, []) if _is_vrs(n)})
        email_to_vrs[em] = nids

    vrs_nids = sorted({n for v in email_to_vrs.values() for n in v})
    nid_num = {n: str(num_of.get(n, {}).get("number") or "").strip() for n in vrs_nids}
    vrs_numbers = sorted({v for v in nid_num.values() if v})

    # 3) Monthly Values for those VRS numbers, month >= cutoff
    num_month = defaultdict(dict)   # number → {YYYY-MM: minutes}
    if vrs_numbers:
        with dash_spinner(f"Pulling Monthly Values for {len(vrs_numbers):,} VRS numbers…"):
            for i in range(0, len(vrs_numbers), 100):
                chunk = vrs_numbers[i:i + 100]
                for o in _seek_mv(["number", "usage_minutes", "service_type", "month_date"],
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                   {"propertyName": "usage_minutes", "operator": "GT", "value": "0"}]):
                    op = o.get("properties", {})
                    num = str(op.get("number") or "").strip()
                    mk = str(op.get("month_date") or "")[:7]
                    if num and mk and mk >= _cut:          # this month → future only
                        num_month[num][mk] = num_month[num].get(mk, 0.0) + (to_float(op.get("usage_minutes")) or 0.0)

    all_months = sorted({mk for nm in num_month.values() for mk in nm})
    rows = []
    for em in _emails:
        nids = email_to_vrs.get(em, [])
        nums = sorted({nid_num.get(n, "") for n in nids} - {""})
        stat = sorted({(num_of.get(n, {}).get("number_status") or "").strip().title() for n in nids} - {""})
        by_month = defaultdict(float)
        for x in nums:
            for mk, m in num_month.get(x, {}).items():
                by_month[mk] += m
        tmin = round(sum(by_month.values()), 1)
        tfcc = round(sum(m * vrs_rate_for_month(mk) for mk, m in by_month.items()), 2)
        cm = con_of.get(email_to_cids.get(em, [None])[0], {}) if email_to_cids.get(em) else {}
        row = {
            "Email": em,
            "Name": f"{(cm.get('firstname') or '').strip()} {(cm.get('lastname') or '').strip()}".strip() or "—",
            "VRS Number(s)": ", ".join(nums) or "—",
            "Status": ", ".join(stat) or "—",
            "Has VRS": "Yes" if nids else "No",
            "VRS Minutes (from " + _cut + ")": tmin,
            "FCC $": tfcc,
        }
        for mk in all_months:
            row[mk] = round(by_month.get(mk, 0.0), 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    mv_rows = [{"Month": mk,
                "VRS Minutes": round(sum(num_month[n].get(mk, 0.0) for n in num_month), 1),
                "FCC $": round(sum(num_month[n].get(mk, 0.0) for n in num_month) * vrs_rate_for_month(mk), 2)}
               for mk in all_months]
    save_report(_key, {"df": df, "mv_df": pd.DataFrame(mv_rows), "cut": _cut,
                       "n_emails": len(_emails)})

saved = load_report(_key)
if saved is None:
    st.info("Paste the emails, set the start month, then click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · from **{saved.get('cut','')}** → future · "
               f"{saved.get('n_emails',0):,} emails")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
hv = int((df["Has VRS"] == "Yes").sum())
_mincol = next((c for c in df.columns if c.startswith("VRS Minutes")), None)
tot_min = int(round(df[_mincol].sum())) if _mincol else 0
tot_fcc = round(df["FCC $"].sum(), 2) if "FCC $" in df.columns else 0
k = st.columns(4)
_card(k[0], "🎁 Emails", f"{N:,}", "giveaway recipients", "#7A5CFF")
_card(k[1], "📞 Have VRS number", f"{hv:,}", f"{hv/N*100:.0f}% of emails" if N else "—", "#0FB5AE")
_card(k[2], "⏱️ VRS minutes", f"{tot_min:,}", f"from {saved.get('cut','')} →", "#4C8DFF")
_card(k[3], "💵 FCC value", f"${tot_fcc:,.0f}", "minutes × FCC rate", "#2DB84B")
st.markdown("")

_mv = saved.get("mv_df")
if _mv is not None and not _mv.empty:
    st.markdown("##### Monthly trend (this month → future)")
    st.dataframe(_mv.sort_values("Month"), use_container_width=True, hide_index=True)

st.markdown("##### Recipients")
f1, f2 = st.columns([1, 3])
hvp = f1.radio("Has VRS", ["All", "Yes", "No"], horizontal=True, key="px_hasvrs")
q = f2.text_input("Search email / name / number").strip().lower()
view = df.copy()
if hvp != "All":
    view = view[view["Has VRS"] == hvp]
if q:
    view = view[view.apply(lambda r: q in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "pixel11_giveaway.csv", "text/csv")

report_header_close()
