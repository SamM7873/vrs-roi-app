import streamlit as st
import pandas as pd
import time
import requests
from utils import (require_auth, is_app_admin, COMMON_CSS, report_header,
                   report_header_close, log_report_view, save_report, load_report,
                   saved_at_label, fetch_all, dash_spinner,
                   headers as _H, BASE_URL as _B)

# match ticket pipelines by label token
PIPE_TOKENS = {"T1": ["t1", "tier 1", "tier1"], "T2": ["t2", "tier 2", "tier2"],
               "VRS Registration": ["vrs registration", "vrs reg"]}


def _pipeline_map():
    """{hubspot_pipeline_id: friendly label} for T1/T2/VRS Reg (others -> real label)."""
    out = {}
    try:
        r = requests.get(f"{_B}/crm/v3/pipelines/tickets", headers=_H, timeout=15)
        if r.status_code == 200:
            for pl in r.json().get("results", []):
                label = (pl.get("label") or "").strip()
                low = label.lower()
                friendly = next((k for k, toks in PIPE_TOKENS.items() if any(t in low for t in toks)),
                                label or pl["id"])
                out[pl["id"]] = friendly
    except Exception:
        pass
    return out

st.set_page_config(page_title="Interactions vs Tickets", layout="wide", page_icon="🔀")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Interactions vs Tickets")

report_header("Interactions vs Tickets",
              "Reconcile Convo360 incoming interactions against tickets created",
              section="Analytics")

if not is_app_admin():
    st.warning("This page is restricted to administrators.")
    report_header_close(); st.stop()

SAVE_KEY = "interactions_vs_tickets"
RETENTION_OPTS = {"24 hours": 24, "48 hours": 48, "72 hours": 72, "7 days": 168}

st.markdown(
    "Upload **both** exports to compare **incoming interactions** (Convo360 — videophone, "
    "videochat, chat) against **tickets created** (HubSpot audit log). The goal: several "
    "contacts from the same consumer should roll up into **one ticket**, not one ticket per "
    "call. A high interactions-per-ticket ratio = good consolidation; near 1:1 = over-ticketing.")

st.info("These two exports share no consumer key, so matching is at the **day** level "
        "(and agent, best-effort) — not per individual consumer.", icon="ℹ️")

with st.expander("📖 Definitions & how the formulas work"):
    st.markdown("""
**Sources (Convo360 interaction types → friendly labels)**
- **Videophone** = `SIP_VIDEO_CALL` · **Videochat** = `VIDEO_CHAT` · **Chat** = `CHAT`
  · **Call** = audio/voice · **Text query** = `QUERY`. Pick which count as an interaction above.

**Core counts (over the overlapping date window of the two files)**
| Metric | Formula |
|---|---|
| Incoming interactions | count of Convo360 rows in the selected sources |
| Tickets created | count of audit rows where `Subcategory = Ticket` **and** `Action = Create` |
| Unique tickets touched | distinct `Target object id` on any ticket audit row |
| **Interactions per ticket** | incoming interactions ÷ tickets created |

**Reading interactions-per-ticket**
- **≥ 2** ✅ good consolidation — several contacts roll into one ticket.
- **1.3–2** 🟡 moderate · **1.0–1.3** 🟡 ~one ticket per contact.
- **< 1** 🔴 *more tickets than interactions* — either agents open multiple tickets per
  contact, **or** tickets come from channels not in the Convo360 export (email, manual, follow-ups).

**Work hours & time per ticket (from ticket-event timestamps)**

*Example:* an agent works 9:00–12:00 (3h), makes **60** changes across **20** tickets, **5** of them new.

| Metric | Plain meaning | Formula | Example |
|---|---|---|---|
| **Ticket events** | Every action on a ticket — each create, edit, status change, note, merge. One ticket = many events. | count of ticket rows | 60 |
| **Tickets created** | Brand-new tickets opened. | rows where `Action = Create` | 5 |
| **Tickets touched** | Distinct tickets worked on (created or edited), counted once each. | unique `Target object id` | 20 |
| **Work hours** | Span from first to last ticket action that day. | last − first event time | 3h |
| **Time/ticket** | Avg time per distinct ticket — "how long does a ticket take?" | work time ÷ tickets touched | 9m 00s |
| **Time/event** | Avg time per single action. | work time ÷ ticket events | 3m 00s |
| **Tickets/hour** | Throughput. | tickets touched ÷ work hours | 6.7 |

*Events count busy-ness (actions); tickets touched count workload breadth (distinct tickets).
One ticket taking 8 edits = 8 events but 1 ticket touched.*

Work hours is a **span proxy** — breaks inside the window count, and work outside HubSpot
(calls, email) isn't included.

**Per-consumer match (live)** pulls each created ticket's subject/description from HubSpot and
matches the Convo360 **Customer Name** by text — a guide, not an exact audit (common names can
over-match; tickets that don't name the consumer won't match).
""")

