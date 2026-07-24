import streamlit as st
import pandas as pd
import altair as alt
import time
import json
import streamlit.components.v1 as components
from datetime import datetime, timezone, timedelta, date
from utils import dash_spinner, require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close, save_report, load_report

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

def _lang(v):
    n = norm(v)
    if n in ("en", "english"):
        return "English"
    if n in ("es", "spanish", "español", "espanol"):
        return "Spanish"
    return (v or "").strip().title() or "—"

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

# Baseline is always Number Created At; language filter removed. Date range and
# Usage Type (Personal / Org / All) are the controls.
date_field_label = "Number Created At"
date_field = "number_created_at"
lang_filter = "All"

col_preset, col_from, col_to, col_usage = st.columns([2, 1, 1, 1.2])
with col_preset:
    preset = st.selectbox("Date range", PRESETS, index=0)
with col_usage:
    usage_filter = st.selectbox("Usage Type", ["All", "Personal", "Org"], index=0,
                                help="Analyze Personal numbers only, Organization (business) numbers only, or all together.")

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

# A stable key for this exact filter combination. The same filters reload the
# saved result from disk instead of re-fetching 45k records every time.
_key = f"number_funnel_v4_{preset}_{date_field}_{usage_filter}_{lang_filter}_{filter_start}_{filter_end}"

run = st.button("Run Number Funnel", use_container_width=False)

# Load a previously saved run for these filters (disk survives app restarts).
payload = load_report(_key)

if run:
    raw = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name",
         "number_status", "service_type", "usage_type", "language_preference",
         "registered_at", "number_created_at",
         "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
        progress_label="Fetching number objects",
    )

    # Keep only live VRS numbers, optionally filtered by usage type and language
    records = []
    for r in raw:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") != "live":
            continue
        if usage_filter != "All":
            _ut = norm(p.get("usage_type") or "")
            if usage_filter == "Personal" and _ut != "personal":
                continue
            if usage_filter == "Org" and _ut not in ("business", "org", "organization", "organisation"):
                continue
        if lang_filter != "All":
            _lg = _lang(p.get("language_preference"))
            if lang_filter in ("English", "Spanish"):
                if _lg != lang_filter:
                    continue
            elif _lg in ("English", "Spanish"):   # "Other" = anything not EN/ES
                continue
        records.append(p)

    if not records:
        st.warning("No live VRS number records found.")
        st.stop()

    # Build date boundary in Central time (to match HubSpot, which filters in
    # CDT/CST). The picked dates are treated as Central-local day boundaries,
    # then compared against the UTC timestamps returned by the API. Using UTC
    # midnight here shifted the window ~5h earlier and over-counted the baseline.
    def _central_tz(mm):
        # CDT (UTC-5) roughly Mar–Nov, CST (UTC-6) otherwise — matches the sidebar sync widget.
        return timezone(timedelta(hours=-5 if 3 <= mm <= 11 else -6))
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, 0, 0, 0, tzinfo=_central_tz(filter_start.month))
        fe = datetime(filter_end.year,   filter_end.month,   filter_end.day, 23, 59, 59, tzinfo=_central_tz(filter_end.month))
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
        ("Number registered at",  "registered_at"),
        ("Number created at",     "number_created_at"),
        ("Convo first login",     "ursa_first_login"),
        ("Convo first outbound",  "ursa_first_outbound_call"),
        ("Convo second outbound", "ursa_second_outbound_call"),
    ]

    def _has(p, field):
        return _parse(p.get(field)) is not None

    # Registration comes first in the real-world flow (a person registers, then a
    # number is provisioned). Count each of the first two milestones against the
    # baseline independently so the earlier stage isn't artificially capped by the
    # later one; the Convo stages remain a strict cohort funnel from "created".
    stages = [("Numbers (baseline)", total, "100%")]
    stages.append(("Number registered at", sum(1 for p in records if _has(p, "registered_at")), None))
    reached = [p for p in records if _has(p, "number_created_at")]
    stages.append(("Number created at", len(reached), None))
    for label, field in funnel_fields[2:]:
        reached = [p for p in reached if _has(p, field)]
        stages.append((label, len(reached), None))

    stages = [
        (label, n, f"{n / total * 100:.1f}%" if pct is None else pct)
        for label, n, pct in stages
    ]

    # Pre-build the per-number detail rows so the whole report is picklable.
    detail_rows = [{
        "Name":            f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
        "Email":           p.get("email") or "—",
        "Number":          p.get("number") or "—",
        "Language":        _lang(p.get("language_preference")),
        "Registered At":   _fmt(p.get("registered_at")),
        "Number Created":  _fmt(p.get("number_created_at")),
        "First Login":     _fmt(p.get("ursa_first_login")),
        "First Outbound":  _fmt(p.get("ursa_first_outbound_call")),
        "Second Outbound": _fmt(p.get("ursa_second_outbound_call")),
    } for p in records]

    payload = {"stages": stages, "detail_rows": detail_rows, "total": total,
               "range_label": range_label, "date_field_label": date_field_label}
    save_report(_key, payload)
    payload = load_report(_key)  # reload so we pick up the saved_at timestamp

