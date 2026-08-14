import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Now without VRS", layout="wide", page_icon="📱")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Now without VRS")

report_header("Convo Now without VRS",
              "Live Convo Now numbers, classified by whether their email has a VRS number and who generated minutes",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
CACHE_VERSION = 4
_key = f"cn_no_vrs_v{CACHE_VERSION}"

# ── usage-based buckets ──────────────────────────────────────────────────────────
B_NOVRS = "🟣 Convo Now has no VRS number"
B_CN_ONLY = "🟠 CN minutes, but VRS generated none"
B_CN_VRS = "✅ CN minutes and VRS minutes"
B_HASVRS_NOCN = "⚪ Has VRS number, no CN minutes"
BUCKETS = [B_NOVRS, B_CN_ONLY, B_CN_VRS, B_HASVRS_NOCN]


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


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _seek_mv(props, filters, label=""):
    """Seek-paginate Monthly Values past the 10k Search cap via hs_object_id cursor."""
    url = f"{_B}/crm/v3/objects/{MV_OBJECT}/search"
    out, last = [], "0"
    ph = st.empty()
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(6):
            r = requests.post(url, headers=_H, json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            ph.empty(); st.error(f"HubSpot error {getattr(r,'status_code','?')}"); break
        batch = r.json().get("results", [])
        out.extend(batch)
        if label:
            ph.caption(f"{label} {len(out):,} rows…")
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.08)
    ph.empty()
    return out


st.markdown("Works purely off the **Number object**. Reads **Convo Now** numbers, then checks each "
            "number's **email** against the VRS numbers. Usage comes from Monthly Values so each "
            "Convo Now number can be classified by whether it — and any VRS number on the same email "
            "— actually generated minutes. Live = `account_status` is Live. Guest credit type excluded.")
c1, c2 = st.columns([1.3, 1.3])
with c1:
    live_only = st.checkbox("Live Convo Now only", value=True)
with c2:
    usage_since = st.date_input("Usage since", value=date(2026, 1, 1))
run = st.button("▶ Run report", type="primary")

if run:
    # 1) VRS numbers → emails that HAVE a VRS number (+ the numbers, to pull VRS usage)
    with dash_spinner("Reading VRS numbers…"):
        vrs = fetch_all(NUM_OBJECT, ["number", "email", "account_status"],
                        filter_groups=[{"filters": [
                            {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}])
    vrs_emails, vrs_live_emails = set(), set()
    vrs_nums_by_email = defaultdict(set)
    for r in vrs:
        p = r.get("properties", {})
        e = (p.get("email") or "").strip().lower()
        n = str(p.get("number") or "").strip()
        if e:
            vrs_emails.add(e)
            if n:
                vrs_nums_by_email[e].add(n)
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
        cn.append(pp)

    # 3) Convo Now usage per CN number (usage_minutes on Convo Now MV rows)
    cn_numbers = sorted({str(p.get("number") or "").strip() for p in cn
                         if str(p.get("number") or "").strip()})
    cn_usage = defaultdict(float)
    with dash_spinner(f"Pulling Convo Now usage for {len(cn_numbers):,} numbers…"):
        for i in range(0, len(cn_numbers), 200):
            chunk = cn_numbers[i:i + 200]
            for o in _seek_mv(["number", "usage_minutes", "service_type", "month_date"],
                              [{"propertyName": "number", "operator": "IN", "values": chunk},
                               {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                               {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                               {"propertyName": "month_date", "operator": "GTE", "value": _ms(usage_since)}]):
                op = o.get("properties", {})
                cn_usage[str(op.get("number") or "").strip()] += to_float(op.get("usage_minutes")) or 0.0

    # 4) VRS usage per email — only for VRS numbers whose email appears on our CN set
    cn_emails = {(p.get("email") or "").strip().lower() for p in cn
                 if (p.get("email") or "").strip()}
    vrs_nums_to_pull = sorted({n for e in cn_emails for n in vrs_nums_by_email.get(e, ())})
    vrs_num_usage = defaultdict(float)
    if vrs_nums_to_pull:
        with dash_spinner(f"Pulling VRS usage for {len(vrs_nums_to_pull):,} numbers…"):
            for i in range(0, len(vrs_nums_to_pull), 200):
                chunk = vrs_nums_to_pull[i:i + 200]
                for o in _seek_mv(["number", "usage_minutes", "cfz_minutes", "ursa_ios_minutes",
                                   "ursa_android_minutes", "ursa_web_minutes", "service_type", "month_date"],
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                   {"propertyName": "month_date", "operator": "GTE", "value": _ms(usage_since)}]):
                    op = o.get("properties", {})
                    nn = str(op.get("number") or "").strip()
                    mins = (to_float(op.get("ursa_ios_minutes")) or 0.0) \
                        + (to_float(op.get("ursa_android_minutes")) or 0.0) \
                        + (to_float(op.get("ursa_web_minutes")) or 0.0)
                    if mins == 0:  # fall back to raw usage_minutes if URSA split is empty
                        mins = to_float(op.get("usage_minutes")) or 0.0
                    vrs_num_usage[nn] += mins
    # roll VRS minutes up to email
    vrs_min_by_email = defaultdict(float)
    for e, nums in vrs_nums_by_email.items():
        vrs_min_by_email[e] = sum(vrs_num_usage.get(n, 0.0) for n in nums)

    rows = []
    for p in cn:
        n = str(p.get("number") or "").strip()
        status = (p.get("account_status") or p.get("number_status") or "").strip()
        e = (p.get("email") or "").strip()
        el = e.lower()
        cn_min = round(cn_usage.get(n, 0.0), 1)
        has_vrs = bool(el) and el in vrs_emails
        vrs_min = round(vrs_min_by_email.get(el, 0.0), 1) if has_vrs else 0.0

        if not has_vrs:
            bucket = B_NOVRS
        elif cn_min > 0 and vrs_min <= 0:
            bucket = B_CN_ONLY
        elif cn_min > 0 and vrs_min > 0:
            bucket = B_CN_VRS
        else:
            bucket = B_HASVRS_NOCN

        rows.append({
            "Convo Now #": n or "—",
            "Email": e or "(none)",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "CN Status": status or "—",
            "Usage Type": (p.get("usage_type") or "").strip() or "—",
            "Credit Type": (p.get("credit_type") or "").strip() or "—",
            "Credit Plan": (p.get("credit_plan_name") or "").strip() or "—",
            "CN Minutes": cn_min,
            "VRS Minutes": vrs_min,
            "Has VRS #": "Yes" if has_vrs else "No",
            "Created": _iso(p.get("number_created_at")),
            "Pendo ID": (p.get("convo_now_account_id") or "").strip() or "—",
            "Bucket": bucket,
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "n_guest": n_guest, "since": str(usage_since)})

saved = load_report(_key)
if saved is None:
    st.info("Click **▶ Run report** to scan Convo Now numbers.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · usage since {saved.get('since','')} · "
               f"click Run to refresh · **{saved.get('n_guest', 0):,} Guest excluded** (blanks kept)")
if df.empty:
    st.warning("No Convo Now numbers found.")
    report_header_close(); st.stop()

# focus on LIVE Convo Now (toggle stored at run time via saved 'live' would be nicer; filter here)
base = df[df["CN Status"].str.lower() == "live"].copy() if live_only else df.copy()
n_base = len(base)
n_novrs = int((base["Bucket"] == B_NOVRS).sum())
n_cn_only = int((base["Bucket"] == B_CN_ONLY).sum())
n_cn_vrs = int((base["Bucket"] == B_CN_VRS).sum())
n_hasvrs_nocn = int((base["Bucket"] == B_HASVRS_NOCN).sum())


def _card(col, title, val, sub, color):
    col.markdown(
        f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {color};border-radius:12px;
             padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{title}</div>
        <div style="font-size:2rem;font-weight:800;color:{color};line-height:1.1;margin:4px 0 2px;">{val:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{sub}</div></div>""", unsafe_allow_html=True)


k = st.columns(4)
_card(k[0], "🟣 No VRS number", n_novrs,
      f"{(n_novrs/n_base*100):.1f}% of CN" if n_base else "—", "#7A5CFF")
_card(k[1], "🟠 CN mins, no VRS mins", n_cn_only, "CN active, VRS silent", "#E8952A")
_card(k[2], "✅ CN + VRS mins", n_cn_vrs, "both generating", "#2DB84B")
_card(k[3], "⚪ Has VRS, no CN mins", n_hasvrs_nocn, "CN idle this window", "#98A2B3")
st.markdown("")

# ── breakdown ────────────────────────────────────────────────────────────────────
st.markdown("##### By bucket")
bd = base["Bucket"].value_counts().rename_axis("Bucket").reset_index(name="Count")
bd["%"] = (bd["Count"] / n_base * 100).round(1) if n_base else 0
st.dataframe(bd, use_container_width=True, hide_index=True,
             column_config={"Count": st.column_config.ProgressColumn(
                 "Count", min_value=0, max_value=int(bd["Count"].max()) if not bd.empty else 1, format="%d")})

# ── records + dropdown ───────────────────────────────────────────────────────────
st.markdown("##### Records")
f1, f2 = st.columns([1.7, 2])
pick = f1.selectbox("Show", ["All buckets"] + BUCKETS, index=1)  # default: No VRS number
search = f2.text_input("Search number / email / name").strip().lower()

view = base.copy()
if pick != "All buckets":
    view = view[view["Bucket"] == pick]
if search:
    view = view[view["Convo Now #"].str.contains(search, case=False, na=False)
                | view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)]
view = view.sort_values("CN Minutes", ascending=False)
st.caption(f"{len(view):,} records")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_now_without_vrs.csv", "text/csv")

st.caption(f"**{B_NOVRS}** — the Convo Now number's email has no VRS number at all (includes records "
           f"with no email). · **{B_CN_ONLY}** — a VRS number shares the email but generated 0 VRS "
           f"minutes while Convo Now did. · **{B_CN_VRS}** — both Convo Now and the VRS number "
           f"generated minutes. · **{B_HASVRS_NOCN}** — a VRS number exists on the email but Convo "
           f"Now generated no minutes in this window. Association inferred by shared email; VRS "
           f"minutes = URSA iOS+Android+Web (fallback usage_minutes).")

report_header_close()
