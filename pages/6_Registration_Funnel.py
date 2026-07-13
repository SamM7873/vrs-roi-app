import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import dash_spinner, require_auth, list_all, norm, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Registration Funnel", layout="wide", page_icon="📋")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Registration Funnel", "Step-by-step conversion from Submitted → LEX → URD → Active", section="Analytics")

if st.button("Load Registration Funnel", use_container_width=False):
    records = list_all(
        "2-58833629",
        [
            "registration_id", "registration_type", "usage_type",
            "email", "first_name", "last_name", "number",
            "submitted_at", "registered_at",
            "lex_verification_status", "lex_verified_at",
            "urd_status", "urd_registration_created_at",
            "is_cancelled", "registration_created_at",
            "portin_status", "state",
        ],
        progress_label="Fetching registration records",
    )

    if not records:
        st.warning("No registration records found.")
        st.stop()

    rows = []
    for r in records:
        p = r.get("properties", {})
        lex = p.get("lex_verification_status") or ""
        urd = p.get("urd_status") or ""
        cancelled = str(p.get("is_cancelled") or "false").lower() == "true"
        lex_done = lex in ("automatic_success", "manual_success")
        urd_done = urd == "completed"
        active = lex_done and urd_done and not cancelled

        rows.append({
            "Registration ID": p.get("registration_id") or r.get("id", ""),
            "Type": (p.get("registration_type") or "").replace("_", " ").title(),
            "Usage Type": (p.get("usage_type") or "").title(),
            "Number": p.get("number") or "—",
            "Email": p.get("email") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "State": p.get("state") or "—",
            "Submitted": p.get("submitted_at") or "",
            "Registered": p.get("registered_at") or "",
            "LEX Status": lex,
            "LEX Verified At": p.get("lex_verified_at") or "",
            "URD Status": urd,
            "Cancelled": cancelled,
            "Port-In Status": p.get("portin_status") or "",
            "Submitted ✓": True,
            "LEX Done ✓": lex_done,
            "URD Done ✓": urd_done,
            "Active ✓": active,
        })

    df = pd.DataFrame(rows)
    total = len(df)

    submitted = total
    lex_done_count = df["LEX Done ✓"].sum()
    urd_done_count = df["URD Done ✓"].sum()
    active_count   = df["Active ✓"].sum()
    cancelled_count = df["Cancelled"].sum()

    def pct(n):
        return f"{n/total*100:.1f}%" if total else "—"

    # Funnel tiles
    st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:1rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Submitted</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{submitted:,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">100%</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">LEX Verified</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{int(lex_done_count):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(lex_done_count)}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">URD Completed</div>
    <div style="font-size:1.4rem;font-weight:800;color:#8B5CF6;">{int(urd_done_count):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(urd_done_count)}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Active</div>
    <div style="font-size:1.4rem;font-weight:800;color:#00A651;">{int(active_count):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(active_count)}</div>
  </div>
  <div style="background:#fff;border:1px solid #FEE2E2;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#EF4444;margin-bottom:0.25rem;">Cancelled</div>
    <div style="font-size:1.4rem;font-weight:800;color:#EF4444;">{int(cancelled_count):,}</div>
    <div style="font-size:0.7rem;color:#9CA3AF;">{pct(cancelled_count)}</div>
  </div>
</div>""", unsafe_allow_html=True)

    # Funnel chart
    funnel_df = pd.DataFrame({
        "Step": ["Submitted", "LEX Verified", "URD Completed", "Active"],
        "Count": [submitted, int(lex_done_count), int(urd_done_count), int(active_count)],
        "Color": ["#6B7280", "#3B82F6", "#8B5CF6", "#00A651"],
    })
    funnel_chart = alt.Chart(funnel_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Step:N", sort=["Submitted", "LEX Verified", "URD Completed", "Active"], axis=alt.Axis(title=None)),
        y=alt.Y("Count:Q", title="Registrations"),
        color=alt.Color("Color:N", scale=None, legend=None),
        tooltip=["Step", "Count"],
    ).properties(height=300)
    st.altair_chart(funnel_chart, use_container_width=True)

    # Breakdown by type
    tab_type, tab_lex, tab_stuck, tab_full = st.tabs(["By Type", "LEX Status", "Stuck Records", "Full Table"])

    with tab_type:
        type_df = df.groupby("Type").agg(
            Total=("Registration ID", "count"),
            LEX_Done=("LEX Done ✓", "sum"),
            URD_Done=("URD Done ✓", "sum"),
            Active=("Active ✓", "sum"),
            Cancelled=("Cancelled", "sum"),
        ).reset_index().sort_values("Total", ascending=False)
        type_df["Active %"] = (type_df["Active"] / type_df["Total"] * 100).round(1)
        st.dataframe(type_df, use_container_width=True, hide_index=True)

    with tab_lex:
        lex_df = df.groupby("LEX Status").size().reset_index(name="Count").sort_values("Count", ascending=False)
        bar2 = alt.Chart(lex_df).mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Count:Q"),
            y=alt.Y("LEX Status:N", sort="-x"),
            tooltip=["LEX Status", "Count"],
        ).properties(height=250)
        st.altair_chart(bar2, use_container_width=True)
        st.dataframe(lex_df, use_container_width=True, hide_index=True)

    with tab_stuck:
        stuck = df[df["LEX Done ✓"] & ~df["URD Done ✓"] & ~df["Cancelled"]]
        st.markdown(f"**{len(stuck)} registrations** passed LEX verification but have not completed URD.")
        def _fmt(v):
            if not v: return "—"
            try: return datetime.fromisoformat(v.replace("Z", "+00:00")).strftime("%b %d, %Y")
            except Exception: return v
        stuck_show = stuck[["Registration ID", "Number", "Name", "Email", "Type", "LEX Status", "URD Status", "Submitted"]].copy()
        stuck_show["Submitted"] = stuck_show["Submitted"].apply(_fmt)
        st.dataframe(stuck_show.reset_index(drop=True), use_container_width=True, hide_index=True)

    with tab_full:
        show_cols = ["Registration ID", "Type", "Number", "Name", "Email", "State",
                     "LEX Status", "URD Status", "Active ✓", "Cancelled", "Port-In Status"]
        st.dataframe(df[show_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False),
                           f"registration_funnel_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

report_header_close()