c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    up_conv = st.file_uploader("Convo360 interaction CSV", type=["csv"], key="ivt_conv")
with c2:
    up_tick = st.file_uploader("HubSpot ticket audit CSV", type=["csv"], key="ivt_tick")
with c3:
    _ret_label = st.selectbox("Keep report for", list(RETENTION_OPTS.keys()), index=1, key="ivt_ret",
                              help="How long the uploaded comparison stays saved before it expires.")
TTL = RETENTION_OPTS[_ret_label] * 3600
run = st.button("▶ Run comparison", type="primary", disabled=(up_conv is None or up_tick is None))


# friendly channel labels from Convo360 raw type values
SOURCE_LABEL = {
    "SIP_VIDEO_CALL": "Videophone",
    "SIP_VIDEO": "Videophone",
    "VIDEO_CHAT": "Videochat",
    "SIP_AUDIO_CALL": "Call",
    "SIP_VOICE_CALL": "Call",
    "CALL": "Call",
    "CHAT": "Chat",
    "QUERY": "Text query",
}


def _source(v):
    key = str(v).strip().upper()
    if key in SOURCE_LABEL:
        return SOURCE_LABEL[key]
    if "VIDEO" in key:
        return "Videophone"
    if "CHAT" in key:
        return "Chat"
    return str(v).strip() or "—"


def _find(cols, *names):
    low = {c.lower(): c for c in cols}
    for n in names:
        for lc, orig in low.items():
            if n in lc:
                return orig
    return None


def _parse_conv(file):
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    dcol = _find(df.columns, "date")
    tcol = _find(df.columns, "type")
    acol = _find(df.columns, "agent", "rep")
    if not dcol:
        return "Convo360: no date column found."
    ccol = _find(df.columns, "customer name", "customer", "name")
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    df = df[df["_day"].notna()].copy()
    df["_type"] = df[tcol].str.strip() if tcol else "—"
    df["_source"] = df["_type"].map(_source)

    def _agent_lbl(v):
        s = str(v).strip()
        return "Missed (no agent)" if (not s or s.lower() in
                                       ("nan", "none", "null", "-", "—", "unassigned", "n/a", "na")) else s
    df["_agent"] = df[acol].map(_agent_lbl) if acol else "—"
    df["_customer"] = df[ccol].str.strip() if ccol else ""
    return df[["_day", "_type", "_source", "_agent", "_customer"]]


def _parse_tick(file):
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    ucol = _find(df.columns, "modified by", "user", "performed")
    dcol = _find(df.columns, "date")
    if not dcol:
        return "Ticket audit: no date column found."
    for c in ("Subcategory", "Action", "Target object id"):
        if c not in df.columns:
            df[c] = ""
    df["_t"] = pd.to_datetime(df[dcol], errors="coerce")
    df["_day"] = df["_t"].dt.date
    df = df[(df["Subcategory"] == "Ticket") & df["_day"].notna()].copy()
    df["_agent"] = df[ucol].str.split("@").str[0].str.strip() if ucol else "—"
    df["_created"] = df["Action"] == "Create"
    return df[["_t", "_day", "_agent", "Action", "_created", "Target object id"]]


if run and up_conv is not None and up_tick is not None:
    cv, tk = _parse_conv(up_conv), _parse_tick(up_tick)
    if isinstance(cv, str):
        st.error(cv); report_header_close(); st.stop()
    if isinstance(tk, str):
        st.error(tk); report_header_close(); st.stop()
    save_report(SAVE_KEY, {"cv": cv, "tk": tk})
    saved = load_report(SAVE_KEY)
