import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import date, datetime, timezone
from utils import (
    require_auth, fetch_all, get_secret, COMMON_CSS, dash_spinner,
    report_header, report_header_close, save_report, load_report,
    pdf_download_button,
)

st.set_page_config(page_title="Jira Ticket Report", layout="wide", page_icon="🧩")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Jira Ticket Report", "Support tickets escalated to Jira — status, priority, assignee & aging", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
PRIMARY, GREEN, BLUE, AMBER, PURPLE, RED = "#C9A876", "#0D3B26", "#3B82F6", "#F59E0B", "#8B5CF6", "#EF4444"

PROPS = [
    "hs_object_id", "subject", "createdate", "closed_date", "hubspot_owner_id",
    "si_jira_issue_key", "si_jira_issue_summary", "si_jira_issue_status",
    "si_jira_issue_priority", "si_jira_issue_assignee", "si_jira_issue_reporter",
    "si_jira_issue_link",
]
RANGES = ["This Month", "This Year", "Last 30 Days", "Last 90 Days",
          "Last 6 Months", "Last 12 Months", "All Time", "Custom Range"]


def _floor(label):
    t = date.today()
    return {
        "This Month": date(t.year, t.month, 1),
        "This Year": date(t.year, 1, 1),
        "Last 30 Days": date.fromordinal(t.toordinal() - 30),
        "Last 90 Days": date.fromordinal(t.toordinal() - 90),
        "Last 6 Months": date.fromordinal(t.toordinal() - 182),
        "Last 12 Months": date.fromordinal(t.toordinal() - 365),
    }.get(label, date(2000, 1, 1))


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


col1, col2, col3, col4 = st.columns([2, 1.3, 1.3, 1])
with col1:
    range_label = st.selectbox("Date range (create date)", RANGES, index=2)
custom_from = custom_to = None
if range_label == "Custom Range":
    with col2:
        custom_from = st.date_input("From", value=date.fromordinal(date.today().toordinal() - 90))
    with col3:
        custom_to = st.date_input("To", value=date.today())
with col4:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)

report_header_close()

_KEY = (f"jira_report_v1_custom_{custom_from}_{custom_to}" if range_label == "Custom Range"
        else "jira_report_v1_" + range_label.replace(" ", "_"))
cached = None if run else load_report(_KEY)
if cached is None and not run:
    st.info("Pick a date range and click **Run Report**. Results are saved and reused next time.")
    st.stop()

if run or cached is None:
    filters = [{"propertyName": "si_jira_issue_key", "operator": "HAS_PROPERTY"}]
    if range_label == "Custom Range":
        _f = datetime(custom_from.year, custom_from.month, custom_from.day, tzinfo=timezone.utc)
        _t = datetime(custom_to.year, custom_to.month, custom_to.day, 23, 59, 59, tzinfo=timezone.utc)
        filters += [{"propertyName": "createdate", "operator": "GTE", "value": str(int(_f.timestamp() * 1000))},
                    {"propertyName": "createdate", "operator": "LTE", "value": str(int(_t.timestamp() * 1000))}]
    elif range_label != "All Time":
        fl = _floor(range_label)
        filters.append({"propertyName": "createdate", "operator": "GTE",
                        "value": str(int(datetime(fl.year, fl.month, fl.day, tzinfo=timezone.utc).timestamp() * 1000))})
    with dash_spinner("Fetching Jira-linked tickets…"):
        records = fetch_all("tickets", PROPS, filter_groups=[{"filters": filters}])
    save_report(_KEY, {"records": records})
    _saved_at = time.time()
else:
    records = cached.get("records", [])
    _saved_at = cached.get("saved_at")

if not records:
    st.warning("No Jira-linked tickets found in this date range.")
    st.stop()

owners = _owners()


def _dt(v):
    return pd.to_datetime(v, errors="coerce", utc=True) if v else pd.NaT


