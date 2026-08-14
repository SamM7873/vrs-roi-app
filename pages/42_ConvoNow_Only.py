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

report_header("Convo Now Only — No VRS Number",
              "Convo Now numbers whose email has no VRS number at all",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
CONVO_RATE = 2.60
CACHE_VERSION = 3
_key = f"cn_only_v{CACHE_VERSION}"


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


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


def _seek_mv(props, filters, label=""):
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


st.markdown("Finds **Convo Now** numbers with **no associated VRS number** (linked by shared email, "
            "matching the Contact → Number model). Optionally pulls Convo Now usage.")
c1, c2, c3 = st.columns([1.2, 1.4, 1.4])
with c1:
    live_only = st.checkbox("Live only", value=True)
with c2:
    usage_since = st.date_input("Usage since", value=date(2026, 1, 1))
with c3:
    with_usage = st.checkbox("Pull Convo Now usage (slower)", value=False)
run = st.button("▶ Run report", type="primary")

if run:
    # 1) emails that HAVE a VRS number
    with dash_spinner("Reading VRS numbers…"):
        vrs = fetch_all(NUM_OBJECT, ["email"],
                        filter_groups=[{"filters": [
                            {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}])
    vrs_emails = {(r.get("properties", {}).get("email") or "").strip().lower()
                  for r in vrs if (r.get("properties", {}).get("email") or "").strip()}

    # 2) Convo Now numbers
    with dash_spinner("Reading Convo Now numbers…"):
        cn = fetch_all(NUM_OBJECT,
                       ["number", "email", "first_name", "last_name", "account_status",
                        "number_status", "usage_type", "state", "number_created_at",
                        "convo_now_account_id", "credit_type", "credit_plan_name"],
                       filter_groups=[{"filters": [
                           {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}]}])

    # keep only Convo Now with NO VRS on the email (or no email); exclude anything Guest
    only, n_guest = [], 0
    for r in cn:
        p = r.get("properties", {})
        _ct = (p.get("credit_type") or "").strip().lower()
        _cp = (p.get("credit_plan_name") or "").strip().lower()
        if "guest" in _ct or "guest" in _cp:
            n_guest += 1
            continue  # exclude Guest (matches credit_type OR credit_plan_name; blanks kept)
        e = (p.get("email") or "").strip().lower()
        if e and e in vrs_emails:
            continue  # has a VRS number → not "Convo Now only"
        only.append(p)

    # 3) usage (optional) — Convo Now minutes since window
    num_usage = defaultdict(float)
    if with_usage:
        _nums = {str(p.get("number") or "").strip() for p in only if str(p.get("number") or "").strip()}
        mv = _seek_mv(["number", "usage_minutes", "service_type", "month_date"],
                      [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                       {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                       {"propertyName": "month_date", "operator": "GTE", "value": _ms(usage_since)}],
                      label="Convo Now usage:")
        for o in mv:
            op = o.get("properties", {})
            nn = str(op.get("number") or "").strip()
            if nn in _nums:
                num_usage[nn] += to_float(op.get("usage_minutes")) or 0.0

    rows = []
    for p in only:
        n = str(p.get("number") or "").strip()
        status = (p.get("account_status") or p.get("number_status") or "").strip()
        e = (p.get("email") or "").strip()
        row = {
            "Convo Now #": n or "—",
            "Email": e or "(none)",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Status": status or "—",
            "Usage Type": (p.get("usage_type") or "").strip() or "—",
            "Credit Type": (p.get("credit_type") or "").strip() or "—",
            "Credit Plan": (p.get("credit_plan_name") or "").strip() or "—",
            "State": (p.get("state") or "").strip() or "—",
            "Created": _iso(p.get("number_created_at")),
            "Pendo ID": (p.get("convo_now_account_id") or "").strip() or "—",
            "Link": "🔴 No email" if not e else "🟠 No VRS number",
        }
        if with_usage:
            row["CN Min (since)"] = round(num_usage.get(n, 0.0), 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "with_usage": with_usage, "since": str(usage_since),
                       "n_guest": n_guest})

saved = load_report(_key)
if saved is None:
    st.info("Click **▶ Run report** to find Convo Now numbers with no VRS.")
    report_header_close(); st.stop()

df = saved["df"]
with_usage = saved.get("with_usage", False)
if saved.get("saved_at"):
    _ng = saved.get("n_guest", 0)
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · "
               f"**{_ng:,} Guest credit-type numbers excluded** (blanks kept)")
if df.empty:
    st.warning("No Convo Now numbers without VRS found.")
    report_header_close(); st.stop()

live = df[df["Status"].str.lower() == "live"]
n_total = len(df)
n_live = len(live)
n_noemail = int((df["Link"] == "🔴 No email").sum())
n_active = int((df["CN Min (since)"] > 0).sum()) if with_usage and "CN Min (since)" in df.columns else 0
cn_min = float(df["CN Min (since)"].sum()) if with_usage and "CN Min (since)" in df.columns else 0.0


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:16px 18px 13px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2.1rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


# ── dashboard cards ──────────────────────────────────────────────────────────────
k = st.columns(4)
_card(k[0], "📱 Convo Now only", f"{n_total:,}", "no VRS number", "#7A5CFF")
_card(k[1], "🟢 Live", f"{n_live:,}", f"{(n_live/n_total*100):.0f}% of total", "#2DB84B")
_card(k[2], "🔴 No email", f"{n_noemail:,}", "can't associate", "#E5484D")
if with_usage:
    _card(k[3], "🚀 Active (usage)", f"{n_active:,}", "generated CN minutes", "#E8952A")
else:
    _card(k[3], "🏢 Usage types", f"{df['Usage Type'].nunique():,}", "distinct types", "#0FB5AE")
if with_usage:
    k2 = st.columns(4)
    _card(k2[0], "⏱️ Convo Now minutes", f"{cn_min:,.0f}", f"since {saved.get('since','')}", "#4C8DFF")
    _card(k2[1], "💵 Convo Now cost", f"${cn_min * CONVO_RATE:,.0f}", f"@ ${CONVO_RATE}/min", "#0FB5AE")
st.markdown("")

# ── breakdowns ───────────────────────────────────────────────────────────────────
b1, b2 = st.columns(2)
with b1:
    st.markdown("##### By status")
    sc = df["Status"].value_counts().rename_axis("Status").reset_index(name="Count")
    st.dataframe(sc, use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(sc["Count"].max()), format="%d")})