else:
    saved = load_report(SAVE_KEY)
    if saved is None:
        st.info("Upload both CSVs, then click **▶ Run comparison**.")
        report_header_close(); st.stop()
    if time.time() - (saved.get("saved_at") or 0) > TTL:
        st.warning(f"Saved comparison is older than {_ret_label} — re-upload and Run.")
        report_header_close(); st.stop()
    cv, tk = saved["cv"], saved["tk"]

if saved and saved.get("saved_at"):
    _rem = max(0, TTL - (time.time() - saved["saved_at"]))
    _left = f"~{int(_rem // 3600)}h left" if _rem >= 3600 else f"~{int(_rem // 60)}m left"
    st.caption(f"📌 Saved {saved_at_label(saved)} · kept for {_ret_label} ({_left}) · "
               f"upload new CSVs + Run to refresh.")

# ── interaction source filter ───────────────────────────────────────────────────
sources = sorted(cv["_source"].unique())
pick = st.multiselect("Sources to count (Videophone / Videochat / Chat / …)", sources,
                      default=sources,
                      help="Choose which Convo360 sources count as an 'incoming interaction'.")
cvf = cv[cv["_source"].isin(pick)] if pick else cv

# the overlapping window of the two files
ov_lo = max(cvf["_day"].min(), tk["_day"].min())
ov_hi = min(cvf["_day"].max(), tk["_day"].max())

# ── date-range preset ───────────────────────────────────────────────────────────
from datetime import date as _date, timedelta as _td
today = _date.today()
preset = st.selectbox("Date range", ["All (overlap)", "Today", "Yesterday", "This week",
                                     "Last week", "This month", "Last month", "Custom"],
                      key="ivt_preset")
if preset == "Today":
    d_lo = d_hi = today
elif preset == "Yesterday":
    d_lo = d_hi = today - _td(days=1)
elif preset == "This week":
    d_lo, d_hi = today - _td(days=today.weekday()), today
elif preset == "Last week":
    _ws = today - _td(days=today.weekday() + 7); d_lo, d_hi = _ws, _ws + _td(days=6)
elif preset == "This month":
    d_lo, d_hi = today.replace(day=1), today
elif preset == "Last month":
    _fm = today.replace(day=1); _lm = _fm - _td(days=1); d_lo, d_hi = _lm.replace(day=1), _lm
elif preset == "Custom":
    _r = st.date_input("Custom range", value=(ov_lo, ov_hi), min_value=ov_lo, max_value=ov_hi,
                       key="ivt_custom")
    d_lo, d_hi = (_r if isinstance(_r, tuple) and len(_r) == 2 else (ov_lo, ov_hi))
else:
    d_lo, d_hi = ov_lo, ov_hi
# clamp to where data actually exists
d_lo, d_hi = max(d_lo, ov_lo), min(d_hi, ov_hi)
st.caption(f"Window: **{d_lo:%b %d}–{d_hi:%b %d, %Y}** (data overlap {ov_lo:%b %d}–{ov_hi:%b %d, %Y}).")
cvf = cvf[(cvf["_day"] >= d_lo) & (cvf["_day"] <= d_hi)]
tkf = tk[(tk["_day"] >= d_lo) & (tk["_day"] <= d_hi)]
if cvf.empty and tkf.empty:
    st.warning("No data in the selected date range.")
    report_header_close(); st.stop()

n_int = len(cvf)
n_created = int(tkf["_created"].sum())
n_touched = tkf["Target object id"].replace("", pd.NA).nunique()
ratio = (n_int / n_created) if n_created else None

# ── KPIs ────────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("📞 Incoming interactions", f"{n_int:,}")
k[1].metric("🎫 Tickets created", f"{n_created:,}")
k[2].metric("Unique tickets touched", f"{n_touched:,}")
k[3].metric("Interactions per ticket", f"{ratio:.1f}" if ratio else "—",
            help="Interactions ÷ tickets created. Higher = better consolidation; ~1 = one ticket per contact.")

