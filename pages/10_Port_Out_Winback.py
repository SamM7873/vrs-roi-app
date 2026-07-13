import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict
from utils import dash_spinner, require_auth, list_all, fetch_all, norm, to_float, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Port-Out Winback / Retention",
    "Deactivated VRS numbers ported out — usage history for winback targeting",
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

def _tenure_from_days(days):
    years  = days // 365
    months = (days % 365) // 30
    rem    = (days % 365) % 30
    if years >= 1:
        return f"{years}y {months}m"
    if months >= 1:
        return f"{months}m {rem}d"
    return f"{days}d"

def _tenure(created, deleted):
    dc = _parse(created)
    dd = _parse(deleted)
    if not dc or not dd:
        return "—"
    days = (dd - dc).days
    if days < 0:
        return "—"
    return _tenure_from_days(days)

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

col_preset, col_from, col_to, col_field, col_reason = st.columns([2, 1, 1, 1.4, 1.6])
with col_preset:
    preset = st.selectbox("Date range", PRESETS, index=0)
with col_field:
    date_field_label = st.selectbox(
        "Filter by",
        ["Number Deleted At", "Number Created At"],
        index=0,
    )
    date_field = "number_deleted_at" if date_field_label == "Number Deleted At" else "number_created_at"
with col_reason:
    reason_filter = st.selectbox(
        "Deleted Reason",
        ["CUSTOMER_PORTED_OUT", "All Reasons"],
        index=0,
    )

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

if st.button("Run Port-Out Winback Report", use_container_width=False):

    raw = list_all(
        "2-40974683",
        ["number", "email", "first_name", "last_name",
         "number_status", "service_type", "usage_type",
         "bandwidth_order_type", "deleted_reason",
         "number_created_at", "number_deleted_at"],
        progress_label="Fetching number objects",
    )

    # Filter: deactivated VRS port-outs
    records = []
    for r in raw:
        p = r.get("properties", {})
        if norm(p.get("service_type") or "") != "vrs":
            continue
        if norm(p.get("number_status") or "") not in ("deactivated", "deleted", "inactive", "cancelled"):
            continue
        bw = norm(p.get("bandwidth_order_type") or "")
        if "port" not in bw:
            continue
        records.append(p)

    if not records:
        # Debug: show what values are present so we can tune the filter
        vrs_all = [r for r in raw if norm(r.get("properties", {}).get("service_type") or "") == "vrs"]
        statuses = list({r.get("properties", {}).get("number_status") or "—" for r in vrs_all})
        bw_types = list({r.get("properties", {}).get("bandwidth_order_type") or "—" for r in vrs_all})
        st.warning("No matching port-out records found.")
        st.markdown(f"**VRS records pulled:** {len(vrs_all):,}")
        st.markdown(f"**number_status values seen:** `{', '.join(sorted(statuses))}`")
        st.markdown(f"**bandwidth_order_type values seen:** `{', '.join(sorted(bw_types))}`")
        st.stop()

    # Apply date filter to baseline
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, 0, 0, 0, tzinfo=timezone.utc)
        fe = datetime(filter_end.year,   filter_end.month,   filter_end.day, 23, 59, 59, tzinfo=timezone.utc)
        def in_range(v):
            dt = _parse(v)
            return dt is not None and fs <= dt <= fe
        records = [p for p in records if in_range(p.get(date_field))]
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
    else:
        range_label = "All Time"

    # Apply deleted reason filter
    if reason_filter != "All Reasons":
        records = [p for p in records if norm(p.get("deleted_reason") or "") == norm(reason_filter)]

    if not records:
        st.warning(f"No records found for the selected filters.")
        st.stop()

    total = len(records)
    all_nums = [str(p.get("number") or "").strip() for p in records if p.get("number")]

    # ── Pull monthly usage for matched numbers ────────────────────────────────
    with dash_spinner(f"Fetching monthly usage for {total:,} numbers..."):
        monthly_raw = []
        for i in range(0, len(all_nums), 100):
            chunk = all_nums[i:i + 100]
            monthly_raw.extend(fetch_all(
                "2-46246179",
                ["number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"],
                filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                ]}]
            ))

    # Aggregate monthly usage per number
    num_monthly = defaultdict(list)
    for r in monthly_raw:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        if not num:
            continue
        num_monthly[num].append({
            "month":         p.get("month_date") or "",
            "usage_minutes": to_float(p.get("usage_minutes")) or 0.0,
            "ursa_minutes":  to_float(p.get("ursa_minutes"))  or 0.0,
            "cfz_minutes":   to_float(p.get("cfz_minutes"))   or 0.0,
        })

    # Compute totals per number
    def _totals(num):
        rows = num_monthly.get(num, [])
        usage  = sum(r["usage_minutes"] for r in rows)
        ursa   = sum(r["ursa_minutes"]  for r in rows)
        cfz    = sum(r["cfz_minutes"]   for r in rows)
        months = len(rows)
        avg    = usage / months if months else 0.0
        return usage, ursa, cfz, months, avg

    # ── Summary tiles ─────────────────────────────────────────────────────────
    total_usage = sum(_totals(str(p.get("number") or ""))[0] for p in records)
    total_ursa  = sum(_totals(str(p.get("number") or ""))[1] for p in records)
    total_cfz   = sum(_totals(str(p.get("number") or ""))[2] for p in records)
    with_history = sum(1 for p in records if num_monthly.get(str(p.get("number") or "")))

    tenure_days_list = []
    for p in records:
        dc = _parse(p.get("number_created_at"))
        dd = _parse(p.get("number_deleted_at"))
        if dc and dd and (dd - dc).days >= 0:
            tenure_days_list.append((dd - dc).days)
    avg_tenure_days = sum(tenure_days_list) / len(tenure_days_list) if tenure_days_list else 0
    avg_tenure_str  = _tenure_from_days(int(avg_tenure_days)) if tenure_days_list else "—"

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; Filtered by <strong style="color:#E6F2EC;">{date_field_label}</strong>
  &nbsp;·&nbsp; Reason: <strong style="color:#E6F2EC;">{reason_filter}</strong>
  &nbsp;·&nbsp; {total:,} port-out numbers
