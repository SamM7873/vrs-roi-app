import streamlit as st
import pandas as pd
import requests
from datetime import date, datetime, timezone
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Ticket Pipelines (T1/T2/VRS Reg)", layout="wide", page_icon="🎟️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Ticket Pipelines")

report_header("Ticket Pipelines — T1 / T2 / VRS Registration",
              "Live pull of HubSpot tickets in the T1, T2 and VRS Registration pipelines",
              section="Support")

CACHE_VERSION = 1
_key = f"ticket_pipelines_v{CACHE_VERSION}"
# match a pipeline by label — a pipeline counts if any of these tokens is in its label
WANTED = {
    "T1": ["t1", "tier 1", "tier1"],
    "T2": ["t2", "tier 2", "tier2"],
    "VRS Registration": ["vrs registration", "vrs reg"],
}


def _dt(v):
    if not v:
        return ""
    try:
        s = str(v)
        d = (datetime.utcfromtimestamp(int(s) / 1000) if s.isdigit()
             else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return d.strftime("%b %d, %Y")
    except Exception:
        return str(v)[:10]


def _resolve_pipelines():
    """Return {our_label: {'id':.., 'stages':{sid:slabel}}} for T1/T2/VRS Reg."""
    r = requests.get(f"{_B}/crm/v3/pipelines/tickets", headers=_H, timeout=15)
    r.raise_for_status()
    found, all_labels = {}, []
    for pl in r.json().get("results", []):
        label = (pl.get("label") or "").strip()
        all_labels.append(label)
        low = label.lower()
        for our, tokens in WANTED.items():
            if our in found:
                continue
            if any(t in low for t in tokens):
                found[our] = {"id": pl["id"], "label": label,
                              "stages": {s["id"]: s.get("label", s["id"]) for s in pl.get("stages", [])}}
    return found, all_labels


run = st.button("Run pipeline ticket pull", type="primary")

if run:
    try:
        with dash_spinner("Reading ticket pipelines…"):
            pipes, all_labels = _resolve_pipelines()
    except Exception as e:
        st.error(f"Failed to load pipelines: {e}")
        report_header_close(); st.stop()

    if not pipes:
        st.warning("None of T1 / T2 / VRS Registration matched a ticket pipeline. "
                   "Pipelines found: " + ", ".join(all_labels))
        report_header_close(); st.stop()

    rows = []
    stage_of = {}
    for our, meta in pipes.items():
        stage_of.update(meta["stages"])
    for our, meta in pipes.items():
        with dash_spinner(f"Pulling {our} tickets…"):
            recs = fetch_all(
                "tickets",
                ["hs_object_id", "subject", "hs_pipeline", "hs_pipeline_stage",
                 "hs_ticket_priority", "createdate", "hs_lastmodifieddate",
                 "closed_date", "hubspot_owner_id"],
                filter_groups=[{"filters": [
                    {"propertyName": "hs_pipeline", "operator": "EQ", "value": meta["id"]}]}])
        for r in recs:
            p = r.get("properties", {})
            sid = p.get("hs_pipeline_stage") or ""
            slabel = meta["stages"].get(sid, stage_of.get(sid, sid))
            closed = str(p.get("closed_date") or "").strip()
            rows.append({
                "Pipeline": our,
                "Ticket ID": str(p.get("hs_object_id") or ""),
                "Subject": (p.get("subject") or "").strip() or "—",
                "Stage": slabel,
                "Priority": (p.get("hs_ticket_priority") or "").strip() or "—",
                "Status": "Closed" if closed else "Open",
                "Created": _dt(p.get("createdate")),
                "Closed": _dt(p.get("closed_date")),
                "_created_raw": p.get("createdate") or "",
            })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "pipes": {k: v["label"] for k, v in pipes.items()}})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run pipeline ticket pull** to load T1 / T2 / VRS Registration tickets.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · matched pipelines: "
               + " · ".join(f"{k} → “{v}”" for k, v in (saved.get("pipes") or {}).items()))

if df.empty:
    st.warning("No tickets found in the matched pipelines.")
    report_header_close(); st.stop()

# ── KPIs ────────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("🎟️ Total tickets", f"{len(df):,}")
k[1].metric("Open", f"{int((df['Status'] == 'Open').sum()):,}")
k[2].metric("Closed", f"{int((df['Status'] == 'Closed').sum()):,}")
k[3].metric("Pipelines", f"{df['Pipeline'].nunique():,}")

# ── by pipeline ─────────────────────────────────────────────────────────────────
st.markdown("##### By pipeline")
bypipe = (df.groupby("Pipeline")
          .agg(Tickets=("Ticket ID", "size"),
               Open=("Status", lambda s: int((s == "Open").sum())),
               Closed=("Status", lambda s: int((s == "Closed").sum())))
          .reset_index().sort_values("Tickets", ascending=False))
st.dataframe(bypipe, use_container_width=True, hide_index=True)

# ── by stage ────────────────────────────────────────────────────────────────────
st.markdown("##### By pipeline & stage")
bystage = (df.groupby(["Pipeline", "Stage"]).size().rename("Tickets").reset_index()
           .sort_values(["Pipeline", "Tickets"], ascending=[True, False]))
st.dataframe(bystage, use_container_width=True, hide_index=True)

# ── filters + list ──────────────────────────────────────────────────────────────
st.markdown("##### Tickets")
f1, f2, f3 = st.columns([1.3, 1.3, 2])
pp = f1.selectbox("Pipeline", ["All"] + sorted(df["Pipeline"].unique().tolist()))
ss = f2.selectbox("Status", ["All", "Open", "Closed"])
search = f3.text_input("Search subject / ticket ID").strip().lower()

view = df.copy()
if pp != "All":
    view = view[view["Pipeline"] == pp]
if ss != "All":
    view = view[view["Status"] == ss]
if search:
    view = view[view["Subject"].str.contains(search, case=False, na=False)
                | view["Ticket ID"].str.contains(search, case=False, na=False)]
view = view.sort_values("_created_raw", ascending=False)
st.caption(f"{len(view):,} tickets")
st.dataframe(view[["Pipeline", "Ticket ID", "Subject", "Stage", "Priority", "Status",
                   "Created", "Closed"]],
             use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.drop(columns="_created_raw").to_csv(index=False),
                   "ticket_pipelines.csv", "text/csv")

report_header_close()