if ratio is not None:
    if ratio >= 2:
        st.success(f"✅ ~{ratio:.1f} interactions per ticket — good consolidation "
                   "(multiple contacts roll into fewer tickets).")
    elif ratio >= 1.3:
        st.warning(f"🟡 ~{ratio:.1f} interactions per ticket — moderate consolidation.")
    elif ratio >= 1.0:
        st.warning(f"🟡 ~{ratio:.1f} interactions per ticket — roughly one ticket per contact "
                   "(little consolidation).")
    else:
        st.error(f"🔴 ~{ratio:.1f} interactions per ticket — **more tickets than incoming "
                 f"interactions** ({n_created:,} tickets vs {n_int:,} interactions). Either agents "
                 "open multiple tickets per contact, or many tickets come from **other channels** "
                 "(email, chat, proactive outreach) not in the Convo360 export.")
st.caption("⚠️ Tickets can originate from channels beyond Convo360 (email, manual, follow-ups), "
           "so a ratio below 1 isn't proof of over-ticketing on its own — it's a flag to investigate.")

# ── interaction source breakdown ────────────────────────────────────────────────
st.markdown("##### Incoming interactions by source")
bytype = cvf["_source"].value_counts().rename_axis("Source").reset_index(name="Interactions")
bytype["%"] = (bytype["Interactions"] / len(cvf) * 100).round(1) if len(cvf) else 0
st.dataframe(bytype, use_container_width=True, hide_index=True)

# ── reconciliation: daily / weekly / monthly ────────────────────────────────────
st.markdown("##### Interactions vs tickets created — over time")
grain = st.radio("Grain", ["Daily", "Weekly", "Monthly"], horizontal=True, key="ivt_grain")


def _period(series_day, g):
    d = pd.to_datetime(series_day)
    if g == "Weekly":
        # week starting Monday
        return d.dt.to_period("W-SUN").apply(lambda p: p.start_time.date().isoformat())
    if g == "Monthly":
        return d.dt.to_period("M").astype(str)
    return d.dt.date.astype(str)


cvf = cvf.copy(); cvf["_p"] = _period(cvf["_day"], grain)
tkc = tkf[tkf["_created"]].copy(); tkc["_p"] = _period(tkc["_day"], grain)
di = cvf.groupby("_p").size().rename("Interactions")
dc = tkc.groupby("_p").size().rename("Tickets created")
daily = pd.concat([di, dc], axis=1).fillna(0).astype(int).reset_index().rename(columns={"_p": "Period"})
daily = daily.sort_values("Period")
daily["Interactions/ticket"] = daily.apply(
    lambda r: round(r["Interactions"] / r["Tickets created"], 1) if r["Tickets created"] else 0, axis=1)
st.dataframe(daily, use_container_width=True, hide_index=True)
st.bar_chart(daily.set_index("Period")[["Interactions", "Tickets created"]], height=260)
st.caption("Weekly = week starting Monday · Monthly = calendar month. "
           "Interactions/ticket = interactions ÷ tickets created that period.")

# ── tickets created — pipeline & detail (live HubSpot) ──────────────────────────
st.markdown("##### 🎟️ Tickets created — pipeline & detail (live)")
st.caption("The audit CSV has ticket IDs but no pipeline/subject — click to enrich them from "
           "HubSpot and see which pipeline (T1 / T2 / VRS Registration) each created ticket is in.")
