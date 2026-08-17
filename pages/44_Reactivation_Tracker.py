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

st.set_page_config(page_title="Campaign Reactivation", layout="wide", page_icon="🚀")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Campaign Reactivation")

report_header("Campaign Reactivation Analysis",
              "Did the campaign audience change behavior? Before → After usage, reactivation & outcomes",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
SAVE_KEY = "campaign_reactivation_v4"

# ── outcome categories ───────────────────────────────────────────────────────────
O_REACT_USAGE = "🚀 Reactivated + Usage"
O_REACT_NOUSE = "🟡 Reactivated — No Usage"
O_ACTIVE_USAGE = "✅ Already Active + Usage"
O_ACTIVE_NONEW = "◽ Already Active — No New Usage"
O_CN_NOVRS = "🟣 CN Active — No VRS"
O_CN_VRS_NOUSE = "🟠 CN Active — VRS No Usage"
O_NOREACT = "⚪ No Reactivation"
O_NOMATCH = "❌ No Match"
O_REVIEW = "🔎 Review Required"
OUTCOMES = [O_REACT_USAGE, O_REACT_NOUSE, O_ACTIVE_USAGE, O_ACTIVE_NONEW,
            O_CN_NOVRS, O_CN_VRS_NOUSE, O_NOREACT, O_NOMATCH, O_REVIEW]


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _month(v):
    try:
        s = str(v)
        dt = (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
              else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return dt.strftime("%Y-%m")
    except Exception:
        return ""


def _dt(v):
    if not v:
        return None
    try:
        s = str(v)
        return (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
                else datetime.fromisoformat(s.replace("Z", "+00:00")))
    except Exception:
        return None


def _nums(cell):
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
    "Upload the **campaign audience CSV** (Pendo IDs, and ideally **VRS Numbers** / **Convo Now "
    "Numbers**). We validate the numbers on the Number object (excluding Guest), pull **Monthly "
    "Values** across a history window, split usage into **before vs after** the campaign, and assign "
    "each audience member a **campaign outcome**. The goal isn't who's active now — it's who "
    "**changed behavior after the campaign**.")

c1, c2, c3 = st.columns([2, 1.3, 1.3])
with c1:
    up = st.file_uploader("Campaign audience CSV", type=["csv"], key="camp_csv")
with c2:
    history_since = st.date_input("History since (month)", value=date(2026, 1, 1),
                                  help="How far back to pull Monthly Values for the before-period.")
with c3:
    campaign_date = st.date_input("Campaign release date", value=date(2026, 7, 31))
run = st.button("▶ Run analysis", type="primary", disabled=(up is None))

if run and up is not None:
    up.seek(0)
    raw = pd.read_csv(up, dtype=str).fillna("")
    cols = {c.lower().strip(): c for c in raw.columns}

    def col(*cands):
        for cand in cands:
            if cand.lower() in cols:
                return cols[cand.lower()]
        return None

    pendo_col = col("Pendo ID", "pendo")
    vrs_col = col("VRS Numbers", "vrs number")
    cn_col = col("Convo Now Numbers", "convo now number", "cn numbers")
    name_col = col("Name")
    email_col = col("Email")

    people = []
    all_nums = set()
    for _, r in raw.iterrows():
        vn = _nums(r.get(vrs_col, "")) if vrs_col else []
        cn = _nums(r.get(cn_col, "")) if cn_col else []
        all_nums.update(vn); all_nums.update(cn)
        people.append({
            "pendo": (r.get(pendo_col) or "").strip() if pendo_col else "",
            "name": (r.get(name_col) or "").strip() if name_col else "",
            "email": (r.get(email_col) or "").strip() if email_col else "",
            "vrs": vn, "cn": cn})

    # "after the campaign" = the month AFTER the campaign date (campaign released
    # Jul 31 2026 → after = Aug 2026 onward). before = everything earlier.
    _ay, _am = campaign_date.year, campaign_date.month + 1
    if _am > 12:
        _am, _ay = 1, _ay + 1
    after_month = f"{_ay:04d}-{_am:02d}"

    # 1) Read Number objects → meta (service, status, credit, email, account_id, logins)
    meta = {}
    nlist = sorted(all_nums)
    nprops = ["number", "service_type", "account_status", "credit_type", "credit_plan_name",
              "email", "account_id", "first_name", "last_name", "number_deleted_at",
              "last_login_ursa_convo_ios_date", "last_login_ursa_convo_android_date",
              "last_login_ursa_convo_web_date"]
    with dash_spinner(f"Validating {len(nlist):,} numbers on the Number object…"):
        for i in range(0, len(nlist), 100):
            chunk = nlist[i:i + 100]
            for rr in fetch_all(NUM_OBJECT, nprops, filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk}]}]):
                p = rr.get("properties", {})
                num = str(p.get("number") or "").strip()
                if not num:
                    continue
                svc = (p.get("service_type") or "").strip().lower()
                guest = ("guest" in (p.get("credit_type") or "").lower()
                         or "guest" in (p.get("credit_plan_name") or "").lower())
                _ios = _dt(p.get("last_login_ursa_convo_ios_date"))
                _and = _dt(p.get("last_login_ursa_convo_android_date"))
                _web = _dt(p.get("last_login_ursa_convo_web_date"))
                meta[num] = {
                    "service": svc, "status": (p.get("account_status") or "").strip(),
                    "credit_type": (p.get("credit_type") or "").strip(),
                    "email": (p.get("email") or "").strip(),
                    "account_id": (p.get("account_id") or "").strip(),
                    "name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                    "guest": guest, "ios": _ios, "android": _and, "web": _web,
                    "deleted": bool((p.get("number_deleted_at") or "").strip()),
                    "login": max([d for d in (_ios, _and, _web) if d], default=None)}

    # 2) Monthly Values for all numbers since history window (before + after).
    #    month_date-keyed; VRS and Convo Now kept SEPARATE (never totaled together).
    d_vrs = defaultdict(float)   # (number, YYYY-MM) -> VRS usage_minutes
    d_cn = defaultdict(float)    # (number, YYYY-MM) -> Convo Now usage_minutes
    d_ursa = defaultdict(float)  # (number, YYYY-MM) -> URSA minutes (VRS rows)
    d_cfz = defaultdict(float)   # (number, YYYY-MM) -> CfZ minutes (VRS rows)
    with dash_spinner(f"Pulling Monthly Values for {len(nlist):,} numbers…"):
        for i in range(0, len(nlist), 100):
            chunk = nlist[i:i + 100]
            for o in _seek_mv(["number", "month_date", "usage_minutes", "ursa_minutes",
                               "cfz_minutes", "service_type"],
                              [{"propertyName": "number", "operator": "IN", "values": chunk},
                               {"propertyName": "month_date", "operator": "GTE", "value": _ms(history_since)}]):
                p = o.get("properties", {})
                nn = str(p.get("number") or "").strip()
                m = _month(p.get("month_date"))
                mins = to_float(p.get("usage_minutes")) or 0.0
                svc = (p.get("service_type") or "").strip().lower()
                if svc == "vrs":
                    d_vrs[(nn, m)] += mins
                    d_ursa[(nn, m)] += to_float(p.get("ursa_minutes")) or 0.0
                    d_cfz[(nn, m)] += to_float(p.get("cfz_minutes")) or 0.0
                elif svc == "convo now":
                    d_cn[(nn, m)] += mins

    # 3) Classify each audience member
    rows = []
    monthly_after = defaultdict(lambda: {"vrs": 0.0, "ursa": 0.0, "cfz": 0.0, "cn": 0.0, "accts": set()})
    for pr in people:
        found = [n for n in (pr["vrs"] + pr["cn"]) if n in meta]
        # exclude Guest and deactivated (number_deleted_at set) numbers
        valid = [n for n in found if not meta[n]["guest"] and not meta[n]["deleted"]]
        vrs_valid = [n for n in valid if meta[n]["service"] == "vrs"]
        cn_valid = [n for n in valid if meta[n]["service"] == "convo now"]

        # before (through campaign) / after (month after campaign onward) — VRS & CN kept separate
        prev_vrs = round(sum(v for (nn, m), v in d_vrs.items()
                             if nn in vrs_valid and m < after_month), 1)
        curr_vrs = round(sum(v for (nn, m), v in d_vrs.items()
                             if nn in vrs_valid and m >= after_month), 1)
        curr_ursa = round(sum(v for (nn, m), v in d_ursa.items()
                              if nn in vrs_valid and m >= after_month), 1)
        curr_cfz = round(sum(v for (nn, m), v in d_cfz.items()
                             if nn in vrs_valid and m >= after_month), 1)
        curr_cn = round(sum(v for (nn, m), v in d_cn.items()
                            if nn in cn_valid and m >= after_month), 1)
        # first post-campaign VRS usage month
        post_months = sorted({m for (nn, m), v in d_vrs.items()
                              if nn in vrs_valid and m >= after_month and v > 0})
        first_post = post_months[0] if post_months else ""
        latest_mv = max([m for (nn, m) in list(d_vrs) + list(d_cn)
                         if nn in valid], default="")

        has_vrs = bool(vrs_valid)
        cn_active = any(meta[n]["status"].lower() == "live" for n in cn_valid) or bool(cn_valid)
        login = max([meta[n]["login"] for n in vrs_valid if meta[n]["login"]], default=None)
        login_post = bool(login and login.date() > campaign_date)
        _fmtd = lambda dd: dd.strftime("%b %d, %Y") if dd else "—"
        ios_l = max([meta[n]["ios"] for n in vrs_valid if meta[n]["ios"]], default=None)
        and_l = max([meta[n]["android"] for n in vrs_valid if meta[n]["android"]], default=None)
        web_l = max([meta[n]["web"] for n in vrs_valid if meta[n]["web"]], default=None)

        # month-level after aggregation (VRS usage, for the trend table)
        for (nn, m), v in d_vrs.items():
            if nn in vrs_valid and m >= after_month and v > 0:
                monthly_after[m]["vrs"] += v
                monthly_after[m]["accts"].add(pr["pendo"] or pr["email"] or id(pr))
        for (nn, m), v in d_ursa.items():
            if nn in vrs_valid and m >= after_month:
                monthly_after[m]["ursa"] += v
        for (nn, m), v in d_cfz.items():
            if nn in vrs_valid and m >= after_month:
                monthly_after[m]["cfz"] += v
        for (nn, m), v in d_cn.items():
            if nn in cn_valid and m >= after_month:
                monthly_after[m]["cn"] += v

        # ── outcome ──
        match_status = "Matched"
        if not found:
            outcome, match_status = O_NOMATCH, "Account Not Found"
        elif has_vrs and prev_vrs <= 0 and curr_vrs > 0:
            outcome = O_REACT_USAGE
        elif has_vrs and prev_vrs <= 0 and curr_vrs <= 0 and login_post:
            outcome = O_REACT_NOUSE
        elif has_vrs and prev_vrs > 0 and curr_vrs > 0:
            outcome = O_ACTIVE_USAGE
        elif has_vrs and prev_vrs > 0 and curr_vrs <= 0:
            outcome = O_ACTIVE_NONEW
        elif not has_vrs and cn_active:
            outcome = O_CN_NOVRS
        elif has_vrs and prev_vrs <= 0 and curr_vrs <= 0 and cn_active:
            outcome = O_CN_VRS_NOUSE
        else:
            outcome = O_NOREACT

        acct_name = pr["name"] or next((meta[n]["name"] for n in valid if meta[n]["name"]), "")
        # Account ID: the Pendo ID IS the Convo Now account's account_id. The VRS
        # number belongs to a DIFFERENT account (linked by same person/email), so
        # prefer the account_id that equals the Pendo ID, and surface VRS's separately.
        cn_acct = next((meta[n]["account_id"] for n in cn_valid if meta[n]["account_id"]), "")
        vrs_acct = next((meta[n]["account_id"] for n in vrs_valid if meta[n]["account_id"]), "")
        pendo = pr["pendo"].strip()
        acct_id = pendo if (pendo and any(meta[n]["account_id"] == pendo for n in valid)) else (cn_acct or vrs_acct)
        if not pendo:
            pendo_match = "—"
        elif any(meta[n]["account_id"] == pendo for n in valid):
            pendo_match = "✅ Pendo = Account"
        elif vrs_acct or cn_acct:
            pendo_match = "🔗 Linked (VRS on separate account)"
        else:
            pendo_match = "⚠️ Not matched"
        email = pr["email"] or next((meta[n]["email"] for n in valid if meta[n]["email"]), "")
        rows.append({
            "Pendo ID": pendo or "—",
            "Account ID": acct_id or "—",
            "VRS Account ID": vrs_acct or "—",
            "Pendo↔Account": pendo_match,
            "Account Name": acct_name or "—",
            "Email": email or "—",
            "Convo Now Number": ", ".join(cn_valid) or "—",
            "Convo Now Status": ", ".join(sorted({meta[n]["status"] for n in cn_valid})) or "—",
            "VRS Number": ", ".join(vrs_valid) or "—",
            "VRS Status": ", ".join(sorted({meta[n]["status"] for n in vrs_valid})) or "—",
            "Credit Type": ", ".join(sorted({meta[n]["credit_type"] for n in valid if meta[n]["credit_type"]})) or "—",
            "Previous Status": "Active" if prev_vrs > 0 else "Inactive",
            "Prev VRS Usage Min": prev_vrs,
            "Current Status": "Active" if curr_vrs > 0 else "Inactive",
            "VRS Usage Min (after)": curr_vrs,
            "URSA Min (after)": curr_ursa,
            "CfZ Min (after)": curr_cfz,
            "CN Usage Min (after)": curr_cn,
            "VRS Usage Change": round(curr_vrs - prev_vrs, 1),
            "First Post-Campaign Usage": first_post or "—",
            "Latest MV Month": latest_mv or "—",
            "URSA iOS Login": _fmtd(ios_l),
            "URSA Android Login": _fmtd(and_l),
            "URSA Web Login": _fmtd(web_l),
            "Login after campaign": "✅ Yes" if login_post else "—",
            "Campaign Outcome": outcome,
            "Match Status": match_status,
        })
    df = pd.DataFrame(rows)

    months = sorted(monthly_after)
    monthly = pd.DataFrame([{
        "Month Date": m,
        "VRS Usage Min": round(monthly_after[m]["vrs"], 1),
        "URSA Min": round(monthly_after[m]["ursa"], 1),
        "CfZ Min": round(monthly_after[m]["cfz"], 1),
        "CN Usage Min": round(monthly_after[m]["cn"], 1),
        "Accounts with VRS usage": len(monthly_after[m]["accts"]),
    } for m in months])

    save_report(SAVE_KEY, {"df": df, "monthly": monthly, "n": len(people),
                           "since": str(history_since), "campaign": str(campaign_date)})

