import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Greeting", layout="wide", page_icon="👋")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Greeting")

report_header("Convo Greeting",
              "Convo Greeting pipeline tickets — do they have a Contact / Number association?",
              section="Support")

NUM_OBJECT = "2-40974683"   # Number object
_key = "convo_greeting_v1"


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

    rows = []
    for t in tks:
        tid = str(t["id"])
        p = t.get("properties", {})
        nc, nn = len(t2c.get(tid, [])), len(t2n.get(tid, []))
        rows.append({
            "Ticket ID": tid,
            "Subject": (p.get("subject") or "—"),
            "Stage": _sl.get(p.get("hs_pipeline_stage"), p.get("hs_pipeline_stage") or "—"),
            "Owner": _own.get(str(p.get("hubspot_owner_id") or ""), "—"),
            "Created": (str(p.get("createdate") or "")[:10]),
            "Contacts": nc,
            "Numbers": nn,
            "Has Contact": "Yes" if nc else "No",
            "Has Number": "Yes" if nn else "No",
            "Association": ("Contact + Number" if nc and nn else
                            "Contact only" if nc else
                            "Number only" if nn else "None"),
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "pipeline": pipe_label})

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