rows = []
for r in records:
    p = r.get("properties", {})
    rows.append({
        "Ticket ID": p.get("hs_object_id") or r.get("id"),
        "Ticket Name": p.get("subject") or "—",
        "Jira Key": p.get("si_jira_issue_key") or "—",
        "Jira Summary": p.get("si_jira_issue_summary") or "—",
        "Status": p.get("si_jira_issue_status") or "—",
        "Priority": p.get("si_jira_issue_priority") or "—",
        "Assignee": p.get("si_jira_issue_assignee") or "Unassigned",
        "Reporter": p.get("si_jira_issue_reporter") or "—",
        "HS Owner": owners.get(str(p.get("hubspot_owner_id")), "—") if p.get("hubspot_owner_id") else "—",
        "Created": _dt(p.get("createdate")),
        "Closed": _dt(p.get("closed_date")),
        "Jira Link": p.get("si_jira_issue_link") or "",
    })
df = pd.DataFrame(rows)

# ── Filters ──────────────────────────────────────────────────────────────────
def _opts(col):
    return sorted([o for o in df[col].unique() if o and o != "—"])

fc1, fc2, fc3, fc4 = st.columns(4)
sel_status = fc1.multiselect("Jira status", _opts("Status"))
sel_prio = fc2.multiselect("Priority", _opts("Priority"))
sel_assignee = fc3.multiselect("Assignee", _opts("Assignee"))
sel_owner = fc4.multiselect("HS owner", _opts("HS Owner"))
for _c, _s in [("Status", sel_status), ("Priority", sel_prio), ("Assignee", sel_assignee), ("HS Owner", sel_owner)]:
    if _s:
        df = df[df[_c].isin(_s)]
if df.empty:
    st.warning("No tickets match the selected filters.")
    st.stop()

# ── KPIs ─────────────────────────────────────────────────────────────────────
total = len(df)
unique_issues = df["Jira Key"].nunique()
hs_open = int(df["Closed"].isna().sum())
unassigned = int((df["Assignee"].str.lower() == "unassigned").sum())
_now = pd.Timestamp.now(tz="UTC")
_age = (_now - df["Created"]).dt.days
aging = int((_age > 30).sum())
high_prio = int(df["Priority"].str.contains("P1|P2", case=False, na=False).sum())

if _saved_at:
    _a = int(time.time() - _saved_at)
    _ago = "just now" if _a < 90 else (f"{_a//60} min ago" if _a < 3600 else f"{_a//3600} h ago")
    st.caption(f"📌 Saved · refreshed {_ago} · {range_label} · click **Run Report** to refresh.")
if len(records) >= 10000:
    st.warning("Showing the first 10,000 Jira-linked tickets (HubSpot search limit). Narrow the range for complete metrics.")


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:0.9rem 1.1rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">{label}</div>
  <div style="font-size:1.35rem;font-weight:800;color:{color};line-height:1.1;">{value}</div>
  {f'<div style="font-size:0.7rem;color:#9CA3AF;margin-top:0.15rem;">{sub}</div>' if sub else ''}
</div>"""

st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.75rem;margin-bottom:1.2rem;">
  {tile("Jira Tickets", f"{total:,}", range_label)}
  {tile("Unique Jira Issues", f"{unique_issues:,}", "distinct Jira keys", BLUE)}
  {tile("HS Open", f"{hs_open:,}", "ticket not yet closed", AMBER)}
  {tile("Unassigned", f"{unassigned:,}", "no Jira assignee", RED)}
  {tile("High Priority", f"{high_prio:,}", "P1 / P2", PURPLE)}
  {tile("Aging (30+ days)", f"{aging:,}", "created 30+ days ago", RED)}
</div>""", unsafe_allow_html=True)

with st.expander("ℹ️ How to read this report"):
    st.markdown("""
- **Jira Tickets** — HubSpot support tickets attached to a Jira issue (an engineering escalation) in the date range.
- **Unique Jira Issues** — distinct Jira keys; several tickets can point to the same Jira issue.
- **HS Open** — the HubSpot ticket isn't closed yet (customer side still open).
- **Unassigned** — the linked Jira issue has no assignee — likely needs triage.
- **High Priority** — Jira priority P1/P2.
- **Aging (30+ days)** — created over 30 days ago; long-lived escalations to watch.

The **Status**, **Priority**, and **Assignee** come straight from Jira (synced onto the ticket). Use the filters to focus on a status (e.g. "Support Triage") or a specific engineer.
""")

