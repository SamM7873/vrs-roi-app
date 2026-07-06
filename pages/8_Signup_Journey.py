import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Sign-Up Journey Report",
    "Contact created → Registration → Number assigned → First Login → First Outbound",
    section="Analytics",
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _parse(v):
    if not v:
        return None
    try:
        ts = v
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None

def _fmt(v):
    if not v:
        return "—"
    dt = _parse(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

def _days(a, b):
    """Days between two raw timestamp strings. Returns None if either is missing."""
    da, db = _parse(a), _parse(b)
    if da and db:
        return round(abs((db - da).total_seconds()) / 86400, 1)
    return None

def _days_fmt(d):
    if d is None:
        return "—"
    if d < 1:
        return f"{round(d * 24, 1)}h"
    return f"{d}d"

# ── date filter helpers ───────────────────────────────────────────────────────

def _date_range_for_preset(preset: str):
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
        start = today - timedelta(days=today.weekday())
        return start, today
    if preset == "Last Week":
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        return start, end
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
    return None, None  # "All Time" / custom

# ── date filter UI ────────────────────────────────────────────────────────────

PRESETS = [
    "All Time", "Today", "Yesterday",
    "Last 7 Days", "Last 30 Days",
    "This Week (Mon–Sun)", "Last Week",
    "This Month", "Last Month", "Last 3 Months",
    "This Quarter", "Last Quarter",
    "This Year", "Last Year",
    "Custom Range",
]

col_preset, col_from, col_to = st.columns([2, 1, 1])
with col_preset:
    preset = st.selectbox("Date range (based on Contact sign-up date)", PRESETS, index=0)

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

    # 1. Pull number objects (VRS live only)
    with st.spinner("Loading number objects..."):
        num_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name",
             "number_status", "service_type",
             "registered_at", "registration_created_at", "registration_updated_at",
             "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
            progress_label="Fetching number objects",
        )

    num_records = [
        r for r in num_records
        if norm(r.get("properties", {}).get("service_type") or "") == "vrs"
        and norm(r.get("properties", {}).get("number_status") or "") == "live"
    ]

    if not num_records:
        st.warning("No live VRS number records found.")
        st.stop()

    num_by_email = {}
    for r in num_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if email:
            num_by_email[email] = p

    # 2. Pull contacts (join key: email)
    with st.spinner("Loading contacts..."):
        contact_records = list_all(
            "contacts",
            ["email", "firstname", "lastname", "createdate"],
            progress_label="Fetching contacts",
        )

    contact_by_email = {}
    for r in contact_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if email:
            contact_by_email[email] = p

    # 3. Join Contact + Number on email
    rows = []
    for email, np in num_by_email.items():
        cp = contact_by_email.get(email, {})

        first = np.get("first_name") or cp.get("firstname") or ""
        last  = np.get("last_name")  or cp.get("lastname")  or ""
        name  = f"{first} {last}".strip() or "—"

        contact_created  = cp.get("createdate")
        registered_at    = np.get("registered_at")
        reg_created      = np.get("registration_created_at")
        reg_updated      = np.get("registration_updated_at")
        first_login      = np.get("ursa_first_login")
        first_outbound   = np.get("ursa_first_outbound_call")
        second_outbound  = np.get("ursa_second_outbound_call")

        d_signup_to_number   = _days(contact_created, registered_at)
        d_number_to_login    = _days(registered_at, first_login)
        d_login_to_outbound  = _days(first_login, first_outbound)
        d_outbound_to_second = _days(first_outbound, second_outbound)
        d_signup_to_outbound = _days(contact_created, first_outbound)

        rows.append({
            "Email": email,
            "Name": name,
            "Number": np.get("number") or "—",
            # Raw timestamps
            "_contact_created": contact_created,
            "_registered_at":   registered_at,
            "_first_login":     first_login,
            "_first_outbound":  first_outbound,
            "_second_outbound": second_outbound,
            # Formatted dates
            "Contact Created":    _fmt(contact_created),
            "Number Registered":  _fmt(registered_at),
            "Reg Created":        _fmt(reg_created),
            "Reg Updated":        _fmt(reg_updated),
            "First Login":        _fmt(first_login),
            "First Outbound":     _fmt(first_outbound),
            "Second Outbound":    _fmt(second_outbound),
            # Durations (raw for avg calc)
            "Signup → Number (days)":    d_signup_to_number,
            "Number → Login (days)":     d_number_to_login,
            "Login → Outbound (days)":   d_login_to_outbound,
            "Outbound → 2nd (days)":     d_outbound_to_second,
            "Signup → Outbound (days)":  d_signup_to_outbound,
            # Formatted durations
            "Signup→Number":    _days_fmt(d_signup_to_number),
            "Number→Login":     _days_fmt(d_number_to_login),
            "Login→Outbound":   _days_fmt(d_login_to_outbound),
            "Outbound→2nd":     _days_fmt(d_outbound_to_second),
            "Signup→Outbound":  _days_fmt(d_signup_to_outbound),
            # Stage flags
            "Has Contact":   bool(cp),
            "Has Number":    True,
            "Has Login":     bool(first_login),
            "Has Outbound":  bool(first_outbound),
            "Has 2nd Outbound": bool(second_outbound),
        })

    if not rows:
        st.warning("No records found after joining.")
        st.stop()

    df = pd.DataFrame(rows)

    # Apply date filter on contact created date
    if filter_start and filter_end:
        tz_utc = timezone.utc
        fs = datetime(filter_start.year, filter_start.month, filter_start.day, tzinfo=tz_utc)
        fe = datetime(filter_end.year, filter_end.month, filter_end.day, 23, 59, 59, tzinfo=tz_utc)
        def _in_range(v):
            dt = _parse(v)
            if dt is None:
                return True  # keep rows with no contact date
            return fs <= dt <= fe
        mask = df["_contact_created"].apply(_in_range)
        df = df[mask].copy()
        label = f"{filter_start.strftime('%b %d, %Y')} – {filter_end.strftime('%b %d, %Y')}"
        st.markdown(f"<div style='font-size:0.8rem;color:#9dc8b0;margin-bottom:0.5rem;'>Filtered to: <strong style='color:#E6F2EC;'>{label}</strong> · {len(df):,} records</div>", unsafe_allow_html=True)

    if df.empty:
        st.warning("No records match the selected date range.")
        st.stop()

    total        = len(df)
    has_contact  = df["Has Contact"].sum()
    has_number   = df["Has Number"].sum()
    has_login    = df["Has Login"].sum()
    has_outbound = df["Has Outbound"].sum()
    has_2nd      = df["Has 2nd Outbound"].sum()

    def pct(n):
        return f"{n / total * 100:.1f}%" if total else "—"

    def avg_days(col):
        vals = df[col].dropna()
        return f"{vals.mean():.1f}d" if len(vals) else "—"

    # ── Funnel tiles ──────────────────────────────────────────────────────────
    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:1rem 0 0.5rem;">
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Numbers (Live VRS)</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">100% — baseline</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Has Contact Record</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{int(has_contact):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_contact)}</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">First Login</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;">{int(has_login):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_login)} · avg {avg_days("Number → Login (days)")}</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">First Outbound</div>
    <div style="font-size:1.4rem;font-weight:800;color:#F59E0B;">{int(has_outbound):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_outbound)} · avg {avg_days("Login → Outbound (days)")}</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Second Outbound</div>
    <div style="font-size:1.4rem;font-weight:800;color:#8B5CF6;">{int(has_2nd):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_2nd)} · avg {avg_days("Outbound → 2nd (days)")}</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Avg time tiles ────────────────────────────────────────────────────────
    st.markdown("""
<div style="font-size:0.72rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
     color:#E6F2EC;margin:1.25rem 0 0.5rem;">Average Time Between Steps</div>
""", unsafe_allow_html=True)
    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.75rem;margin-bottom:1.5rem;">
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Signup → Number</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Signup → Number (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Number → First Login</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Number → Login (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Login → First Outbound</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Login → Outbound (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">1st → 2nd Outbound</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Outbound → 2nd (days)")}</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Funnel bar chart ──────────────────────────────────────────────────────
    funnel_df = pd.DataFrame({
        "Stage": ["Live Numbers", "Has Contact", "First Login", "First Outbound", "Second Outbound"],
        "Count": [total, int(has_contact), int(has_login), int(has_outbound), int(has_2nd)],
        "Color": ["#6B7280", "#3B82F6", "#00A651", "#F59E0B", "#8B5CF6"],
    })
    chart = alt.Chart(funnel_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Stage:N", sort=list(funnel_df["Stage"]), axis=alt.Axis(title=None)),
        y=alt.Y("Count:Q", title="Users"),
        color=alt.Color("Color:N", scale=None, legend=None),
        tooltip=["Stage", "Count"],
    ).properties(height=280)
    st.altair_chart(chart, use_container_width=True)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_journey, tab_dropped, tab_full = st.tabs(["Journey Timeline", "Drop-Off Analysis", "Full Table"])

    with tab_journey:
        journey_cols = ["Name", "Email", "Number",
                        "Contact Created", "Number Registered", "Reg Created", "Reg Updated",
                        "First Login", "First Outbound", "Second Outbound",
                        "Signup→Number", "Number→Login", "Login→Outbound", "Outbound→2nd", "Signup→Outbound"]
        st.dataframe(df[journey_cols].reset_index(drop=True), use_container_width=True, hide_index=True)

    with tab_dropped:
        never_outbound = df[~df["Has Outbound"]].copy()
        st.markdown(f"**{len(never_outbound):,}** numbers have not made a first outbound call yet.")

        drop_df = pd.DataFrame({
            "Dropped at Stage": [
                "No Login (has number)",
                "No Outbound (has login)",
                "No 2nd Outbound (has 1st)",
            ],
            "Count": [
                int((~df["Has Login"]).sum()),
                int((df["Has Login"] & ~df["Has Outbound"]).sum()),
                int((df["Has Outbound"] & ~df["Has 2nd Outbound"]).sum()),
            ],
        })
        bar_drop = alt.Chart(drop_df).mark_bar(color="#EF4444", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q"),
            y=alt.Y("Dropped at Stage:N", sort="-x"),
            tooltip=["Dropped at Stage", "Count"],
        ).properties(height=180)
        st.altair_chart(bar_drop, use_container_width=True)
        st.dataframe(drop_df, use_container_width=True, hide_index=True)

    with tab_full:
        full_cols = ["Name", "Email", "Number",
                     "Contact Created", "Number Registered", "Reg Created", "Reg Updated",
                     "First Login", "First Outbound", "Second Outbound",
                     "Signup→Number", "Number→Login", "Login→Outbound", "Outbound→2nd", "Signup→Outbound",
                     "Has Contact", "Has Login", "Has Outbound", "Has 2nd Outbound"]
        st.dataframe(df[full_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            df[full_cols].to_csv(index=False),
            f"signup_journey_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