if st.button("🔗 Load ticket pipelines & subjects", key="ivt_pipe_btn"):
    ids = sorted(tkf[tkf["_created"]]["Target object id"].replace("", pd.NA).dropna().unique().tolist())
    pipe_map = _pipeline_map()
    trows = []
    with dash_spinner(f"Fetching {len(ids):,} created tickets…"):
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            for rec in fetch_all("tickets",
                                 ["hs_object_id", "subject", "hs_pipeline", "hs_pipeline_stage",
                                  "hs_ticket_priority", "createdate", "hs_object_source_label",
                                  "hs_object_source", "hs_created_by_user_id"],
                                 filter_groups=[{"filters": [
                                     {"propertyName": "hs_object_id", "operator": "IN", "values": chunk}]}]):
                p = rec.get("properties", {})
                cd = str(p.get("createdate") or "")
                try:
                    cd = (pd.to_datetime(int(cd), unit="ms") if cd.isdigit()
                          else pd.to_datetime(cd)).strftime("%b %d, %Y")
                except Exception:
                    cd = cd[:10]
                # Manual = a real user created it (has hs_created_by_user_id and a UI-ish source).
                src = (p.get("hs_object_source_label") or p.get("hs_object_source") or "").strip()
                src_u = src.upper()
                has_user = bool(str(p.get("hs_created_by_user_id") or "").strip())
                auto_srcs = ("FORM", "WORKFLOW", "AUTOMATION", "INTEGRATION", "API",
                             "IMPORT", "EMAIL", "BOT", "MOBILE_MESSAGING", "CONVERSATIONS")
                if any(a in src_u for a in auto_srcs):
                    origin = "Automatic"
                elif has_user or "CRM_UI" in src_u or "SALES_UI" in src_u or "UI" in src_u:
                    origin = "Manual"
                else:
                    origin = "Automatic"
                trows.append({
                    "Ticket ID": str(p.get("hs_object_id") or ""),
                    "Pipeline": pipe_map.get(p.get("hs_pipeline"), "Other"),
                    "Origin": origin,
                    "Source": src or "—",
                    "Subject": (p.get("subject") or "").strip() or "—",
                    "Priority": (p.get("hs_ticket_priority") or "").strip() or "—",
                    "Created": cd,
                })
    if not trows:
        st.info("No created tickets could be enriched.")
    else:
        tdf = pd.DataFrame(trows)
        st.session_state["ivt_tickets_df"] = tdf
if "ivt_tickets_df" in st.session_state:
    tdf = st.session_state["ivt_tickets_df"]
    cca, ccb = st.columns(2)
    with cca:
        st.markdown("**By pipeline**")
        pv = tdf["Pipeline"].value_counts().rename_axis("Pipeline").reset_index(name="Tickets")
        pv["%"] = (pv["Tickets"] / len(tdf) * 100).round(1)
        st.dataframe(pv, use_container_width=True, hide_index=True)
    with ccb:
        st.markdown("**Manual vs Automatic**")
        ov = tdf["Origin"].value_counts().rename_axis("Origin").reset_index(name="Tickets")
        ov["%"] = (ov["Tickets"] / len(tdf) * 100).round(1)
        st.dataframe(ov, use_container_width=True, hide_index=True)
    st.caption("**Manual** = created by a person in HubSpot. **Automatic** = created by a form, "
               "workflow, integration, API, email, or bot (from the ticket's source field).")
    fc1, fc2 = st.columns(2)
    _pp = fc1.selectbox("Filter by pipeline", ["All"] + pv["Pipeline"].tolist(), key="ivt_pp")
    _oo = fc2.selectbox("Filter by origin", ["All", "Manual", "Automatic"], key="ivt_oo")
    _tv = tdf.copy()
    if _pp != "All":
        _tv = _tv[_tv["Pipeline"] == _pp]
    if _oo != "All":
        _tv = _tv[_tv["Origin"] == _oo]
    st.dataframe(_tv[["Ticket ID", "Pipeline", "Origin", "Source", "Subject", "Priority", "Created"]]
                 .sort_values(["Origin", "Pipeline"]),
                 use_container_width=True, hide_index=True, height=380)
    st.download_button("📥 Export tickets (CSV)", tdf.to_csv(index=False),
                       "created_tickets_by_pipeline.csv", "text/csv", key="ivt_pipe_csv")

    # who handled manual vs automatic tickets
    st.markdown("**Who handled Manual vs Automatic tickets**")
    _id2org = dict(zip(tdf["Ticket ID"].astype(str), tdf["Origin"]))
    _ev = tkf[tkf["Target object id"] != ""].copy()
    _ev["Origin"] = _ev["Target object id"].map(lambda i: _id2org.get(str(i), "Older / not in window"))
    handled = (_ev.groupby(["_agent", "Origin"])["Target object id"].nunique()
               .unstack(fill_value=0).reset_index().rename(columns={"_agent": "Agent"}))
    for col in ("Manual", "Automatic", "Older / not in window"):
        if col not in handled.columns:
            handled[col] = 0
    handled["Total handled"] = handled[["Manual", "Automatic", "Older / not in window"]].sum(axis=1)
    handled = handled.sort_values("Total handled", ascending=False)
    order = ["Agent", "Total handled", "Manual", "Automatic", "Older / not in window"]
    st.dataframe(handled[order], use_container_width=True, hide_index=True)
    st.caption("Distinct tickets each agent **touched**, split by how the ticket was created. "
               "**Automatic** tickets have no creator in the audit log, so this shows who *works* "
               "them. 'Older / not in window' = ticket created before the export period.")
    st.download_button("📥 Export handled-by-origin (CSV)", handled[order].to_csv(index=False),
                       "handled_by_origin.csv", "text/csv", key="ivt_hbo_csv")

