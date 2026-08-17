import streamlit as st
import pandas as pd
import time
import re
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Reactivation Tracker", layout="wide", page_icon="🚀")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Reactivation Tracker")

report_header("VRS Reactivation Tracker",
              "Upload a VRS-zero / CN-active cohort → track URSA logins & minutes after the campaign",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
SAVE_KEY = "reactivation_tracker_v1"


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _period(v):
    try:
        s = str(v)
        dt = (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
              else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return dt.strftime("%Y-%m")
    except Exception:
        return "—"


def _dt(v):
    if not v:
        return None
    try:
        s = str(v)
        return (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
                else datetime.fromisoformat(s.replace("Z", "+00:00")))
    except Exception:
        return None


def _latest(*vals):
    ds = [d for d in (_dt(v) for v in vals) if d]
    return max(ds) if ds else None


def _nums(cell):
    """Split a 'VRS Numbers' / 'Convo Now Numbers' cell into a clean list of digit strings."""
    return [re.sub(r"\D", "", x) for x in str(cell or "").split(",") if re.sub(r"\D", "", x)]


def _seek_mv(props, filters):
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
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.06)
    ph.empty()
    return out


st.markdown(
    "Upload the **VRS Zero / Convo Now Active** export (or any CSV with **VRS Numbers** and "
    "**Convo Now Numbers** columns). We pull **Monthly Values** (VRS / URSA / CfZ / CN minutes) and "
    "the **URSA last-login dates** for those numbers, then flag who reactivated **after the campaign**.")

c1, c2, c3 = st.columns([2, 1.3, 1.3])
with c1:
    up = st.file_uploader("Cohort CSV", type=["csv"], key="react_csv")
with c2:
    usage_since = st.date_input("Usage since (month)", value=date(2026, 5, 1))
with c3:
    campaign_date = st.date_input("Campaign release date", value=date(2026, 7, 31),
                                  help="Flag URSA logins on/after this date as reactivated.")
run = st.button("▶ Run", type="primary", disabled=(up is None))

if run and up is not None:
    up.seek(0)
    raw = pd.read_csv(up, dtype=str).fillna("")
    cols = {c.lower().strip(): c for c in raw.columns}

    def col(*cands):
        for cand in cands:
            if cand.lower() in cols:
                return cols[cand.lower()]
        return None

    vrs_col = col("VRS Numbers", "vrs number")
    cn_col = col("Convo Now Numbers", "convo now number", "cn numbers")
    name_col = col("Name")
    email_col = col("Email")
    pendo_col = col("Pendo ID")
    if not vrs_col and not cn_col:
        st.error(f"Couldn't find 'VRS Numbers' or 'Convo Now Numbers' columns. Found: {list(raw.columns)}")
        st.stop()

    people = []
    all_vrs, all_cn = set(), set()
    for _, r in raw.iterrows():
        vn = _nums(r.get(vrs_col, "")) if vrs_col else []
        cn = _nums(r.get(cn_col, "")) if cn_col else []
        all_vrs.update(vn); all_cn.update(cn)
        people.append({
            "Name": (r.get(name_col) or "").strip() if name_col else "",
            "Email": (r.get(email_col) or "").strip() if email_col else "",
            "Pendo ID": (r.get(pendo_col) or "").strip() if pendo_col else "",
            "vrs": vn, "cn": cn})

    # 1) URSA last-login dates from the Number object (per VRS number)
    login_by_num = {}
    vrs_list = sorted(all_vrs)
    lprops = ["number", "last_login_ursa_convo_ios_date", "last_login_ursa_convo_android_date",
              "last_login_ursa_convo_web_date"]
    with dash_spinner(f"Reading URSA logins for {len(vrs_list):,} VRS numbers…"):
        for i in range(0, len(vrs_list), 100):
            chunk = vrs_list[i:i + 100]
            for rr in fetch_all(NUM_OBJECT, lprops, filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk}]}]):
                p = rr.get("properties", {})
                login_by_num[str(p.get("number") or "").strip()] = {
                    "ios": p.get("last_login_ursa_convo_ios_date") or "",
                    "android": p.get("last_login_ursa_convo_android_date") or "",
                    "web": p.get("last_login_ursa_convo_web_date") or ""}

    # 2) Monthly Values for VRS + CN numbers since window
    v_vrs, v_ursa, v_cfz, v_cn = (defaultdict(float) for _ in range(4))
    d_ursa, d_vrs, d_cfz, d_cn = (defaultdict(float) for _ in range(4))
    both = sorted(all_vrs | all_cn)
    with dash_spinner(f"Pulling Monthly Values for {len(both):,} numbers…"):
        for i in range(0, len(both), 100):
            chunk = both[i:i + 100]
            for o in _seek_mv(["number", "month_date", "usage_minutes", "ursa_minutes",
                               "cfz_minutes", "service_type"],
                              [{"propertyName": "number", "operator": "IN", "values": chunk},
                               {"propertyName": "month_date", "operator": "GTE", "value": _ms(usage_since)}]):
                p = o.get("properties", {})
                nn = str(p.get("number") or "").strip()
                mins = to_float(p.get("usage_minutes")) or 0.0
                ursa = to_float(p.get("ursa_minutes")) or 0.0
                cfz = to_float(p.get("cfz_minutes")) or 0.0
                svc = (p.get("service_type") or "").strip().lower()
                m = _period(p.get("month_date"))
                if svc == "vrs":
                    v_vrs[nn] += mins; v_ursa[nn] += ursa; v_cfz[nn] += cfz
                    d_vrs[(nn, m)] += mins; d_ursa[(nn, m)] += ursa; d_cfz[(nn, m)] += cfz
                elif svc == "convo now":
                    v_cn[nn] += mins; d_cn[(nn, m)] += mins

    rows = []
    for pr in people:
        ios = _latest(*[login_by_num.get(n, {}).get("ios") for n in pr["vrs"]])
        andr = _latest(*[login_by_num.get(n, {}).get("android") for n in pr["vrs"]])
        web = _latest(*[login_by_num.get(n, {}).get("web") for n in pr["vrs"]])
        latest_login = _latest(*[x for n in pr["vrs"] for x in login_by_num.get(n, {}).values()])
        post = bool(latest_login and latest_login.date() >= campaign_date)
        vrs_min = round(sum(v_vrs.get(n, 0.0) for n in pr["vrs"]), 1)
        ursa_min = round(sum(v_ursa.get(n, 0.0) for n in pr["vrs"]), 1)
        cfz_min = round(sum(v_cfz.get(n, 0.0) for n in pr["vrs"]), 1)
        cn_min = round(sum(v_cn.get(n, 0.0) for n in pr["cn"]), 1)
        rows.append({
            "Name": pr["Name"] or "—", "Email": pr["Email"] or "—", "Pendo ID": pr["Pendo ID"] or "—",
            "VRS Numbers": ", ".join(pr["vrs"]) or "—",
            "CN Numbers": ", ".join(pr["cn"]) or "—",
            "Reactivated": "🚀 Yes" if (post or ursa_min > 0) else "—",
            "Login after campaign": "✅ Yes" if post else "—",
            "URSA Min": ursa_min, "VRS Min": vrs_min, "CfZ Min": cfz_min, "CN Min": cn_min,
            "URSA iOS Login": (ios.strftime("%b %d, %Y") if ios else "—"),
            "URSA Android Login": (andr.strftime("%b %d, %Y") if andr else "—"),
            "URSA Web Login": (web.strftime("%b %d, %Y") if web else "—"),
        })
    df = pd.DataFrame(rows)

    months = sorted({m for (_, m) in list(d_ursa) + list(d_vrs) + list(d_cfz) + list(d_cn)})
    monthly = pd.DataFrame([{
        "Month": m,
        "VRS Minutes": round(sum(v for (nn, mm), v in d_vrs.items() if mm == m), 1),
        "URSA Minutes": round(sum(v for (nn, mm), v in d_ursa.items() if mm == m), 1),
        "CfZ Minutes": round(sum(v for (nn, mm), v in d_cfz.items() if mm == m), 1),
        "CN Minutes": round(sum(v for (nn, mm), v in d_cn.items() if mm == m), 1),
        "URSA Active accounts": sum(1 for (nn, mm), v in d_ursa.items() if mm == m and v > 0),
    } for m in months])

    save_report(SAVE_KEY, {"df": df, "monthly": monthly, "n": len(people),
                           "since": str(usage_since), "campaign": str(campaign_date)})

