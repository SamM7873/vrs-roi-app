import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner, to_float, vrs_rate_for_month,
                   CONVO_NOW_RATE_PER_MINUTE,
                   save_report, load_report, saved_at_label, log_report_view, pdf_download_button)

st.set_page_config(page_title="Convo Greeting", layout="wide", page_icon="👋")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Greeting")

report_header("Convo Greeting",
              "Convo Greeting pipeline tickets — do they have a Contact / Number association?",
              section="Support")

NUM_OBJECT = "2-40974683"   # Number object
MV_OBJECT = "2-46246179"    # Monthly Values
_key = "convo_greeting_v14_cnrate"


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
    """Seek-paginated Monthly Values search (past the 10k cap)."""
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
# resolve the Convo Greeting pipeline by label
_cg_options = {v: k for k, v in _pl.items()}
_cg_default = next((v for v in _cg_options if "convo greeting" in v.lower()), None)
_labels = sorted(_cg_options.keys())

st.markdown("Pulls every ticket in the **Convo Greeting** pipeline, then follows "
            "**ticket → Contact → Number → Monthly Values**. Shows which tickets have a contact / "
            "number and the ROI (usage minutes × FCC rate) from those numbers.")

c1, c2 = st.columns([2, 1])
pipe_label = c1.selectbox("Ticket pipeline", _labels,
                          index=(_labels.index(_cg_default) if _cg_default in _labels else 0))
run = c2.button("▶ Run", type="primary", use_container_width=True)
c2.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)

