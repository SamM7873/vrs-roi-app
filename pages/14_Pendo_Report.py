import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import (
    require_auth, fetch_all, dash_spinner, to_float,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="Pendo Report", layout="wide", page_icon="📱")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Pendo Report",
    "Contact-level product engagement from Pendo — search by email or number",
    section="Analytics",
)
report_header_close()

# Pendo properties synced onto the HubSpot Contact
PENDO_PROPS = [
    "email", "firstname", "lastname", "phone",
    "convo_now_account_id",            # Pendo ID
    "pendo_first_visit", "pendo_last_visit",
    "pendo_events_30d", "pendo_days_active_30d",
    "pendo_time_spent_on_app_30d", "pendo_usage_trending_30d",
]


def _dt(v):
    """HubSpot datetime → short date string."""
    if not v:
        return ""
    try:
        return str(v)[:10]
    except Exception:
        return ""


with st.expander("ℹ️ What is Pendo, and what do these fields mean?"):
    st.markdown("""
**Pendo** is a product-analytics platform embedded in the Convo Now app. Every time a user
opens the app, Pendo records their visits, clicks, and time spent, tied to a unique
**Pendo ID** per account. That data is synced back onto the **HubSpot Contact**, which is
why this report works at the contact level.

| Field | HubSpot property | Meaning |
|---|---|---|
| **Pendo ID** | `convo_now_account_id` | The unique account identifier linking this contact to Pendo's analytics. |
| **First Visit** | `pendo_first_visit` | The first time this user ever opened the app. |
| **Last Visit** | `pendo_last_visit` | The most recent time they opened the app — the freshest signal of whether they're still active. |
| **Events (30d)** | `pendo_events_30d` | Number of tracked interactions (clicks, taps, page views) in the last 30 days. |
| **Days Active (30d)** | `pendo_days_active_30d` | On how many of the last 30 days they opened the app (0–30). |
| **Time on App (30d)** | `pendo_time_spent_on_app_30d` | Total time spent in the app over the last 30 days. |
| **Usage Trend (30d)** | `pendo_usage_trending_30d` | Whether their usage is growing or shrinking vs the prior period (%). Negative = disengaging. |

**How to read it:** *Last Visit* tells you if they're still around; *Days Active* tells you
how habitual the app is for them; *Usage Trend* is your early churn warning — a user with a
recent Last Visit but a steep negative trend is quietly disengaging. Note that Pendo measures
**app engagement** (opens, taps), which is different from the **Monthly Values** usage minutes
(actual call time) — a user can browse the app daily yet make no calls, and vice versa.
""")

# ── Load contacts that have a Pendo ID ───────────────────────────────────────
cached = load_report("pendo_contacts")
refresh = st.button("Load / Refresh Pendo Contacts", type="primary")

if refresh or cached is None:
    if refresh:
        with dash_spinner("Fetching contacts with a Pendo ID…"):
            recs = fetch_all(
                "contacts",
                PENDO_PROPS,
                filter_groups=[{"filters": [
                    {"propertyName": "convo_now_account_id", "operator": "HAS_PROPERTY"},
                ]}]
            )
        rows = []
        for r in recs:
            p = r.get("properties", {})
            rows.append({
                "Contact ID":       str(r.get("id") or ""),
                "Name":             f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
                "Email":            (p.get("email") or "").strip().lower(),
                "Phone":            (p.get("phone") or "").strip(),
                "Pendo ID":         (p.get("convo_now_account_id") or "").strip(),
                "First Visit":      _dt(p.get("pendo_first_visit")),
                "Last Visit":       _dt(p.get("pendo_last_visit")),
                "Events (30d)":     to_float(p.get("pendo_events_30d")) or 0,
                "Days Active (30d)": to_float(p.get("pendo_days_active_30d")) or 0,
                "Time on App (30d)": to_float(p.get("pendo_time_spent_on_app_30d")) or 0,
                "Usage Trend % (30d)": to_float(p.get("pendo_usage_trending_30d")),
            })
        df = pd.DataFrame(rows)
        cached = {"df": df}
        save_report("pendo_contacts", cached)
        cached = load_report("pendo_contacts") or cached

if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Click **Load / Refresh Pendo Contacts** to pull contacts with a Pendo ID.")
    st.stop()

df = cached["df"]
if cached.get("saved_at"):
    st.caption(f"📌 Data as of {saved_at_label(cached)} · click the button above to refresh")

# ── Search: email, phone number, name, or Pendo ID ──────────────────────────
search = st.text_input(
    "Search",
    placeholder="Search by email, phone number, name, or Pendo ID…",
)

