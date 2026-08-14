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
SAVE_KEY = "pendo_segment_match"
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


st.markdown("Upload a **Pendo segment CSV** (a column of **Visitor IDs**). We match each visitor to "
            "a HubSpot **Contact** via `convo_now_account_id`, resolve the contact's **VRS "
            "number(s)** by email, and (optionally) pull recent **VRS usage** to see who's active / "
            "reactivated.")

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

    # 1) Contacts by convo_now_account_id (= Pendo visitor id)
    contact_by_vid = {}
    with dash_spinner(f"Matching {len(vids):,} visitor IDs to Contacts…"):
        for i in range(0, len(vids), 100):
            chunk = vids[i:i + 100]
            for c in fetch_all("contacts",
                               ["email", "firstname", "lastname", "convo_now_account_id", "lifecyclestage"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "convo_now_account_id", "operator": "IN", "values": chunk}]}]):
                cp = c.get("properties", {})
                vid = (cp.get("convo_now_account_id") or "").strip()
                if vid and vid not in contact_by_vid:
                    contact_by_vid[vid] = {
                        "email": (cp.get("email") or "").strip().lower(),
                        "name": f"{(cp.get('firstname') or '').strip()} {(cp.get('lastname') or '').strip()}".strip(),
                        "lifecycle": (cp.get("lifecyclestage") or "").strip()}

    # 2) Numbers by contact email (VRS)
    emails = sorted({m["email"] for m in contact_by_vid.values() if m["email"]})
    email_numbers = defaultdict(list)   # email -> list of (number, status)
    with dash_spinner(f"Resolving VRS numbers for {len(emails):,} emails…"):
        for i in range(0, len(emails), 100):
            chunk = emails[i:i + 100]
            for r in fetch_all(NUM_OBJECT, ["number", "email", "service_type", "account_status"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk},
                                   {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}]):
                p = r.get("properties", {})
                e = (p.get("email") or "").strip().lower()
                n = str(p.get("number") or "").strip()
                if e and n:
                    email_numbers[e].append((n, (p.get("account_status") or "").strip()))

    # 3) VRS usage since react_since (optional)
    num_usage = defaultdict(float)
    if with_usage:
        all_nums = [n for lst in email_numbers.values() for n, _ in lst]
        mv = _seek_mv(["number", "month_date", "usage_minutes", "service_type"],
                      [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                       {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                       {"propertyName": "month_date", "operator": "GTE", "value": _ms(react_since)}],
                      label="VRS usage:")
        _numset = set(all_nums)
        for o in mv:
            p = o.get("properties", {})
            n = str(p.get("number") or "").strip()
            if n in _numset:
                num_usage[n] += to_float(p.get("usage_minutes")) or 0.0

    rows = []
    for vid in vids:
        c = contact_by_vid.get(vid)
        if not c:
            rows.append({"Visitor ID": vid, "Matched": "No contact", "Email": "—", "Name": "—",
                         "VRS Numbers": "—", "VRS Status": "—", "VRS Min (since)": 0.0})
            continue
        nums = email_numbers.get(c["email"], [])
        vrs_min = round(sum(num_usage.get(n, 0.0) for n, _ in nums), 1)
        live = any(s.lower() == "live" for _, s in nums)
        rows.append({
            "Visitor ID": vid,
            "Matched": "✅ Contact" if not nums else ("✅ Live VRS" if live else "🟡 VRS not live"),
            "Email": c["email"] or "—",
            "Name": c["name"] or "—",
            "Lifecycle": c["lifecycle"] or "—",
            "VRS Numbers": ", ".join(n for n, _ in nums) or "— (no VRS)",
            "VRS Status": ", ".join(sorted({s for _, s in nums})) or "—",
            "VRS Min (since)": vrs_min,
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
n_contact = int((df["Matched"] != "No contact").sum())
n_hasvrs = int((df["VRS Numbers"] != "— (no VRS)").sum() - (df["VRS Numbers"] == "—").sum())
n_live = int((df["Matched"] == "✅ Live VRS").sum())
n_react = int((df["VRS Min (since)"] > 0).sum())


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


k = st.columns(5)
_card(k[0], "🧬 Segment size", n_seg, "Pendo visitor IDs", "#7A5CFF")
_card(k[1], "👤 Matched to contact", n_contact,
      f"{(n_contact/n_seg*100):.0f}%" if n_seg else "—", "#4C8DFF")
_card(k[2], "📞 Have VRS number", n_hasvrs, "resolved a VRS #", "#0FB5AE")
_card(k[3], "🟢 Live VRS", n_live, "at least one Live", "#2DB84B")
_card(k[4], "🚀 Generated VRS min", n_react, "active since window", "#E8952A")
st.markdown("")

st.info(f"Of **{n_seg:,}** in the Pendo segment: **{n_contact:,}** matched a Contact · "
        f"**{n_hasvrs:,}** have a VRS number · **{n_react:,}** generated VRS minutes since the window "
        "(reactivated).")

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
view = view.sort_values("VRS Min (since)", ascending=False)
st.caption(f"{len(view):,} rows")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "pendo_segment_match.csv", "text/csv")

report_header_close()