if run:
    pid = _cg_options.get(pipe_label)
    _own = _owner_names()
    with dash_spinner(f"Reading “{pipe_label}” tickets…"):
        tks = _search_all("tickets",
                          ["subject", "createdate", "closed_date", "hs_pipeline_stage",
                           "hubspot_owner_id"],
                          [{"propertyName": "hs_pipeline", "operator": "EQ", "value": pid}])
    if not tks:
        st.warning("No tickets in that pipeline."); report_header_close(); st.stop()

    tids = [str(t["id"]) for t in tks]
    # 1) ticket → contact
    with dash_spinner(f"Linking {len(tids):,} tickets to contacts…"):
        t2c = _assoc("tickets", "contacts", tids)
    # 2) contact → number (the chain: ticket → contact → number)
    all_cids = sorted({c for cs in t2c.values() for c in cs})
    with dash_spinner(f"Linking {len(all_cids):,} contacts to Number objects…"):
        c2n = _assoc("contacts", NUM_OBJECT, all_cids)
    # also keep any direct ticket → number association (union, so nothing is missed)
    with dash_spinner("Checking direct ticket → number associations…"):
        t2n_direct = _assoc("tickets", NUM_OBJECT, tids)
    # numbers reached by each ticket = via its contacts ∪ direct
    t2n = {}
    for tid in tids:
        via_contact = {n for c in t2c.get(tid, []) for n in c2n.get(c, [])}
        t2n[tid] = sorted(via_contact | set(t2n_direct.get(tid, [])))

    # ── Number objects → Monthly Values → ROI ──────────────────────────────────
    all_nids = sorted({n for ns in t2n.values() for n in ns})
    num_of = {}
    if all_nids:
        with dash_spinner(f"Reading {len(all_nids):,} associated Number objects…"):
            num_of = _batch_read(NUM_OBJECT, all_nids, ["number", "service_type"])
    # Classify associated Number objects by service_type:
    #   vrs_nid_set → contains "vrs" (incl. combined "VRS and Convo Now")
    #   cn_nid_set  → contains "convo now" (incl. combined)
    # Both are needed so Convo Now Monthly Values (which live on Convo Now
    # Number objects) are pulled and attributed too.
    def _has(st_val, token):
        return token in str(st_val or "").lower()
    vrs_nid_set = {nid for nid, p in num_of.items() if _has(p.get("service_type"), "vrs")}
    cn_nid_set = {nid for nid, p in num_of.items() if _has(p.get("service_type"), "convo now")}
    relevant_nids = vrs_nid_set | cn_nid_set
    nid_to_num = {nid: str(num_of.get(nid, {}).get("number") or "").strip()
                  for nid in relevant_nids if str(num_of.get(nid, {}).get("number") or "").strip()}
    all_numbers = sorted(set(nid_to_num.values()))          # phones for the MV pull (VRS + CN)
    vrs_numbers = sorted({nid_to_num[n] for n in vrs_nid_set if n in nid_to_num})

    # each ticket's close month, its VRS + CN numbers, and the earliest close month per number
    tclose = {str(t["id"]): (str(t.get("properties", {}).get("closed_date") or "")[:7]) for t in tks}
    tid_vrs_nums, tid_cn_nums = {}, {}
    num_since = {}   # number → earliest close month among tickets owning it
    for tid in tids:
        vnums = sorted({nid_to_num.get(n, "") for n in t2n.get(tid, []) if n in vrs_nid_set} - {""})
        cnums = sorted({nid_to_num.get(n, "") for n in t2n.get(tid, []) if n in cn_nid_set} - {""})
        tid_vrs_nums[tid] = vnums
        tid_cn_nums[tid] = cnums
        cm = tclose.get(tid)
        if cm:
            for num in set(vnums) | set(cnums):
                if num not in num_since or cm < num_since[num]:
                    num_since[num] = cm

    # Collect Monthly Values rows from BOTH sources, deduped by MV object id:
    #   1) phone-number string match on the "number" property (VRS + CN numbers)
    #   2) the Number → Monthly Values CRM association (so nothing is missed)
    _mv_props = ["number", "usage_minutes", "service_type", "month_date"]
    mv_objs = {}          # mv_id → properties
    mvid_to_num = {}      # mv_id → phone from its Number object (fallback key)
    if all_numbers:
        with dash_spinner(f"Pulling Monthly Values for {len(all_numbers):,} numbers…"):
            for i in range(0, len(all_numbers), 100):
                chunk = all_numbers[i:i + 100]
                for o in _seek_mv(_mv_props,
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "usage_minutes", "operator": "GT", "value": "0"}]):
                    mv_objs[str(o["id"])] = o.get("properties", {})
    if relevant_nids:
        with dash_spinner(f"Checking Number → Monthly Values associations for {len(relevant_nids):,} numbers…"):
            n2mv = _assoc(NUM_OBJECT, MV_OBJECT, sorted(relevant_nids))
            _need = []
            for nid, mvids in n2mv.items():
                for mvid in mvids:
                    mvid = str(mvid)
                    mvid_to_num[mvid] = nid_to_num.get(nid, "")
                    if mvid not in mv_objs:
                        _need.append(mvid)
            _need = sorted(set(_need))
            if _need:
                for i in range(0, len(_need), 100):
                    got = _batch_read(MV_OBJECT, _need[i:i + 100], _mv_props)
                    mv_objs.update(got)

    # bucket the deduped rows, split by service (VRS drives FCC ROI)
    num_month_vrs = defaultdict(dict)   # number → {YYYY-MM: minutes}
    num_month_cn = defaultdict(dict)    # number → {YYYY-MM: minutes}
    for mvid, op in mv_objs.items():
        s = str(op.get("service_type") or "").lower()
        num = str(op.get("number") or "").strip() or mvid_to_num.get(mvid, "")
        mk = str(op.get("month_date") or "")[:7]
        mins = to_float(op.get("usage_minutes")) or 0.0
        if not (num and mk) or mins <= 0:
            continue
        # a row counts as VRS when its service_type mentions VRS (incl. the
        # combined "VRS and Convo Now"); otherwise, if it mentions Convo Now,
        # it's tracked in the Convo Now bucket.
        if "vrs" in s:
            num_month_vrs[num][mk] = num_month_vrs[num].get(mk, 0.0) + mins
        elif "convo now" in s:
            num_month_cn[num][mk] = num_month_cn[num].get(mk, 0.0) + mins

    # roll up usage from each number's earliest close month → present
    monthly = defaultdict(lambda: {"min": 0.0, "fcc": 0.0, "cn": 0.0, "cn_val": 0.0})
    for num, since in num_since.items():
        for mk, mins in num_month_vrs.get(num, {}).items():
            if mk >= since:
                monthly[mk]["min"] += mins
                monthly[mk]["fcc"] += mins * vrs_rate_for_month(mk)
        for mk, mins in num_month_cn.get(num, {}).items():
            if mk >= since:
                monthly[mk]["cn"] += mins
                monthly[mk]["cn_val"] += mins * CONVO_NOW_RATE_PER_MINUTE

    # every month present in Monthly Values (either service) → a column in the table
    all_months = sorted({mk for nm in num_month_vrs.values() for mk in nm}
                        | {mk for nm in num_month_cn.values() for mk in nm})
    rows = []
    for t in tks:
        tid = str(t["id"])
        p = t.get("properties", {})
        _nums = tid_vrs_nums.get(tid, [])
        _cnums = tid_cn_nums.get(tid, [])
        nc, nn = len(t2c.get(tid, [])), len(_nums)
        _cm = tclose.get(tid, "")
        # this ticket's usage per month, split by service: VRS from its VRS
        # numbers, Convo Now from its Convo Now numbers
        _by_v, _by_c = defaultdict(float), defaultdict(float)
        for x in _nums:
            for mk, m in num_month_vrs.get(x, {}).items():
                _by_v[mk] += m
        for x in _cnums:
            for mk, m in num_month_cn.get(x, {}).items():
                _by_c[mk] += m
        _tmin = round(sum(m for mk, m in _by_v.items() if _cm and mk >= _cm), 1)
        _tcn = round(sum(m for mk, m in _by_c.items() if _cm and mk >= _cm), 1)
        _row = {
            "Ticket ID": tid,
            "Subject": (p.get("subject") or "—"),
            "Stage": _sl.get(p.get("hs_pipeline_stage"), p.get("hs_pipeline_stage") or "—"),
            "Owner": _own.get(str(p.get("hubspot_owner_id") or ""), "—"),
            "Created": (str(p.get("createdate") or "")[:10]),
            "Closed": (str(p.get("closed_date") or "")[:10] or "—"),
            "Contacts": nc,
            "VRS Numbers": nn,
            "VRS Number(s)": ", ".join(_nums) or "—",
            "Convo Now Numbers": len(_cnums),
            "Convo Now Number(s)": ", ".join(_cnums) or "—",
            "VRS Min (since close)": _tmin,
            "Convo Now Min (since close)": _tcn,
            "Has Contact": "Yes" if nc else "No",
            "Has Number": "Yes" if nn else "No",
            "Association": ("Contact + Number" if nc and nn else
                            "Contact only" if nc else
                            "Number only" if nn else "None"),
        }
        # two columns per month: VRS and Convo Now minutes for this ticket's numbers
        for mk in all_months:
            _row[f"{mk} VRS"] = round(_by_v.get(mk, 0.0), 1)
            _row[f"{mk} CN"] = round(_by_c.get(mk, 0.0), 1)
        rows.append(_row)
    df = pd.DataFrame(rows)
    _mrows = [{"Month": mk, "VRS Minutes": round(v["min"], 1), "FCC $": round(v["fcc"], 2),
               "Convo Now Minutes": round(v["cn"], 1), "Convo Now $": round(v["cn_val"], 2)}
              for mk, v in sorted(monthly.items())]
    mv_df = pd.DataFrame(_mrows)

    # per-number monthly detail (each month's VRS + Convo Now value, from close month on)
    _drows = []
    for num, since in sorted(num_since.items()):
        _months = sorted(set(num_month_vrs.get(num, {})) | set(num_month_cn.get(num, {})))
        for mk in _months:
            vmin = num_month_vrs.get(num, {}).get(mk, 0.0)
            cmin = num_month_cn.get(num, {}).get(mk, 0.0)
            _drows.append({
                "VRS Number": num,
                "Month": mk,
                "VRS Minutes": round(vmin, 1),
                "FCC $": round(vmin * vrs_rate_for_month(mk), 2),
                "Convo Now Minutes": round(cmin, 1),
                "Convo Now $": round(cmin * CONVO_NOW_RATE_PER_MINUTE, 2),
                "Since close?": "Yes" if mk >= since else "No",
            })
    detail_df = pd.DataFrame(_drows)

    save_report(_key, {"df": df, "pipeline": pipe_label, "mv_df": mv_df, "detail_df": detail_df,
                       "tot_min": round(sum(v["min"] for v in monthly.values()), 1),
                       "tot_cn": round(sum(v["cn"] for v in monthly.values()), 1),
                       "tot_fcc": round(sum(v["fcc"] for v in monthly.values()), 2),
                       "tot_cn_val": round(sum(v["cn_val"] for v in monthly.values()), 2)})

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


