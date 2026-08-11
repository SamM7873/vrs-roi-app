import streamlit as st
import pandas as pd
import requests
from datetime import datetime
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Email Bounce Report", layout="wide", page_icon="📧")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Email Bounce Report")

report_header("Email Bounce Report",
              "All contact emails and who has hard-bounced",
              section="Analytics")

CACHE_VERSION = 1
_key = f"email_bounce_v{CACHE_VERSION}"


def _count(filters):
    """Total count for a contacts search filter (reads the `total` field)."""
    try:
        r = requests.post(f"{_B}/crm/v3/objects/contacts/search", headers=_H,
                          json={"filterGroups": [{"filters": filters}],
                                "properties": ["email"], "limit": 1}, timeout=15)
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return None


def _dt(v):
    if not v:
        return ""
    try:
        s = str(v)
        d = (datetime.utcfromtimestamp(int(s) / 1000) if s.isdigit()
             else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return d.strftime("%b %d, %Y")
    except Exception:
        return str(v)[:10]


st.markdown("Pulls all contacts with an email and flags **hard bounces** "
            "(HubSpot property `hs_email_hard_bounce_reason`).")
run = st.button("Run email bounce report", type="primary")

if run:
    # totals (fast — reads the search `total`)
    total_email = _count([{"propertyName": "email", "operator": "HAS_PROPERTY"}])
    total_bounced = _count([{"propertyName": "hs_email_hard_bounce_reason", "operator": "HAS_PROPERTY"}])

    # pull every bounced contact
    with dash_spinner("Pulling bounced contacts…"):
        recs = fetch_all(
            "contacts",
            ["email", "firstname", "lastname", "hs_email_hard_bounce_reason",
             "hs_email_hard_bounce_reason_enum", "hs_email_last_send_date",
             "hs_email_optout", "lifecyclestage"],
            filter_groups=[{"filters": [
                {"propertyName": "hs_email_hard_bounce_reason", "operator": "HAS_PROPERTY"}]}])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip()
        if not email:
            continue
        rows.append({
            "Email": email,
            "Name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
            "Domain": email.split("@")[-1].lower() if "@" in email else "",
            "Bounce Reason": (p.get("hs_email_hard_bounce_reason") or "").strip() or "—",
            "Reason Type": (p.get("hs_email_hard_bounce_reason_enum") or "").strip() or "—",
            "Last Send": _dt(p.get("hs_email_last_send_date")),
            "Opted Out": "Yes" if (p.get("hs_email_optout") in ("true", "True", True)) else "",
            "Lifecycle": (p.get("lifecyclestage") or "").strip() or "—",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "total_email": total_email, "total_bounced": total_bounced})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run email bounce report** to pull all emails and bounces.")
    report_header_close(); st.stop()

df = saved["df"]
total_email = saved.get("total_email")
total_bounced = saved.get("total_bounced")
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh")

# ── KPIs ────────────────────────────────────────────────────────────────────────
n_bounced = len(df)
rate = (n_bounced / total_email * 100) if total_email else None
k = st.columns(4)
k[0].metric("📧 Contacts with email", f"{total_email:,}" if total_email is not None else "—")
k[1].metric("⛔ Hard bounced", f"{total_bounced:,}" if total_bounced is not None else f"{n_bounced:,}")
k[2].metric("Bounce rate", f"{rate:.1f}%" if rate is not None else "—")
k[3].metric("Bounced domains", f"{df['Domain'].nunique():,}" if not df.empty else "0")

if df.empty:
    st.success("No hard-bounced contacts found. 🎉")
    report_header_close(); st.stop()

# ── by reason type ──────────────────────────────────────────────────────────────
st.markdown("##### By bounce reason type")
byreason = (df["Reason Type"].value_counts().rename_axis("Reason Type").reset_index(name="Count"))
byreason["%"] = (byreason["Count"] / len(df) * 100).round(1)
st.dataframe(byreason, use_container_width=True, hide_index=True)

# ── top bounced domains ─────────────────────────────────────────────────────────
st.markdown("##### Top bounced email domains")
bydom = (df["Domain"].value_counts().head(20).rename_axis("Domain").reset_index(name="Bounced"))
st.dataframe(bydom, use_container_width=True, hide_index=True)

# ── filters + list ──────────────────────────────────────────────────────────────
st.markdown("##### Bounced contacts")
f1, f2 = st.columns([1.5, 2])
rt_opts = ["All"] + sorted([r for r in df["Reason Type"].unique() if r and r != "—"])
rt = f1.selectbox("Reason type", rt_opts)
search = f2.text_input("Search email / name / domain").strip().lower()

view = df.copy()
if rt != "All":
    view = view[view["Reason Type"] == rt]
if search:
    view = view[view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)
                | view["Domain"].str.contains(search, case=False, na=False)]
st.caption(f"{len(view):,} bounced contacts")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export bounced CSV", view.to_csv(index=False),
                   "email_bounces.csv", "text/csv")

report_header_close()
