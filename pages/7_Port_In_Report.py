import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Port-In Report", layout="wide", page_icon="📲")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Port-In Report", "Track port-in registrations and their current status", section="Analytics")

if st.button("Load Port-In Report", use_container_width=False):
    with st.spinner("Loading registration records..."):
        records = list_all(
            "2-58833629",
            [
                "registration_id", "registration_type", "usage_type",
                "email", "first_name", "last_name", "number",
                "portin_status", "submitted_at", "registered_at",
                "lex_verification_status", "urd_status",
                "is_cancelled", "registration_created_at", "state",
            ],
            progress_label="Fetching registration records",
        )

    port_in_records = [
        r for r in records
        if (r.get("properties", {}).get("portin_status") or "").strip()
        or "port" in (r.get("properties", {}).get("registration_type") or "").lower()
    ]

    if not port_in_records:
        st.info("No port-in registration records found.")
        st.stop()

    rows = []
    for r in port_in_records:
        p = r.get("properties", {})
        lex = p.get("lex_verification_status") or ""
        urd = p.get("urd_status") or ""
        cancelled = str(p.get("is_cancelled") or "false").lower() == "true"
        lex_done = lex in ("automatic_success", "manual_success")
        urd_done = urd == "completed"
        portin_status = (p.get("portin_status") or "").strip()

        def _fmt(v):
            if not v: return "—"
            try: return datetime.fromisoformat(v.replace("Z", "+00:00")).strftime("%b %d, %Y")
            except Exception: return v

        rows.append({
            "Registration ID": p.get("registration_id") or r.get("id", ""),
            "Type": (p.get("registration_type") or "").replace("_", " ").title(),
            "Number": p.get("number") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "State": p.get("state") or "—",
            "Port-In Status": portin_status or "—",
            "LEX Status": lex or "—",
            "URD Status": urd or "—",
            "LEX Done": lex_done,
            "URD Done": urd_done,
            "Cancelled": cancelled,
            "Active": lex_done and urd_done and not cancelled,
            "Submitted": _fmt(p.get("submitted_at")),
            "Registered": _fmt(p.get("registered_at")),
        })

    df = pd.DataFrame(rows)
    total = len(df)

    completed = df["Active"].sum()
    cancelled  = df["Cancelled"].sum()
    in_prog    = total - int(completed) - int(cancelled)

    st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;margin:1rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Total Port-Ins</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total:,}</div>
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
    <div style="font-size:1.4rem;font-weight:800;color:#EF4444;">{int(cancelled):,}</div>
  </div>
</div>""", unsafe_allow_html=True)

    # Status breakdown chart
    if df["Port-In Status"].nunique() > 1 or df["Port-In Status"].iloc[0] != "—":
        status_counts = df[df["Port-In Status"] != "—"].groupby("Port-In Status").size().reset_index(name="Count").sort_values("Count", ascending=False)
        if not status_counts.empty:
            st.markdown("#### Port-In Status Breakdown")
            bar = alt.Chart(status_counts).mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Count:Q", title="Count"),
                y=alt.Y("Port-In Status:N", sort="-x", title=None),
                tooltip=["Port-In Status", "Count"],
            ).properties(height=max(180, len(status_counts) * 30))
            st.altair_chart(bar, use_container_width=True)

    tab_progress, tab_cancelled, tab_full = st.tabs(["In Progress", "Cancelled", "All Port-Ins"])

    with tab_progress:
        prog_df = df[~df["Active"] & ~df["Cancelled"]].copy()
        st.markdown(f"**{len(prog_df)} port-in(s) currently in progress**")
        show = ["Registration ID", "Number", "Name", "Email", "State", "Port-In Status", "LEX Status", "URD Status", "Submitted"]
        st.dataframe(prog_df[show].reset_index(drop=True), use_container_width=True, hide_index=True)

    with tab_cancelled:
        canc_df = df[df["Cancelled"]].copy()
        st.markdown(f"**{len(canc_df)} cancelled port-in(s)**")
        st.dataframe(canc_df[["Registration ID", "Number", "Name", "Email", "State", "Port-In Status", "Submitted"]].reset_index(drop=True),
                     use_container_width=True, hide_index=True)

    with tab_full:
        st.dataframe(df.reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False),
                           f"port_in_report_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

report_header_close()