# ── render (from the saved payload) ───────────────────────────────────────────
if payload is None:
    st.info("Click **Run Number Funnel** to build the report. Your result is saved "
            "automatically and will reload here next time — no need to start over.")
    report_header_close()
    st.stop()

stages           = payload["stages"]
detail_rows      = payload["detail_rows"]
total            = payload["total"]
range_label      = payload["range_label"]
date_field_label = payload["date_field_label"]
_saved_at        = payload.get("saved_at")

if _saved_at:
    _a = int(time.time() - _saved_at)
    _ago = "just now" if _a < 90 else (f"{_a // 60} min ago" if _a < 3600 else f"{_a // 3600} h ago")
    st.caption(f"📌 Saved result · refreshed {_ago} · click **Run Number Funnel** to rebuild.")

st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Baseline filtered by <strong style="color:#E6F2EC;">{date_field_label}</strong>
  &nbsp;·&nbsp; Usage: <strong style="color:#E6F2EC;">{usage_filter}</strong>
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

# ── Language preference breakdown of the baseline ──────────────────────────
_lang_counts = pd.Series([r.get("Language", "—") for r in detail_rows]).value_counts()
if not _lang_counts.empty:
    _lang_df = _lang_counts.rename_axis("Language").reset_index(name="Numbers")
    _lang_df["% of baseline"] = (_lang_df["Numbers"] / _lang_df["Numbers"].sum() * 100).round(1)
    with st.expander(f"🌐 Language preference breakdown ({len(_lang_counts)} languages)"):
        st.dataframe(_lang_df, use_container_width=True, hide_index=True,
                     column_config={"% of baseline": st.column_config.NumberColumn("% of baseline", format="%.1f%%")})

# ── Numbers created in-window but missing a registration date ──────────────
_missing_reg = [r for r in detail_rows if r.get("Registered At") in (None, "—")]
if _missing_reg:
    st.markdown(
        f"<div style='font-size:0.8rem;color:#b45309;margin:0.25rem 0 0.5rem;'>"
        f"⚠️ <strong>{len(_missing_reg)}</strong> number(s) were created in this window "
        f"but have no <em>Registered At</em> date. A missing registration date typically "
        f"means the number was <strong>added manually by the Customer Support team</strong> — "
        f"e.g. a number-change request or a new Spanish number — so it never went through the "
        f"normal self-registration flow:</div>",
        unsafe_allow_html=True,
    )
    _mdf = pd.DataFrame(_missing_reg)[["Name", "Email", "Number", "Language", "Number Created", "Registered At"]]
    st.dataframe(_mdf, use_container_width=True, hide_index=True)

