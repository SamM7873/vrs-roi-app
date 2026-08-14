import streamlit as st
import pandas as pd
from datetime import datetime, timezone
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, dash_spinner, save_report, load_report,
                   saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Now without VRS", layout="wide", page_icon="📱")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Now without VRS")

report_header("Convo Now without VRS",
              "Live Convo Now numbers that have no associated VRS number",
              section="Customers")

NUM_OBJECT = "2-40974683"
CACHE_VERSION = 1
_key = f"cn_no_vrs_v{CACHE_VERSION}"


def _iso(v):
    if not v:
        return ""
    try:
        s = str(v)
        d = (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
             else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return d.strftime("%b %d, %Y")
    except Exception:
        return str(v)[:10]


st.markdown("Finds **Live Convo Now** numbers that are **not linked to any VRS number**. "
            "The link between a customer's Convo Now and VRS numbers is the shared **email**, so a "
            "Convo Now number is flagged when its email has **no VRS number** — or when it has "
            "**no email at all** (nothing to associate on).")
run = st.button("Run report", type="primary")

if run:
    # 1) all VRS numbers → build the set of emails that HAVE a VRS number
    with dash_spinner("Reading VRS numbers…"):
        vrs = fetch_all(NUM_OBJECT, ["number", "email", "account_status"],
                        filter_groups=[{"filters": [
                            {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}])
    vrs_emails, vrs_live_emails = set(), set()
    for r in vrs:
        p = r.get("properties", {})
        e = (p.get("email") or "").strip().lower()
        if e:
            vrs_emails.add(e)
            if (p.get("account_status") or "").strip().lower() == "live":
                vrs_live_emails.add(e)

    # 2) all Convo Now numbers
    with dash_spinner("Reading Convo Now numbers…"):
        cn = fetch_all(NUM_OBJECT,
                       ["number", "email", "first_name", "last_name", "account_status",
                        "number_status", "usage_type", "number_created_at", "convo_now_account_id"],
                       filter_groups=[{"filters": [
                           {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}]}])
    rows = []
    for r in cn:
        p = r.get("properties", {})
        status = (p.get("account_status") or p.get("number_status") or "").strip()
        n = str(p.get("number") or "").strip()
        e = (p.get("email") or "").strip()
        el = e.lower()
        if not el:
            link = "🔴 No email (can't associate)"
        elif el not in vrs_emails:
            link = "🟠 No VRS number on this email"
        elif el not in vrs_live_emails:
            link = "🟡 VRS number exists but not Live"
        else:
            link = "✅ Has Live VRS"
        rows.append({
            "Convo Now #": n or "—",
            "Email": e or "(none)",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "CN Status": status or "—",
            "Usage Type": (p.get("usage_type") or "").strip() or "—",
            "Created": _iso(p.get("number_created_at")),
            "Pendo ID": (p.get("convo_now_account_id") or "").strip() or "—",
            "VRS Link": link,
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run report** to scan Convo Now numbers.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh")
if df.empty:
    st.warning("No Convo Now numbers found.")
    report_header_close(); st.stop()

# focus on LIVE Convo Now
live = df[df["CN Status"].str.lower() == "live"].copy()
n_live = len(live)
n_noemail = int((live["VRS Link"] == "🔴 No email (can't associate)").sum())
n_novrs = int((live["VRS Link"] == "🟠 No VRS number on this email").sum())
n_notlive = int((live["VRS Link"] == "🟡 VRS number exists but not Live").sum())
n_hasvrs = int((live["VRS Link"] == "✅ Has Live VRS").sum())
# "Convo Now without VRS" = the two problem buckets
n_without = n_noemail + n_novrs


def _card(col, title, val, sub, color):
    col.markdown(
        f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {color};border-radius:12px;
             padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{title}</div>
        <div style="font-size:2rem;font-weight:800;color:{color};line-height:1.1;margin:4px 0 2px;">{val:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{sub}</div></div>""", unsafe_allow_html=True)


k = st.columns(5)
_card(k[0], "📱 Live Convo Now", n_live, "total live CN numbers", "#4C8DFF")
_card(k[1], "🚩 CN without VRS", n_without,
      f"{(n_without/n_live*100):.1f}% of live CN" if n_live else "—", "#E5484D")
_card(k[2], "🟠 No VRS on email", n_novrs, "email has no VRS number", "#E8952A")
_card(k[3], "🔴 No email", n_noemail, "can't associate", "#B0342A")
_card(k[4], "✅ Has Live VRS", n_hasvrs, "linked to a live VRS", "#2DB84B")
st.markdown("")

st.info(f"**{n_without:,} Live Convo Now numbers have no VRS number** "
        f"({n_novrs:,} with an email that has no VRS · {n_noemail:,} with no email). "
        f"Additionally {n_notlive:,} share an email with a VRS number that isn't Live.")

# ── breakdown by VRS link ────────────────────────────────────────────────────────
st.markdown("##### By VRS link status (Live Convo Now)")
bd = live["VRS Link"].value_counts().rename_axis("VRS Link").reset_index(name="Count")
bd["%"] = (bd["Count"] / n_live * 100).round(1) if n_live else 0
st.dataframe(bd, use_container_width=True, hide_index=True,
             column_config={"Count": st.column_config.ProgressColumn(
                 "Count", min_value=0, max_value=int(bd["Count"].max()) if not bd.empty else 1, format="%d")})

# ── records ──────────────────────────────────────────────────────────────────────
st.markdown("##### Records")
f1, f2, f3 = st.columns([1.4, 1.4, 2])
link_opts = ["🟠 No VRS number on this email", "🔴 No email (can't associate)",
             "🟡 VRS number exists but not Live", "✅ Has Live VRS"]
pick = f1.multiselect("VRS link", link_opts,
                      default=["🟠 No VRS number on this email", "🔴 No email (can't associate)"])
status_pick = f2.selectbox("CN status", ["Live only", "All statuses"])
search = f3.text_input("Search number / email / name").strip().lower()

view = df.copy()
if status_pick == "Live only":
    view = view[view["CN Status"].str.lower() == "live"]
if search:
    view = view[view["Convo Now #"].str.contains(search, case=False, na=False)
                | view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)]
elif pick:
    view = view[view["VRS Link"].isin(pick)]
st.caption(f"{len(view):,} records")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_now_without_vrs.csv", "text/csv")

st.caption("🟠 = the Convo Now number's email has no VRS number at all · 🔴 = no email on the "
           "record, so it can't be linked to any VRS number · 🟡 = a VRS number shares the email "
           "but isn't Live · ✅ = linked to a Live VRS number. Association is inferred by shared email.")

report_header_close()