# ── new vs catch-up tickets handled per day ─────────────────────────────────────
st.markdown("##### 🆕 Tickets handled per day — new vs catch-up")
st.caption("Of the tickets an agent **touches** in a day: **New** = created that same day · "
           "**Catch-up** = created earlier (a reminder / follow-up / older ticket worked today).")
_tt = tkf[tkf["Target object id"] != ""].copy()
# optional source filter — needs the live ticket enrichment (Pipeline / Origin) run above
_enr = st.session_state.get("ivt_tickets_df")
if _enr is not None and not _tt.empty:
    id2pipe = dict(zip(_enr["Ticket ID"].astype(str), _enr["Pipeline"]))
    id2org = dict(zip(_enr["Ticket ID"].astype(str), _enr["Origin"]))
    sf1, sf2 = st.columns(2)
    _pipes = sorted(set(id2pipe.values()))
    _sp = sf1.multiselect("Pipeline (source)", _pipes, default=_pipes, key="ivt_nvc_pipe")
    _so = sf2.multiselect("Origin", ["Manual", "Automatic"], default=["Manual", "Automatic"],
                          key="ivt_nvc_org")
    _tt = _tt[_tt["Target object id"].map(
        lambda i: id2pipe.get(str(i), "Other") in _sp and id2org.get(str(i), "Automatic") in _so)]
    st.caption(f"Filtered by ticket source · {_tt['Target object id'].nunique():,} tickets match.")
else:
    st.caption("💡 Run **🔗 Load ticket pipelines & subjects** above to enable a "
               "Pipeline / Manual-vs-Automatic source filter here.")
if _tt.empty:
    st.info("No ticket activity in range.")
else:
    # earliest create day per ticket (within the loaded data); missing = created before the window
    _cre = _tt[_tt["_created"]].groupby("Target object id")["_day"].min()
    _touch = _tt.groupby(["_day", "Target object id"]).size().reset_index(name="_n")
    _touch["_create_day"] = _touch["Target object id"].map(_cre)
    _touch["Kind"] = _touch.apply(
        lambda r: "New" if (pd.notna(r["_create_day"]) and r["_create_day"] == r["_day"])
        else "Catch-up", axis=1)
    perday = (_touch.groupby(["_day", "Kind"]).size().unstack(fill_value=0)
              .reset_index().rename(columns={"_day": "Day"}))
    for col in ("New", "Catch-up"):
        if col not in perday.columns:
            perday[col] = 0
    perday["Total handled"] = perday["New"] + perday["Catch-up"]
    perday["% catch-up"] = (perday["Catch-up"] / perday["Total handled"] * 100).round(1)
    perday["Day"] = perday["Day"].astype(str)
    st.dataframe(perday[["Day", "Total handled", "New", "Catch-up", "% catch-up"]],
                 use_container_width=True, hide_index=True)
    st.bar_chart(perday.set_index("Day")[["New", "Catch-up"]], height=240)
    _tot = int(perday["Total handled"].sum())
    _cu = int(perday["Catch-up"].sum())
    st.caption(f"Across the range: **{_tot:,}** tickets handled · **{_tot - _cu:,} new** · "
               f"**{_cu:,} catch-up** ({(_cu/_tot*100):.0f}% catch-up)." if _tot else "")
    st.download_button("📥 Download new-vs-catchup (CSV)", perday.to_csv(index=False),
                       "new_vs_catchup.csv", "text/csv", key="ivt_nvc_csv")

    # by agent — who handled new vs catch-up
    st.markdown("###### By agent")
    _ta = _tt.groupby(["_agent", "_day", "Target object id"]).size().reset_index(name="_n")
    _ta["_create_day"] = _ta["Target object id"].map(_cre)
    _ta["Kind"] = _ta.apply(
        lambda r: "New" if (pd.notna(r["_create_day"]) and r["_create_day"] == r["_day"])
        else "Catch-up", axis=1)
    byagent = (_ta.groupby(["_agent", "Kind"]).size().unstack(fill_value=0)
               .reset_index().rename(columns={"_agent": "Agent"}))
    for col in ("New", "Catch-up"):
        if col not in byagent.columns:
            byagent[col] = 0
    byagent["Total handled"] = byagent["New"] + byagent["Catch-up"]
    byagent["% catch-up"] = (byagent["Catch-up"] / byagent["Total handled"] * 100).round(1)
    byagent = byagent.sort_values("Total handled", ascending=False)
    st.dataframe(byagent[["Agent", "Total handled", "New", "Catch-up", "% catch-up"]],
                 use_container_width=True, hide_index=True)
    st.caption("Per agent, counts each (day × ticket) they touched — New if the ticket was created "
               "that day, else Catch-up. The same ticket touched by two agents counts for both.")
    st.download_button("📥 Download by-agent new-vs-catchup (CSV)", byagent.to_csv(index=False),
                       "new_vs_catchup_by_agent.csv", "text/csv", key="ivt_nvc_agent_csv")

