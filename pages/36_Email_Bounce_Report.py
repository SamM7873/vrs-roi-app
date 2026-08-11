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


st.markdown("Pulls all contacts with an email and flags **bounced / undeliverable** addresses. "
            "Bounce data can live in different HubSpot fields depending on how email is sent — "
            "this checks several and uses whichever has data.")

# candidate bounce signals (property, how-to-detect)
BOUNCE_PROPS = [
    ("hs_email_hard_bounce_reason", "HAS_PROPERTY", None),
    ("hs_email_hard_bounce_reason_enum", "HAS_PROPERTY", None),
    ("hs_emailconfirmationstatus", "EQ", "5"),          # 5 = Bounced (marketing status)
    ("hs_email_quarantined", "EQ", "true"),
    ("hs_email_bounce", "GT", "0"),
]
run = st.button("Run email bounce report", type="primary")

if run:
    total_email = _count([{"propertyName": "email", "operator": "HAS_PROPERTY"}])
    # probe each candidate property to see which one actually holds data
    probe = {}
    for prop, op, val in BOUNCE_PROPS:
        flt = {"propertyName": prop, "operator": op}
        if val is not None:
            flt["value"] = val
        probe[prop] = _count([flt])
    # pick the properties that returned any matches
    active = [(p, op, v) for (p, op, v) in BOUNCE_PROPS if (probe.get(p) or 0) > 0]
    total_bounced = sum(probe.get(p, 0) or 0 for p, _, _ in active) if active else 0

    recs = []
    if active:
        with dash_spinner("Pulling bounced contacts…"):
            # OR across the active signals via multiple filterGroups
            fgs = []
            for p, op, v in active:
                f = {"propertyName": p, "operator": op}
                if v is not None:
                    f["value"] = v
                fgs.append({"filters": [f]})
            recs = fetch_all(
                "contacts",
                ["email", "firstname", "lastname", "hs_email_hard_bounce_reason",
                 "hs_email_hard_bounce_reason_enum", "hs_email_bounce", "hs_email_quarantined",
                 "hs_email_last_send_date", "hs_email_optout", "lifecyclestage"],
                filter_groups=fgs)
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
            "Bounces": p.get("hs_email_bounce") or "",
            "Quarantined": "Yes" if str(p.get("hs_email_quarantined")).lower() == "true" else "",
            "Last Send": _dt(p.get("hs_email_last_send_date")),
            "Opted Out": "Yes" if (p.get("hs_email_optout") in ("true", "True", True)) else "",
            "Lifecycle": (p.get("lifecyclestage") or "").strip() or "—",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "total_email": total_email, "total_bounced": total_bounced,
                       "probe": probe})

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

# diagnostic — which bounce field actually holds data
_probe = saved.get("probe") or {}
if _probe:
    with st.expander("🔬 Which bounce field has data?"):
        pdf = (pd.DataFrame([{"Property": k, "Contacts matched": (v if v is not None else "error")}
                             for k, v in _probe.items()]))
        st.dataframe(pdf, use_container_width=True, hide_index=True)
        st.caption("If every property shows 0, this HubSpot portal simply doesn't record email "
                   "bounces on contacts (e.g. email isn't sent through HubSpot). Tell me where "
                   "bounces are tracked and I'll point the report there.")

if df.empty:
    st.warning("No bounced contacts matched any of the checked fields. See the diagnostic above "
               "for which fields were probed.")
    report_header_close(); st.stop()

# ── reason-type meaning + recommended action ────────────────────────────────────
REASON_INFO = {
    "UNKNOWN_USER": ("Mailbox doesn't exist — wrong, mistyped, or deleted address.",
                     "Permanent", "🧹 Clean up / correct — will never deliver."),
    "MAILBOX_FULL": ("Recipient's inbox is full.",
                     "Often abandoned", "⏳ Usually an inactive account; low priority to retry."),
    "POLICY": ("Recipient's mail server blocked the message (blocklist, sender "
               "reputation, or a content/attachment rule).",
               "Sender-side", "🔎 Check your sending domain reputation — may affect many contacts."),
    "SPAM": ("Recipient's server flagged the message as spam.",
             "Sender-side", "🔎 Review email content & reputation."),
    "OTHER": ("A delivery error that doesn't fit the standard categories.",
              "Mixed", "👀 Review case-by-case."),
    "—": ("Hard bounce recorded, but HubSpot didn't capture a specific reason.",
          "Unknown", "👀 Treat like a bounce; verify the address."),
}

with st.expander("📖 What do these bounce reasons mean?", expanded=True):
    st.markdown("HubSpot classifies each **hard bounce** (permanent failure) into a reason type. "
                "The two big ones — **UNKNOWN_USER** and **MAILBOX_FULL** — are about the "
                "*recipient*; **POLICY** and **SPAM** are about *your sending reputation*.")
    gl = pd.DataFrame([{"Reason Type": k, "Meaning": v[0], "Nature": v[1],
                        "What to do": v[2]} for k, v in REASON_INFO.items()])
    st.dataframe(gl, use_container_width=True, hide_index=True)
    st.caption("**Hard bounce** = permanent failure (won't retry). **Soft bounce** = temporary "
               "(server retries). This report shows hard bounces / quarantined addresses. "
               "**Quarantined** = HubSpot has stopped sending to that address after repeated failures.")

# ── by reason type ──────────────────────────────────────────────────────────────
st.markdown("##### By bounce reason type")
byreason = (df["Reason Type"].value_counts().rename_axis("Reason Type").reset_index(name="Count"))
byreason["%"] = (byreason["Count"] / len(df) * 100).round(1)
byreason["Meaning"] = byreason["Reason Type"].map(lambda x: REASON_INFO.get(x, ("—",))[0])
byreason["Recommended action"] = byreason["Reason Type"].map(
    lambda x: REASON_INFO.get(x, ("", "", "—"))[2])
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
