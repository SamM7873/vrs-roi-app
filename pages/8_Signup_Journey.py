import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone
from utils import require_auth, list_all, fetch_all, norm, COMMON_CSS, report_header, report_header_close

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

# ── run ───────────────────────────────────────────────────────────────────────

if st.button("Run Sign-Up Journey Report", use_container_width=False):

    # 1. Pull registrations (join key: email)
    with st.spinner("Loading registration records..."):
        reg_records = list_all(
            "2-58833629",
            ["email", "first_name", "last_name", "number",
             "registration_type", "usage_type",
             "lex_verification_status", "lex_verified_at",
             "registration_created_at", "registered_at"],
            progress_label="Fetching registrations",
        )

    if not reg_records:
        st.warning("No registration records found.")
        st.stop()

    # Build email → registration lookup (keep most recent if multiple)
    reg_by_email = {}
    for r in reg_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if not email:
            continue
        existing = reg_by_email.get(email)
        if existing is None or (p.get("registration_created_at") or "") > (existing.get("registration_created_at") or ""):
            reg_by_email[email] = p

    # Also build number → registration lookup
    reg_by_number = {}
    for r in reg_records:
        p = r.get("properties", {})
        num = (p.get("number") or "").strip()
        if num:
            reg_by_number[num] = p

    # 2. Pull number objects (join key: email or number)
    with st.spinner("Loading number objects..."):
        num_records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name",
             "number_status", "service_type",
             "registered_at", "registration_created_at", "registration_updated_at",
             "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"],
            progress_label="Fetching number objects",
        )

    # Filter to live VRS
    num_records = [
        r for r in num_records
        if norm(r.get("properties", {}).get("service_type") or "") == "vrs"
        and norm(r.get("properties", {}).get("number_status") or "") == "live"
    ]

    num_by_email = {}
    for r in num_records:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if email:
            num_by_email[email] = p

    # 3. Pull contacts (join key: email)
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

    # 4. Join all three on email
    rows = []
    all_emails = set(num_by_email.keys()) | set(reg_by_email.keys())

    for email in all_emails:
        np = num_by_email.get(email, {})
        rp = reg_by_email.get(email, {})
        cp = contact_by_email.get(email, {})

        number = np.get("number") or rp.get("number") or "—"
        first = np.get("first_name") or rp.get("first_name") or cp.get("firstname") or ""
        last = np.get("last_name") or rp.get("last_name") or cp.get("lastname") or ""
        name = f"{first} {last}".strip() or "—"

        contact_created  = cp.get("createdate")
        reg_created      = rp.get("registration_created_at")
        registered_at    = np.get("registered_at") or rp.get("registered_at")
        reg_updated      = np.get("registration_updated_at")
        lex_verified_at  = rp.get("lex_verified_at")
        first_login      = np.get("ursa_first_login")
        first_outbound   = np.get("ursa_first_outbound_call")
        second_outbound  = np.get("ursa_second_outbound_call")

        # Time calculations
        d_signup_to_reg      = _days(contact_created, reg_created)
        d_reg_to_number      = _days(reg_created, registered_at)
        d_signup_to_number   = _days(contact_created, registered_at)
        d_number_to_login    = _days(registered_at, first_login)
        d_login_to_outbound  = _days(first_login, first_outbound)
        d_signup_to_outbound = _days(contact_created, first_outbound)

        rows.append({
            "Email": email,
            "Name": name,
            "Number": number,
            "Registration Type": (rp.get("registration_type") or "").replace("_", " ").title(),
            "Usage Type": (rp.get("usage_type") or "").title(),
            "LEX Status": rp.get("lex_verification_status") or "—",
            # Raw timestamps for sorting
            "_contact_created":  contact_created,
            "_reg_created":      reg_created,
            "_registered_at":    registered_at,
            "_lex_verified_at":  lex_verified_at,
            "_first_login":      first_login,
            "_first_outbound":   first_outbound,
            "_second_outbound":  second_outbound,
            # Formatted dates
            "Contact Created":   _fmt(contact_created),
            "Registration Created": _fmt(reg_created),
            "Number Registered": _fmt(registered_at),
            "LEX Verified At":   _fmt(lex_verified_at),
            "First Login":       _fmt(first_login),
            "First Outbound":    _fmt(first_outbound),
            "Second Outbound":   _fmt(second_outbound),
            # Durations
            "Signup → Reg (days)":      d_signup_to_reg,
            "Reg → Number (days)":      d_reg_to_number,
            "Signup → Number (days)":   d_signup_to_number,
            "Number → Login (days)":    d_number_to_login,
            "Login → Outbound (days)":  d_login_to_outbound,
            "Signup → Outbound (days)": d_signup_to_outbound,
            # Formatted durations
            "Signup→Reg":      _days_fmt(d_signup_to_reg),
            "Reg→Number":      _days_fmt(d_reg_to_number),
            "Signup→Number":   _days_fmt(d_signup_to_number),
            "Number→Login":    _days_fmt(d_number_to_login),
            "Login→Outbound":  _days_fmt(d_login_to_outbound),
            "Signup→Outbound": _days_fmt(d_signup_to_outbound),
            # Stage flags
            "Has Registration": bool(rp),
            "Has Number":       bool(np),
            "Has Login":        bool(first_login),
            "Has Outbound":     bool(first_outbound),
        })

    if not rows:
        st.warning("No records found after joining.")
        st.stop()

    df = pd.DataFrame(rows)
    total = len(df)

    has_reg      = df["Has Registration"].sum()
    has_number   = df["Has Number"].sum()
    has_login    = df["Has Login"].sum()
    has_outbound = df["Has Outbound"].sum()

    def pct(n):
        return f"{n / total * 100:.1f}%" if total else "—"

    def avg_days(col):
        vals = df[col].dropna()
        return f"{vals.mean():.1f}d" if len(vals) else "—"

    # ── Funnel tiles ──────────────────────────────────────────────────────────
    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:1rem 0 0.5rem;">
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Contacts</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">100% — baseline</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Registered</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{int(has_reg):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_reg)} · avg {avg_days("Signup → Reg (days)")}</div>
  </div>
  <div style="background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#7A8A7A;margin-bottom:0.25rem;">Number Assigned</div>
    <div style="font-size:1.4rem;font-weight:800;color:#8B5CF6;">{int(has_number):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(has_number)} · avg {avg_days("Signup → Number (days)")}</div>
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
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Signup → Registration</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Signup → Reg (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Registration → Number</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Reg → Number (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Number → First Login</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Number → Login (days)")}</div>
  </div>
  <div style="background:#1a4d32;border:1px solid #2d6b47;border-radius:10px;padding:0.85rem 1rem;">
    <div style="font-size:0.6rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#9dc8b0;margin-bottom:0.2rem;">Login → First Outbound</div>
    <div style="font-size:1.3rem;font-weight:800;color:#E6F2EC;">{avg_days("Login → Outbound (days)")}</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Funnel bar chart ──────────────────────────────────────────────────────
    funnel_df = pd.DataFrame({
        "Stage": ["Contact Created", "Registration", "Number Assigned", "First Login", "First Outbound"],
        "Count": [total, int(has_reg), int(has_number), int(has_login), int(has_outbound)],
        "Color": ["#6B7280", "#3B82F6", "#8B5CF6", "#00A651", "#F59E0B"],
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

    def _fmt_row(v):
        return _fmt(v) if v else "—"

    with tab_journey:
        journey_cols = ["Name", "Email", "Number",
                        "Contact Created", "Registration Created", "Number Registered",
                        "LEX Verified At", "First Login", "First Outbound", "Second Outbound",
                        "Signup→Reg", "Reg→Number", "Number→Login", "Login→Outbound", "Signup→Outbound"]
        st.dataframe(df[journey_cols].reset_index(drop=True), use_container_width=True, hide_index=True)

    with tab_dropped:
        # People who signed up but never reached outbound call
        never_outbound = df[~df["Has Outbound"]].copy()
        st.markdown(f"**{len(never_outbound):,}** contacts have not made a first outbound call yet.")

        drop_df = pd.DataFrame({
            "Dropped at Stage": [
                "No Registration",
                "No Number (has reg)",
                "No Login (has number)",
                "No Outbound (has login)",
            ],
            "Count": [
                int((~df["Has Registration"]).sum()),
                int((df["Has Registration"] & ~df["Has Number"]).sum()),
                int((df["Has Number"] & ~df["Has Login"]).sum()),
                int((df["Has Login"] & ~df["Has Outbound"]).sum()),
            ],
        })
        bar_drop = alt.Chart(drop_df).mark_bar(color="#EF4444", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q"),
            y=alt.Y("Dropped at Stage:N", sort="-x"),
            tooltip=["Dropped at Stage", "Count"],
        ).properties(height=200)
        st.altair_chart(bar_drop, use_container_width=True)
        st.dataframe(drop_df, use_container_width=True, hide_index=True)

    with tab_full:
        full_cols = ["Name", "Email", "Number", "Registration Type", "Usage Type", "LEX Status",
                     "Contact Created", "Registration Created", "Number Registered",
                     "First Login", "First Outbound", "Second Outbound",
                     "Signup→Reg", "Reg→Number", "Number→Login", "Login→Outbound", "Signup→Outbound",
                     "Has Registration", "Has Number", "Has Login", "Has Outbound"]
        st.dataframe(df[full_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            df[full_cols].to_csv(index=False),
            f"signup_journey_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )

report_header_close()
