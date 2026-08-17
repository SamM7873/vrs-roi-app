import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)
import requests

st.set_page_config(page_title="Pendo Segment Match", layout="wide", page_icon="🧬")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Pendo Segment Match")

report_header("Pendo Segment Match",
              "Match a Pendo ID segment to the Number object (account_id) → Monthly Values usage",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
SAVE_KEY = "pendo_segment_match_v4"
TTL = 48 * 3600


def _period_of(v):
    if not v:
        return None
    try:
        s = str(v)
        dt = (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
              else datetime.fromisoformat(s.replace("Z", "+00:00")))
        return pd.Period(dt.strftime("%Y-%m"), freq="M")
    except Exception:
        return None


def _ms(d):
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


st.markdown("Upload a **Pendo segment CSV** (a column of **Pendo IDs** — the UUID). We match each "
            "Pendo ID **directly to the Number object** via `account_id`, bridge to the person's VRS "
            "number by email, then check **Monthly Values** for usage since the window. "
            "Flow: **Pendo ID → Number (account_id) → Monthly Values**. "
            "Click **▶ Run match** after uploading — the cards show the *last saved run* until you do.")

c1, c2, c3 = st.columns([2, 1.4, 1.4])
with c1:
    up = st.file_uploader("Pendo segment CSV (Visitor IDs)", type=["csv"], key="pseg_csv")
with c2:
    _cur_month = date.today().replace(day=1)
    react_since = st.date_input("Usage since (month)", value=_cur_month,
                                help="Pulls Monthly Values on/after this month. Defaults to the "
                                     "current month so it reflects the latest usage.")
with c3:
    with_usage = st.checkbox("Pull VRS usage (slower)", value=True)

# let the user confirm which column holds the Pendo ID (defaults to the 'Pendo ID' column)
id_col = None
if up is not None:
    try:
        _cols = pd.read_csv(up, nrows=0).columns.tolist()
        up.seek(0)
        _default = (next((c for c in _cols if "pendo" in c.lower()), None)
                    or next((c for c in _cols if "visitor" in c.lower()), None)
                    or _cols[0])
        id_col = st.selectbox("Pendo ID column (matched to the Number object's account_id)",
                              _cols, index=_cols.index(_default))
    except Exception:
        id_col = None
run = st.button("▶ Run match", type="primary", disabled=(up is None))


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


if run and up is not None:
    up.seek(0)
    raw = pd.read_csv(up, dtype=str).fillna("")
    # Pendo ID column = the picker choice, else 'Pendo ID' / 'visitor' / first column
    vcol = id_col if (id_col and id_col in raw.columns) else (
        next((c for c in raw.columns if "pendo" in c.lower()), None)
        or next((c for c in raw.columns if "visitor" in c.lower()), None)
        or raw.columns[0])
    _all_ids = [v.strip() for v in raw[vcol] if v.strip()]
    n_csv_ids = len(_all_ids)              # total Pendo IDs in the CSV (incl. duplicates)
    vids = sorted(set(_all_ids))           # unique Pendo IDs
    st.caption(f"Using **{vcol}** as the Pendo ID · {n_csv_ids:,} rows · {len(vids):,} unique IDs")

    # Match the Pendo ID (UUID from the CSV) directly to the Number object's account_id.
    # No Contact hop — the Pendo ID is the account_id.
    vidset = set(vids)
    num_by_vid = defaultdict(list)   # vid -> list of number-record dicts
    props = ["number", "email", "first_name", "last_name", "service_type", "account_status",
             "account_id", "credit_type", "credit_plan_name"]
    seen = set()

    def _rec(p):
        return {
            "number": str(p.get("number") or "").strip(),
            "email": (p.get("email") or "").strip().lower(),
            "name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
            "service": (p.get("service_type") or "").strip(),
            "status": (p.get("account_status") or "").strip(),
        }

    # A) direct: Number.account_id == Pendo ID
    with dash_spinner(f"Matching {len(vids):,} Pendo IDs to the Number object (account_id)…"):
        for i in range(0, len(vids), 100):
            chunk = vids[i:i + 100]
            for r in fetch_all(NUM_OBJECT, props, filter_groups=[{"filters": [
                    {"propertyName": "account_id", "operator": "IN", "values": chunk}]}]):
                p = r.get("properties", {})
                aid = (p.get("account_id") or "").strip()
                if aid in vidset and r.get("id") not in seen:
                    seen.add(r.get("id"))
                    num_by_vid[aid].append(_rec(p))

    contact_meta = {}  # not used (no Contact hop)

    # A2) email bridge (Number → email → VRS Number): attach the person's VRS number(s)
    #     so we can see VRS minutes even when the Pendo ID matched a Convo Now number.
    emails_by_vid = defaultdict(set)
    for vid, recs in num_by_vid.items():
        for r in recs:
            if r["email"]:
                emails_by_vid[vid].add(r["email"])
    all_emails = sorted({e for s in emails_by_vid.values() for e in s})
    vrs_by_email = defaultdict(list)   # email -> list of VRS number recs
    if all_emails:
        with dash_spinner(f"Finding VRS numbers for {len(all_emails):,} emails…"):
            for i in range(0, len(all_emails), 100):
                chunk = all_emails[i:i + 100]
                for r in fetch_all(NUM_OBJECT, props, filter_groups=[{"filters": [
                        {"propertyName": "email", "operator": "IN", "values": chunk},
                        {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}]):
                    p = r.get("properties", {})
                    vrs_by_email[(p.get("email") or "").strip().lower()].append(_rec(p))
    for vid, emails in emails_by_vid.items():
        have = {r["number"] for r in num_by_vid[vid]}
        for em in emails:
            for vr in vrs_by_email.get(em, ()):
                if vr["number"] not in have:
                    num_by_vid[vid].append(vr)
                    have.add(vr["number"])

    # 2) Monthly Values usage_minutes for the matched numbers, since the window
    #    split by service (VRS vs Convo Now)
    num_usage = defaultdict(float)       # number -> total minutes
    num_usage_vrs = defaultdict(float)   # number -> VRS minutes
    num_usage_cn = defaultdict(float)    # number -> Convo Now minutes
    if with_usage:
        all_nums = sorted({r["number"] for lst in num_by_vid.values() for r in lst if r["number"]})
        with dash_spinner(f"Pulling Monthly Values for {len(all_nums):,} matched numbers…"):
            for i in range(0, len(all_nums), 100):
                chunk = all_nums[i:i + 100]
                for o in _seek_mv(["number", "month_date", "usage_minutes", "service_type"],
                                  [{"propertyName": "number", "operator": "IN", "values": chunk},
                                   {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                                   {"propertyName": "month_date", "operator": "GTE", "value": _ms(react_since)}]):
                    p = o.get("properties", {})
                    nn = str(p.get("number") or "").strip()
                    mins = to_float(p.get("usage_minutes")) or 0.0
                    svc = (p.get("service_type") or "").strip().lower()
                    num_usage[nn] += mins
                    if svc == "vrs":
                        num_usage_vrs[nn] += mins
                    elif svc == "convo now":
                        num_usage_cn[nn] += mins

    rows = []
    for vid in vids:
        recs = num_by_vid.get(vid, [])
        if not recs:
            cm = contact_meta.get(vid, {})
            rows.append({"Visitor ID": vid, "Flag": "⚪ No usage",
                         "Matched": "👤 Contact, no number" if cm else "No match",
                         "Email": cm.get("email") or "—", "Name": cm.get("name") or "—",
                         "Numbers": "—", "Service": "—", "Status": "—", "Has VRS": "No",
                         "VRS Min": 0.0, "CN Min": 0.0, "Total Min (since)": 0.0})
            continue
        vrs_min = round(sum(num_usage_vrs.get(r["number"], 0.0) for r in recs), 1)
        cn_min = round(sum(num_usage_cn.get(r["number"], 0.0) for r in recs), 1)
        tot_min = round(sum(num_usage.get(r["number"], 0.0) for r in recs), 1)
        live = any(r["status"].lower() == "live" for r in recs)
        has_vrs = any(r["service"].lower() == "vrs" for r in recs)
        name = next((r["name"] for r in recs if r["name"]), "")
        email = next((r["email"] for r in recs if r["email"]), "")

        # reactivation flag: Convo Now active, VRS generated nothing → target
        if cn_min > 0 and vrs_min <= 0:
            flag = "🎯 Reactivate VRS (CN active, VRS silent)"
        elif cn_min > 0 and vrs_min > 0:
            flag = "✅ Both active"
        elif cn_min <= 0 and vrs_min > 0:
            flag = "📞 VRS only"
        else:
            flag = "⚪ No usage"

        rows.append({
            "Visitor ID": vid,
            "Flag": flag,
            "Matched": "🟢 Live" if live else "🟡 Not live",
            "Email": email or "—",
            "Name": name or "—",
            "Numbers": ", ".join(r["number"] for r in recs if r["number"]) or "—",
            "Service": ", ".join(sorted({r["service"] for r in recs if r["service"]})) or "—",
            "Status": ", ".join(sorted({r["status"] for r in recs if r["status"]})) or "—",
            "Has VRS": "Yes" if has_vrs else "No",
            "VRS Min": vrs_min,
            "CN Min": cn_min,
            "Total Min (since)": tot_min,
        })
    df = pd.DataFrame(rows)
    save_report(SAVE_KEY, {"df": df, "n_seg": len(vids), "n_csv_ids": n_csv_ids,
                           "since": str(react_since), "with_usage": with_usage})

saved = load_report(SAVE_KEY)
if saved is None:
    st.info("Upload a Pendo segment CSV and click **▶ Run match**.")
    report_header_close(); st.stop()
if time.time() - (saved.get("saved_at") or 0) > TTL and not run:
    st.warning("Saved match is older than 48h — re-upload and Run.")
df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · segment of {saved.get('n_seg', len(df)):,} · "
               f"VRS usage since {saved.get('since','')}")

# ── KPIs ────────────────────────────────────────────────────────────────────────
n_seg = len(df)
n_matched = int((df["Matched"] != "No match").sum())
n_hasvrs = int((df["Has VRS"] == "Yes").sum()) if "Has VRS" in df.columns else 0
n_cn_active = int((df["CN Min"] > 0).sum()) if "CN Min" in df.columns else 0
n_reactivate = int(df["Flag"].str.startswith("🎯").sum()) if "Flag" in df.columns else 0


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


n_csv_ids = saved.get("n_csv_ids", n_seg)
k = st.columns(5)
_card(k[0], "🧬 Pendo IDs (CSV)", n_csv_ids,
      f"{n_seg:,} unique" if n_csv_ids != n_seg else "unique Pendo IDs", "#7A5CFF")
_card(k[1], "🔢 Matched to a Number", n_matched,
      f"{(n_matched/n_seg*100):.0f}%" if n_seg else "—", "#4C8DFF")
_card(k[2], "📞 Have VRS number", n_hasvrs, "VRS on same email", "#0FB5AE")
_card(k[3], "📱 CN active", n_cn_active, "Convo Now minutes > 0", "#E8952A")
_card(k[4], "🎯 Reactivate VRS", n_reactivate, "CN active, VRS silent", "#E5484D")

tot_cn = float(df["CN Min"].sum()) if "CN Min" in df.columns else 0.0
tot_vrs = float(df["VRS Min"].sum()) if "VRS Min" in df.columns else 0.0
tot_all = float(df["Total Min (since)"].sum()) if "Total Min (since)" in df.columns else 0.0
k2 = st.columns(3)
_card(k2[0], "⏱️ Total usage minutes", int(round(tot_all)), "CN + VRS since window", "#3563E9")
_card(k2[1], "📱 Total CN minutes", int(round(tot_cn)), "Convo Now", "#E8952A")
_card(k2[2], "📞 Total VRS minutes", int(round(tot_vrs)), "VRS", "#0FB5AE")
st.markdown("")

st.info(f"**🎯 {n_reactivate:,} reactivation targets** — Convo Now is active but the person's VRS "
        f"number generated **no** minutes since the window. Of **{n_seg:,}** Pendo IDs: "
        f"**{n_matched:,}** matched a Number · **{n_hasvrs:,}** have a VRS number (same email) · "
        f"**{n_cn_active:,}** are Convo Now active.")

# ── filters + table ─────────────────────────────────────────────────────────────
f1, f2 = st.columns([2, 2])
fopts = ["🎯 Reactivate VRS (CN active, VRS silent)", "✅ Both active", "📞 VRS only", "⚪ No usage"]
fopts = [o for o in fopts if o in set(df.get("Flag", pd.Series(dtype=str)))]
fpick = f1.multiselect("Flag", fopts, default=[o for o in fopts if o.startswith("🎯")])
search = f2.text_input("Search email / name / number / visitor id").strip().lower()
view = df.copy()
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
elif fpick and "Flag" in view.columns:
    view = view[view["Flag"].isin(fpick)]
view = view.sort_values("CN Min", ascending=False)
vc = st.columns([1, 3])
_n_view = view["Visitor ID"].nunique()
_card(vc[0], "🧬 Pendo IDs in view", _n_view,
      f"out of {n_csv_ids:,} in CSV", "#7A5CFF")
st.markdown("")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "pendo_segment_match.csv", "text/csv")

report_header_close()
