import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Now Only (No VRS)", layout="wide", page_icon="📱")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Now Only")

report_header("Convo Now Only — VRS relationship",
              "Convo Now numbers classified by whether their email has a VRS number and who generated minutes",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
CONVO_RATE = 2.60
CACHE_VERSION = 9
_key = f"cn_only_v{CACHE_VERSION}"

# ── usage-based buckets ──────────────────────────────────────────────────────────
B_NOVRS = "🟣 Convo Now has no VRS number"
B_CN_ONLY = "🟠 CN minutes, but VRS generated none"
B_CN_VRS = "✅ CN minutes and VRS minutes"
B_HASVRS_NOCN = "⚪ Has VRS number, no CN minutes"
BUCKETS = [B_NOVRS, B_CN_ONLY, B_CN_VRS, B_HASVRS_NOCN]


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _month_firsts(start=date(2025, 1, 1)):
    """First-of-month dates from `start` through the current month."""
    out, y, m, today = [], start.year, start.month, date.today()
    while (y, m) <= (today.year, today.month):
        out.append(date(y, m, 1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _trailing(n, include_current):
    """List of n month-firsts, ending at current month (include_current) or the month before."""
    today = date.today()
    y, m = today.year, today.month
    if not include_current:
        m -= 1
        if m < 1:
            m, y = 12, y - 1
    out = []
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m < 1:
            m, y = 12, y - 1
    return list(reversed(out))


PERIODS = ["This month", "Last month", "Last 2 months", "Last 3 months", "Last 4 months",
           "Last 5 months", "Last 6 months", "This quarter", "Last quarter",
           "This year", "Last year", "All since Jan 2026", "Custom (month picker)"]


def _resolve_period(period, custom):
    """Return a list of first-of-month dates for the chosen period ([] = all-since)."""
    today = date.today()
    cy, cm = today.year, today.month
    if period == "Custom (month picker)":
        return custom
    if period == "All since Jan 2026":
        return []
    if period == "This month":
        return [date(cy, cm, 1)]
    if period == "Last month":
        return _trailing(1, include_current=False)
    if period.startswith("Last ") and period.endswith("months"):
        return _trailing(int(period.split()[1]), include_current=False)
    if period == "This quarter":
        qs = ((cm - 1) // 3) * 3 + 1
        return [date(cy, mm, 1) for mm in range(qs, cm + 1)]
    if period == "Last quarter":
        qs = ((cm - 1) // 3) * 3 + 1 - 3
        y = cy
        if qs < 1:
            qs, y = qs + 12, cy - 1
        return [date(y, qs + i, 1) for i in range(3)]
    if period == "This year":
        return [date(cy, mm, 1) for mm in range(1, cm + 1)]
    if period == "Last year":
        return [date(cy - 1, mm, 1) for mm in range(1, 13)]
    return []


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


def _seek(object_id, props, filters, label=""):
    """Seek-paginate any object past the 10k Search cap via hs_object_id cursor."""
    url = f"{_B}/crm/v3/objects/{object_id}/search"
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


st.markdown("Works off the **Number object**. Reads **Convo Now** numbers, checks each number's "
            "**email** against the VRS numbers, and pulls **CN and VRS minutes** from Monthly Values "
            "so each number lands in one of these buckets. Guest credit type excluded.")

# ── live quick lookup (bypasses buckets/filters; uses the app's own token) ────────
with st.expander("🔎 Quick lookup — find a number or email in the Number object (live, no filters)"):
    q = st.text_input("Enter a phone number or email", key="quicklook").strip()
    if st.button("Look up", key="quicklook_btn") and q:
        is_email = "@" in q
        prop = "email" if is_email else "number"
        val = q.lower() if is_email else "".join(ch for ch in q if ch.isdigit())
        with dash_spinner("Searching the Number object…"):
            hits = fetch_all(NUM_OBJECT,
                             ["number", "email", "first_name", "last_name", "service_type",
                              "account_status", "credit_type", "credit_plan_name", "account_id",
                              "usage_type", "convo_now_account_id"],
                             filter_groups=[{"filters": [
                                 {"propertyName": prop, "operator": "EQ", "value": val}]}])
        if not hits:
            st.warning(f"No Number-object record where **{prop} = {val}**. "
                       "If you searched a number, it may be stored with different formatting; "
                       "try the email instead.")
        else:
            qrows = []
            for h in hits:
                pp = h.get("properties", {})
                qrows.append({
                    "Number": pp.get("number") or "—",
                    "Service": pp.get("service_type") or "—",
                    "Email": pp.get("email") or "—",
                    "Name": f"{(pp.get('first_name') or '').strip()} {(pp.get('last_name') or '').strip()}".strip() or "—",
                    "Status": pp.get("account_status") or "—",
                    "Credit Type": pp.get("credit_type") or "—",
                    "Credit Plan": pp.get("credit_plan_name") or "—",
                    "Account ID": pp.get("account_id") or "—",
                })
            qdf = pd.DataFrame(qrows)
            st.dataframe(qdf, use_container_width=True, hide_index=True)
            svc = qdf["Service"].str.lower()
            has_cn = svc.str.contains("convo now").any()
            has_vrs = (svc == "vrs").any()
            is_guest = qdf.apply(lambda r: "guest" in str(r["Credit Type"]).lower()
                                 or "guest" in str(r["Credit Plan"]).lower(), axis=1).any()
            msg = []
            msg.append("✅ Has a **Convo Now** number" if has_cn else "❌ No **Convo Now** number "
                       "(so it won't appear on this page)")
            msg.append("✅ Has a **VRS** number on this email" if has_vrs else "🟣 No VRS number on this email")
            if is_guest:
                msg.append("⚠️ A matching row is **Guest** credit type → excluded from the report")
            st.info(" · ".join(msg))
c1, c2 = st.columns([1.6, 1.1])
with c1:
    pick = st.selectbox("Filter (bucket)", ["All buckets"] + BUCKETS, index=1)
with c2:
    live_only = st.checkbox("Live only", value=True)

_fmt = "%m-%d-%Y"
p1, p2 = st.columns([1.3, 2])
period = p1.selectbox("Period", PERIODS, index=PERIODS.index("This month"))
_month_opts = list(reversed(_month_firsts()))  # newest first
custom_months = p2.multiselect("Custom months (used when Period = Custom)",
                               _month_opts, default=[], format_func=lambda d: d.strftime(_fmt))
months = _resolve_period(period, custom_months)
if months:
    st.caption("Months included: " + ", ".join(m.strftime(_fmt) for m in months))
else:
    st.caption("Months included: all since 01-01-2026")
usage_since = date(2026, 1, 1)
run = st.button("▶ Run report", type="primary")

if run:
    # month filter: exact first-of-month values if chosen, else everything since Jan 2026
    _month_filter = ({"propertyName": "month_date", "operator": "IN",
                      "values": [_ms(m) for m in months]} if months else
                     {"propertyName": "month_date", "operator": "GTE", "value": _ms(usage_since)})

    # 1) VRS numbers → emails that have a VRS number, and the VRS numbers per email
    with dash_spinner("Reading VRS numbers…"):
        vrs = _seek(NUM_OBJECT, ["number", "email", "account_status"],
                    [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"}],
                    label="VRS numbers:")
    vrs_emails = set()
    vrs_nums_by_email = defaultdict(set)
    for r in vrs:
        p = r.get("properties", {})
        e = (p.get("email") or "").strip().lower()
        n = str(p.get("number") or "").strip()
        if e:
            vrs_emails.add(e)
            if n:
                vrs_nums_by_email[e].add(n)

    # 2) Convo Now numbers (exclude Guest; keep blanks)
    with dash_spinner("Reading Convo Now numbers…"):
        cn_raw = _seek(NUM_OBJECT,
                       ["number", "email", "first_name", "last_name", "account_status",
                        "number_status", "usage_type", "state", "number_created_at",
                        "number_deleted_at", "account_id", "convo_now_account_id",
                        "credit_type", "credit_plan_name"],
                       [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}],
                       label="Convo Now numbers:")
    cn, n_guest = [], 0
    for r in cn_raw:
        p = r.get("properties", {})
        if "guest" in (p.get("credit_type") or "").strip().lower() \
                or "guest" in (p.get("credit_plan_name") or "").strip().lower():
            n_guest += 1
            continue
        cn.append(p)

    # 3) Convo Now usage per CN number
    cn_numbers = sorted({str(p.get("number") or "").strip() for p in cn
                         if str(p.get("number") or "").strip()})
    cn_usage = defaultdict(float)
    with dash_spinner(f"Pulling Convo Now usage for {len(cn_numbers):,} numbers…"):
        for i in range(0, len(cn_numbers), 100):
            chunk = cn_numbers[i:i + 100]
            for o in _seek(MV_OBJECT, ["number", "usage_minutes", "service_type", "month_date"],
                              [{"propertyName": "number", "operator": "IN", "values": chunk},
                               {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                               {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                               _month_filter]):
                op = o.get("properties", {})
                cn_usage[str(op.get("number") or "").strip()] += to_float(op.get("usage_minutes")) or 0.0

    # 4) VRS usage per email — only VRS numbers whose email is on our CN set
    cn_emails = {(p.get("email") or "").strip().lower() for p in cn if (p.get("email") or "").strip()}
    vrs_nums_to_pull = sorted({n for e in cn_emails for n in vrs_nums_by_email.get(e, ())})
    vrs_num_usage = defaultdict(float)
    if vrs_nums_to_pull:
        with dash_spinner(f"Pulling VRS usage for {len(vrs_nums_to_pull):,} numbers…"):
            for i in range(0, len(vrs_nums_to_pull), 100):
                chunk = vrs_nums_to_pull[i:i + 100]
                for o in _seek(MV_OBJECT, ["number", "usage_minutes", "service_type", "month_date"],
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                   _month_filter]):
                    op = o.get("properties", {})
                    nn = str(op.get("number") or "").strip()
                    vrs_num_usage[nn] += to_float(op.get("usage_minutes")) or 0.0
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
            "Account ID": (p.get("account_id") or "").strip() or "—",
            "Email": e or "(none)",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Status": status or "—",
            "Usage Type": (p.get("usage_type") or "").strip() or "—",
            "Credit Type": (p.get("credit_type") or "").strip() or "—",
            "Credit Plan": (p.get("credit_plan_name") or "").strip() or "—",
            "State": (p.get("state") or "").strip() or "—",
            "CN Minutes": cn_min,
            "VRS Minutes": vrs_min,
            "Has VRS #": "Yes" if has_vrs else "No",
            "Created": _iso(p.get("number_created_at")),
            "Deleted": _iso(p.get("number_deleted_at")),
            "Bucket": bucket,
        })
    df = pd.DataFrame(rows)
    _mrange = ", ".join(m.strftime(_fmt) for m in months) if months else "all since 01-01-2026"
    _mlabel = f"{period} · {_mrange}"
    save_report(_key, {"df": df, "since": str(usage_since), "months": _mlabel, "n_guest": n_guest})

saved = load_report(_key)
if saved is None:
    st.info("Click **▶ Run report** to classify Convo Now numbers.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · months: {saved.get('months', saved.get('since',''))} · "
               f"click Run to refresh · **{saved.get('n_guest', 0):,} Guest excluded** (blanks kept)")
if df.empty:
    st.warning("No Convo Now numbers found.")
    report_header_close(); st.stop()

base = df[df["Status"].str.lower() == "live"].copy() if live_only else df.copy()
n_base = len(base)


def _card(col, t, v, s, c, active=False):
    ring = f"box-shadow:0 0 0 2px {c}55;" if active else ""
    bg = f"{c}12" if active else "rgba(127,127,127,0.03)"
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:16px 18px 13px;background:{bg};{ring}">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2.1rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


# ── live filters: credit type + credit plan drive the whole dashboard ────────────
ctype_opts = sorted([x for x in base["Credit Type"].dropna().unique() if x and x != "—"])
plan_opts = sorted([x for x in base["Credit Plan"].dropna().unique() if x and x != "—"])
fc1, fc2, fc3 = st.columns([1.4, 1.6, 2])
ctype_pick = fc1.multiselect("Credit type (filters everything below)", ctype_opts, default=[])
plan_pick = fc2.multiselect("Credit plan (filters everything below)", plan_opts, default=[])
search = fc3.text_input("Search — number / email / first name / last name / account ID").strip().lower()

# credit-type + plan-filtered slice → drives bucket cards & breakdowns
pv = base.copy()
if ctype_pick:
    pv = pv[pv["Credit Type"].isin(ctype_pick)]
if plan_pick:
    pv = pv[pv["Credit Plan"].isin(plan_pick)]
fv = pv if pick == "All buckets" else pv[pv["Bucket"] == pick].copy()
n_pv, n_fv = len(pv), len(fv)

# context banner — what fraction of total you're viewing
sel_bits = []
if pick != "All buckets":
    sel_bits.append(f"bucket **{pick}**")
if ctype_pick:
    sel_bits.append(f"type **{', '.join(ctype_pick)}**")
if plan_pick:
    sel_bits.append(f"plan **{', '.join(plan_pick)}**")
sel_txt = " · ".join(sel_bits) if sel_bits else "no filter"
st.markdown(
    f"""<div style="border:1px solid #E6E9F0;border-radius:10px;padding:10px 14px;margin:2px 0 12px;
    background:rgba(76,141,255,0.06);font-size:.85rem;">
    Showing <b>{n_fv:,}</b> of <b>{n_base:,}</b> Convo Now numbers · {sel_txt}</div>""",
    unsafe_allow_html=True)

# ── bucket cards (reflect the plan-filtered slice; selected bucket highlighted) ───
n_novrs = int((pv["Bucket"] == B_NOVRS).sum())
n_cn_only = int((pv["Bucket"] == B_CN_ONLY).sum())
n_cn_vrs = int((pv["Bucket"] == B_CN_VRS).sum())
n_hasvrs_nocn = int((pv["Bucket"] == B_HASVRS_NOCN).sum())
k = st.columns(4)
_card(k[0], "🟣 No VRS number", f"{n_novrs:,}",
      f"{(n_novrs/n_pv*100):.1f}% of shown" if n_pv else "—", "#7A5CFF", pick == B_NOVRS)
_card(k[1], "🟠 CN mins, no VRS mins", f"{n_cn_only:,}", "CN active, VRS silent", "#E8952A", pick == B_CN_ONLY)
_card(k[2], "✅ CN + VRS mins", f"{n_cn_vrs:,}", "both generating", "#2DB84B", pick == B_CN_VRS)
_card(k[3], "⚪ Has VRS, no CN mins", f"{n_hasvrs_nocn:,}", "CN idle this window", "#98A2B3", pick == B_HASVRS_NOCN)

# ── totals (reflect the fully-filtered view: bucket + plan) ───────────────────────
cn_min_fv = float(fv["CN Minutes"].sum())
k2 = st.columns(4)
_card(k2[0], "📱 Convo Now (shown)", f"{n_fv:,}", f"of {n_base:,} total", "#4C8DFF")
_card(k2[1], "⏱️ Convo Now minutes", f"{cn_min_fv:,.0f}", saved.get('months', ''), "#0FB5AE")
_card(k2[2], "💵 Convo Now cost", f"${cn_min_fv * CONVO_RATE:,.0f}", f"@ ${CONVO_RATE}/min", "#3563E9")
_card(k2[3], "🏢 Usage types", f"{fv['Usage Type'].nunique():,}", "distinct types", "#E5484D")
st.markdown("")

# ── breakdowns (reflect the filtered view) ───────────────────────────────────────
b1, b2 = st.columns(2)
with b1:
    st.markdown("##### By bucket (within filter)")
    bd = pv["Bucket"].value_counts().rename_axis("Bucket").reset_index(name="Count")
    bd["%"] = (bd["Count"] / n_pv * 100).round(1) if n_pv else 0
    st.dataframe(bd, use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(bd["Count"].max()) if not bd.empty else 1,
                     format="%d")})
with b2:
    st.markdown("##### By credit plan (within filter)")
    cc = fv["Credit Plan"].value_counts().rename_axis("Credit Plan").reset_index(name="Count")
    st.dataframe(cc, use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(cc["Count"].max()) if not cc.empty else 1,
                     format="%d")})

# ── records ──────────────────────────────────────────────────────────────────────
if search:
    # search ignores bucket/plan AND the Live-only filter so any record is findable
    st.markdown("##### Records — search (all buckets, all statuses)")
    view = df[df.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
else:
    st.markdown(f"##### Records — {pick}")
    view = fv.copy()
view = view.sort_values("CN Minutes", ascending=False)
st.caption(f"{len(view):,} records")
st.dataframe(view, use_container_width=True, hide_index=True, height=440)
st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_now_only.csv", "text/csv")

st.caption(f"**{B_NOVRS}** — email has no VRS number at all (includes no-email records). · "
           f"**{B_CN_ONLY}** — a VRS number shares the email but generated 0 VRS minutes while Convo "
           f"Now did. · **{B_CN_VRS}** — both generated minutes. · **{B_HASVRS_NOCN}** — a VRS number "
           f"exists on the email but Convo Now generated no minutes this window. Association inferred "
           f"by shared email; VRS minutes = usage_minutes on the VRS number's Monthly Values rows.")

report_header_close()
