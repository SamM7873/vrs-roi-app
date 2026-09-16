import streamlit as st
import pandas as pd
import time
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view, pdf_download_button)

st.set_page_config(page_title="T2 Support", layout="wide", page_icon="🛠️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("T2 Support")

report_header("T2 Support",
              "T2 Support pipeline tickets — filtered, with associated Number objects.",
              section="Support")

NUM_OBJECT = "2-40974683"   # Number object
_key = "t2_support_v2_vrs"


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
def _pipelines():
    """{id: label} and {stage_id: label} for the tickets object."""
    pl, sl = {}, {}
    try:
        r = requests.get(f"{_B}/crm/v3/pipelines/tickets", headers=_H, timeout=15)
        if r.status_code == 200:
            for pipe in r.json().get("results", []):
                pl[pipe["id"]] = pipe.get("label", pipe["id"])
                for stg in pipe.get("stages", []):
                    sl[stg["id"]] = stg.get("label", stg["id"])
    except Exception:
        pass
    return pl, sl


def _search_all(obj, props, filters):
    url = f"{_B}/crm/v3/objects/{obj}/search"
    out, after = [], None
    while True:
        body = {"filterGroups": [{"filters": filters}], "properties": props, "limit": 100,
                "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}]}
        if after:
            body["after"] = after
        r = None
        for attempt in range(5):
            r = requests.post(url, headers=_H, json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            st.error(f"HubSpot error {getattr(r,'status_code','?')}"); break
        data = r.json()
        out.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after or len(out) >= 10000:
            break
        time.sleep(0.06)
    return out


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
        time.sleep(0.06)
    return out


_pl, _sl = _pipelines()
_opts = {v: k for k, v in _pl.items()}
_default = next((v for v in _opts if "t2 support" in v.lower()), None)
_labels = sorted(_opts.keys())

st.markdown("Pulls every ticket in the **T2 Support** pipeline, applies the "
            "**Subcategory** and **Ticket description** filters, then follows "
            "**ticket → Number** (directly and via associated contacts) to show the "
            "associated VRS number(s) and their status.")

c1, c2, c3 = st.columns([1.6, 1.6, 1])
pipe_label = c1.selectbox("Ticket pipeline", _labels,
                          index=(_labels.index(_default) if _default in _labels else 0))
kw_raw = c2.text_input("Ticket description contains any of (comma-separated)",
                       value="cfz, mobile, tablet")
run = c3.button("▶ Run", type="primary", use_container_width=True)
c3.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)

_keywords = [k.strip().lower() for k in kw_raw.split(",") if k.strip()]

if run:
    pid = _opts.get(pipe_label)
    with dash_spinner(f"Reading “{pipe_label}” tickets…"):
        tks = _search_all("tickets",
                          ["subject", "content", "convo_products", "hs_ticket_category",
                           "subcategory", "first_name", "last_name", "hs_pipeline_stage",
                           "hubspot_owner_id", "createdate", "closed_date"],
                          [{"propertyName": "hs_pipeline", "operator": "EQ", "value": pid}])
    if not tks:
        st.warning("No tickets in that pipeline."); report_header_close(); st.stop()

    tids = [str(t["id"]) for t in tks]
    # ticket → contact (for emails + contact → number chain)
    with dash_spinner(f"Linking {len(tids):,} tickets to contacts…"):
        t2c = _assoc("tickets", "contacts", tids)
    all_cids = sorted({c for cs in t2c.values() for c in cs})
    with dash_spinner(f"Reading {len(all_cids):,} contacts…"):
        con_of = _batch_read("contacts", all_cids, ["email", "firstname", "lastname"]) if all_cids else {}
    with dash_spinner(f"Linking {len(all_cids):,} contacts to Number objects…"):
        c2n = _assoc("contacts", NUM_OBJECT, all_cids) if all_cids else {}
    with dash_spinner("Checking direct ticket → number associations…"):
        t2n_direct = _assoc("tickets", NUM_OBJECT, tids)
    # numbers reached by each ticket = via its contacts ∪ direct
    t2n = {}
    for tid in tids:
        via_contact = {n for c in t2c.get(tid, []) for n in c2n.get(c, [])}
        t2n[tid] = sorted(via_contact | set(t2n_direct.get(tid, [])))

    all_nids = sorted({n for ns in t2n.values() for n in ns})
    num_of = {}
    if all_nids:
        with dash_spinner(f"Reading {len(all_nids):,} associated Number objects…"):
            num_of = _batch_read(NUM_OBJECT, all_nids, ["number", "number_status", "service_type"])
    # keep VRS numbers only — do NOT count Convo Now (or other) service types
    vrs_nid_set = {nid for nid, pr in num_of.items()
                   if (pr.get("service_type") or "").strip().lower() == "vrs"}

    rows = []
    for t in tks:
        tid = str(t["id"])
        p = t.get("properties", {})
        # emails from associated contacts
        emails = sorted({str(con_of.get(c, {}).get("email") or "").strip()
                         for c in t2c.get(tid, [])} - {""})
        # associated VRS numbers only (with status)
        _nids = [n for n in t2n.get(tid, []) if n in vrs_nid_set]
        nums = sorted({str(num_of.get(n, {}).get("number") or "").strip() for n in _nids} - {""})
        statuses = sorted({str(num_of.get(n, {}).get("number_status") or "").strip() for n in _nids} - {""})
        rows.append({
            "Record ID": tid,
            "Convo Products": (p.get("convo_products") or "—"),
            "Category": (p.get("hs_ticket_category") or "—"),
            "Subcategory": (p.get("subcategory") or "—"),
            "First Name": (p.get("first_name") or "—"),
            "Last Name": (p.get("last_name") or "—"),
            "All associated contact emails": ", ".join(emails) or "—",
            "Ticket description": (p.get("content") or "—"),
            "Number": ", ".join(nums) or "—",
            "Number Status": ", ".join(statuses) or "—",
            "Stage": _sl.get(p.get("hs_pipeline_stage"), p.get("hs_pipeline_stage") or "—"),
            "Created": (str(p.get("createdate") or "")[:10]),
            "_content_lc": (p.get("content") or "").lower(),
            "_has_number": bool(nums),
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "pipeline": pipe_label,
                       "subcats": sorted({r["Subcategory"] for r in rows if r["Subcategory"] != "—"})})

saved = load_report(_key)
if saved is None:
    st.info("Pick the pipeline and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · pipeline: **{saved.get('pipeline','')}**")
if df.empty:
    st.warning("No tickets."); report_header_close(); st.stop()


def _cards(items):
    cols = st.columns(len(items))
    for col, (t, v, s, c) in zip(cols, items):
        col.markdown(
            f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:14px;
                padding:16px 18px 13px;background:rgba(127,127,127,0.03);height:100%;">
                <div style="font-size:.70rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
                    color:#667085;">{t}</div>
                <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.05;margin:5px 0 3px;">{v}</div>
                <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""",
            unsafe_allow_html=True)


# ── Filters ─────────────────────────────────────────────────────────────────
st.markdown("##### Filters")
f1, f2 = st.columns([1.4, 1.6])
_subcats = saved.get("subcats", [])
_zoom_default = [s for s in _subcats if s.strip().lower() == "zoom"]
subpick = f1.multiselect("Subcategory", _subcats, default=_zoom_default)
kw_raw2 = f2.text_input("Ticket description contains any of (comma-separated)",
                        value="cfz, mobile, tablet", key="t2_kw2")
_kw = [k.strip().lower() for k in kw_raw2.split(",") if k.strip()]

view = df.copy()
if subpick:
    view = view[view["Subcategory"].isin(subpick)]
if _kw:
    view = view[view["_content_lc"].apply(lambda c: any(k in c for k in _kw))]

N = len(view)
hn = int(view["_has_number"].sum()) if "_has_number" in view.columns else 0
_pct = (lambda x: f"{x/N*100:.0f}% of tickets" if N else "—")
_cards([
    ("🎫 Tickets (filtered)", f"{N:,}", f"of {len(df):,} in pipeline", "#1A2234"),
    ("📞 Has VRS Number", f"{hn:,}", _pct(hn), "#0FB5AE"),
    ("🚫 No VRS number", f"{N-hn:,}", _pct(N-hn), "#E5484D"),
])
st.markdown("")

_cols = ["Record ID", "Convo Products", "Category", "Subcategory", "First Name", "Last Name",
         "All associated contact emails", "Ticket description", "Number", "Number Status"]
st.markdown("##### Tickets")
st.caption(f"{N:,} of {len(df):,}")
disp = view[_cols] if all(c in view.columns for c in _cols) else view
st.dataframe(disp.sort_values("Record ID"), use_container_width=True, hide_index=True, height=520)

_e1, _e2 = st.columns(2)
with _e1:
    st.download_button("📥 Export CSV", disp.to_csv(index=False), "t2_support.csv", "text/csv")
with _e2:
    _pdf_metrics = [
        ("Tickets (filtered)", f"{N:,}"),
        ("Has Number", f"{hn:,}"),
        ("No number", f"{N-hn:,}"),
    ]
    pdf_download_button(disp, "t2_support.pdf", "T2 Support",
                        subtitle=f"Pipeline: {saved.get('pipeline','')}",
                        metrics=_pdf_metrics, key="t2_pdf")

report_header_close()
