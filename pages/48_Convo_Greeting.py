import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner, to_float, vrs_rate_for_month,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Greeting", layout="wide", page_icon="👋")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Greeting")

report_header("Convo Greeting",
              "Convo Greeting pipeline tickets — do they have a Contact / Number association?",
              section="Support")

NUM_OBJECT = "2-40974683"   # Number object
MV_OBJECT = "2-46246179"    # Monthly Values
_key = "convo_greeting_v1"


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

st.markdown("Pulls every ticket in the **Convo Greeting** pipeline and checks whether each ticket has "
            "an associated **Contact** and/or **Number** object.")

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
    with dash_spinner(f"Checking associations for {len(tids):,} tickets…"):
        t2c = _assoc("tickets", "contacts", tids)
        t2n = _assoc("tickets", NUM_OBJECT, tids)

    # ── Number objects → Monthly Values → ROI ──────────────────────────────────
    all_nids = sorted({n for ns in t2n.values() for n in ns})
    num_of = {}
    if all_nids:
        with dash_spinner(f"Reading {len(all_nids):,} associated Number objects…"):
            num_of = _batch_read(NUM_OBJECT, all_nids, ["number", "service_type"])
    nid_to_num = {nid: str(p.get("number") or "").strip() for nid, p in num_of.items()
                  if str(p.get("number") or "").strip()}
    vrs_numbers = sorted({v for nid, v in nid_to_num.items()
                          if (num_of.get(nid, {}).get("service_type") or "").strip().lower() == "vrs"})

    num_usage = defaultdict(float)          # number string → total VRS minutes (all months)
    monthly = defaultdict(lambda: {"min": 0.0, "fcc": 0.0})
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
                    mins = to_float(op.get("usage_minutes")) or 0.0
                    mk = (str(op.get("month_date") or "")[:7])   # YYYY-MM
                    num_usage[num] += mins
                    if mk:
                        monthly[mk]["min"] += mins
                        monthly[mk]["fcc"] += mins * vrs_rate_for_month(mk)

    rows = []
    for t in tks:
        tid = str(t["id"])
        p = t.get("properties", {})
        _nids = t2n.get(tid, [])
        nc, nn = len(t2c.get(tid, [])), len(_nids)
        _nums = sorted({nid_to_num.get(n, "") for n in _nids} - {""})
        _tmin = round(sum(num_usage.get(x, 0.0) for x in _nums), 1)
        rows.append({
            "Ticket ID": tid,
            "Subject": (p.get("subject") or "—"),
            "Stage": _sl.get(p.get("hs_pipeline_stage"), p.get("hs_pipeline_stage") or "—"),
            "Owner": _own.get(str(p.get("hubspot_owner_id") or ""), "—"),
            "Created": (str(p.get("createdate") or "")[:10]),
            "Contacts": nc,
            "Numbers": nn,
            "VRS Number(s)": ", ".join(_nums) or "—",
            "VRS Min": _tmin,
            "Has Contact": "Yes" if nc else "No",
            "Has Number": "Yes" if nn else "No",
            "Association": ("Contact + Number" if nc and nn else
                            "Contact only" if nc else
                            "Number only" if nn else "None"),
        })
    df = pd.DataFrame(rows)
    _mrows = [{"Month": mk, "VRS Minutes": round(v["min"], 1), "FCC $": round(v["fcc"], 2)}
              for mk, v in sorted(monthly.items())]
    mv_df = pd.DataFrame(_mrows)
    save_report(_key, {"df": df, "pipeline": pipe_label, "mv_df": mv_df,
                       "tot_min": round(sum(v["min"] for v in monthly.values()), 1),
                       "tot_fcc": round(sum(v["fcc"] for v in monthly.values()), 2)})

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
_tot_fcc = saved.get("tot_fcc", 0.0)
_n_active = int((df.get("VRS Min", pd.Series(dtype=float)) > 0).sum()) if "VRS Min" in df.columns else 0
st.markdown("##### 💵 ROI from Monthly Values (associated VRS numbers)")
_cards([
    ("⏱️ Total VRS minutes", f"{_tot_min:,.0f}", "from associated numbers", "#4C8DFF"),
    ("💵 FCC value", f"${_tot_fcc:,.0f}", "minutes × FCC rate", "#2DB84B"),
    ("🚀 Tickets generating usage", f"{_n_active:,}", _pct(_n_active), "#0FB5AE"),
    ("Avg $ / ticket w/ number", f"${_tot_fcc/hn:,.0f}" if hn else "—", "over tickets with a number", "#7A5CFF"),
])
_mv = saved.get("mv_df")
if _mv is not None and not _mv.empty:
    st.markdown("###### Monthly trend")
    st.dataframe(_mv.sort_values("Month"), use_container_width=True, hide_index=True)
st.caption("VRS minutes = usage on the tickets' associated VRS numbers (Monthly Values). "
           "FCC value = minutes × the VRS FCC rate for each month.")
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
st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_greeting.csv", "text/csv")

report_header_close()
