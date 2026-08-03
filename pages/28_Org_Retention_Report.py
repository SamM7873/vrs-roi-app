import streamlit as st
import pandas as pd
from datetime import date, datetime, timezone
from utils import (require_auth, get_secret, COMMON_CSS, report_header,
                   report_header_close, fetch_all, log_report_view)

st.set_page_config(page_title="Org Retention Report", layout="wide", page_icon="🏢")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Org Retention Report")

report_header("Organization Retention Report",
              "Are organization VRS customers maintaining usage vs. their 6-month baseline?",
              section="Analytics")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _period_of(month_date):
    """Monthly Value month_date → pandas Period('M'). Handles epoch-ms or ISO."""
    if not month_date:
        return None
    try:
        s = str(month_date)
        if s.isdigit():
            dt = datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return pd.Period(dt.strftime("%Y-%m"), freq="M")
    except Exception:
        return None


# ── month window: 6 baseline months + latest completed month ────────────────
today = date.today()
latest = pd.Period(today.strftime("%Y-%m"), freq="M") - 1   # previous completed month
baseline_months = [latest - i for i in range(6, 0, -1)]   # e.g. Jan..Jun
all_months = baseline_months + [latest]                   # Jan..Jul
month_cols = [m.strftime("%b %Y") for m in all_months]
latest_label = latest.strftime("%b %Y")

st.caption(f"Baseline = mean of **{baseline_months[0].strftime('%b %Y')}–{baseline_months[-1].strftime('%b %Y')}** "
           f"· Compared month = **{latest_label}** (latest completed; current month excluded).")

run = st.button("Run Organization Retention Report", type="primary")

