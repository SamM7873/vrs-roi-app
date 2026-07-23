import streamlit as st
import pandas as pd
import altair as alt
import requests
import copy
import time
from datetime import date, datetime, timezone
from utils import (
    require_auth, get_secret, COMMON_CSS, report_header, report_header_close,
    save_report, load_report, pdf_download_button,
)

st.set_page_config(page_title="Retention Report", layout="wide", page_icon="🔁")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Retention Report", "Do VRS / CfZ customers stay? Cohort retention at 3, 6 and 12 months", section="Analytics")

BASE_URL = "https://api.hubapi.com"
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
MV_OBJECT = "2-46246179"
PRIMARY, GREEN, BLUE, AMBER = "#C9A876", "#0D3B26", "#3B82F6", "#F59E0B"


def _groups(metric, start_ms):
    date_f = {"propertyName": "month_date", "operator": "GTE", "value": start_ms}
    vrs = {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}
    usage = {"propertyName": "usage_minutes", "operator": "GT", "value": "0"}
    cfz = {"propertyName": "cfz_minutes", "operator": "GT", "value": "0"}
    if metric == "CfZ usage":
        return [{"filters": [cfz, date_f]}]
    if metric == "VRS or CfZ":
        return [{"filters": [vrs, usage, date_f]}, {"filters": [cfz, date_f]}]
    if metric == "VRS and CfZ (both)":
        return [{"filters": [vrs, usage, cfz, date_f]}]  # same month must have both
    return [{"filters": [vrs, usage, date_f]}]  # VRS usage (default)


