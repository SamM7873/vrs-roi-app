import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Sign-Up Journey Report",
    "Contact created → Number assigned → First Login → First Outbound → Second Outbound",
    section="Analytics",
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

def _fmt(v):
    if not v:
        return "—"
    dt = _parse(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

def _days(a, b):
    da, db = _parse(a), _parse(b)
    if da and db:
        return round(abs((db - da).total_seconds()) / 86400, 1)
    return None

def _days_fmt(d):
    if d is None:
        return "—"
    return f"{round(d*24,1)}h" if d < 1 else f"{d}d"

# ── date presets ──────────────────────────────────────────────────────────────

def _date_range_for_preset(preset):
    today = date.today()
    if preset == "Today":
        return today, today
    if preset == "Yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "Last 7 Days":
        return today - timedelta(days=6), today
    if preset == "Last 30 Days":
        return today - timedelta(days=29), today
    if preset == "This Week (Mon–Sun)":
        return today - timedelta(days=today.weekday()), today
    if preset == "Last Week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if preset == "This Month":
        return today.replace(day=1), today
    if preset == "Last Month":
        first_this = today.replace(day=1)
        last_prev  = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if preset == "Last 3 Months":
        return today - timedelta(days=89), today
    if preset == "This Quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_start_month, day=1), today
    if preset == "Last Quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        lq_end   = today.replace(month=q_start_month, day=1) - timedelta(days=1)
        lq_start = lq_end.replace(month=((lq_end.month - 1) // 3) * 3 + 1, day=1)
        return lq_start, lq_end
    if preset == "This Year":
        return today.replace(month=1, day=1), today
    if preset == "Last Year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None, None

PRESETS = [
    "All Time", "Today", "Yesterday",
    "Last 7 Days", "Last 30 Days",
    "This Week (Mon–Sun)", "Last Week",
    "This Month", "Last Month", "Last 3 Months",
    "This Quarter", "Last Quarter",
    "This Year", "Last Year",
    "Custom Range",
]

# ── filter UI ─────────────────────────────────────────────────────────────────

col_preset, col_from, col_to = st.columns([2, 1, 1])
with col_preset:
    preset = st.selectbox("Date range (Contact created date)", PRESETS, index=0)

if preset == "Custom Range":
    with col_from:
        custom_from = st.date_input("From", value=date.today() - timedelta(days=29))
    with col_to:
        custom_to = st.date_input("To", value=date.today())
    filter_start, filter_end = custom_from, custom_to
else:
    filter_start, filter_end = _date_range_for_preset(preset)
    if filter_start:
        with col_from:
            st.markdown(f"<div style='padding-top:1.85rem;font-size:0.82rem;color:#9dc8b0;'>{filter_start.strftime('%b %d, %Y')}</div>", unsafe_allow_html=True)
        with col_to:
            st.markdown(f"<div style='padding-top:1.85rem;font-size:0.82rem;color:#9dc8b0;'>{filter_end.strftime('%b %d, %Y')}</div>", unsafe_allow_html=True)

st.markdown("<div style='margin-bottom:0.75rem;'></div>", unsafe_allow_html=True)

# ── run ───────────────────────────────────────────────────────────────────────

if st.button("Run Sign-Up Journey Report", use_container_width=False):

    # 1. Pull contacts
    with st.spinner("Loading contacts..."):
        contact_records = list_all(
            "contacts",
            ["email", "firstname", "lastname", "createdate"],
            progress_label="Fetching contacts",
        )

    if not contact_records:
        st.warning("No contact records found.")
        st.stop()

    # Filter contacts by date range
    tz_utc = timezone.utc
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, tzinfo=tz_utc)
        fe = datetime(filter_end.year, filter_end.month, filter_end.day, 23, 59, 59, tzinfo=tz_utc)
        def _in_range(v):
            dt = _parse(v)
            return dt is not None and fs <= dt <= fe
    else:
        def _in_range(v):
            return True

    contacts_in_range = []
    for r in contact_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if not email:
            continue
        if _in_range(p.get("createdate")):
            contacts_in_range.append(p)

    contact_emails = {p["email"].strip().lower() for p in contacts_in_range if p.get("email")}
    total_contacts = len(contact_emails)

    if total_contacts == 0:
        st.warning("No contacts found in the selected date range.")
        st.stop()

    # 2. Pull number objects (VRS live only)
    with st.spinner("Loading number objects..."):
        num_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name",
             "number_status", "service_type",
             "registered_at", "account_created_at", "registration_created_at", "registration_updated_at",
             "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
            progress_label="Fetching number objects",
        )

    num_records = [
        r for r in num_records
        if norm(r.get("properties", {}).get("service_type") or "") == "vrs"
        and norm(r.get("properties", {}).get("number_status") or "") == "live"
    ]

    # Index numbers by email, only keep those matching contacts in range
    num_by_email = {}
    for r in num_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if email and email in contact_emails:
            num_by_email[email] = p

    # 3. Count each funnel stage
    has_number       = sum(1 for e in contact_emails if num_by_email.get(e, {}).get("createdate"))
    has_registered   = sum(1 for e in contact_emails if num_by_email.get(e, {}).get("registered_at"))
    has_login        = sum(1 for e in contact_emails if num_by_email.get(e, {}).get("ursa_first_login"))
    has_outbound     = sum(1 for e in contact_emails if num_by_email.get(e, {}).get("ursa_first_outbound_call"))
    has_2nd_outbound = sum(1 for e in contact_emails if num_by_email.get(e, {}).get("ursa_second_outbound_call"))

    def pct(n):
        return f"{n / total_contacts * 100:.1f}%" if total_contacts else "—"

    # ── Date range label ──────────────────────────────────────────────────────
    if filter_start and filter_end:
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
    else:
        range_label = "All Time"

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Used HubSpot integration
</div>
""", unsafe_allow_html=True)

    # ── Funnel table ──────────────────────────────────────────────────────────
    funnel_rows = [
        ("Contact create date",    total_contacts,  "100%"),
        ("Number registered at",   has_registered,  pct(has_registered)),
        ("Number created at",      has_number,      pct(has_number)),
        ("Convo first login",      has_login,       pct(has_login)),
        ("Convo first outbound",   has_outbound,    pct(has_outbound)),
        ("Convo second outbound",  has_2nd_outbound, pct(has_2nd_outbound)),
    ]

    table_html = """
<div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:12px;overflow:hidden;margin-bottom:1.5rem;">
  <div style="display:grid;grid-template-columns:1fr 120px 140px;padding:0.6rem 1.25rem;
              background:#e8e4db;border-bottom:1px solid #DDD9CC;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">Stage</div>
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">Count</div>
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">% of Created</div>
  </div>
"""
    for i, (stage, count, pct_val) in enumerate(funnel_rows):
        bg = "#F4F1E8" if i % 2 == 0 else "#EFECE3"
        count_color = "#1F2937" if count > 0 else "#9CA3AF"
        count_display = f"{count:,}" if count > 0 else "0"
        pct_display = pct_val if count > 0 else "—"
        table_html += f"""
  <div style="display:grid;grid-template-columns:1fr 120px 140px;padding:0.75rem 1.25rem;
              background:{bg};border-bottom:1px solid #DDD9CC;">
    <div style="font-size:0.9rem;color:#374151;font-weight:500;">{stage}</div>
    <div style="font-size:0.9rem;font-weight:700;color:{count_color};font-variant-numeric:tabular-nums;">{count_display}</div>
    <div style="font-size:0.9rem;color:#6B7280;font-variant-numeric:tabular-nums;">{pct_display}</div>
  </div>"""

    table_html += "</div>"
    st.markdown(table_html, unsafe_allow_html=True)

    # ── Bar chart ─────────────────────────────────────────────────────────────
    chart_df = pd.DataFrame({
        "Stage": [r[0] for r in funnel_rows],
        "Count": [r[1] for r in funnel_rows],
        "Color": ["#6B7280", "#3B82F6", "#8B5CF6", "#00A651", "#F59E0B", "#EF4444"],
    })
    chart = alt.Chart(chart_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Stage:N", sort=[r[0] for r in funnel_rows], axis=alt.Axis(title=None, labelAngle=-20)),
        y=alt.Y("Count:Q", title="Count"),
        color=alt.Color("Color:N", scale=None, legend=None),
        tooltip=["Stage", "Count"],
    ).properties(height=260)
    st.altair_chart(chart, use_container_width=True)

    # ── Per-person detail table ───────────────────────────────────────────────
    with st.expander("View per-person detail"):
        contact_map = {p["email"].strip().lower(): p for p in contacts_in_range if p.get("email")}
        detail_rows = []
        for email in sorted(contact_emails):
            cp = contact_map.get(email, {})
            np = num_by_email.get(email, {})
            first = np.get("first_name") or cp.get("firstname") or ""
            last  = np.get("last_name")  or cp.get("lastname")  or ""
            detail_rows.append({
                "Name":            f"{first} {last}".strip() or "—",
                "Email":           email,
                "Number":          np.get("number") or "—",
                "Contact Created": _fmt(cp.get("createdate")),
                "Registered At":   _fmt(np.get("registered_at")),
                "Number Created":  _fmt(np.get("account_created_at")),
                "First Login":     _fmt(np.get("ursa_first_login")),
                "First Outbound":  _fmt(np.get("ursa_first_outbound_call")),
                "Second Outbound": _fmt(np.get("ursa_second_outbound_call")),
            })
        detail_df = pd.DataFrame(detail_rows)
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            detail_df.to_csv(index=False),
            f"signup_journey_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
