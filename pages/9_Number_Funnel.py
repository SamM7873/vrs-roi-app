import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import dash_spinner, require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Number Funnel Report",
    "Live VRS numbers: Created → Registered → First Login → First Outbound → Second Outbound",
    section="Analytics",
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _fmt(v):
    dt = _parse(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

# ── date presets ──────────────────────────────────────────────────────────────

PRESETS = [
    "All Time", "Today", "Yesterday",
    "Last 7 Days", "Last 30 Days",
    "This Week (Mon–Sun)", "Last Week",
    "This Month", "Last Month", "Last 3 Months",
    "This Quarter", "Last Quarter",
    "This Year", "Last Year",
    "Custom Range",
]

def _date_range_for_preset(preset):
    today = date.today()
    if preset == "Today":           return today, today
    if preset == "Yesterday":       d = today - timedelta(days=1); return d, d
    if preset == "Last 7 Days":     return today - timedelta(days=6), today
    if preset == "Last 30 Days":    return today - timedelta(days=29), today
    if preset == "This Week (Mon–Sun)": return today - timedelta(days=today.weekday()), today
    if preset == "Last Week":
        s = today - timedelta(days=today.weekday() + 7); return s, s + timedelta(days=6)
    if preset == "This Month":      return today.replace(day=1), today
    if preset == "Last Month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if preset == "Last 3 Months":   return today - timedelta(days=89), today
    if preset == "This Quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q, day=1), today
    if preset == "Last Quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        end = today.replace(month=q, day=1) - timedelta(days=1)
        start = end.replace(month=((end.month - 1) // 3) * 3 + 1, day=1)
        return start, end
    if preset == "This Year":       return today.replace(month=1, day=1), today
    if preset == "Last Year":       return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None, None

# ── filter UI ─────────────────────────────────────────────────────────────────

col_preset, col_from, col_to, col_field, col_usage = st.columns([2, 1, 1, 1.5, 1])
with col_preset:
    preset = st.selectbox("Date range", PRESETS, index=0)
with col_field:
    date_field_label = st.selectbox(
        "Filter baseline by",
        ["Number Created At", "Registered At"],
        index=0,
    )
    date_field = "number_created_at" if date_field_label == "Number Created At" else "registered_at"
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

    raw = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name",
         "number_status", "service_type", "usage_type",
         "registered_at", "number_created_at",
         "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
        progress_label="Fetching number objects",
    )

    # Keep only live VRS numbers, optionally filtered by usage type
    records = []
    for r in raw:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        if usage_filter != "All" and norm(p.get("usage_type") or "") != norm(usage_filter):
            continue
        records.append(p)

    if not records:
        st.warning("No live VRS number records found.")
        st.stop()

    # Build date boundary in UTC
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, 0, 0, 0, tzinfo=timezone.utc)
        fe = datetime(filter_end.year,   filter_end.month,   filter_end.day, 23, 59, 59, tzinfo=timezone.utc)
        def in_range(v):
            dt = _parse(v)
            return dt is not None and fs <= dt <= fe
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
        # Filter baseline: only records where the selected date field is in range
        records = [p for p in records if in_range(p.get(date_field))]
    else:
        def in_range(v):
            return bool(v)
        range_label = "All Time"

    if not records:
        st.warning(f"No records found where {date_field_label} is in the selected date range.")
        st.stop()

    total = len(records)

    # Chronological funnel milestones, in the true order a number progresses.
    # Each stage is a MILESTONE with a timestamp field; a number "reached" a
    # stage if it has that timestamp. We treat it as a true funnel — a record
    # counts at stage k only if it also reached every earlier stage — so the
    # counts can only ever DECREASE. (Counting each field independently caused
    # the old bug: registered_at is sparse while number_created_at is on nearly
    # every record, so the funnel dipped then rebounded to ~100%.)
    funnel_fields = [
        ("Number created at",     "number_created_at"),
        ("Number registered at",  "registered_at"),
        ("Convo first login",     "ursa_first_login"),
        ("Convo first outbound",  "ursa_first_outbound_call"),
        ("Convo second outbound", "ursa_second_outbound_call"),
    ]

    def _has(p, field):
        return _parse(p.get(field)) is not None

    stages = [("Numbers (baseline)", total, "100%")]
    reached = list(records)  # records still "in the funnel"
    for label, field in funnel_fields:
        reached = [p for p in reached if _has(p, field)]
        stages.append((label, len(reached), None))

    stages = [
        (label, n, f"{n / total * 100:.1f}%" if pct is None else pct)
        for label, n, pct in stages
    ]

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Baseline filtered by <strong style="color:#E6F2EC;">{date_field_label}</strong>
  &nbsp;·&nbsp; {total:,} numbers
</div>
""", unsafe_allow_html=True)

    # ── Funnel table ──────────────────────────────────────────────────────────
    table_html = """
<div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:12px;overflow:hidden;margin-bottom:1.5rem;">
  <div style="display:grid;grid-template-columns:1fr 120px 140px;padding:0.6rem 1.25rem;
              background:#e8e4db;border-bottom:1px solid #DDD9CC;">
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">Stage</div>
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">Count</div>
    <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:#5a6a5a;">% of Total</div>
  </div>
"""
    for i, (label, n, pct_val) in enumerate(stages):
        bg = "#F4F1E8" if i % 2 == 0 else "#EFECE3"
        color = "#1F2937" if n > 0 else "#9CA3AF"
        table_html += f"""
  <div style="display:grid;grid-template-columns:1fr 120px 140px;padding:0.75rem 1.25rem;
              background:{bg};border-bottom:1px solid #DDD9CC;">
    <div style="font-size:0.9rem;color:#374151;font-weight:500;">{label}</div>
    <div style="font-size:0.9rem;font-weight:700;color:{color};font-variant-numeric:tabular-nums;">{n:,}</div>
    <div style="font-size:0.9rem;color:#6B7280;font-variant-numeric:tabular-nums;">{pct_val if n > 0 else "—"}</div>
  </div>"""
    table_html += "</div>"
    st.markdown(table_html, unsafe_allow_html=True)

    # ── Bar chart ─────────────────────────────────────────────────────────────
    chart_df = pd.DataFrame({
        "Stage": [s[0] for s in stages],
        "Count": [s[1] for s in stages],
        "Color": ["#6B7280", "#3B82F6", "#8B5CF6", "#00A651", "#F59E0B", "#EF4444"],
    })
    chart = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Stage:N", sort=[s[0] for s in stages], axis=alt.Axis(title=None, labelAngle=-20)),
            y=alt.Y("Count:Q", title="Count"),
            color=alt.Color("Color:N", scale=None, legend=None),
            tooltip=["Stage", "Count"],
        )
        .properties(height=260)
    )
    st.altair_chart(chart, use_container_width=True)

    # ── Detail table ──────────────────────────────────────────────────────────
    with st.expander("View per-number detail"):
        rows = [{
            "Name":            f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email":           p.get("email") or "—",
            "Number":          p.get("number") or "—",
            "Registered At":   _fmt(p.get("registered_at")),
            "Number Created":  _fmt(p.get("number_created_at")),
            "First Login":     _fmt(p.get("ursa_first_login")),
            "First Outbound":  _fmt(p.get("ursa_first_outbound_call")),
            "Second Outbound": _fmt(p.get("ursa_second_outbound_call")),
        } for p in records]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            df.to_csv(index=False),
            f"number_funnel_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )
        from utils import pdf_download_button
        _pdf_metrics = [(str(s[0]), f"{s[1]:,}") for s in stages][:4]
        _pdf_charts = [{"data": chart_df[["Stage", "Count"]], "kind": "bar",
                        "x": "Stage", "y": "Count", "title": "Number funnel"}]
        pdf_download_button(df, "number_funnel.pdf", "Number Funnel",
                            metrics=_pdf_metrics, charts=_pdf_charts, key="numfun")

report_header_close()
