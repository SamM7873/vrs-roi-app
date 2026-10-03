import streamlit as st
import pandas as pd
import time
from calendar import monthrange
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Now Reminder Balance", layout="wide", page_icon="⏱️")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Now Reminder Balance")

report_header("Convo Now Reminder Balance (QA)",
              "Billing-cycle remainder = 20-min allowance − cycle minutes (NOT remainder_balance)",
              section="Support")

# ── object types ──────────────────────────────────────────────────────────────────────
NUMBER_OBJECT = "2-40974683"
MONTHLY_VALUES_OBJECT = "2-46246179"
SUBSCRIPTION_OBJECT = "2-39730970"

REQUIRED_SERVICE_TYPE = "convo now"
REQUIRED_ACCOUNT_STATUS = "live"
REQUIRED_CREDIT_PLAN = "convo now: access complimentary"   # 20-min complimentary plan
EXCLUDED_CREDIT_TYPE = "guest"
DEFAULT_CREDIT_MINIMUM = 20

_key = "convonow_reminder_balance_v7_carryforward"


def _norm(v):
    return " ".join(str(v or "").strip().lower().split())


def _mv_minutes(mv):
    """Minutes used on a Monthly Values record: prefer convo_now_minutes_used, else usage_minutes."""
    for k in ("convo_now_minutes_used", "usage_minutes"):
        v = mv.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except Exception:
                pass
    return 0.0


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    v = str(value).strip()
    if v.isdigit():                       # epoch millis
        try:
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).date()
        except Exception:
            return None
    if "T" in v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except Exception:
            pass
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _seek(obj, props, filters):
    """Search with hs_object_id cursor pagination (bypasses the 10k cap)."""
    url = f"{_B}/crm/v3/objects/{obj}/search"
    out, last = [], "0"
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(5):
            r = requests.post(url, headers=_H, json=body, timeout=60)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            break
        batch = r.json().get("results", [])
        out.extend(batch)
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.03)
    return out


def _assoc(from_obj, to_obj, from_ids):
    out = defaultdict(list)
    for i in range(0, len(from_ids), 100):
        chunk = [str(x) for x in from_ids[i:i + 100]]
        try:
            r = requests.post(f"{_B}/crm/v4/associations/{from_obj}/{to_obj}/batch/read",
                              headers=_H, json={"inputs": [{"id": s} for s in chunk]}, timeout=60)
            if r.status_code in (200, 207):
                for res in r.json().get("results", []):
                    fid = str(res.get("from", {}).get("id", ""))
                    for a in res.get("to", []):
                        tid = str(a.get("toObjectId") or a.get("id") or "")
                        if tid:
                            out[fid].append(tid)
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.03)
    return out


def _batch_read(obj, ids, props):
    out = {}
    ids = [str(x) for x in ids]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{obj}/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk], "properties": props},
                              timeout=60)
            if r.status_code in (200, 207):
                for o in r.json().get("results", []):
                    out[str(o["id"])] = o.get("properties", {})
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.03)
    return out


def _determine_billing_period(billing_start, billing_end, billing_type):
    bs = _parse_date(billing_start)
    be = _parse_date(billing_end)
    bt = _norm(billing_type)
    if bs is None:
        return dict(billing_start=None, billing_end=None, billing_days=None,
                    status="MISSING BILLING START", source=None)
    if be is not None:
        if be > bs:
            return dict(billing_start=bs, billing_end=be, billing_days=(be - bs).days,
                        status="OK", source="HubSpot")
        return dict(billing_start=bs, billing_end=be, billing_days=None,
                    status="INVALID BILLING PERIOD", source="HubSpot")
    # billing end missing → calculate from cycle type
    if "30" in bt:
        ce = bs + timedelta(days=30)
        return dict(billing_start=bs, billing_end=ce, billing_days=30,
                    status="OK - CALCULATED", source="Calculated 30-day")
    if "month" in bt:
        y, m = bs.year, bs.month
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        nd = min(bs.day, monthrange(ny, nm)[1])
        ce = date(ny, nm, nd)
        return dict(billing_start=bs, billing_end=ce, billing_days=(ce - bs).days,
                    status="OK - CALCULATED", source="Calculated monthly")
    return dict(billing_start=bs, billing_end=None, billing_days=None,
                status="UNKNOWN BILLING TYPE", source=None)