N = len(df)
hc = int((df["Has Contact"] == "Yes").sum())
hn = int((df["Has Number"] == "Yes").sum())
both = int(((df["Has Contact"] == "Yes") & (df["Has Number"] == "Yes")).sum())
neither = int(((df["Has Contact"] == "No") & (df["Has Number"] == "No")).sum())


def _pct(x):
    return f"{x/N*100:.0f}% of tickets" if N else "—"


_cards([
    ("🎫 Tickets", f"{N:,}", "in this pipeline", "#1A2234"),
    ("👤 Has Contact", f"{hc:,}", _pct(hc), "#4C8DFF"),
    ("📞 Has Number", f"{hn:,}", _pct(hn), "#0FB5AE"),
    ("✅ Contact + Number", f"{both:,}", _pct(both), "#2DB84B"),
    ("🚫 No association", f"{neither:,}", _pct(neither), "#E5484D"),
])
st.markdown("")

# ── ROI from Monthly Values ─────────────────────────────────────────────────
_tot_min = saved.get("tot_min", 0.0)
_tot_cn = saved.get("tot_cn", 0.0)
_tot_fcc = saved.get("tot_fcc", 0.0)
_tot_cn_val = saved.get("tot_cn_val", 0.0)
_vmcol = "VRS Min (since close)"
_n_active = int((df.get(_vmcol, pd.Series(dtype=float)) > 0).sum()) if _vmcol in df.columns else 0
st.markdown("##### 💵 ROI from Monthly Values (closed date → present · associated numbers)")
_cards([
    ("⏱️ Total VRS minutes", f"{_tot_min:,.0f}", "from associated numbers", "#4C8DFF"),
    ("💵 FCC value (VRS)", f"${_tot_fcc:,.0f}", "VRS min × FCC rate", "#2DB84B"),
    ("📱 Total Convo Now minutes", f"{_tot_cn:,.0f}", "from Convo Now numbers", "#B4883F"),
    (f"💲 Convo Now value", f"${_tot_cn_val:,.0f}", f"CN min × ${CONVO_NOW_RATE_PER_MINUTE:.2f}/min", "#E8A33D"),
    ("Total value (VRS + CN)", f"${_tot_fcc + _tot_cn_val:,.0f}", "combined", "#7A5CFF"),
])
_mv = saved.get("mv_df")
if _mv is not None and not _mv.empty:
    st.markdown("###### Monthly trend")
    st.dataframe(_mv.sort_values("Month"), use_container_width=True, hide_index=True)