# ── Funnel visualizations ──────────────────────────────────────────────────
_stage_order = [s[0] for s in stages]
_colors = ["#6B7280", "#3B82F6", "#8B5CF6", "#00A651", "#F59E0B", "#EF4444"]
chart_df = pd.DataFrame({
    "Stage": _stage_order,
    "Count": [s[1] for s in stages],
    "Pct": [s[1] / total * 100 if total else 0 for s in stages],
    "Color": _colors[:len(stages)],
})
chart_df["Label"] = chart_df.apply(lambda r: f"{int(r['Count']):,}  ({r['Pct']:.0f}%)", axis=1)
_maxc = float(chart_df["Count"].max()) or 1.0
# centered band for the classic tapering funnel: x from (max-count)/2 to (max+count)/2
chart_df["x0"] = (_maxc - chart_df["Count"]) / 2
chart_df["x1"] = (_maxc + chart_df["Count"]) / 2

st.markdown("##### Funnel")
_base = alt.Chart(chart_df)

# 1) Highcharts funnel with a neck (matches the classic funnel look)
_hc_data = [{"name": s[0], "y": int(s[1]),
             "pct": round(s[1] / total * 100, 1) if total else 0} for s in stages]
_hc_colors = _colors[:len(stages)]
_hc_html = f"""
<div id="funnel_container" style="width:100%;height:460px;"></div>
<script src="https://code.highcharts.com/highcharts.js"></script>
<script src="https://code.highcharts.com/modules/funnel.js"></script>
<script>
Highcharts.chart('funnel_container', {{
  chart: {{ type: 'funnel', backgroundColor: 'transparent',
            style: {{ fontFamily: 'Inter, -apple-system, Segoe UI, sans-serif' }} }},
  title: {{ text: null }},
  colors: {json.dumps(_hc_colors)},
  plotOptions: {{
    series: {{
      dataLabels: {{
        enabled: true,
        format: '<b>{{point.name}}</b><br>{{point.y:,.0f}} ({{point.pct}}%)',
        softConnector: true,
        style: {{ fontSize: '13px', color: '#1F2937', textOutline: 'none' }}
      }},
      center: ['40%', '50%'],
      neckWidth: '0%',
      neckHeight: '0%',
      width: '80%'
    }}
  }},
  legend: {{ enabled: false }},
  credits: {{ enabled: false }},
  series: [{{ name: 'Numbers', data: {json.dumps(_hc_data)} }}],
  responsive: {{ rules: [{{
    condition: {{ maxWidth: 500 }},
    chartOptions: {{ plotOptions: {{ series: {{
      dataLabels: {{ inside: true }}, center: ['50%', '50%'], width: '100%' }} }} }}
  }}] }}
}});
</script>
"""
components.html(_hc_html, height=480)

with st.expander("Other chart views (horizontal bars · vertical bars)"):
    # 2) Horizontal bar funnel (left-aligned, longest at top)
    _hbar = _base.mark_bar(cornerRadius=4).encode(
        y=alt.Y("Stage:N", sort=_stage_order, axis=alt.Axis(title=None, labelLimit=200)),
        x=alt.X("Count:Q", title="Count"),
        color=alt.Color("Color:N", scale=None, legend=None),
        tooltip=["Stage", alt.Tooltip("Count:Q", format=","), alt.Tooltip("Pct:Q", format=".1f", title="% of total")],
    ).properties(height=50 * len(stages))
    _hlabel = _base.mark_text(align="left", dx=5, color="#374151", fontWeight="bold").encode(
        y=alt.Y("Stage:N", sort=_stage_order), x="Count:Q", text="Label:N")
    st.altair_chart(_hbar + _hlabel, use_container_width=True)

    # 3) Vertical bars
    _vbar = _base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Stage:N", sort=_stage_order, axis=alt.Axis(title=None, labelAngle=-20)),
        y=alt.Y("Count:Q", title="Count"),
        color=alt.Color("Color:N", scale=None, legend=None),
        tooltip=["Stage", alt.Tooltip("Count:Q", format=",")],
    ).properties(height=260)
    st.altair_chart(_vbar, use_container_width=True)

# ── Detail table ──────────────────────────────────────────────────────────
with st.expander("View per-number detail"):
    df = pd.DataFrame(detail_rows)
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