# ── per-agent (best effort — names differ between systems) ──────────────────────
st.markdown("##### By agent (best-effort — Convo360 names vs HubSpot usernames differ)")
ai = cvf.groupby("_agent").size().rename("Interactions").reset_index()
ac = tkf[tkf["_created"]].groupby("_agent").size().rename("Tickets created").reset_index()
st.columns(2)[0].caption("Convo360 handled")
cA, cB = st.columns(2)
with cA:
    st.markdown("**Convo360 — interactions by agent**")
    st.dataframe(ai.sort_values("Interactions", ascending=False),
                 use_container_width=True, hide_index=True, height=300)
with cB:
    st.markdown("**HubSpot — tickets created by agent**")
    st.dataframe(ac.sort_values("Tickets created", ascending=False),
                 use_container_width=True, hide_index=True, height=300)

st.download_button("📥 Download daily reconciliation (CSV)", daily.to_csv(index=False),
                   "interactions_vs_tickets.csv", "text/csv")

# ── work hours & avg time per ticket ────────────────────────────────────────────
st.markdown("##### ⏱️ Work hours & average time per ticket (by agent)")
st.caption("Work hours = first-to-last ticket-event span per day (proxy). "
           "Time/ticket = work time ÷ unique tickets touched (shown as min & sec).")
MIN_DAY = st.slider("Ignore days shorter than (hours)", 0.0, 3.0, 1.0, 0.5, key="ivt_min")
gspan = (tkf.groupby(["_agent", "_day"])
         .agg(start=("_t", "min"), end=("_t", "max"), events=("_t", "size")).reset_index())
gspan["span_h"] = (gspan["end"] - gspan["start"]).dt.total_seconds() / 3600.0
gspan = gspan[gspan["span_h"] >= MIN_DAY]


def _hm(h):
    return f"{int(h)}h {int(round((h - int(h)) * 60)):02d}m"


