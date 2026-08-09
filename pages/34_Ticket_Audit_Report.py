import streamlit as st
import pandas as pd
import time
from utils import (require_auth, is_app_admin, COMMON_CSS,
                   report_header, report_header_close, log_report_view,
                   save_report, load_report, saved_at_label)

st.set_page_config(page_title="Ticket Audit Report", layout="wide", page_icon="🎫")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Ticket Audit Report")

report_header("Ticket Audit Report",
              "Support ticket meter + work hours from the HubSpot audit log export",
              section="Admin")

if not is_app_admin():
    st.warning("This page is restricted to administrators.")
    report_header_close(); st.stop()

SAVE_KEY = "ticket_audit"
RETENTION_OPTS = {"24 hours": 24, "48 hours": 48, "7 days": 168}

st.markdown(
    "Export your HubSpot **audit log** (Settings → Account Defaults → **Review account's audit "
    "log** → Export) and upload the CSV. This measures **support ticket activity** (create / "
    "update / merge / delete) per agent, plus each agent's **work hours** (first-to-last recorded "
    "action per day). Work-hours is a span proxy — breaks inside the window are included; work "
    "outside HubSpot is not counted.")

c_up, c_ret = st.columns([3, 1])
with c_up:
    up = st.file_uploader("HubSpot audit log CSV", type=["csv"], key="tk_csv")
with c_ret:
    _ret_label = st.selectbox("Keep report for", list(RETENTION_OPTS.keys()), index=1, key="tk_ret",
                              help="How long the uploaded report stays saved before it expires.")
TTL_SECONDS = RETENTION_OPTS[_ret_label] * 3600


def _hm(h):
    return f"{int(h)}h {int(round((h - int(h)) * 60)):02d}m"


def _parse(file):
    raw = pd.read_csv(file, dtype=str).fillna("")
    ucol = next((c for c in raw.columns if c.strip().lower() in
                 ("modified by", "user", "performed by")), None)
    dcol = next((c for c in raw.columns if "date" in c.strip().lower()), None)
    if not ucol or not dcol:
        return f"Couldn't find user / date columns. Found: {list(raw.columns)}"
    raw["_u"] = raw[ucol].str.strip()
    raw["_t"] = pd.to_datetime(raw[dcol].str.strip(), errors="coerce")
    raw = raw[(raw["_u"] != "") & raw["_t"].notna()].copy()
    raw["_day"] = raw["_t"].dt.date
    for c in ("Category", "Subcategory", "Action", "Target object id"):
        if c not in raw.columns:
            raw[c] = ""
    keep = raw[["_u", "_t", "_day", "Category", "Subcategory", "Action", "Target object id"]].copy()
    return keep, len(raw), raw["_day"].min(), raw["_day"].max()


if up is not None:
    res = _parse(up)
    if isinstance(res, str):
        st.error(res); report_header_close(); st.stop()
    ev, n_events, d0, d1 = res
    save_report(SAVE_KEY, {"ev": ev, "n_events": n_events, "d0": d0, "d1": d1})
    saved = load_report(SAVE_KEY)
else:
    saved = load_report(SAVE_KEY)
    if saved is None:
        st.info("Upload the audit CSV to see the report.")
        report_header_close(); st.stop()
    if time.time() - (saved.get("saved_at") or 0) > TTL_SECONDS:
        st.warning(f"The saved report is older than {_ret_label} — please upload a fresh export.")
        report_header_close(); st.stop()
    ev, n_events, d0, d1 = saved["ev"], saved["n_events"], saved["d0"], saved["d1"]

if saved and saved.get("saved_at"):
    _rem = max(0, TTL_SECONDS - (time.time() - saved["saved_at"]))
    _left = f"~{int(_rem // 3600)}h left" if _rem >= 3600 else f"~{int(_rem // 60)}m left"
    st.caption(f"📌 Saved {saved_at_label(saved)} · kept for {_ret_label} ({_left}) · "
               f"upload a new CSV to replace it.")

# ── month filter ────────────────────────────────────────────────────────────────
ev["_month"] = pd.to_datetime(ev["_day"]).dt.to_period("M")
_mopts = sorted(ev["_month"].unique())
_mlabels = [p.strftime("%b %Y") for p in _mopts]
_pick = st.multiselect("Filter by month", _mlabels, default=_mlabels, key="tk_month")
_keep = {p for p, lab in zip(_mopts, _mlabels) if lab in _pick} or set(_mopts)
ev = ev[ev["_month"].isin(_keep)].copy()
if ev.empty:
    st.warning("No events in the selected month(s).")
    report_header_close(); st.stop()

tickets = ev[ev["Subcategory"] == "Ticket"].copy()
_s, _e = ev["_day"].min(), ev["_day"].max()
st.caption(f"Window **{_s:%b %d}–{_e:%b %d, %Y}** · {len(ev):,} audit events · "
           f"{len(tickets):,} ticket events · times as exported (account timezone).")

# ════════════════════════════════════════════════════════════════════════════════
# 1) TICKET METER
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("### 🎫 Support ticket meter")
if tickets.empty:
    st.info("No ticket events in this export.")
