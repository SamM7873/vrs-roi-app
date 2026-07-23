import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from utils import (
    require_auth, dash_spinner, norm, to_float,
    headers as _hs_headers, BASE_URL as _BASE_URL,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)


def _search_seek(object_type, properties, base_filters, progress_label="Fetching…"):
    """Search past HubSpot's 10k cap using hs_object_id seek pagination."""
    url = f"{_BASE_URL}/crm/v3/objects/{object_type}/search"
    results = []
    last_id = "0"
    ph = st.empty()
    while True:
        filters = list(base_filters) + [
            {"propertyName": "hs_object_id", "operator": "GT", "value": last_id}
        ]
        payload = {
            "limit": 100, "properties": properties,
            "filterGroups": [{"filters": filters}],
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
        }
        for attempt in range(4):
            resp = requests.post(url, headers=_hs_headers, json=payload, timeout=30)
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1)); continue
            break
        if resp.status_code != 200:
            ph.empty()
            st.error(f"HubSpot error {resp.status_code}: {resp.text[:200]}")
            break
        batch = resp.json().get("results", [])
        if not batch:
            break
        results.extend(batch)
        last_id = str(batch[-1]["id"])
        ph.markdown(f"<div style='color:#6B7280;font-size:0.85rem;'>{progress_label} {len(results):,} records…</div>",
                    unsafe_allow_html=True)
        if len(batch) < 100:
            break
        time.sleep(0.05)
    ph.empty()
    return results

st.set_page_config(page_title="Year-over-Year Comparison", layout="wide", page_icon="📆")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Year-over-Year Comparison",
    "Same calendar month across years — e.g. Jul 2025 vs Jul 2026",
    section="Analytics",
)
report_header_close()

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# URSA Minutes = iOS + Android + Web (computed), so it equals the platform sum.
METRICS = {
    "CfZ Minutes":          "cfz_minutes",
    "Usage Minutes":        "usage_calc",
    "URSA Minutes":         "ursa_sum",
    "URSA iOS Minutes":     "ursa_ios_minutes",
    "URSA Android Minutes": "ursa_android_minutes",
    "URSA Web Minutes":     "ursa_web_minutes",
}
_MV_FIELDS = ["ursa_minutes", "ursa_ios_minutes", "ursa_android_minutes",
              "ursa_web_minutes", "cfz_minutes", "usage_minutes"]

c1, c2, c3 = st.columns([1, 1, 1])
with c1:
    metric_label = st.selectbox("Metric", list(METRICS.keys()), index=0)
with c2:
    svc = st.selectbox("Service Type", ["VRS", "Convo Now", "Both"], index=0)
with c3:
    start_year = st.selectbox("Compare from year", [2024, 2025, 2026], index=1)

# URSA metrics only exist in the new app (from May 2026). CfZ spans both eras.
_URSA_ONLY = {"URSA Minutes", "URSA iOS Minutes", "URSA Android Minutes", "URSA Web Minutes"}
if metric_label in _URSA_ONLY:
    st.caption("URSA metrics only exist from May 2026, so earlier months show no data. "
               "For a full year-over-year comparison, use **CfZ Minutes**.")

run = st.button("Run Comparison", type="primary")

_CACHE_VERSION = 1
cache_key = "yoy_comparison"