def _ms(minutes):
    """Decimal minutes → 'Xm Ys' (or 'Ys' when under a minute)."""
    if not minutes or minutes <= 0:
        return "—"
    total = int(round(minutes * 60))
    m, s = divmod(total, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


if gspan.empty:
    st.info("No qualifying working days for the ticket events.")
else:
    hrs = gspan.groupby("_agent")["span_h"].sum()
    ev_by = tkf.groupby("_agent").size()
    cr_by = tkf[tkf["_created"]].groupby("_agent").size()
    tt_by = tkf.groupby("_agent")["Target object id"].apply(lambda s: s.replace("", pd.NA).nunique())
    wrows = []
    for a, h in hrs.items():
        events = int(ev_by.get(a, 0))
        created = int(cr_by.get(a, 0))
        touched = int(tt_by.get(a, 0))
        wrows.append({
            "Agent": a,
            "Work hours": _hm(h),
            "Ticket events": events,
            "Tickets created": created,
            "Tickets touched": touched,
            "Time/ticket": _ms(h * 60 / touched) if touched else "—",
            "Time/event": _ms(h * 60 / events) if events else "—",
            "Tickets/hour": round(touched / h, 1) if h else 0,
            "_sort": h,
        })
    weff = pd.DataFrame(wrows).sort_values("_sort", ascending=False).drop(columns="_sort")
    st.dataframe(weff, use_container_width=True, hide_index=True)
    st.download_button("📥 Download work-hours efficiency (CSV)", weff.to_csv(index=False),
                       "ticket_work_hours.csv", "text/csv", key="ivt_wh_csv")

# ── per-consumer match (live HubSpot) ───────────────────────────────────────────
st.markdown("---")
st.markdown("### 🧩 Per-consumer match (live)")
st.caption("Match each Convo360 **Customer Name** to the tickets **created in the window** by "
           "pulling ticket subjects/descriptions live from HubSpot. Answers: for a consumer who "
           "contacted N times, how many tickets were opened?")

if "_customer" not in cvf.columns or cvf["_customer"].str.strip().eq("").all():
    st.info("The Convo360 export has no Customer Name column — can't match per consumer.")
elif st.button("🔗 Run per-consumer match (queries HubSpot)"):
    ids = sorted(tkf[tkf["_created"]]["Target object id"].replace("", pd.NA).dropna().unique().tolist())
    pipe_map = _pipeline_map()
    subjmap, pipe_of = {}, {}
    with dash_spinner(f"Fetching {len(ids):,} created tickets from HubSpot…"):
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            for rec in fetch_all("tickets",
                                 ["hs_object_id", "subject", "content", "createdate", "hs_pipeline"],
                                 filter_groups=[{"filters": [
                                     {"propertyName": "hs_object_id", "operator": "IN", "values": chunk}]}]):
                p = rec.get("properties", {})
                hid = str(p.get("hs_object_id") or "").strip()
                if hid:
                    subjmap[hid] = f"{p.get('subject') or ''} {p.get('content') or ''}".lower()
                    pipe_of[hid] = pipe_map.get(p.get("hs_pipeline"), "Other")
    tickets_text = list(subjmap.values())

    # tickets created by pipeline (T1 / T2 / VRS Registration / Other)
    st.markdown("##### Tickets created by pipeline")
    pv = pd.Series(list(pipe_of.values())).value_counts().rename_axis("Pipeline").reset_index(name="Tickets")
    pv["%"] = (pv["Tickets"] / len(pipe_of) * 100).round(1) if pipe_of else 0
    st.dataframe(pv, use_container_width=True, hide_index=True)

    def _match_count(name):
        name = name.strip().lower()
        if not name:
            return 0
        toks = [t for t in name.split() if len(t) > 1]
        c = 0
        for txt in tickets_text:
            if name in txt or (len(toks) >= 2 and all(t in txt for t in toks)):
                c += 1
        return c

    cust = (cvf.groupby("_customer")
            .agg(Interactions=("_customer", "size"),
                 Source=("_source", lambda s: ", ".join(sorted(set(s)))))
            .reset_index().rename(columns={"_customer": "Customer"}))
    cust = cust[cust["Customer"].str.strip() != ""]
    with dash_spinner("Matching customers to tickets…"):
        cust["Tickets"] = cust["Customer"].map(_match_count)
    cust["Int/ticket"] = cust.apply(
        lambda r: round(r["Interactions"] / r["Tickets"], 1) if r["Tickets"] else 0, axis=1)
    cust["Flag"] = cust.apply(
        lambda r: "🔴 more tickets than contacts" if r["Tickets"] > r["Interactions"]
        else ("⚠️ no ticket found" if r["Tickets"] == 0 else "✅"), axis=1)
    cust = cust.sort_values(["Interactions", "Tickets"], ascending=False)

    matched = int((cust["Tickets"] > 0).sum())
    st.caption(f"{len(cust):,} consumers · {matched:,} matched to ≥1 ticket · "
               f"{int((cust['Tickets'] > cust['Interactions']).sum()):,} have more tickets than contacts.")
    st.dataframe(cust[["Customer", "Interactions", "Source", "Tickets", "Int/ticket", "Flag"]],
                 use_container_width=True, hide_index=True, height=460)
    st.download_button("📥 Download per-consumer match (CSV)", cust.to_csv(index=False),
                       "per_consumer_match.csv", "text/csv", key="ivt_cust_csv")
    st.caption("Matching is name-substring on ticket subject/description — a common name may "
               "over-match, and a ticket that doesn't name the consumer won't match. Treat as a guide.")

report_header_close()
