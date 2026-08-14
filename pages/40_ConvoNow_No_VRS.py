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
MV_OBJECT = "2-46246179"
CACHE_VERSION = 3
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


st.markdown("Follows **Contact → Number → Monthly Values**: matches the Contact's **email** to the "
            "Number object, then the Number to Monthly Values. Flags a **Live Convo Now** number "
            "when its email has **no VRS number** (or no email to associate on). "
            "Live = Number `account_status` is Live.")
with_usage = st.checkbox("Include Convo Now usage from Monthly Values (slower)", value=False)
run = st.button("Run report", type="primary")

if run:
    # 1) VRS numbers → emails that HAVE a VRS number (and which are Live)
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

    # 2) Convo Now numbers (exclude Guest credit type; keep blanks)
    with dash_spinner("Reading Convo Now numbers…"):
        cn_raw = fetch_all(NUM_OBJECT,
                           ["number", "email", "first_name", "last_name", "account_status",
                            "number_status", "usage_type", "number_created_at",
                            "convo_now_account_id", "credit_type", "credit_plan_name"],
                           filter_groups=[{"filters": [
                               {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}]}])
    cn, n_guest = [], 0
    for r in cn_raw:
        pp = r.get("properties", {})
        if "guest" in (pp.get("credit_type") or "").strip().lower() \
                or "guest" in (pp.get("credit_plan_name") or "").strip().lower():
            n_guest += 1
            continue
        cn.append(r)

    # 3) Contacts — confirm each Convo Now email maps to a real Contact (Contact→Number link)
    cn_emails = sorted({(r.get("properties", {}).get("email") or "").strip().lower()
                        for r in cn if (r.get("properties", {}).get("email") or "").strip()})
    contact_of = {}
    with dash_spinner(f"Matching {len(cn_emails):,} emails to Contacts…"):
        for i in range(0, len(cn_emails), 100):
            chunk = cn_emails[i:i + 100]
            for c in fetch_all("contacts", ["email", "firstname", "lastname", "lifecyclestage",
                                            "convo_now_account_id"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                cp = c.get("properties", {})
                ce = (cp.get("email") or "").strip().lower()
                if ce and ce not in contact_of:
                    contact_of[ce] = {
                        "name": f"{(cp.get('firstname') or '').strip()} {(cp.get('lastname') or '').strip()}".strip(),
                        "lifecycle": (cp.get("lifecyclestage") or "").strip(),
                        "pendo": (cp.get("convo_now_account_id") or "").strip()}

    # 4) Monthly Values — Convo Now usage per number (optional; Number→MV link)
    cn_usage = {}
    if with_usage:
        cn_numbers = sorted({str(r.get("properties", {}).get("number") or "").strip()
                             for r in cn if str(r.get("properties", {}).get("number") or "").strip()})
        with dash_spinner(f"Pulling Monthly Values for {len(cn_numbers):,} Convo Now numbers…"):
            for i in range(0, len(cn_numbers), 100):
                chunk = cn_numbers[i:i + 100]
                for o in fetch_all(MV_OBJECT, ["number", "usage_minutes", "service_type"],
                                   filter_groups=[{"filters": [
                                       {"propertyName": "number", "operator": "IN", "values": chunk},
                                       {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}]}]):
                    op = o.get("properties", {})
                    nn = str(op.get("number") or "").strip()
                    try:
                        cn_usage[nn] = cn_usage.get(nn, 0.0) + float(op.get("usage_minutes") or 0)
                    except (TypeError, ValueError):
                        pass

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
        c = contact_of.get(el, {})
        nm = c.get("name") or f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip()
        row = {
            "Convo Now #": n or "—",
            "Email": e or "(none)",
            "Contact": nm or "—",
            "Contact found": "Yes" if (el and el in contact_of) else "No",
            "Lifecycle": c.get("lifecycle") or "—",
            "CN Status": status or "—",
            "Usage Type": (p.get("usage_type") or "").strip() or "—",
            "Credit Type": (p.get("credit_type") or "").strip() or "—",
            "Credit Plan": (p.get("credit_plan_name") or "").strip() or "—",
            "Created": _iso(p.get("number_created_at")),
            "Pendo ID": (p.get("convo_now_account_id") or c.get("pendo") or "").strip() or "—",
            "VRS Link": link,
        }
        if with_usage:
            row["CN Minutes"] = round(cn_usage.get(n, 0.0), 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "with_usage": with_usage, "n_guest": n_guest})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run report** to scan Convo Now numbers.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · "
               f"**{saved.get('n_guest', 0):,} Guest excluded** (blanks kept)")
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