saved = load_report(SAVE_KEY)
if saved is None:
    st.info("Upload the cohort CSV and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · cohort of {saved.get('n', len(df)):,} · "
               f"usage since {saved.get('since','')} · campaign {saved.get('campaign','')}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


n_total = len(df)
n_react = int((df["Reactivated"] == "🚀 Yes").sum())
n_login = int((df["Login after campaign"] == "✅ Yes").sum())
tot_ursa = int(round(df["URSA Min"].sum()))
k = st.columns(4)
_card(k[0], "🧬 Cohort", n_total, "uploaded rows", "#7A5CFF")
_card(k[1], "🚀 Reactivated", n_react, "URSA login post-campaign or URSA min", "#2DB84B")
_card(k[2], "📲 Login after campaign", n_login, f"URSA login ≥ {saved.get('campaign','')}", "#4C8DFF")
_card(k[3], "⏱️ Total URSA minutes", tot_ursa, "since window", "#0FB5AE")
st.markdown("")

monthly = saved.get("monthly")
st.markdown("##### 📈 Reactivation by month")
if monthly is not None and not monthly.empty:
    mx = int(max(monthly["URSA Minutes"].max(), monthly["VRS Minutes"].max(),
                 monthly["CN Minutes"].max(), 1))
    st.dataframe(monthly, use_container_width=True, hide_index=True, column_config={
        "VRS Minutes": st.column_config.ProgressColumn("VRS Minutes", min_value=0, max_value=mx, format="%.0f"),
        "URSA Minutes": st.column_config.ProgressColumn("URSA Minutes", min_value=0, max_value=mx, format="%.0f"),
        "CfZ Minutes": st.column_config.ProgressColumn("CfZ Minutes", min_value=0, max_value=mx, format="%.0f"),
        "CN Minutes": st.column_config.ProgressColumn("CN Minutes", min_value=0, max_value=mx, format="%.0f")})
else:
    st.info("No monthly usage — set **Usage since** earlier and re-run.")

st.markdown("##### Records")
f1, f2 = st.columns([1.5, 2])
only_react = f1.selectbox("Show", ["Reactivated only", "Login after campaign only", "All"])
search = f2.text_input("Search name / email / number / pendo id").strip().lower()
view = df.copy()
if only_react == "Reactivated only":
    view = view[view["Reactivated"] == "🚀 Yes"]
elif only_react == "Login after campaign only":
    view = view[view["Login after campaign"] == "✅ Yes"]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
view = view.sort_values("URSA Min", ascending=False)
st.caption(f"{len(view):,} of {n_total:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "reactivation_tracker.csv", "text/csv")

report_header_close()
