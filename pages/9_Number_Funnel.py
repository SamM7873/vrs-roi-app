import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Number Funnel Report",
    "Live VRS numbers: Registered → Created → First Login → First Outbound → Second Outbound",
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
    dt = _parse(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

# ── date presets ──────────────────────────────────────────────────────────────

def _date_range_for_preset(preset):
    today = date.today()
    if preset == "Today":
        return today, today
    if preset == "Yesterday":
        y = today - timedelta(days=1); return y, y
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
        q = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q, day=1), today
    if preset == "Last Quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        lq_end   = today.replace(month=q, day=1) - timedelta(days=1)
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

col_preset, col_from, col_to, col_field, col_usage = st.columns([2, 1, 1, 1, 1])
with col_preset:
    preset = st.selectbox("Date range", PRESETS, index=0)
with col_field:
    date_field = st.selectbox("Filter by", ["registered_at", "number_created_at", "Both"], index=0)
with col_usage:
    usage_filter = st.selectbox("Usage Type", ["All", "Personal", "Business", "Other"], index=0)

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

if st.button("Run Number Funnel", use_container_width=False):

    with st.spinner("Loading number objects..."):
        records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name",
             "number_status", "service_type", "usage_type",
             "registered_at", "number_created_at",
             "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
            progress_label="Fetching number objects",
        )

    # Filter to live VRS only + usage type
    records = [
        r for r in records
        if norm(r.get("properties", {}).get("service_type") or "") == "vrs"
        and norm(r.get("properties", {}).get("number_status") or "") == "live"
        and (usage_filter == "All" or norm(r.get("properties", {}).get("usage_type") or "") == norm(usage_filter))
    ]

    if not records:
        st.warning("No live VRS number records found.")
        st.stop()

    # Merge top-level createdAt as fallback for number_created_at
    for r in records:
        p = r.get("properties", {})
        if not p.get("number_created_at"):
            p["number_created_at"] = r.get("createdAt") or ""

    # Apply date filter based on chosen field
    tz_utc = timezone.utc
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, tzinfo=tz_utc)
        fe = datetime(filter_end.year, filter_end.month, filter_end.day, 23, 59, 59, tzinfo=tz_utc)
        def _in_range(v):
            dt = _parse(v)
            return dt is not None and fs <= dt <= fe
        if date_field == "Both":
            records = [r for r in records if
                       _in_range(r.get("properties", {}).get("registered_at")) or
                       _in_range(r.get("properties", {}).get("number_created_at"))]
        else:
            records = [r for r in records if _in_range(r.get("properties", {}).get(date_field))]

    total = len(records)
    if total == 0:
        st.warning("No records match the selected date range.")
        st.stop()

    def _count(field):
        return sum(1 for r in records if r.get("properties", {}).get(field))

    has_registered   = _count("registered_at")
    has_created      = _count("number_created_at")
    has_login        = _count("ursa_first_login")
    has_outbound     = _count("ursa_first_outbound_call")
    has_2nd_outbound = _count("ursa_second_outbound_call")

    def pct(n):
        return f"{n / total * 100:.1f}%" if total else "—"

    # ── Date range label ──────────────────────────────────────────────────────
    if filter_start and filter_end:
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
    else:
        range_label = "All Time"

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Filtered by <strong style="color:#E6F2EC;">{"registered_at or number_created_at" if date_field == "Both" else date_field}</strong>
  &nbsp;·&nbsp; {total:,} numbers
</div>
""", unsafe_allow_html=True)

    # ── Funnel table ──────────────────────────────────────────────────────────
    funnel_rows = [
        ("Numbers (baseline)",     total,           "100%"),
        ("Number registered at",   has_registered,  pct(has_registered)),
        ("Number created at",      has_created,     pct(has_created)),
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
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">% of Total</div>
  </div>
"""
    for i, (stage, count, pct_val) in enumerate(funnel_rows):
        bg = "#F4F1E8" if i % 2 == 0 else "#EFECE3"
        count_color = "#1F2937" if count > 0 else "#9CA3AF"
        table_html += f"""
  <div style="display:grid;grid-template-columns:1fr 120px 140px;padding:0.75rem 1.25rem;
              background:{bg};border-bottom:1px solid #DDD9CC;">
    <div style="font-size:0.9rem;color:#374151;font-weight:500;">{stage}</div>
    <div style="font-size:0.9rem;font-weight:700;color:{count_color};font-variant-numeric:tabular-nums;">{count:,}</div>
    <div style="font-size:0.9rem;color:#6B7280;font-variant-numeric:tabular-nums;">{pct_val if count > 0 else "—"}</div>
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

    # ── Detail table ──────────────────────────────────────────────────────────
    with st.expander("View per-number detail"):
        detail_rows = []
        for r in records:
            p = r.get("properties", {})
            detail_rows.append({
                "Name":            f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
                "Email":           p.get("email") or "—",
                "Number":          p.get("number") or "—",
                "Registered At":   _fmt(p.get("registered_at")),
                "Number Created":  _fmt(p.get("number_created_at")),
                "First Login":     _fmt(p.get("ursa_first_login")),
                "First Outbound":  _fmt(p.get("ursa_first_outbound_call")),
                "Second Outbound": _fmt(p.get("ursa_second_outbound_call")),
            })
        detail_df = pd.DataFrame(detail_rows)
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            detail_df.to_csv(index=False),
            f"number_funnel_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