df_view = df.copy()
if search.strip():
    q = search.strip().lower()
    q_digits = "".join(ch for ch in q if ch.isdigit())
    mask = (
        df_view["Email"].str.lower().str.contains(q, na=False, regex=False) |
        df_view["Name"].str.lower().str.contains(q, na=False, regex=False) |
        df_view["Pendo ID"].str.lower().str.contains(q, na=False, regex=False)
    )
    if q_digits:
        phone_digits = df_view["Phone"].map(lambda v: "".join(ch for ch in str(v) if ch.isdigit()))
        mask = mask | phone_digits.str.contains(q_digits, na=False, regex=False)

        # Number tie: resolve the digits against Number objects (VRS / Convo
        # Now numbers) and match their email back to the contact.
        if len(q_digits) >= 7:
            _nq_key = f"_pendo_numlookup_{q_digits}"
            if _nq_key not in st.session_state:
                num_recs = fetch_all(
                    "2-40974683",
                    ["number", "email"],
                    filter_groups=[{"filters": [
                        {"propertyName": "number", "operator": "CONTAINS_TOKEN", "value": q_digits},
                    ]}]
                ) or fetch_all(
                    "2-40974683",
                    ["number", "email"],
                    filter_groups=[{"filters": [
                        {"propertyName": "number", "operator": "EQ", "value": q_digits},
                    ]}]
                )
                st.session_state[_nq_key] = sorted({
                    (r.get("properties", {}).get("email") or "").strip().lower()
                    for r in num_recs
                    if (r.get("properties", {}).get("email") or "").strip()
                })
            tied_emails = st.session_state[_nq_key]
            if tied_emails:
                mask = mask | df_view["Email"].isin(tied_emails)
                st.caption(f"🔗 Number tie: {q_digits} belongs to {', '.join(tied_emails)}")

    df_view = df_view[mask]
    st.caption(f'{len(df_view):,} of {len(df):,} contacts match "{search}"')

# ── Summary tiles ─────────────────────────────────────────────────────────────
now = datetime.now()
recent = df_view[df_view["Last Visit"] >= (pd.Timestamp(now) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")]
active_habit = df_view[df_view["Days Active (30d)"] >= 10]
declining = df_view[df_view["Usage Trend % (30d)"].fillna(0) < -25]

def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.45rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;">{value}</div>
  <div style="font-size:0.72rem;color:#9CA3AF;">{sub}</div>
</div>"""

st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:0.75rem 0 1.5rem;">
  {tile("Contacts with Pendo ID", f"{len(df_view):,}", "convo_now_account_id set")}
  {tile("Visited Last 30 Days", f"{len(recent):,}", "by Last Visit", "#00A651")}
  {tile("Habitual Users", f"{len(active_habit):,}", "active ≥ 10 of last 30 days", "#3B82F6")}
  {tile("Declining Usage", f"{len(declining):,}", "trend < −25% (churn risk)", "#EF4444")}
  {tile("Median Days Active", f"{df_view['Days Active (30d)'].median():.0f}", "of last 30 days")}
</div>""", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────────
ch1, ch2 = st.columns(2)

with ch1:
    dist = df_view["Days Active (30d)"].round(0).astype(int).value_counts().reset_index()
    dist.columns = ["Days Active", "Contacts"]
    st.altair_chart(
        alt.Chart(dist.sort_values("Days Active"))
        .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Days Active:O", title="Days active in last 30"),
            y=alt.Y("Contacts:Q"),
            tooltip=["Days Active", "Contacts"],
        )
        .properties(height=240, title="Engagement Habit — Days Active (last 30)"),
        use_container_width=True,
    )

with ch2:
    lv = df_view[df_view["Last Visit"] != ""].copy()
    if not lv.empty:
        lv["Last Visit Month"] = lv["Last Visit"].str[:7]
        lvm = lv["Last Visit Month"].value_counts().reset_index()
        lvm.columns = ["Month", "Contacts"]
        st.altair_chart(
            alt.Chart(lvm.sort_values("Month"))
            .mark_bar(color="#00A651", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Month:N", sort=list(lvm.sort_values("Month")["Month"]),
                        axis=alt.Axis(labelAngle=-20, title=None)),
                y=alt.Y("Contacts:Q"),
                tooltip=["Month", "Contacts"],
            )
            .properties(height=240, title="Recency — Last Visit by Month"),
            use_container_width=True,
        )

# ── Table + download ─────────────────────────────────────────────────────────
st.markdown("#### Contact Detail")
st.dataframe(
    df_view.sort_values("Days Active (30d)", ascending=False).reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Events (30d)":        st.column_config.NumberColumn(format="%.0f"),
        "Days Active (30d)":   st.column_config.NumberColumn(format="%.0f"),
        "Time on App (30d)":   st.column_config.NumberColumn(format="%.0f"),
        "Usage Trend % (30d)": st.column_config.NumberColumn(format="%.0f%%"),
    },
)
st.download_button(
    "Download CSV",
    df_view.to_csv(index=False),
    "pendo_contacts.csv",
    "text/csv",
)
