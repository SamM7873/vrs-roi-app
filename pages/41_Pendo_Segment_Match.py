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
              "Match a Pendo visitor-ID segment to HubSpot Contacts → Numbers → usage",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
SAVE_KEY = "pendo_segment_match_v2"
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


st.markdown("Upload a **Pendo segment CSV** (a column of **Visitor IDs / Pendo IDs**). We match each "
            "Pendo ID **directly to the Number object** via `convo_now_account_id`, then check "
            "**Monthly Values** for usage since the window. Flow: **Pendo ID → Number → "
            "Monthly Values**.")

c1, c2, c3 = st.columns([2, 1.4, 1.4])
with c1:
    up = st.file_uploader("Pendo segment CSV (Visitor IDs)", type=["csv"], key="pseg_csv")
with c2:
    react_since = st.date_input("VRS usage since", value=date(2026, 5, 1),
                                help="Count VRS minutes generated on/after this month.")
with c3:
    with_usage = st.checkbox("Pull VRS usage (slower)", value=True)
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
    raw = pd.read_csv(up, dtype=str).fillna("")
    # visitor-id column = first column, or one containing 'visitor'/'id'
    vcol = next((c for c in raw.columns if "visitor" in c.lower() or c.lower() == "id"), raw.columns[0])
    vids = sorted({v.strip() for v in raw[vcol] if v.strip()})

    # 1) Match visitor IDs DIRECTLY to the Number object (by convo_now_account_id OR account_id)
    vidset = set(vids)
    num_by_vid = defaultdict(list)   # vid -> list of number-record dicts
    props = ["number", "email", "first_name", "last_name", "service_type", "account_status",
             "convo_now_account_id", "account_id", "credit_type", "credit_plan_name"]
    seen = set()

    def _absorb(recs):
        for r in recs:
            rid = r.get("id")
            if rid in seen:
                continue
            p = r.get("properties", {})
            vid = (p.get("convo_now_account_id") or "").strip()
            if vid not in vidset:
                continue
            seen.add(rid)
            num_by_vid[vid].append({
                "number": str(p.get("number") or "").strip(),
                "email": (p.get("email") or "").strip().lower(),
                "name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
                "service": (p.get("service_type") or "").strip(),
                "status": (p.get("account_status") or "").strip(),
            })

    with dash_spinner(f"Matching {len(vids):,} Pendo IDs to the Number object…"):
        for i in range(0, len(vids), 100):
            chunk = vids[i:i + 100]
            _absorb(fetch_all(NUM_OBJECT, props, filter_groups=[{"filters": [
                {"propertyName": "convo_now_account_id", "operator": "IN", "values": chunk}]}]))

    # 2) Monthly Values usage for the matched numbers, since the window
    num_usage = defaultdict(float)   # number -> minutes
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
                    num_usage[str(p.get("number") or "").strip()] += to_float(p.get("usage_minutes")) or 0.0

    rows = []
    for vid in vids:
        recs = num_by_vid.get(vid, [])
        if not recs:
            rows.append({"Visitor ID": vid, "Matched": "No number", "Email": "—", "Name": "—",
                         "Numbers": "—", "Service": "—", "Status": "—", "Min (since)": 0.0})
            continue
        mins = round(sum(num_usage.get(r["number"], 0.0) for r in recs), 1)
        live = any(r["status"].lower() == "live" for r in recs)
        has_vrs = any(r["service"].lower() == "vrs" for r in recs)
        name = next((r["name"] for r in recs if r["name"]), "")
        email = next((r["email"] for r in recs if r["email"]), "")
        rows.append({
            "Visitor ID": vid,
            "Matched": "🟢 Live" if live else "🟡 Not live",
            "Email": email or "—",
            "Name": name or "—",
            "Numbers": ", ".join(r["number"] for r in recs if r["number"]) or "—",
            "Service": ", ".join(sorted({r["service"] for r in recs if r["service"]})) or "—",
            "Status": ", ".join(sorted({r["status"] for r in recs if r["status"]})) or "—",
            "Has VRS": "Yes" if has_vrs else "No",
            "Min (since)": mins,
        })
    df = pd.DataFrame(rows)
    save_report(SAVE_KEY, {"df": df, "n_seg": len(vids), "since": str(react_since),
                           "with_usage": with_usage})

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
n_matched = int((df["Matched"] != "No number").sum())
n_hasvrs = int((df["Has VRS"] == "Yes").sum()) if "Has VRS" in df.columns else 0
n_live = int((df["Matched"] == "🟢 Live").sum())
n_react = int((df["Min (since)"] > 0).sum())


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


k = st.columns(5)
_card(k[0], "🧬 Segment size", n_seg, "Pendo visitor IDs", "#7A5CFF")
_card(k[1], "🔢 Matched to a Number", n_matched,
      f"{(n_matched/n_seg*100):.0f}%" if n_seg else "—", "#4C8DFF")
_card(k[2], "📞 Have VRS number", n_hasvrs, "matched a VRS #", "#0FB5AE")
_card(k[3], "🟢 Live", n_live, "at least one Live", "#2DB84B")
_card(k[4], "🚀 Generated minutes", n_react, "active since window", "#E8952A")
st.markdown("")

st.info(f"Of **{n_seg:,}** in the Pendo segment: **{n_matched:,}** matched a Number "
        f"(directly, via convo_now_account_id / account_id) · **{n_hasvrs:,}** have a VRS number · "
        f"**{n_react:,}** generated minutes since the window.")

# ── filters + table ─────────────────────────────────────────────────────────────
f1, f2 = st.columns([2, 2])
mopts = sorted(df["Matched"].unique())
mpick = f1.multiselect("Match status", mopts, default=mopts)
search = f2.text_input("Search email / name / number / visitor id").strip().lower()
view = df.copy()
if mpick:
    view = view[view["Matched"].isin(mpick)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
view = view.sort_values("Min (since)", ascending=False)
st.caption(f"{len(view):,} rows")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "pendo_segment_match.csv", "text/csv")

report_header_close()