with b2:
    st.markdown("##### By usage type")
    uc = df["Usage Type"].value_counts().rename_axis("Usage Type").reset_index(name="Count")
    st.dataframe(uc, use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(uc["Count"].max()), format="%d")})

# ── by credit type (verify Guest is excluded) ────────────────────────────────────
if "Credit Type" in df.columns:
    st.markdown("##### By credit type (Guest excluded)")
    cc = df["Credit Type"].value_counts().rename_axis("Credit Type").reset_index(name="Count")
    cc["%"] = (cc["Count"] / len(df) * 100).round(1)
    st.dataframe(cc, use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(cc["Count"].max()) if not cc.empty else 1,
                     format="%d")})
    st.caption("Guest is removed before this table — if you still see a 'Guest' row, tell me the "
               "exact value so I can match it.")

# ── records ──────────────────────────────────────────────────────────────────────
st.markdown("##### Records")
f1, f2 = st.columns([1.4, 2])
status_pick = f2.selectbox("Status", ["Live only", "All statuses"])
search = f1.text_input("Search number / email / name").strip().lower()
view = df.copy()
if status_pick == "Live only":
    view = view[view["Status"].str.lower() == "live"]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
if with_usage and "CN Min (since)" in view.columns:
    view = view.sort_values("CN Min (since)", ascending=False)
st.caption(f"{len(view):,} records")
st.dataframe(view, use_container_width=True, hide_index=True, height=440)
st.download_button("📥 Export CSV", view.to_csv(index=False), "convo_now_only_no_vrs.csv", "text/csv")

st.caption("**Convo Now only** = a Convo Now number whose email has **no VRS number** (or has no "
           "email to associate on). Association is inferred by shared email, per the "
           "Contact → Number model.")

report_header_close()