_det = saved.get("detail_df")
if _det is not None and not _det.empty:
    with st.expander(f"📅 Monthly values by number — each month ({len(_det):,} rows)", expanded=False):
        _dc1, _dc2 = st.columns([1.6, 1.2])
        _numsel = _dc1.multiselect("VRS Number", sorted(_det["VRS Number"].unique()), default=[])
        _sincesel = _dc2.selectbox("Rows", ["All months", "Only since close"], index=0)
        _dv = _det.copy()
        if _numsel:
            _dv = _dv[_dv["VRS Number"].isin(_numsel)]
        if _sincesel == "Only since close":
            _dv = _dv[_dv["Since close?"] == "Yes"]
        st.dataframe(_dv.sort_values(["VRS Number", "Month"]), use_container_width=True,
                     hide_index=True, height=420)
        st.download_button("📥 Export monthly detail (CSV)", _dv.to_csv(index=False),
                           "convo_greeting_monthly_detail.csv", "text/csv", key="cg_detail_csv")
        # optional wide pivots: months as columns, numbers as rows — one per service
        for _svc_col, _lbl in [("VRS Minutes", "VRS"), ("Convo Now Minutes", "Convo Now")]:
            try:
                if _svc_col in _det.columns and _det[_svc_col].sum() > 0:
                    _piv = _det.pivot_table(index="VRS Number", columns="Month",
                                            values=_svc_col, aggfunc="sum", fill_value=0).reset_index()
                    st.markdown(f"**Pivot — {_lbl} minutes by month**")
                    st.dataframe(_piv, use_container_width=True, hide_index=True)
            except Exception:
                pass
