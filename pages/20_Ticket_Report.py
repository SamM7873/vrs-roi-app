import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import date, datetime, timezone
from utils import (
    require_auth, fetch_all, get_secret, COMMON_CSS,
    report_header, report_header_close, save_report, load_report,
    pdf_download_button,
)

st.set_page_config(page_title="Ticket Report", layout="wide", page_icon="🎫")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Ticket Report", "Support ticket KPIs — response & resolution times, category, owner, Jira", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
PRIMARY, GREEN, BLUE, AMBER = "#C9A876", "#0D3B26", "#3B82F6", "#F59E0B"

PROPS = [
    "hs_object_id", "subject", "createdate", "closed_date",
    "time_to_close", "time_to_first_agent_reply",
    "hubspot_owner_id", "hs_pipeline", "hs_pipeline_stage",
    "hs_ticket_category", "subcategory",
    "si_jira_issue_key", "si_jira_issue_link",
]

RANGES = ["This Month", "This Year", "Last 30 Days", "Last 90 Days",
          "Last 6 Months", "Last 12 Months", "All Time", "Custom Range"]


def _floor(label):
    t = date.today()
    if label == "This Month":     return date(t.year, t.month, 1)
    if label == "This Year":      return date(t.year, 1, 1)
    if label == "Last 30 Days":   return date.fromordinal(t.toordinal() - 30)
    if label == "Last 90 Days":   return date.fromordinal(t.toordinal() - 90)
    if label == "Last 6 Months":  return date.fromordinal(t.toordinal() - 182)
    if label == "Last 12 Months": return date.fromordinal(t.toordinal() - 365)
    return date(2000, 1, 1)


@st.cache_data(ttl=3600, show_spinner=False)
def _owners():
    out, after = {}, None
    for _ in range(50):
        url = f"{BASE_URL}/crm/v3/owners?limit=100" + (f"&after={after}" if after else "")
        r = requests.get(url, headers=_headers, timeout=30)
        if r.status_code != 200:
            break
        d = r.json()
        for o in d.get("results", []):
            nm = f"{o.get('firstName','')} {o.get('lastName','')}".strip() or o.get("email")
            if nm:
                out[str(o.get("id"))] = nm
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _pipelines():
    labels, stages = {}, {}
    r = requests.get(f"{BASE_URL}/crm/v3/pipelines/tickets", headers=_headers, timeout=30)
    if r.status_code == 200:
        for p in r.json().get("results", []):
            labels[str(p.get("id"))] = p.get("label")
            for s in p.get("stages", []):
                stages[str(s.get("id"))] = s.get("label")
    return labels, stages


col1, col2, col3, col4 = st.columns([2, 1.3, 1.3, 1])
with col1:
    range_label = st.selectbox("Date range (create date)", RANGES, index=2)

custom_from = custom_to = None
if range_label == "Custom Range":
    with col2:
        custom_from = st.date_input("From", value=date.fromordinal(date.today().toordinal() - 30))
    with col3:
        custom_to = st.date_input("To", value=date.today())
with col4:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)

report_header_close()

if range_label == "Custom Range":
    _KEY = f"ticket_report_custom_{custom_from}_{custom_to}"
else:
    _KEY = "ticket_report_" + range_label.replace(" ", "_")
cached = None if run else load_report(_KEY)

if cached is None and not run:
    st.info("Pick a date range and click **Run Report**. Results are saved and reused next time.")
    st.stop()

if run or cached is None:
    fg = None
    if range_label == "Custom Range":
        _f = datetime(custom_from.year, custom_from.month, custom_from.day, tzinfo=timezone.utc)
        _t = datetime(custom_to.year, custom_to.month, custom_to.day, 23, 59, 59, tzinfo=timezone.utc)
        fg = [{"filters": [
            {"propertyName": "createdate", "operator": "GTE", "value": str(int(_f.timestamp() * 1000))},
            {"propertyName": "createdate", "operator": "LTE", "value": str(int(_t.timestamp() * 1000))},
        ]}]
    elif range_label != "All Time":
        floor = _floor(range_label)
        floor_ms = str(int(datetime(floor.year, floor.month, floor.day, tzinfo=timezone.utc).timestamp() * 1000))
        fg = [{"filters": [{"propertyName": "createdate", "operator": "GTE", "value": floor_ms}]}]
    from utils import dash_spinner
    with dash_spinner("Fetching tickets…"):
        records = fetch_all("tickets", PROPS, filter_groups=fg)
    save_report(_KEY, {"records": records})
    _saved_at = time.time()