else:
    n_touch = tickets["Target object id"].replace("", pd.NA).nunique()
    acts = tickets["Action"].value_counts()
    created = int(acts.get("Create", 0))
    updated = int(acts.get("Update", 0))
    merged = int(acts.get("Merge", 0))
    deleted = int(acts.get("Delete", 0))
    n_created_ids = tickets[tickets["Action"] == "Create"]["Target object id"].replace("", pd.NA).nunique()

    k = st.columns(6)
    k[0].metric("Ticket events", f"{len(tickets):,}")
    k[1].metric("Unique tickets touched", f"{n_touch:,}")
    k[2].metric("Tickets created", f"{n_created_ids:,}")
    k[3].metric("Updates", f"{updated:,}")
    k[4].metric("Merged", f"{merged:,}")
    k[5].metric("Deleted", f"{deleted:,}")

    # per-day trend
    st.markdown("##### Ticket events per day")
    per_day = (tickets.groupby(tickets["_t"].dt.date)
               .size().rename("Ticket events").reset_index()
               .rename(columns={"_t": "Day"}))
    per_day["Day"] = per_day.iloc[:, 0].astype(str)
    st.bar_chart(per_day.set_index("Day")["Ticket events"], height=240)

    # per-agent
    st.markdown("##### By agent")
    agent = (tickets.groupby("_u")
             .agg(Ticket_events=("Action", "size"),
                  Created=("Action", lambda s: int((s == "Create").sum())),
                  Updated=("Action", lambda s: int((s == "Update").sum())),
                  Tickets_touched=("Target object id",
                                   lambda s: s.replace("", pd.NA).nunique()))
             .reset_index().rename(columns={"_u": "Agent", "Ticket_events": "Ticket events",
                                            "Tickets_touched": "Tickets touched"}))
    agent["Agent"] = agent["Agent"].str.split("@").str[0]
    agent = agent.sort_values("Ticket events", ascending=False)
    st.dataframe(agent, use_container_width=True, hide_index=True)

    # action breakdown
    st.markdown("##### Action breakdown")
    ab = tickets["Action"].value_counts().rename_axis("Action").reset_index(name="Count")
    st.dataframe(ab, use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════════════════════
# 2) WORK HOURS
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("### ⏱️ Work hours per agent")
MIN_DAY = st.slider("Ignore days shorter than (hours) — filters out quick check-ins",
                    0.0, 3.0, 1.0, 0.5, key="tk_min")
g = (ev.groupby(["_u", "_day"])
     .agg(start=("_t", "min"), end=("_t", "max"), events=("_t", "size")).reset_index())
g["span_h"] = (g["end"] - g["start"]).dt.total_seconds() / 3600.0
clean = g[g["span_h"] >= MIN_DAY].copy()

if clean.empty:
    st.info("No qualifying working days after the filter.")
else:
    def _tag(h):
        return "🟢 Full" if h >= 8 else ("🟡 Steady" if h >= 6.5 else "🔴 Short")

    summ = (clean.groupby("_u")
            .agg(Days=("span_h", "size"), Avg=("span_h", "mean"),
                 Median=("span_h", "median"), Longest=("span_h", "max"),
                 Total=("span_h", "sum"))
            .reset_index().sort_values("Avg", ascending=False))
    cols = st.columns(min(len(summ), 6))
    for col, (_, r) in zip(cols, summ.iterrows()):
        col.metric(r["_u"].split("@")[0], _hm(r["Avg"]), _tag(r["Avg"]))

    show = summ.copy()
    show["Agent"] = show["_u"].str.split("@").str[0]
    for c in ("Avg", "Median", "Longest"):
        show[c] = show[c].map(_hm)
    show["Total"] = show["Total"].map(lambda h: f"{h:.0f}h")
    st.dataframe(show[["Agent", "Days", "Avg", "Median", "Longest", "Total"]],
                 use_container_width=True, hide_index=True)
    st.caption("🟢 ≥ 8h · 🟡 6.5–8h · 🔴 < 6.5h average day.")

    # day-by-day
    st.markdown("##### Day-by-day detail")
    who = st.selectbox("Agent", sorted(clean["_u"].unique()), key="tk_who")
    det = g[g["_u"] == who].sort_values("_day").copy()
    det["Date"] = det["_day"].map(lambda d: d.strftime("%a %b %d"))
    det["Start"] = det["start"].dt.strftime("%H:%M")
    det["End"] = det["end"].dt.strftime("%H:%M")
    det["Span"] = det["span_h"].map(_hm)
    det["Events"] = det["events"]
    # ticket events that day for this agent
    tk_day = (tickets[tickets["_u"] == who].groupby(tickets["_t"].dt.date).size()
              if not tickets.empty else pd.Series(dtype=int))
    det["Ticket events"] = det["_day"].map(lambda d: int(tk_day.get(d, 0)) if len(tk_day) else 0)
    st.dataframe(det[["Date", "Start", "End", "Span", "Events", "Ticket events"]],
                 use_container_width=True, hide_index=True, height=420)

# ── export ────────────────────────────────────────────────────────────────────
if not tickets.empty:
    st.download_button("📥 Download ticket events (CSV)",
                       tickets[["_u", "_t", "Action", "Target object id"]]
                       .rename(columns={"_u": "agent", "_t": "timestamp",
                                        "Target object id": "ticket_id"}).to_csv(index=False),
                       "ticket_audit_events.csv", "text/csv")

report_header_close()