def _overlap(month_start, billing_start, billing_end):
    last = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start + timedelta(days=last)       # exclusive
    os_, oe = max(month_start, billing_start), min(month_end, billing_end)
    if os_ >= oe:
        return None
    return dict(overlap_start=os_, overlap_end=oe, overlap_days=(oe - os_).days)


# ── controls ──────────────────────────────────────────────────────────────────────────
st.markdown("Reviews every **Convo Now + Live** number on the **Convo Now: Access Complimentary** "
            "plan: pulls its **Subscription** billing cycle and **Monthly Values**, excludes "
            "**Guest** credit type, keeps only months overlapping the billing cycle, and recomputes "
            "the correct remainder as **20 − total billing-cycle minutes** (clamped at 0). "
            "A number is 🚩 flagged when its **displayed balance is overstated** — the cycle crosses a "
            "calendar month and the later Monthly Value **doesn't carry forward** the earlier month's "
            "usage, so it shows more remaining minutes than the cycle actually has left.")
st.caption(f"Credit allowance is fixed at **{DEFAULT_CREDIT_MINIMUM} minutes** per billing cycle.")
_allow = DEFAULT_CREDIT_MINIMUM
run = st.button("▶ Run QA", type="primary")

if run:
    with dash_spinner("Reading Convo Now numbers…"):
        nums = _seek(NUMBER_OBJECT,
                     ["number", "service_type", "account_status", "number_status",
                      "credit_type", "credit_plan_name"],
                     [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}])

    def _is_live(pp):   # Live status lives in account_status OR number_status
        return _norm(pp.get("account_status") or pp.get("number_status")) == REQUIRED_ACCOUNT_STATUS

    def _is_plan(pp):   # 20-min complimentary plan only
        return _norm(pp.get("credit_plan_name")) == REQUIRED_CREDIT_PLAN

    eligible = [o for o in nums
                if _norm(o.get("properties", {}).get("service_type")) == REQUIRED_SERVICE_TYPE
                and _is_live(o.get("properties", {}))
                and _is_plan(o.get("properties", {}))]
    if not eligible:
        st.warning("No Convo Now + Live numbers found."); report_header_close(); st.stop()

    nid_list = [str(o["id"]) for o in eligible]
    num_of = {str(o["id"]): o.get("properties", {}) for o in eligible}

    with dash_spinner("Linking subscriptions & monthly values…"):
        nid_subs = _assoc(NUMBER_OBJECT, SUBSCRIPTION_OBJECT, nid_list)
        nid_mvs = _assoc(NUMBER_OBJECT, MONTHLY_VALUES_OBJECT, nid_list)

    all_sub_ids = sorted({s for v in nid_subs.values() for s in v})
    all_mv_ids = sorted({m for v in nid_mvs.values() for m in v})
    with dash_spinner(f"Reading {len(all_sub_ids):,} subscriptions…"):
        sub_of = _batch_read(SUBSCRIPTION_OBJECT, all_sub_ids,
                             ["billing_start_date", "current_billing_period_end_date",
                              "billing_cycle_type"])
    with dash_spinner(f"Reading {len(all_mv_ids):,} monthly values…"):
        mv_of = _batch_read(MONTHLY_VALUES_OBJECT, all_mv_ids,
                            ["month_date", "convo_now_minutes_used", "usage_minutes", "service_type",
                             "remainder_balance", "number", "credit_type"])

    consumer_rows, detail_rows = [], []
    prog = st.progress(0.0)
    for i, nid in enumerate(nid_list, 1):
        p = num_of.get(nid, {})
        number_value = p.get("number") or "—"
        base = {"number": number_value, "number_id": nid,
                "service_type": p.get("service_type") or "—",
                "account_status": p.get("account_status") or p.get("number_status") or "—",
                "credit_plan_name": p.get("credit_plan_name") or "—"}

        sub_ids = nid_subs.get(nid, [])
        if not sub_ids:
            consumer_rows.append({**base, "billing_start": None, "billing_end": None,
                                  "billing_days": None, "billing_type": None, "billing_source": None,
                                  "monthly_values": 0, "minutes_used": 0,
                                  "credit_allowance": _allow, "remainder": None,
                                  "source_remainder_sum": None, "mismatch": "",
                                  "status": "NO SUBSCRIPTION"})
            prog.progress(i / len(nid_list)); continue

        sp = sub_of.get(str(sub_ids[0]), {})
        billing = _determine_billing_period(sp.get("billing_start_date"),
                                            sp.get("current_billing_period_end_date"),
                                            sp.get("billing_cycle_type"))
        bs, be = billing["billing_start"], billing["billing_end"]
        if bs is None or be is None:
            consumer_rows.append({**base, "billing_start": bs, "billing_end": be,
                                  "billing_days": billing["billing_days"],
                                  "billing_type": sp.get("billing_cycle_type"),
                                  "billing_source": billing["source"], "monthly_values": 0,
                                  "minutes_used": 0, "credit_allowance": _allow, "remainder": None,
                                  "source_remainder_sum": None, "mismatch": "",
                                  "status": billing["status"]})
            prog.progress(i / len(nid_list)); continue

        minutes_total, applicable, src_rem_sum = 0.0, 0, 0.0
        guest_excluded, guest_leaked = 0, 0
        month_rows = []
        for mid in nid_mvs.get(nid, []):
            mv = mv_of.get(str(mid), {})
            credit_type = mv.get("credit_type")
            month_date = _parse_date(mv.get("month_date"))
            src_rem = mv.get("remainder_balance")
            try:
                src_rem_v = float(src_rem) if src_rem not in (None, "") else None
            except Exception:
                src_rem_v = None
            d = {"number": number_value, "number_id": nid, "monthly_value_id": mid,
                 "month_date": mv.get("month_date"), "minutes_used": _mv_minutes(mv),
                 "source_remainder_balance": src_rem, "credit_type": credit_type,
                 "included": False, "exclude_reason": ""}
            if _norm(credit_type) == EXCLUDED_CREDIT_TYPE:
                guest_excluded += 1
                d["exclude_reason"] = "Guest credit type"; detail_rows.append(d); continue
            if month_date is None:
                d["exclude_reason"] = "Missing month_date"; detail_rows.append(d); continue
            ov = _overlap(month_date.replace(day=1), bs, be)
            if ov is None:
                d["exclude_reason"] = "No billing-cycle overlap"; detail_rows.append(d); continue
            # Minutes used this month: prefer the raw minutes field; if it's empty/0,
            # derive from the month's own remainder (20 − remainder_balance). Each month
            # resets to 20, so this recovers the usage and avoids the double-count.
            raw = _mv_minutes(mv)
            if raw > 0:
                minutes = raw
            elif src_rem_v is not None:
                minutes = max(_allow - src_rem_v, 0)
            else:
                minutes = 0.0
            minutes_total += minutes; applicable += 1
            if src_rem_v is not None:
                src_rem_sum += src_rem_v
            month_rows.append((month_date, minutes, src_rem_v))
            d.update({"minutes_used": minutes, "month_remainder": src_rem_v, "included": True,
                      "overlap_start": ov["overlap_start"], "overlap_end": ov["overlap_end"],
                      "overlap_days": ov["overlap_days"]})
            detail_rows.append(d)

        # Correct billing-cycle remainder = 20 − total minutes used across the cycle's months.
        remainder = max(_allow - minutes_total, 0)
        cycle_months = applicable
        # The balance a consumer actually sees = the LATEST calendar month's Monthly Value
        # remainder. Because it does NOT carry forward earlier-month usage, it is overstated
        # by the usage that happened in the cycle's earlier months.
        displayed_remainder, overstated = None, 0.0
        if month_rows:
            _latest = max(month_rows, key=lambda r: r[0])
            latest_minutes = _latest[1]
            displayed_remainder = (_latest[2] if _latest[2] is not None
                                   else max(_allow - latest_minutes, 0))
            overstated = round(max(displayed_remainder - remainder, 0), 1)
        earlier_usage = round(minutes_total - (max(month_rows, key=lambda r: r[0])[1] if month_rows else 0), 1)
        mismatch = ""
        if overstated > 0:
            mismatch = (f"shows {displayed_remainder:.0f} min, should be {remainder:.0f} "
                        f"(overstated by {overstated:.0f} — {earlier_usage:.0f} earlier-month min not carried forward)")
        # red flags
        flags = []
        if overstated > 0:
            flags.append(f"🚩 Balance not carried forward — shows {displayed_remainder:.0f}, should be {remainder:.0f} (overstated +{overstated:.0f})")
        if guest_leaked:
            flags.append(f"🚩 Guest leaked ({guest_leaked})")
        red_flag = " · ".join(flags)
        consumer_rows.append({**base, "subscription_id": sub_ids[0],
                              "billing_start": bs, "billing_end": be,
                              "billing_days": billing["billing_days"],
                              "billing_type": sp.get("billing_cycle_type"),
                              "billing_source": billing["source"],
                              "cycle_months": cycle_months, "monthly_values": applicable,
                              "guest_excluded": guest_excluded,
                              "minutes_used": round(minutes_total, 1), "credit_allowance": _allow,
                              "displayed_remainder": (round(displayed_remainder, 1)
                                                      if displayed_remainder is not None else None),
                              "correct_remainder": round(remainder, 1),
                              "overstated_by": overstated,
                              "remainder": round(remainder, 1),
                              "source_remainder_sum": round(src_rem_sum, 1) if applicable else None,
                              "mismatch": mismatch, "red_flag": red_flag, "status": billing["status"]})
        prog.progress(i / len(nid_list))
    prog.empty()

    consumer_df = pd.DataFrame(consumer_rows)
    detail_df = pd.DataFrame(detail_rows)
    save_report(_key, {"consumer": consumer_df, "detail": detail_df,
                       "n_numbers": len(nums), "n_eligible": len(eligible), "allowance": _allow})