# ── Charts ───────────────────────────────────────────────────────────────────
def _barh(col, color, title, n=12):
    d = df[df[col] != "—"][col].value_counts().head(n).reset_index()
    d.columns = [col, "Tickets"]
    st.markdown(f"##### {title}")
    if d.empty:
        st.caption("No data.")
        return
    st.altair_chart(alt.Chart(d).mark_bar(color=color, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("Tickets:Q"), y=alt.Y(f"{col}:N", sort="-x", title=None), tooltip=[col, "Tickets"]
    ).properties(height=max(180, len(d) * 26)), use_container_width=True)


a1, a2 = st.columns(2)
with a1:
    _barh("Status", PRIMARY, "By Jira Status")
with a2:
    _barh("Priority", PURPLE, "By Priority")
a3, a4 = st.columns(2)
with a3:
    _barh("Assignee", BLUE, "By Assignee")
with a4:
    st.markdown("##### Jira Tickets Created over Time")
    tdf = df.dropna(subset=["Created"]).copy()
    if not tdf.empty:
        tdf["Month"] = tdf["Created"].dt.to_period("M").dt.to_timestamp()
        bm = tdf.groupby("Month").size().reset_index(name="Tickets")
        st.altair_chart(alt.Chart(bm).mark_bar(color=PRIMARY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Month:T", title=None), y=alt.Y("Tickets:Q"), tooltip=["Month:T", "Tickets"]
        ).properties(height=260), use_container_width=True)

# ── Table ────────────────────────────────────────────────────────────────────
st.markdown("##### Jira Ticket Detail")
search = st.text_input("Search", placeholder="Filter by name, Jira key, summary, assignee…",
                       label_visibility="collapsed")
show = df.copy()
show["Age (days)"] = (_now - show["Created"]).dt.days
for c in ("Created", "Closed"):
    show[c] = show[c].dt.strftime("%b %d, %Y")
table_cols = ["Ticket ID", "Ticket Name", "Jira Key", "Jira Summary", "Status", "Priority",
              "Assignee", "Reporter", "HS Owner", "Created", "Closed", "Age (days)", "Jira Link"]
show = show[table_cols]
if search.strip():
    q = search.strip().lower()
    show = show[show.astype(str).apply(lambda x: x.str.lower().str.contains(q, na=False)).any(axis=1)]
    st.caption(f"{len(show):,} of {total:,} match “{search}”")

st.dataframe(show, use_container_width=True, hide_index=True, height=480,
             column_config={"Jira Link": st.column_config.LinkColumn("Jira", display_text="Open ↗")})

st.download_button("📥 Download CSV", show.to_csv(index=False),
                   f"jira_report_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

# ── PDF ──────────────────────────────────────────────────────────────────────
_pdf_metrics = [("Jira Tickets", f"{total:,}"), ("Unique Issues", f"{unique_issues:,}"),
                ("HS Open", f"{hs_open:,}"), ("Unassigned", f"{unassigned:,}"),
                ("High Priority", f"{high_prio:,}")]
_pdf_charts = []
_st = df[df["Status"] != "—"]["Status"].value_counts().head(12).reset_index()
_st.columns = ["Status", "Tickets"]
if not _st.empty:
    _pdf_charts.append({"data": _st, "kind": "barh", "x": "Status", "y": "Tickets", "title": "By Jira status"})
_pr = df[df["Priority"] != "—"]["Priority"].value_counts().head(12).reset_index()
_pr.columns = ["Priority", "Tickets"]
if not _pr.empty:
    _pdf_charts.append({"data": _pr, "kind": "barh", "x": "Priority", "y": "Tickets", "title": "By priority"})
pdf_download_button(show, "jira_report.pdf", f"Jira Ticket Report — {range_label}",
                    subtitle="Tickets escalated to Jira", metrics=_pdf_metrics, charts=_pdf_charts, key="jirarpt")

report_header_close()