saved = load_report(SAVE_KEY)
if saved is None:
    st.info("Upload the campaign audience CSV and click **▶ Run analysis**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · audience of {saved.get('n', len(df)):,} · "
               f"history since {saved.get('since','')} · campaign {saved.get('campaign','')}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
oc = df["Campaign Outcome"]


def _pct(n):
    return f"{n/N*100:.0f}%" if N else "—"


n_react_usage = int((oc == O_REACT_USAGE).sum())
n_react = int(oc.isin([O_REACT_USAGE, O_REACT_NOUSE]).sum())
n_usage = int((df["VRS Usage Min (after)"] > 0).sum())
n_active = int(oc.isin([O_ACTIVE_USAGE, O_ACTIVE_NONEW]).sum())
n_cn_novrs = int((oc == O_CN_NOVRS).sum())
n_cn_vrs_nouse = int((oc == O_CN_VRS_NOUSE).sum())
n_nomatch = int((oc == O_NOMATCH).sum())

# ── headline metrics ─────────────────────────────────────────────────────────────
k = st.columns(4)
_card(k[0], "🧬 Campaign audience", f"{N:,}", "people in CSV", "#7A5CFF")
_card(k[1], "🚀 Reactivated + Usage", f"{n_react_usage:,}", f"{_pct(n_react_usage)} · strongest outcome", "#2DB84B")
_card(k[2], "🔄 Reactivated (any)", f"{n_react:,}", f"{_pct(n_react)} of audience", "#4C8DFF")
_card(k[3], "⏱️ Generated usage", f"{n_usage:,}", f"{_pct(n_usage)} post-campaign VRS min", "#0FB5AE")
k2 = st.columns(4)
_card(k2[0], "✅ Already active", f"{n_active:,}", f"{_pct(n_active)} (not reactivation)", "#98A2B3")
_card(k2[1], "🟣 CN Active — No VRS", f"{n_cn_novrs:,}", _pct(n_cn_novrs), "#7A5CFF")
_card(k2[2], "🟠 CN Active — VRS No Usage", f"{n_cn_vrs_nouse:,}", _pct(n_cn_vrs_nouse), "#E8952A")
_card(k2[3], "❌ No match", f"{n_nomatch:,}", _pct(n_nomatch), "#E5484D")
st.markdown("")

# ── Output 2: campaign summary ───────────────────────────────────────────────────
st.markdown("##### Campaign summary — outcome breakdown")
summ = oc.value_counts().rename_axis("Outcome").reset_index(name="Count")
summ = summ.set_index("Outcome").reindex(OUTCOMES).dropna(how="all").reset_index()
summ["Count"] = summ["Count"].fillna(0).astype(int)
summ["% of audience"] = (summ["Count"] / N * 100).round(1)
st.dataframe(summ, use_container_width=True, hide_index=True, column_config={
    "Count": st.column_config.ProgressColumn("Count", min_value=0,
                                             max_value=int(summ["Count"].max()) if not summ.empty else 1, format="%d")})

# ── monthly reactivation trend ───────────────────────────────────────────────────
monthly = saved.get("monthly")
st.markdown("##### After campaign — minutes by month_date (VRS & Convo Now kept separate)")
if monthly is not None and not monthly.empty:
    mx = int(max(monthly["VRS Usage Min"].max(), monthly["URSA Min"].max(),
                 monthly["CfZ Min"].max(), monthly["CN Usage Min"].max(), 1))
    st.dataframe(monthly, use_container_width=True, hide_index=True, column_config={
        "VRS Usage Min": st.column_config.ProgressColumn("VRS Usage Min", min_value=0, max_value=mx, format="%.0f"),
        "URSA Min": st.column_config.ProgressColumn("URSA Min", min_value=0, max_value=mx, format="%.0f"),
        "CfZ Min": st.column_config.ProgressColumn("CfZ Min", min_value=0, max_value=mx, format="%.0f"),
        "CN Usage Min": st.column_config.ProgressColumn("CN Usage Min", min_value=0, max_value=mx, format="%.0f")})
else:
    st.info("No post-campaign usage yet.")

# ── Output 1: individual dataset ─────────────────────────────────────────────────
st.markdown("##### Individual audience dataset")
f1, f2 = st.columns([1.6, 2])
pick = f1.multiselect("Outcome", OUTCOMES, default=[])
search = f2.text_input("Search pendo / account / email / number").strip().lower()
view = df.copy()
if pick:
    view = view[view["Campaign Outcome"].isin(pick)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export individual dataset", view.to_csv(index=False),
                   "campaign_reactivation.csv", "text/csv")

st.caption("**Reactivated + Usage** = previously inactive (no VRS usage before) and generated VRS "
           "usage after the campaign — the strongest outcome. **Already Active** = had VRS usage "
           "before the campaign, so not a reactivation. Guest numbers excluded.")

report_header_close()