</div>
""", unsafe_allow_html=True)

    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.85rem;margin:0.5rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Port-Out Numbers</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Avg Tenure</div>
    <div style="font-size:1.4rem;font-weight:800;color:#F59E0B;">{avg_tenure_str}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">With Usage History</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{with_history:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total Usage (min)</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total_usage:,.0f}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total URSA (min)</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;">{total_ursa:,.0f}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total CfZ (min)</div>
    <div style="font-size:1.4rem;font-weight:800;color:#8B5CF6;">{total_cfz:,.0f}</div>
  </div>
</div>""", unsafe_allow_html=True)

    # ── Detail table ──────────────────────────────────────────────────────────
    detail_rows = []
    for p in records:
        num = str(p.get("number") or "").strip()
        usage, ursa, cfz, months, avg = _totals(num)
        dc = _parse(p.get("number_created_at"))
        dd = _parse(p.get("number_deleted_at"))
        tenure_days = (dd - dc).days if dc and dd and (dd - dc).days >= 0 else None
        detail_rows.append({
            "Number":           num or "—",
            "Name":             f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email":            p.get("email") or "—",
            "Usage Type":       (p.get("usage_type") or "—").title(),
            "Deleted Reason":   p.get("deleted_reason") or "—",
            "Created At":       _fmt(p.get("number_created_at")),
            "Deleted At":       _fmt(p.get("number_deleted_at")),
            "Tenure":           _tenure(p.get("number_created_at"), p.get("number_deleted_at")),
            "Tenure (days)":    tenure_days if tenure_days is not None else 0,
            "History Months":   months,
            "Total Usage (min)":  round(usage, 1),
            "Total URSA (min)":   round(ursa, 1),
            "Total CfZ (min)":    round(cfz, 1),
            "Avg Monthly (min)":  round(avg, 1),
        })

    df = pd.DataFrame(detail_rows).sort_values("Total Usage (min)", ascending=False)

    # ── Monthly usage chart (top 20 by usage) ────────────────────────────────
    if monthly_raw:
        # Build a month-level aggregate across all port-out numbers
        month_agg = defaultdict(lambda: {"usage_minutes": 0.0, "ursa_minutes": 0.0, "cfz_minutes": 0.0})
        matched_nums = set(all_nums)
        for r in monthly_raw:
            p2 = r.get("properties", {})
            num = str(p2.get("number") or "").strip()
            if num not in matched_nums:
                continue
            mk = (p2.get("month_date") or "")[:7]  # YYYY-MM
            if not mk:
                continue
            month_agg[mk]["usage_minutes"] += to_float(p2.get("usage_minutes")) or 0.0
            month_agg[mk]["ursa_minutes"]  += to_float(p2.get("ursa_minutes"))  or 0.0
            month_agg[mk]["cfz_minutes"]   += to_float(p2.get("cfz_minutes"))   or 0.0

        if month_agg:
            months_sorted = sorted(month_agg.keys())
            chart_rows = []
            for mk in months_sorted:
                v = month_agg[mk]
                chart_rows += [
                    {"Month": mk, "Type": "Usage (total)", "Minutes": round(v["usage_minutes"], 1)},
                    {"Month": mk, "Type": "URSA",          "Minutes": round(v["ursa_minutes"],  1)},
                    {"Month": mk, "Type": "CfZ",           "Minutes": round(v["cfz_minutes"],   1)},
                ]
            chart_df = pd.DataFrame(chart_rows)
            st.markdown("#### Monthly Usage — All Port-Out Numbers")
            line = alt.Chart(chart_df).mark_line(point=True).encode(
                x=alt.X("Month:N", sort=months_sorted, axis=alt.Axis(title=None, labelAngle=-30)),
                y=alt.Y("Minutes:Q", title="Minutes"),
                color=alt.Color("Type:N", scale=alt.Scale(
                    domain=["Usage (total)", "URSA", "CfZ"],
                    range=["#3B82F6", "#00A651", "#8B5CF6"],
                )),
                tooltip=["Month", "Type", "Minutes"],
            ).properties(height=260)
            st.altair_chart(line, use_container_width=True)

    # ── Deleted reason breakdown ───────────────────────────────────────────────
    reason_counts = df["Deleted Reason"].value_counts().reset_index()
    reason_counts.columns = ["Deleted Reason", "Count"]
    if len(reason_counts) > 1 or reason_counts.iloc[0]["Deleted Reason"] != "—":
        st.markdown("#### Deleted Reason Breakdown")
        bar = alt.Chart(reason_counts).mark_bar(color="#EF4444", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q", title="Count"),
            y=alt.Y("Deleted Reason:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
            tooltip=["Deleted Reason", "Count"],
        ).properties(height=max(120, len(reason_counts) * 36))
        st.altair_chart(bar, use_container_width=True)

    # ── Data table ────────────────────────────────────────────────────────────
    st.markdown(f"#### {total:,} Port-Out Numbers — sorted by usage")
    st.dataframe(df.reset_index(drop=True), use_container_width=True, hide_index=True)

    # ── Per-number monthly detail ──────────────────────────────────────────────
    with st.expander("View monthly usage per number"):
        monthly_rows = []
        for r in monthly_raw:
            p2 = r.get("properties", {})
            num = str(p2.get("number") or "").strip()
            if num not in set(all_nums):
                continue
            monthly_rows.append({
                "Number":         num,
                "Month":          (p2.get("month_date") or "")[:7],
                "Usage (min)":    round(to_float(p2.get("usage_minutes")) or 0.0, 1),
                "URSA (min)":     round(to_float(p2.get("ursa_minutes"))  or 0.0, 1),
                "CfZ (min)":      round(to_float(p2.get("cfz_minutes"))   or 0.0, 1),
            })
        if monthly_rows:
            monthly_df = pd.DataFrame(monthly_rows).sort_values(["Number", "Month"])
            st.dataframe(monthly_df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download CSV",
        df.to_csv(index=False),
        f"port_out_winback_{datetime.now().strftime('%Y%m%d')}.csv",
        "text/csv",
    )

report_header_close()