saved = load_report(_key)
if saved is None:
    st.info("Click **▶ Run QA**."); report_header_close(); st.stop()

consumer_df = saved["consumer"]
detail_df = saved["detail"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · allowance {saved.get('allowance', 20)} min")
if consumer_df.empty:
    st.warning("No results."); report_header_close(); st.stop()

_ok = consumer_df["status"].isin(["OK", "OK - CALCULATED"])
calc_df = consumer_df[_ok]
issues_df = consumer_df[~_ok]
if "red_flag" not in consumer_df.columns:
    consumer_df["red_flag"] = ""
mism_df = calc_df[calc_df.get("mismatch", "").astype(str).str.len() > 0]
flag_df = consumer_df[consumer_df["red_flag"].astype(str).str.len() > 0]

# Guest safety: no Guest record may be included in any calculation
guest_excluded_total = int(detail_df["credit_type"].apply(lambda c: _norm(c) == EXCLUDED_CREDIT_TYPE).sum()) if not detail_df.empty else 0
guest_leaked_total = 0
if not detail_df.empty and "included" in detail_df.columns:
    guest_leaked_total = int(detail_df[(detail_df["included"]) &
                             (detail_df["credit_type"].apply(lambda c: _norm(c) == EXCLUDED_CREDIT_TYPE))].shape[0])


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


k = st.columns(4)
_card(k[0], "📞 Eligible (Convo Now · Live · Complimentary)", f"{saved.get('n_eligible', 0):,}",
      f"of {saved.get('n_numbers', 0):,} Convo Now scanned", "#4C8DFF")
_card(k[1], "✅ Billing calculated", f"{len(calc_df):,}", "valid billing cycle", "#2DB84B")
_card(k[2], "🚩 Red flags", f"{len(flag_df):,}",
      "balance not carried forward", "#E5484D")
_card(k[3], "⚠️ Issues", f"{len(issues_df):,}", "missing / invalid billing", "#E5A23D")
st.markdown("")

# Guest-exclusion safety banner
if guest_leaked_total == 0:
    st.success(f"🛡️ Guest check passed — **{guest_excluded_total:,}** Guest monthly-value "
               f"record(s) excluded, **0** leaked into any remainder calculation.")
else:
    st.error(f"🚨 Guest check FAILED — **{guest_leaked_total:,}** Guest record(s) were counted in "
             f"a remainder calculation. These must be excluded.")
st.markdown("")

tab0, tab1, tab2, tab3, tab4 = st.tabs(
    [f"🚩 Red flags ({len(flag_df):,})", f"Results ({len(consumer_df):,})",
     f"Mismatches ({len(mism_df):,})", f"Issues ({len(issues_df):,})",
     f"Monthly detail ({len(detail_df):,})"])

with tab0:
    st.caption("Numbers whose **displayed balance is overstated** — the billing cycle crosses a "
               "calendar month, and the later month's Monthly Value **doesn't carry forward** the "
               "earlier month's usage, so it shows **more remaining minutes than the cycle actually "
               "has left**. Correct remainder = **20 − total `convo_now_minutes_used`** across the cycle.")
    if flag_df.empty:
        st.success("No red flags — every displayed balance matches the correct cycle remainder.")
    else:
        _cols = [c for c in ["number", "red_flag", "billing_start", "billing_end", "cycle_months",
                             "minutes_used", "displayed_remainder", "correct_remainder",
                             "overstated_by", "mismatch", "guest_excluded", "status"]
                 if c in flag_df.columns]
        st.dataframe(flag_df[_cols].sort_values("number"),
                     use_container_width=True, hide_index=True, height=500)
        st.download_button("📥 Export red flags", flag_df.to_csv(index=False),
                           "convonow_red_flags.csv", "text/csv", key="flag_dl")

with tab1:
    q = st.text_input("Search number", key="rb_q").strip()
    v = consumer_df if not q else consumer_df[consumer_df["number"].astype(str).str.contains(q, case=False, na=False)]
    st.dataframe(v.sort_values("number"), use_container_width=True, hide_index=True, height=520)
    st.download_button("📥 Export CSV", consumer_df.to_csv(index=False),
                       "convonow_reminder_balance.csv", "text/csv")

with tab2:
    st.caption("Numbers where the summed Monthly Values `remainder_balance` ≠ the recomputed "
               "(20 − cycle minutes) remainder.")
    if mism_df.empty:
        st.success("No mismatches — every remainder matches the recomputed value.")
    else:
        st.dataframe(mism_df[["number", "billing_start", "billing_end", "minutes_used",
                              "credit_allowance", "remainder", "source_remainder_sum", "mismatch",
                              "status"]].sort_values("number"),
                     use_container_width=True, hide_index=True, height=480)
        st.download_button("📥 Export mismatches", mism_df.to_csv(index=False),
                           "convonow_reminder_mismatches.csv", "text/csv", key="mm_dl")

with tab3:
    if issues_df.empty:
        st.success("No billing-cycle issues.")
    else:
        st.dataframe(issues_df.sort_values("status"), use_container_width=True, hide_index=True, height=480)
        st.download_button("📥 Export issues", issues_df.to_csv(index=False),
                           "convonow_reminder_issues.csv", "text/csv", key="iss_dl")

with tab4:
    if detail_df.empty:
        st.caption("No monthly values.")
    else:
        only_inc = st.checkbox("Included months only", value=False, key="rb_inc")
        dv = detail_df[detail_df["included"]] if only_inc else detail_df
        st.dataframe(dv.sort_values(["number", "month_date"]),
                     use_container_width=True, hide_index=True, height=480)
        st.download_button("📥 Export monthly detail", detail_df.to_csv(index=False),
                           "convonow_reminder_detail.csv", "text/csv", key="det_dl")

# status breakdown
with st.expander("📊 Billing status breakdown"):
    sc = consumer_df["status"].value_counts().rename_axis("status").reset_index(name="count")
    st.dataframe(sc, use_container_width=True, hide_index=True)

report_header_close()