if run:
    floor = date(start_year, 1, 1)
    floor_ms = str(int(datetime(floor.year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
    filters = [
        {"propertyName": "month_date", "operator": "GTE", "value": floor_ms},
        {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
    ]
    if svc != "Both":
        filters.append({"propertyName": "service_type", "operator": "EQ", "value": svc})

    with dash_spinner("Fetching monthly values…"):
        recs = _search_seek(
            "2-46246179",
            ["month_date", "service_type"] + _MV_FIELDS,
            filters,
            progress_label="Fetching monthly values —",
        )

    # aggregate every metric by YYYY-MM so switching metric is instant
    agg = defaultdict(lambda: {f: 0.0 for f in _MV_FIELDS} | {"records": 0})
    for r in recs:
        p = r.get("properties", {})
        mk = (p.get("month_date") or "")[:7]  # YYYY-MM
        if not mk:
            continue
        a = agg[mk]
        for f in _MV_FIELDS:
            a[f] += to_float(p.get(f)) or 0.0
        a["records"] += 1

    rows = []
    for mk, vals in sorted(agg.items()):
        row = {"Month": mk, **{k: round(v, 1) for k, v in vals.items()}}
        # URSA Minutes = iOS + Android + Web (platform sum)
        ursa_sum = vals["ursa_ios_minutes"] + vals["ursa_android_minutes"] + vals["ursa_web_minutes"]
        row["ursa_sum"] = round(ursa_sum, 1)
        # Usage = CfZ + platform-summed URSA. Replace the raw URSA in usage_minutes
        # with the platform sum so untagged URSA minutes are excluded. For legacy
        # 2025 months (no URSA platforms) this equals the raw usage_minutes.
        row["usage_calc"] = round(vals["usage_minutes"] - vals["ursa_minutes"] + ursa_sum, 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    save_report(cache_key, {"df": df, "svc": svc, "start_year": start_year, "version": _CACHE_VERSION})

cached = load_report(cache_key)
if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Choose a metric and click **Run Comparison** to load monthly values.")
    st.stop()

df = cached["df"]
# Backfill computed URSA Minutes (= iOS+Android+Web) for reports saved before it existed
if "ursa_sum" not in df.columns and {"ursa_ios_minutes", "ursa_android_minutes", "ursa_web_minutes"} <= set(df.columns):
    df = df.copy()
    df["ursa_sum"] = (df["ursa_ios_minutes"] + df["ursa_android_minutes"] + df["ursa_web_minutes"]).round(1)
if "usage_calc" not in df.columns and {"usage_minutes", "ursa_minutes", "ursa_sum"} <= set(df.columns):
    df = df.copy()
    df["usage_calc"] = (df["usage_minutes"] - df["ursa_minutes"] + df["ursa_sum"]).round(1)
metric_col = METRICS[metric_label]
if metric_col not in df.columns:
    st.info("This saved report predates the selected metric — click **Run Comparison** to refresh.")
    st.stop()
if cached.get("saved_at"):
    st.caption(f"📌 Data as of {saved_at_label(cached)} · service: {cached.get('svc','Both')} · "
               f"from {cached.get('start_year')} · click Run to refresh")

# Split YYYY-MM into year + month number
df = df.copy()
df["Year"] = df["Month"].str[:4].astype(int)
df["MonthNum"] = df["Month"].str[5:7].astype(int)
df["MonthName"] = df["MonthNum"].map(lambda m: MONTH_NAMES[m - 1])

years = sorted(df["Year"].unique())

# ── Metric cards: all metrics at a glance for the current month, YoY ─────────
def _mval(field, year, mnum):
    row = df[(df["Year"] == year) & (df["MonthNum"] == mnum)]
    return float(row[field].sum()) if not row.empty and field in df.columns else 0.0

# Focus on the latest month that actually has data (the current data month)
_latest = df["Month"].max()              # YYYY-MM
_cur_year = int(_latest[:4])
_cur_mnum = int(_latest[5:7])

def metric_card(label, field, color):
    cur = _mval(field, _cur_year, _cur_mnum)
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;
        padding:1.1rem 1.15rem;border-top:3px solid {color};">
      <div style="font-size:0.66rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#6B7280;margin-bottom:0.4rem;">{label}</div>
      <div style="font-size:1.6rem;font-weight:800;color:#1F2937;font-variant-numeric:tabular-nums;line-height:1.1;">{cur:,.0f}</div>
      <div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">minutes</div>
    </div>"""

_mn = MONTH_NAMES[_cur_mnum - 1]
st.markdown(f"#### {_mn} {_cur_year} — Current Month")
_cards = [
    metric_card("CfZ Minutes",     "cfz_minutes",          "#F59E0B"),
    metric_card("URSA iOS",        "ursa_ios_minutes",     "#3B82F6"),
    metric_card("URSA Android",    "ursa_android_minutes", "#8B5CF6"),
    metric_card("URSA Web",        "ursa_web_minutes",     "#06B6D4"),
    metric_card("URSA Minutes",    "ursa_sum",             "#00A651"),
]
st.markdown(f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:0.25rem 0 1.5rem;">{"".join(_cards)}</div>',
            unsafe_allow_html=True)

# ── Pivot: rows = calendar month, columns = year ─────────────────────────────
pivot = (
    df.pivot_table(index=["MonthNum", "MonthName"], columns="Year",
                   values=metric_col, aggfunc="sum")
    .reset_index()
    .sort_values("MonthNum")
)

# ── Highlight tiles: current calendar month YoY ──────────────────────────────
this_month = date.today().month
def _val(year, mnum):
    row = df[(df["Year"] == year) & (df["MonthNum"] == mnum)]
    return float(row[metric_col].sum()) if not row.empty else None


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.45rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;">{value}</div>
  <div style="font-size:0.72rem;color:#9CA3AF;">{sub}</div>
</div>"""

if len(years) >= 2:
    y_new, y_old = years[-1], years[-2]
    mn = MONTH_NAMES[this_month - 1]
    cur_new, cur_old = _val(y_new, this_month), _val(y_old, this_month)
    tiles = []
    if cur_old is not None or cur_new is not None:
        delta = (cur_new or 0) - (cur_old or 0)
        pct = (delta / cur_old * 100) if cur_old else None
        dcolor = "#00A651" if delta >= 0 else "#EF4444"
        tiles.append(tile(f"{mn} {y_old}", f"{cur_old:,.0f}" if cur_old is not None else "—", metric_label))
        tiles.append(tile(f"{mn} {y_new}", f"{cur_new:,.0f}" if cur_new is not None else "—", metric_label, "#3B82F6"))
        tiles.append(tile("YoY Change",
                          (f"{'+' if delta>=0 else ''}{delta:,.0f}"),
                          (f"{'+' if (pct or 0)>=0 else ''}{pct:.0f}% vs last year" if pct is not None else "—"),
                          dcolor))
    # Same-period totals: only the month numbers present in BOTH years, so the
    # comparison is apples-to-apples (not full-year vs partial-year).
    months_new = set(df[df["Year"] == y_new]["MonthNum"])
    months_old = set(df[df["Year"] == y_old]["MonthNum"])
    shared_months = sorted(months_new & months_old)
    if shared_months:
        tot_new = df[(df["Year"] == y_new) & (df["MonthNum"].isin(shared_months))][metric_col].sum()
        tot_old = df[(df["Year"] == y_old) & (df["MonthNum"].isin(shared_months))][metric_col].sum()
        span = f"{MONTH_NAMES[shared_months[0]-1]}–{MONTH_NAMES[shared_months[-1]-1]}"
        tiles.append(tile(f"{span} total", f"{tot_new:,.0f}",
                          f"{y_new} vs {tot_old:,.0f} in {y_old} (same months)"))
    st.markdown(f'<div style="display:grid;grid-template-columns:repeat({len(tiles)},1fr);gap:0.85rem;margin:0.5rem 0 1.25rem;">{"".join(tiles)}</div>',
                unsafe_allow_html=True)
    st.caption("⚠️ The current month may be partial (still accumulating), and 2025 usage "
               "used legacy VRS minutes while 2026 uses URSA — so a drop can reflect the "
               "metric change or an incomplete month, not only real decline.")

# ── Grouped bar chart: month on x, one bar per year ──────────────────────────
st.markdown(f"#### {metric_label} by Month — Year over Year")
chart_df = df.groupby(["MonthNum", "MonthName", "Year"], as_index=False)[metric_col].sum()
chart_df["MonthName"] = pd.Categorical(chart_df["MonthName"], categories=MONTH_NAMES, ordered=True)
bar = (
    alt.Chart(chart_df)
    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
    .encode(
        x=alt.X("MonthName:N", sort=MONTH_NAMES, axis=alt.Axis(title=None, labelAngle=0)),
        xOffset=alt.XOffset("Year:N"),
        y=alt.Y(f"{metric_col}:Q", title=metric_label),
        color=alt.Color("Year:N", scale=alt.Scale(scheme="greens"),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["MonthName", "Year", alt.Tooltip(f"{metric_col}:Q", title=metric_label, format=",.1f")],
    )
    .properties(height=360)
)
st.altair_chart(bar, use_container_width=True)

# ── YoY table with deltas ────────────────────────────────────────────────────
st.markdown("#### Month-by-Month Table")
disp = pivot.drop(columns="MonthNum").rename(columns={"MonthName": "Month"})
year_cols = [c for c in disp.columns if isinstance(c, (int,)) or (isinstance(c, str) and c.isdigit())]
disp = disp.rename(columns={c: str(c) for c in disp.columns if c != "Month"})
year_cols = [str(y) for y in years]
if len(years) >= 2:
    a, b = str(years[-2]), str(years[-1])
    disp[f"Δ {b} vs {a}"] = disp[b].fillna(0) - disp[a].fillna(0)

    def _pct(r):
        old, new = r.get(a), r.get(b)
        old = 0.0 if pd.isna(old) else float(old)
        new = 0.0 if pd.isna(new) else float(new)
        if old == 0 and new == 0:
            return "—"
        if old == 0:
            return "New"          # no prior-year value to compare against
        return f"{((new - old) / old * 100):+.0f}%"

    disp["Δ %"] = disp.apply(_pct, axis=1)
for yc in year_cols:
    if yc in disp.columns:
        disp[yc] = disp[yc].map(lambda x: f"{x:,.1f}" if pd.notna(x) else "—")
if f"Δ {str(years[-1])} vs {str(years[-2])}" in disp.columns:
    dcol = f"Δ {str(years[-1])} vs {str(years[-2])}"
    disp[dcol] = disp[dcol].map(lambda x: f"{x:+,.1f}" if pd.notna(x) else "—")
st.dataframe(disp, use_container_width=True, hide_index=True)
st.download_button("Download CSV", disp.to_csv(index=False),
                   f"yoy_{metric_col}.csv", "text/csv")
from utils import pdf_download_button
_yr = int(chart_df["Year"].max())
_yoy_cd = (chart_df[chart_df["Year"] == _yr].sort_values("MonthNum")[["MonthName", metric_col]]
           .rename(columns={metric_col: "Value"}))
_yoy_cd["MonthName"] = _yoy_cd["MonthName"].astype(str)
pdf_download_button(disp, "yoy.pdf", f"Year-over-Year — {metric_label}",
                    subtitle=f"{metric_label} by month",
                    charts=[{"data": _yoy_cd, "kind": "bar", "x": "MonthName", "y": "Value",
                             "title": f"{metric_label} by month ({_yr})"}], key="yoy")
