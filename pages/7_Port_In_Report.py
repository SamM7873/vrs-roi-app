import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import dash_spinner, require_auth, list_all, fetch_all, norm, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Port-In Report", "Numbers ported in — matched from registration to number object", section="Analytics")

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

col_preset, col_from, col_to = st.columns([2, 1, 1])
with col_preset:
    preset = st.selectbox("Date range (Registered At)", PRESETS, index=0)

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

if st.button("Run Port-In Report", use_container_width=False):

    # Step 1: pull registrations where registration_type = port_in
    reg_records = list_all(
        "2-58833629",
        ["registration_type", "number", "email", "first_name", "last_name",
         "portin_status", "submitted_at", "registered_at", "state",
         "lex_verification_status", "urd_status", "is_cancelled"],
        progress_label="Fetching registration records",
    )

    port_in_regs = [
        r for r in reg_records
        if "port" in (r.get("properties", {}).get("registration_type") or "").lower()
    ]

    if not port_in_regs:
        st.warning("No port-in registration records found.")
        st.stop()

    # Apply date filter on registered_at
    if filter_start and filter_end:
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, 0, 0, 0, tzinfo=timezone.utc)
        fe = datetime(filter_end.year,   filter_end.month,   filter_end.day, 23, 59, 59, tzinfo=timezone.utc)
        def in_range(v):
            dt = _parse(v)
            return dt is not None and fs <= dt <= fe
        port_in_regs = [r for r in port_in_regs if in_range(r.get("properties", {}).get("registered_at"))]
        range_label = f"{filter_start.strftime('%b %d')}–{filter_end.strftime('%b %d, %Y')}"
    else:
        range_label = "All Time"

    if not port_in_regs:
        st.warning("No port-in registrations found in the selected date range.")
        st.stop()

    # Build lookup: number → registration properties
    reg_by_number = {}
    for r in port_in_regs:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        if num:
            reg_by_number[num] = p

    port_in_numbers = list(reg_by_number.keys())

    # Step 2: fetch number objects matching those numbers
    with dash_spinner(f"Fetching number objects for {len(port_in_numbers):,} port-in numbers..."):
        num_records_raw = []
        for i in range(0, len(port_in_numbers), 100):
            chunk = port_in_numbers[i:i + 100]
            num_records_raw.extend(fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name",
                 "number_status", "service_type", "usage_type",
                 "bandwidth_order_type", "number_created_at", "registered_at",
                 "ursa_first_login", "ursa_first_outbound_call"],
                filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                ]}]
            ))

    # Index number objects by number
    num_obj_by_number = {}
    for r in num_records_raw:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        if num:
            num_obj_by_number[num] = p

    # Step 3: build detail rows — registration data + number object data
    rows = []
    for num in port_in_numbers:
        rp  = reg_by_number.get(num, {})
        np  = num_obj_by_number.get(num, {})

        cancelled  = str(rp.get("is_cancelled") or "false").lower() == "true"
        lex        = rp.get("lex_verification_status") or ""
        urd        = rp.get("urd_status") or ""
        lex_done   = lex in ("automatic_success", "manual_success")
        urd_done   = urd == "completed"
        num_status = (np.get("number_status") or "—").title()

        rows.append({
            "Number":          num or "—",
            "Name":            f"{rp.get('first_name') or np.get('first_name') or ''} {rp.get('last_name') or np.get('last_name') or ''}".strip() or "—",
            "Email":           rp.get("email") or np.get("email") or "—",
            "State":           rp.get("state") or "—",
            "Port-In Status":  (rp.get("portin_status") or "—"),
            "Number Status":   num_status,
            "Usage Type":      (np.get("usage_type") or "—").title(),
            "LEX Status":      lex or "—",
            "URD Status":      urd or "—",
            "Submitted":       _fmt(rp.get("submitted_at")),
            "Registered At":   _fmt(rp.get("registered_at")),
            "Number Created":  _fmt(np.get("number_created_at")),
            "First Login":     _fmt(np.get("ursa_first_login")),
            "First Outbound":  _fmt(np.get("ursa_first_outbound_call")),
            "In Number Object": bool(np),
            "Cancelled":       cancelled,
            "Active":          lex_done and urd_done and not cancelled,
        })

    df = pd.DataFrame(rows)
    total      = len(df)
    matched    = df["In Number Object"].sum()
    completed  = df["Active"].sum()
    cancelled_ = df["Cancelled"].sum()
    in_prog    = total - int(completed) - int(cancelled_)

    st.markdown(f"""
<div style="font-size:0.8rem;color:#9dc8b0;margin-bottom:1rem;">
  Snapshot: <strong style="color:#E6F2EC;">{range_label}</strong>
  &nbsp;·&nbsp; {total:,} port-in registrations
</div>
""", unsafe_allow_html=True)

    # ── Summary tiles ──────────────────────────────────────────────────────────
    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:0.5rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total Port-Ins</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Matched in Number Object</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{int(matched):,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Completed / Active</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;">{int(completed):,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">In Progress</div>
    <div style="font-size:1.4rem;font-weight:800;color:#F59E0B;">{in_prog:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #FEE2E2;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#EF4444;margin-bottom:0.25rem;">Cancelled</div>
    <div style="font-size:1.4rem;font-weight:800;color:#EF4444;">{int(cancelled_):,}</div>
  </div>
</div>""", unsafe_allow_html=True)

    # ── Port-In Status breakdown chart ────────────────────────────────────────
    status_counts = df[df["Port-In Status"] != "—"].groupby("Port-In Status").size().reset_index(name="Count").sort_values("Count", ascending=False)
    if not status_counts.empty:
        st.markdown("#### Port-In Status Breakdown")
        bar = alt.Chart(status_counts).mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q", title="Count"),
            y=alt.Y("Port-In Status:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
            tooltip=["Port-In Status", "Count"],
        ).properties(height=max(120, len(status_counts) * 32))
        st.altair_chart(bar, use_container_width=True)

    # ── Number Status breakdown chart ─────────────────────────────────────────
    ns_counts = df[df["In Number Object"]].groupby("Number Status").size().reset_index(name="Count").sort_values("Count", ascending=False)
    if not ns_counts.empty:
        st.markdown("#### Number Object Status Breakdown")
        bar2 = alt.Chart(ns_counts).mark_bar(color="#00A651", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q", title="Count"),
            y=alt.Y("Number Status:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
            tooltip=["Number Status", "Count"],
        ).properties(height=max(120, len(ns_counts) * 32))
        st.altair_chart(bar2, use_container_width=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    display_cols = ["Number", "Name", "Email", "State", "Port-In Status", "Number Status",
                    "LEX Status", "URD Status", "Usage Type",
                    "Submitted", "Registered At", "Number Created", "First Login", "First Outbound"]

    tab_active, tab_progress, tab_cancelled, tab_all = st.tabs(["Active", "In Progress", "Cancelled", "All"])

    with tab_active:
        a_df = df[df["Active"]][display_cols].reset_index(drop=True)
        st.markdown(f"**{len(a_df):,} active port-ins**")
        st.dataframe(a_df, use_container_width=True, hide_index=True)

    with tab_progress:
        p_df = df[~df["Active"] & ~df["Cancelled"]][display_cols].reset_index(drop=True)
        st.markdown(f"**{len(p_df):,} port-in(s) in progress**")
        st.dataframe(p_df, use_container_width=True, hide_index=True)

    with tab_cancelled:
        c_df = df[df["Cancelled"]][display_cols].reset_index(drop=True)
        st.markdown(f"**{len(c_df):,} cancelled port-in(s)**")
        st.dataframe(c_df, use_container_width=True, hide_index=True)

    with tab_all:
        st.dataframe(df[display_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            df[display_cols].to_csv(index=False),
            f"port_in_report_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