else:
    records = cached.get("records", [])
    _saved_at = cached.get("saved_at")

if not records:
    st.warning("No tickets found in this date range.")
    st.stop()

owners = _owners()
pipe_labels, stage_labels = _pipelines()


def _dt(v):
    if not v:
        return pd.NaT
    return pd.to_datetime(v, errors="coerce", utc=True)


rows = []
for r in records:
    p = r.get("properties", {})
    ttc = pd.to_numeric(p.get("time_to_close"), errors="coerce")
    ttfr = pd.to_numeric(p.get("time_to_first_agent_reply"), errors="coerce")
    rows.append({
        "Ticket ID": p.get("hs_object_id") or r.get("id"),
        "Ticket Name": p.get("subject") or "—",
        "Created": _dt(p.get("createdate")),
        "Closed": _dt(p.get("closed_date")),
        "Owner": owners.get(str(p.get("hubspot_owner_id")), "—") if p.get("hubspot_owner_id") else "—",
        "Pipeline": pipe_labels.get(str(p.get("hs_pipeline")), p.get("hs_pipeline") or "—"),
        "Stage": stage_labels.get(str(p.get("hs_pipeline_stage")), p.get("hs_pipeline_stage") or "—"),
        "Category": p.get("hs_ticket_category") or "—",
        "Subcategory": p.get("subcategory") or "—",
        "Jira Key": p.get("si_jira_issue_key") or "—",
        "Jira Link": p.get("si_jira_issue_link") or "",
        "_ttc_ms": ttc,
        "_ttfr_ms": ttfr,
    })
df = pd.DataFrame(rows)

# ── KPIs ─────────────────────────────────────────────────────────────────────
total = len(df)
closed = int(df["Closed"].notna().sum())
open_ct = total - closed
avg_ttc_days = (df["_ttc_ms"].mean() / 86_400_000) if df["_ttc_ms"].notna().any() else None
avg_ttfr_hrs = (df["_ttfr_ms"].mean() / 3_600_000) if df["_ttfr_ms"].notna().any() else None
with_jira = int((df["Jira Key"] != "—").sum())
close_rate = (closed / total * 100) if total else 0

if _saved_at:
    _age = int(time.time() - _saved_at)
    _ago = "just now" if _age < 90 else (f"{_age//60} min ago" if _age < 3600 else f"{_age//3600} h ago")
    st.caption(f"📌 Saved · refreshed {_ago} · {range_label} · click **Run Report** to refresh.")
if len(records) >= 10000:
    st.warning("Showing the first 10,000 tickets (HubSpot search limit). Narrow the date range for complete metrics.")


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:0.9rem 1.1rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">{label}</div>
  <div style="font-size:1.35rem;font-weight:800;color:{color};line-height:1.1;">{value}</div>
  {f'<div style="font-size:0.7rem;color:#9CA3AF;margin-top:0.15rem;">{sub}</div>' if sub else ''}
</div>"""

st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.75rem;margin-bottom:1.4rem;">
  {tile("Tickets", f"{total:,}", range_label)}
  {tile("Closed", f"{closed:,}", f"{close_rate:.0f}% close rate", GREEN)}
  {tile("Open", f"{open_ct:,}", "not yet closed", AMBER)}
  {tile("Avg Time to Close", f"{avg_ttc_days:.1f} d" if avg_ttc_days is not None else "—", "create → closed", BLUE)}
  {tile("Avg First Response", f"{avg_ttfr_hrs:.1f} h" if avg_ttfr_hrs is not None else "—", "create → first reply", BLUE)}
  {tile("With Jira", f"{with_jira:,}", f"{(with_jira/total*100):.0f}% linked", "#8B5CF6")}
</div>""", unsafe_allow_html=True)

# ── Charts ───────────────────────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.markdown("##### Tickets by Category")
    cat = df[df["Category"] != "—"]["Category"].value_counts().head(12).reset_index()
    cat.columns = ["Category", "Tickets"]
    if not cat.empty:
        st.altair_chart(alt.Chart(cat).mark_bar(color=PRIMARY, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            x=alt.X("Tickets:Q"), y=alt.Y("Category:N", sort="-x", title=None), tooltip=["Category", "Tickets"]
        ).properties(height=max(200, len(cat) * 24)), use_container_width=True)