def _paged_search(base_groups, props):
    """Fetch all matching Monthly Value rows, paging past HubSpot's 10k cap
    via an hs_object_id cursor added to each filter group."""
    url = f"{BASE_URL}/crm/v3/objects/{MV_OBJECT}/search"
    props = list(props) + (["hs_object_id"] if "hs_object_id" not in props else [])
    out, last = [], None
    loader = st.empty()
    WINDOW = 9900
    while True:
        groups = copy.deepcopy(base_groups)
        if last is not None:
            for g in groups:
                g["filters"].append({"propertyName": "hs_object_id", "operator": "GT", "value": str(last)})
        after, window, hit = None, 0, False
        while True:
            payload = {"limit": 100, "properties": props, "filterGroups": groups,
                       "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}]}
            if after:
                payload["after"] = after
            r = requests.post(url, headers=_headers, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(1.5); continue
            if r.status_code != 200:
                loader.empty()
                if not out:
                    st.error(f"Error {r.status_code}: {r.text[:200]}")
                return out
            data = r.json()
            res = data.get("results", [])
            if not res:
                break
            out.extend(res)
            window += len(res)
            last = res[-1].get("properties", {}).get("hs_object_id") or res[-1].get("id")
            loader.markdown(
                f"<div style='padding:0.6rem 1rem;background:#F4F1E8;border:1px solid #DDD9CC;border-radius:10px;'>"
                f"Fetching active user-months… <strong>{len(out):,}</strong></div>", unsafe_allow_html=True)
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            if window >= WINDOW:
                hit = True; break
        if not hit:
            break
    loader.empty()
    return out


def _ord(p):
    return p.year * 12 + (p.month - 1)


# ── controls ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns([2, 1.6, 1.8, 1])
metric = c1.selectbox("Active means…", ["VRS usage", "CfZ usage", "VRS or CfZ", "VRS and CfZ (both)"],
                      help="A user is 'active' in a month if they generated at least 1 minute of this usage. "
                           "'both' requires VRS and CfZ minutes in the same month.")
lookback = c2.selectbox("Look back",
                        ["Last 3 months", "Last 6 months", "Last 9 months", "Last 12 months",
                         "Last 18 months", "Last 24 months"], index=3)
unit = c3.selectbox("Count users by", ["VRS Number", "Person (email)"],
                    help="Person merges a customer's multiple numbers into one user via email.")
with c4:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)
report_header_close()

_months_back = int(lookback.split()[1])
_start = (date.today().replace(day=1))
_y, _m = _start.year, _start.month - _months_back
while _m <= 0:
    _m += 12; _y -= 1
start_date = date(_y, _m, 1)
start_ms = str(int(datetime(start_date.year, start_date.month, 1, tzinfo=timezone.utc).timestamp() * 1000))

# active user-months are cached by metric+window only (unit-independent)
_BASE = f"retention_base_{metric.replace(' ', '_')}_{_months_back}m_{start_date}"
base = None if run else load_report(_BASE)
if base is None and not run:
    st.info("Choose what counts as **active**, a look-back window and unit, then **Run Report**. "
            "The first run scans active user-months (can take a minute); results are then saved.")
    st.stop()

if run or base is None:
    recs = _paged_search(_groups(metric, start_ms), ["number", "month_date"])
    rows = [{"number": str(r.get("properties", {}).get("number") or "").strip(),
             "month_date": r.get("properties", {}).get("month_date")} for r in recs]
    save_report(_BASE, {"rows": rows})
    _saved_at = time.time()
else:
    rows = base.get("rows", [])
    _saved_at = base.get("saved_at")

if not rows:
    st.warning("No active user-months found for this window/metric.")
    st.stop()

active = pd.DataFrame(rows)
active = active[active["number"] != ""]

# ── resolve numbers → person (email) if requested ────────────────────────────
if unit == "Person (email)":
    from utils import fetch_all
    _EMAP = f"retention_emap_{metric.replace(' ', '_')}_{_months_back}m_{start_date}"
    _cachedmap = None if run else load_report(_EMAP)
    if run or _cachedmap is None:
        _nums = sorted(active["number"].unique())
        _map = {}
        with st.spinner(f"Resolving emails for {len(_nums):,} numbers…"):
            for i in range(0, len(_nums), 100):
                chunk = _nums[i:i + 100]
                for r in fetch_all("2-40974683", ["number", "email"],
                                   filter_groups=[{"filters": [{"propertyName": "number", "operator": "IN", "values": chunk}]}]):
                    p = r.get("properties", {})
                    n = str(p.get("number") or "").strip()
                    e = str(p.get("email") or "").strip().lower()
                    if n and e:
                        _map.setdefault(n, e)
        save_report(_EMAP, {"map": _map})
    else:
        _map = _cachedmap.get("map", {})
    active["user"] = active["number"].map(lambda n: _map.get(n, n))  # fall back to number
    _unit_label = "people"
else:
    active["user"] = active["number"]
    _unit_label = "numbers"

# ── build cohorts (keyed by user) ────────────────────────────────────────────
active["month"] = pd.to_datetime(active["month_date"], errors="coerce").dt.to_period("M")
active = active.dropna(subset=["month"]).drop_duplicates(["user", "month"])

first = active.groupby("user")["month"].min().rename("cohort")
active = active.merge(first, on="user")
active["_mo"] = active["month"].apply(_ord)
active["_co"] = active["cohort"].apply(_ord)
active["offset"] = active["_mo"] - active["_co"]

latest_ord = int(active["_mo"].max())
cohort_size = active.groupby("cohort")["user"].nunique()
retained = active.groupby(["cohort", "offset"])["user"].nunique().reset_index(name="retained")

MAXO = min(12, _months_back)  # can't measure further than the look-back window


def overall_retention(o):
    elig = [c for c in cohort_size.index if _ord(c) + o <= latest_ord]
    if not elig:
        return None, 0
    base = int(cohort_size[elig].sum())
    ret = int(retained[(retained["offset"] == o) & (retained["cohort"].isin(elig))]["retained"].sum())
    return (ret / base * 100 if base else None), base


r1, b1 = overall_retention(1)
r3, b3 = overall_retention(3)
r6, b6 = overall_retention(6)
r9, b9 = overall_retention(9)
r12, b12 = overall_retention(12)

if _saved_at:
    _a = int(time.time() - _saved_at)
    _ago = "just now" if _a < 90 else (f"{_a//60} min ago" if _a < 3600 else f"{_a//3600} h ago")
    st.caption(f"📌 Saved · refreshed {_ago} · {metric} · {lookback} · click **Run Report** to refresh.")


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.15rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">{label}</div>
  <div style="font-size:1.5rem;font-weight:800;color:{color};line-height:1.1;">{value}</div>
  {f'<div style="font-size:0.7rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
</div>"""

def _pct(v):
    return f"{v:.0f}%" if v is not None else "—"

st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.7rem;margin-bottom:1.4rem;">
  {tile(f"{_unit_label.title()} tracked", f"{active['user'].nunique():,}", f"active since {start_date:%b %Y}")}
  {tile("1-Month", _pct(r1), f"of {b1:,} eligible", "#6B7280")}
  {tile("3-Month", _pct(r3), f"of {b3:,} eligible", GREEN)}
  {tile("6-Month", _pct(r6), f"of {b6:,} eligible", BLUE)}
  {tile("9-Month", _pct(r9), f"of {b9:,} eligible", "#8B5CF6")}
  {tile("12-Month", _pct(r12), f"of {b12:,} eligible", AMBER)}
</div>""", unsafe_allow_html=True)

with st.expander("ℹ️ How retention is calculated"):
    st.markdown(f"""
- A **user** = a {"person (customer email; a customer's multiple numbers count once)" if unit == "Person (email)" else "VRS number"}. They're **active** in a month if they generated ≥ 1 minute of **{metric}** that month.
- Each user's **cohort** is their **first active month** in the window.
- **N-Month Retention** = of a cohort, the share still active **N months after** their first month, averaged across all cohorts old enough to have reached that point (weighted by cohort size).
- The **cohort table** below shows each starting month across the top offsets (M0 = 100% by definition). Blank cells mean that cohort hasn't reached that age yet.
- Window: active months since **{start_date:%b %Y}**. Widen the look-back for more 12-month data points.
""")

# ── retention curve ──────────────────────────────────────────────────────────
curve = []
for o in range(0, MAXO + 1):
    v, base = overall_retention(o)
    if v is not None:
        curve.append({"Month": o, "Retention": round(v, 1), "Users": base})
curve_df = pd.DataFrame(curve)

st.markdown("##### Retention Curve — % of cohort still active by month")
if not curve_df.empty:
    line = alt.Chart(curve_df).mark_area(
        line={"color": PRIMARY, "strokeWidth": 3},
        point=alt.OverlayMarkDef(color=GREEN, size=60),
        color=alt.Gradient(gradient="linear",
                           stops=[alt.GradientStop(color="#F4F1E8", offset=0),
                                  alt.GradientStop(color=PRIMARY, offset=1)], x1=1, x2=1, y1=1, y2=0)).encode(
        x=alt.X("Month:O", title="Months since first active"),
        y=alt.Y("Retention:Q", title="% still active", scale=alt.Scale(domain=[0, 100])),
        tooltip=["Month", "Retention", "Users"]).properties(height=300)
    st.altair_chart(line, use_container_width=True)

# ── cohort table (triangle) ──────────────────────────────────────────────────
st.markdown("##### Cohort Retention (%) by starting month")
pivot = retained.pivot(index="cohort", columns="offset", values="retained")
pct = pivot.divide(cohort_size, axis=0) * 100
pct = pct[[c for c in range(0, MAXO + 1) if c in pct.columns]]
pct.columns = [f"M{c}" for c in pct.columns]
coh = pct.reset_index()
coh.insert(0, "Cohort", coh.pop("cohort").astype(str))
coh.insert(1, "Users", cohort_size.reindex([pd.Period(c, "M") for c in coh["Cohort"]]).values)
coh = coh.sort_values("Cohort", ascending=False)
_fmt_cols = {c: st.column_config.NumberColumn(c, format="%.0f%%") for c in coh.columns if c.startswith("M")}
st.dataframe(coh, use_container_width=True, hide_index=True, column_config=_fmt_cols)

st.download_button("📥 Download CSV", coh.to_csv(index=False),
                   f"retention_{metric.replace(' ', '_')}_{datetime.now():%Y%m%d}.csv", "text/csv")

# ── PDF ──────────────────────────────────────────────────────────────────────
_pdf_metrics = [(f"{_unit_label.title()}", f"{active['user'].nunique():,}"),
                ("1-Mo", _pct(r1)), ("3-Mo", _pct(r3)), ("6-Mo", _pct(r6)),
                ("9-Mo", _pct(r9)), ("12-Mo", _pct(r12))]
_pdf_charts = [{"data": curve_df.rename(columns={"Month": "Month #"}), "kind": "line",
                "x": "Month #", "y": "Retention", "title": "Retention curve (% active by month)"}] if not curve_df.empty else []
pdf_download_button(coh, "retention_report.pdf", f"Retention Report — {metric}",
                    subtitle=f"{lookback} · active = ≥1 min {metric}",
                    metrics=_pdf_metrics, charts=_pdf_charts, key="retention")

report_header_close()