if run:
    # ── 1) Organization VRS Live numbers ────────────────────────────────────
    # usage_type is stored inconsistently (Organization / Business / Org …), so
    # fetch VRS + Live and match org variants client-side.
    ORG_VALUES = {"organization", "organisation", "org", "business"}
    with st.spinner("Fetching Organization VRS numbers…"):
        num_recs = fetch_all(
            NUM_OBJECT,
            ["number", "email", "first_name", "last_name", "usage_type",
             "service_type", "number_status"],
            filter_groups=[{"filters": [
                {"propertyName": "usage_type", "operator": "EQ", "value": "Organization"},
                {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                {"propertyName": "number_status", "operator": "EQ", "value": "Live"},
            ]}])

    num_info = {}
    _seen_types = {}
    for r in num_recs:
        p = r.get("properties", {})
        ut = (p.get("usage_type") or "").strip()
        _seen_types[ut] = _seen_types.get(ut, 0) + 1
        if ut.lower() not in ORG_VALUES:
            continue
        num = str(p.get("number") or "").strip()
        if not num:
            continue
        org = (f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
               or (p.get("email") or "").strip() or "—")
        num_info[num] = {"org": org, "email": (p.get("email") or "").strip()}

    if not num_info:
        st.warning("No Organization VRS Live numbers found.")
        if _seen_types:
            st.caption("usage_type values seen on VRS Live numbers: "
                       + ", ".join(f"'{k or '(blank)'}' ({v})"
                                   for k, v in sorted(_seen_types.items(), key=lambda x: -x[1])[:10]))
        report_header_close()
        st.stop()

    numbers = list(num_info.keys())
    st.caption(f"{len(numbers):,} Organization VRS Live numbers.")

    # ── 2) Monthly Values for those numbers (from baseline start) ────────────
    start_ms = str(int(datetime(baseline_months[0].year, baseline_months[0].month, 1,
                                tzinfo=timezone.utc).timestamp() * 1000))
    usage = {}  # number -> {Period: minutes}
    with st.spinner("Fetching monthly usage values…"):
        for i in range(0, len(numbers), 100):
            chunk = numbers[i:i + 100]
            mv = fetch_all(
                MV_OBJECT, ["number", "month_date", "usage_minutes", "service_type"],
                filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                    {"propertyName": "month_date", "operator": "GTE", "value": start_ms},
                    {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                ]}])
            for o in mv:
                p = o.get("properties", {})
                num = str(p.get("number") or "").strip()
                per = _period_of(p.get("month_date"))
                if num in num_info and per in all_months:
                    usage.setdefault(num, {}).setdefault(per, 0.0)
                    usage[num][per] += _num(p.get("usage_minutes"))

    # ── 3) Build table ──────────────────────────────────────────────────────
    rows = []
    for num, meta in num_info.items():
        mm = usage.get(num, {})
        month_vals = [round(mm.get(m, 0.0), 1) for m in all_months]
        base_vals = [mm.get(m, 0.0) for m in baseline_months]
        baseline = sum(base_vals) / 6.0
        jul = mm.get(latest, 0.0)
        variance = jul - baseline
        retention = (jul / baseline * 100) if baseline > 0 else None
        rows.append({
            "Organization": meta["org"],
            "Number": num,
            **{month_cols[i]: month_vals[i] for i in range(len(all_months))},
            "6-Month Baseline": round(baseline, 1),
            f"{latest_label} vs Baseline": round(variance, 1),
            "Retention %": round(retention, 1) if retention is not None else None,
            "_diff_pct": ((variance / baseline * 100) if baseline > 0 else None),
            "_jul": jul,
        })
    df = pd.DataFrame(rows)

    # ── status band ──
    def _status(row):
        if row["_jul"] <= 0:
            return "⚪ No usage"
        d = row["_diff_pct"]
        if d is None:
            return "⚪ No baseline"
        if d >= 10:
            return "🟢 Improved (+10%)"
        if d >= 5:
            return "🟢 Up (5–10%)"
        if d > -5:
            return "🟡 Stable (±5%)"
        if d >= -15:
            return "🟠 Declining (5–15%)"
        return "🔴 At risk (>15%)"
    df["Status"] = df.apply(_status, axis=1)
    df = df.sort_values("Retention %", ascending=True, na_position="first").reset_index(drop=True)

    # ── KPIs ──
    total_nums = len(df)
    avg_baseline = df["6-Month Baseline"].mean()
    total_jul = df[latest_label].sum()
    avg_ret = df["Retention %"].dropna().mean()
    above = int((df["_diff_pct"] > 0).sum())
    below = int((df["_diff_pct"] < 0).sum())
    no_jul = int((df["_jul"] <= 0).sum())

    st.markdown("##### Summary")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total org numbers", f"{total_nums:,}")
    k2.metric("Avg baseline (min)", f"{avg_baseline:,.1f}")
    k3.metric(f"Total {latest_label} usage", f"{total_jul:,.0f} min")
    k4.metric("Avg retention %", f"{avg_ret:.1f}%" if pd.notna(avg_ret) else "—")
    k5, k6, k7 = st.columns(3)
    k5.metric("🟢 Above baseline", f"{above:,}")
    k6.metric("🔴 Below baseline", f"{below:,}")
    k7.metric(f"⚪ No {latest_label} usage", f"{no_jul:,}")

    # ── color-coded table ──
    st.markdown("##### Retention by organization number")
    disp_cols = (["Organization", "Number"] + month_cols +
                 ["6-Month Baseline", f"{latest_label} vs Baseline", "Retention %", "Status"])
    disp = df[disp_cols].copy()

    _band_color = {
        "🟢 Improved (+10%)": "#1E8449",
        "🟢 Up (5–10%)": "#A9DFBF",
        "🟡 Stable (±5%)": "#F9E79F",
        "🟠 Declining (5–15%)": "#F5B041",
        "🔴 At risk (>15%)": "#E74C3C",
        "⚪ No usage": "#E5E7EB",
        "⚪ No baseline": "#E5E7EB",
    }

    def _row_style(row):
        c = _band_color.get(row["Status"], "")
        styles = [""] * len(row)
        for col in ("Retention %", f"{latest_label} vs Baseline", "Status"):
            if col in row.index:
                idx = list(row.index).index(col)
                dark = row["Status"] in ("🟢 Improved (+10%)", "🔴 At risk (>15%)")
                styles[idx] = f"background-color:{c};" + ("color:white;" if dark else "")
        return styles

    try:
        styled = disp.style.apply(_row_style, axis=1).format(precision=1)
        st.dataframe(styled, use_container_width=True, hide_index=True, height=520)
    except Exception:
        st.dataframe(disp, use_container_width=True, hide_index=True, height=520)

    st.download_button("📥 Export CSV", disp.to_csv(index=False),
                       f"org_retention_{today:%Y%m%d}.csv", "text/csv")

report_header_close()