st.caption(f"VRS / Convo Now minutes = usage on the tickets' associated numbers from the ticket's **closed-date "
           f"month → present** (Monthly Values month_date ≥ closed date). FCC value = **VRS** minutes × the VRS FCC "
           f"rate. Convo Now value = **Convo Now** minutes × **${CONVO_NOW_RATE_PER_MINUTE:.2f}/min**.")
st.markdown("")

# breakdown by association type
st.markdown("##### By association")
b = (df.groupby("Association").size().reset_index(name="Tickets")
     .sort_values("Tickets", ascending=False))
b["%"] = (b["Tickets"] / N * 100).round(1).astype(str) + "%"
st.dataframe(b, use_container_width=True, hide_index=True)

# records
st.markdown("##### Tickets")
f1, f2, f3 = st.columns([1.2, 1.2, 2])
apick = f1.multiselect("Association", sorted(df["Association"].unique()), default=[])
spick = f2.multiselect("Stage", sorted(df["Stage"].unique()), default=[])
search = f3.text_input("Search subject / ID / owner").strip().lower()
view = df.copy()
if apick:
    view = view[view["Association"].isin(apick)]
if spick:
    view = view[view["Stage"].isin(spick)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view.sort_values("Created", ascending=False), use_container_width=True,
             hide_index=True, height=460)
_ex1, _ex2 = st.columns(2)
with _ex1:
    st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_greeting.csv", "text/csv")
with _ex2:
    _pdf_metrics = [
        ("Tickets", f"{N:,}"),
        ("Has Contact", f"{hc:,}"),
        ("Has Number", f"{hn:,}"),
        ("Total VRS minutes", f"{_tot_min:,.0f}"),
        ("FCC value (VRS)", f"${_tot_fcc:,.0f}"),
        ("Total Convo Now minutes", f"{_tot_cn:,.0f}"),
        ("Convo Now value", f"${_tot_cn_val:,.0f}"),
    ]
    _pdf_charts = []
    if _mv is not None and not _mv.empty:
        _pdf_charts = [{"data": _mv[["Month", "VRS Minutes"]], "kind": "bar",
                        "x": "Month", "y": "VRS Minutes", "title": "VRS minutes by month"}]
    pdf_download_button(view, "convo_greeting.pdf", "Convo Greeting",
                        subtitle=f"Pipeline: {saved.get('pipeline','')}",
                        metrics=_pdf_metrics, charts=_pdf_charts, key="cg_pdf")

report_header_close()
