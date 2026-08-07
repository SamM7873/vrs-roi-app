import streamlit as st
import pandas as pd
import requests
from datetime import date, datetime, timezone
from collections import defaultdict
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="VRS Reactivation", layout="wide", page_icon="🔁")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("VRS Reactivation")

report_header("VRS Reactivation",
              "Inactive VRS users who generated minutes again — measure a reactivation campaign",
              section="Analytics")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"


def _month_floor(d):
    return date(d.year, d.month, 1)


def _add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _ms(d):
    return str(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000))


def _seek_mv(filters, label):
    """All Monthly Values matching filters, paged by hs_object_id (past the 10k cap)."""
    url = f"{_B}/crm/v3/objects/{MV_OBJECT}/search"
    out, last = [], "0"
    ph = st.empty()
    while True:
        body = {"limit": 100, "properties": ["number", "month_date", "usage_minutes", "service_type"],
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = requests.post(url, headers=_H, json=body, timeout=30)
        if r.status_code != 200:
            ph.empty(); st.error(f"HubSpot error {r.status_code}: {r.text[:200]}"); break
        batch = r.json().get("results", [])
        out.extend(batch)
        ph.caption(f"{label} {len(out):,} usage rows…")
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"])
    ph.empty()
    return out


# ── controls ──────────────────────────────────────────────────────────────────
st.markdown("**Cohort:** customers who already have a VRS number but had **no VRS usage** "
            "during the inactive window, and then **generated VRS minutes** in the reactivation "
            "window. Usage is monthly, so windows are measured in whole months.")
c1, c2 = st.columns(2)
with c1:
    react_since = st.date_input("Reactivated since (campaign date)", value=date(2026, 7, 31),
                                help="Reactivation window = the month of this date onward.")
with c2:
    lookback = st.selectbox("Inactive lookback", ["Last 3 months (~90 days)",
                                                  "Last 6 months", "Last 12 months"], index=0)
run = st.button("Run reactivation report", type="primary")

_months = {"Last 3 months (~90 days)": 3, "Last 6 months": 6, "Last 12 months": 12}[lookback]
react_floor = _month_floor(react_since)                 # e.g. Aug 1 2026
inactive_start = _add_months(react_floor, -_months)      # e.g. May 1 2026
st.caption(f"Inactive window: **{inactive_start:%b %Y} – {_add_months(react_floor,-1):%b %Y}** "
           f"(no VRS usage) · Reactivation window: **{react_floor:%b %Y} onward**.")

_key = f"vrs_react_{react_floor:%Y%m}_{_months}"

if run:
    react_ms = _ms(react_floor)
    inact_ms = _ms(inactive_start)
    # numbers active NOW (reactivation window)
    now_rows = _seek_mv([
        {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
        {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
        {"propertyName": "month_date", "operator": "GTE", "value": react_ms},
    ], "Active now:")
    active_now = defaultdict(float)
    for o in now_rows:
        p = o.get("properties", {})
        n = str(p.get("number") or "").strip()
        if n:
            active_now[n] += to_float(p.get("usage_minutes")) or 0.0
    # numbers active in the inactive window (→ these were NOT inactive, exclude them)
    prior_rows = _seek_mv([
        {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
        {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
        {"propertyName": "month_date", "operator": "GTE", "value": inact_ms},
        {"propertyName": "month_date", "operator": "LT", "value": react_ms},
    ], "Active in lookback:")
    active_prior = {str(o.get("properties", {}).get("number") or "").strip() for o in prior_rows}

    reactivated = {n: m for n, m in active_now.items() if n and n not in active_prior}
    nums = list(reactivated.keys())

    # enrich reactivated numbers with Number-object details (Live VRS only)
    rows = []
    with dash_spinner(f"Reading {len(nums):,} reactivated numbers…"):
        for i in range(0, len(nums), 100):
            chunk = nums[i:i + 100]
            recs = fetch_all(NUM_OBJECT,
                             ["number", "email", "first_name", "last_name", "service_type",
                              "account_status", "number_created_at"],
                             filter_groups=[{"filters": [
                                 {"propertyName": "number", "operator": "IN", "values": chunk},
                                 {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}])
            for r in recs:
                p = r.get("properties", {})
                n = str(p.get("number") or "").strip()
                if n not in reactivated:
                    continue
                rows.append({
                    "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
                    "Email": (p.get("email") or "").strip(),
                    "Number": n,
                    "Status": (p.get("account_status") or "").strip(),
                    "Number Created": (str(p.get("number_created_at") or "")[:10]),
                    "VRS Min (reactivated)": round(reactivated[n], 1),
                })
    # Pendo ID lives on the Contact (convo_now_account_id) — look it up by email.
    if rows:
        _emails = sorted({r["Email"].lower() for r in rows if r["Email"] and "@" in r["Email"]})
        _email_pendo = {}
        with dash_spinner("Matching Pendo IDs…"):
            for i in range(0, len(_emails), 100):
                chunk = _emails[i:i + 100]
                for c in fetch_all("contacts", ["email", "convo_now_account_id"],
                                   filter_groups=[{"filters": [
                                       {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                    cp = c.get("properties", {})
                    e = (cp.get("email") or "").strip().lower()
                    pid = (cp.get("convo_now_account_id") or "").strip()
                    if e and pid:
                        _email_pendo.setdefault(e, pid)
        for r in rows:
            r["Pendo ID"] = _email_pendo.get(r["Email"].lower(), "—")

    df = pd.DataFrame(rows).sort_values("VRS Min (reactivated)", ascending=False) if rows else pd.DataFrame()
    save_report(_key, {"df": df})

saved = load_report(_key)
if saved is None:
    st.info("Set the campaign date and click **Run reactivation report**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh")

n_react = len(df)
total_min = float(df["VRS Min (reactivated)"].sum()) if n_react else 0.0
k1, k2 = st.columns(2)
k1.metric("🔁 Reactivated (VRS)", f"{n_react:,}")
k2.metric("VRS minutes generated", f"{total_min:,.1f}")

if n_react:
    st.markdown("##### Who reactivated")
    st.dataframe(df, use_container_width=True, hide_index=True, height=440)
    st.download_button("📥 Export reactivated CSV", df.to_csv(index=False),
                       f"vrs_reactivated_{react_floor:%Y%m}.csv", "text/csv")
else:
    st.info("No reactivations found for this window yet.")

st.caption("Reactivated = a VRS number with **0 usage in the inactive window** that generated "
           "**VRS minutes in the reactivation window**. Monthly data, so 'last 7 days' ≈ the "
           "current month on/after the campaign date.")

report_header_close()