with c2:
    st.markdown("##### Tickets by Stage")
    stg = df["Stage"].value_counts().head(12).reset_index()
    stg.columns = ["Stage", "Tickets"]
    st.altair_chart(alt.Chart(stg).mark_bar(color=BLUE, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("Tickets:Q"), y=alt.Y("Stage:N", sort="-x", title=None), tooltip=["Stage", "Tickets"]
    ).properties(height=max(200, len(stg) * 24)), use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    st.markdown("##### Tickets Created over Time")
    tdf = df.dropna(subset=["Created"]).copy()
    if not tdf.empty:
        tdf["Month"] = tdf["Created"].dt.to_period("M").dt.to_timestamp()
        bm = tdf.groupby("Month").size().reset_index(name="Tickets")
        st.altair_chart(alt.Chart(bm).mark_bar(color=PRIMARY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Month:T", title=None), y=alt.Y("Tickets:Q"), tooltip=["Month:T", "Tickets"]
        ).properties(height=260), use_container_width=True)
with c4:
    st.markdown("##### Avg Time to Close by Owner (days)")
    od = df[df["_ttc_ms"].notna() & (df["Owner"] != "—")].copy()
    if not od.empty:
        od["days"] = od["_ttc_ms"] / 86_400_000
        ob = od.groupby("Owner")["days"].mean().round(1).sort_values(ascending=False).head(12).reset_index()
        st.altair_chart(alt.Chart(ob).mark_bar(color=GREEN, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            x=alt.X("days:Q", title="Avg days to close"), y=alt.Y("Owner:N", sort="-x", title=None),
            tooltip=["Owner", "days"]
        ).properties(height=max(200, len(ob) * 24)), use_container_width=True)

# ── Table ────────────────────────────────────────────────────────────────────
st.markdown("##### Ticket Detail")
search = st.text_input("Search tickets", placeholder="Filter by name, owner, category, Jira key…",
                       label_visibility="collapsed")

show = df.copy()
show["Time to Close (d)"] = (show["_ttc_ms"] / 86_400_000).round(1)
show["First Response (h)"] = (show["_ttfr_ms"] / 3_600_000).round(1)
for c in ("Created", "Closed"):
    show[c] = show[c].dt.strftime("%b %d, %Y")
table_cols = ["Ticket ID", "Ticket Name", "Created", "Closed", "Time to Close (d)",
              "First Response (h)", "Owner", "Pipeline", "Stage", "Category", "Subcategory", "Jira Key"]
show = show[table_cols]

if search.strip():
    q = search.strip().lower()
    show = show[show.astype(str).apply(lambda x: x.str.lower().str.contains(q, na=False)).any(axis=1)]
    st.caption(f"{len(show):,} of {total:,} tickets match “{search}”")

st.dataframe(show, use_container_width=True, hide_index=True, height=460)

st.download_button("📥 Download CSV", show.to_csv(index=False),
                   f"ticket_report_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

# ── PDF (KPIs + charts) ──────────────────────────────────────────────────────
_pdf_metrics = [
    ("Tickets", f"{total:,}"), ("Closed", f"{closed:,}"),
    ("Avg Close (d)", f"{avg_ttc_days:.1f}" if avg_ttc_days is not None else "—"),
    ("Avg 1st Resp (h)", f"{avg_ttfr_hrs:.1f}" if avg_ttfr_hrs is not None else "—"),
    ("With Jira", f"{with_jira:,}"),
]
_pdf_charts = []
_cat = df[df["Category"] != "—"]["Category"].value_counts().head(12).reset_index()
_cat.columns = ["Category", "Tickets"]
if not _cat.empty:
    _pdf_charts.append({"data": _cat, "kind": "barh", "x": "Category", "y": "Tickets", "title": "Tickets by category"})
_stg = df["Stage"].value_counts().head(12).reset_index()
_stg.columns = ["Stage", "Tickets"]
_pdf_charts.append({"data": _stg, "kind": "barh", "x": "Stage", "y": "Tickets", "title": "Tickets by stage"})
pdf_download_button(show, "ticket_report.pdf", f"Ticket Report — {range_label}",
                    subtitle="Support ticket KPIs", metrics=_pdf_metrics, charts=_pdf_charts, key="ticketrpt")

report_header_close()
