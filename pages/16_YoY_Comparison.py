import streamlit as st
import pandas as pd
import altair as alt
from collections import defaultdict
from datetime import date, datetime, timezone
from utils import (
    require_auth, fetch_all, dash_spinner, norm, to_float,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)

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

# VRS Minutes (Legacy) uses URSA minutes as a temporary proxy (URSA ≈ VRS).
METRICS = {
    "VRS Minutes (Legacy)": "ursa_minutes",
    "URSA iOS Minutes":     "ursa_ios_minutes",
    "URSA Android Minutes": "ursa_android_minutes",
    "URSA Web Minutes":     "ursa_web_minutes",
    "CfZ Minutes":          "cfz_minutes",
    "Usage Minutes (URSA + CfZ)": "usage_minutes",
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
        recs = fetch_all(
            "2-46246179",
            ["month_date", "service_type"] + _MV_FIELDS,
            filter_groups=[{"filters": filters}],
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

    rows = [{"Month": mk, **{k: round(v, 1) for k, v in vals.items()}} for mk, vals in sorted(agg.items())]
    df = pd.DataFrame(rows)
    save_report(cache_key, {"df": df, "svc": svc, "start_year": start_year, "version": _CACHE_VERSION})

cached = load_report(cache_key)
if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Choose a metric and click **Run Comparison** to load monthly values.")
    st.stop()

df = cached["df"]
metric_col = METRICS[metric_label]
if cached.get("saved_at"):
    st.caption(f"📌 Data as of {saved_at_label(cached)} · service: {cached.get('svc','Both')} · "
               f"from {cached.get('start_year')} · click Run to refresh")

# Split YYYY-MM into year + month number
df = df.copy()
df["Year"] = df["Month"].str[:4].astype(int)
df["MonthNum"] = df["Month"].str[5:7].astype(int)
df["MonthName"] = df["MonthNum"].map(lambda m: MONTH_NAMES[m - 1])

years = sorted(df["Year"].unique())

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
    # full-year totals
    tot_new = df[df["Year"] == y_new][metric_col].sum()
    tot_old = df[df["Year"] == y_old][metric_col].sum()
    tiles.append(tile(f"{y_new} YTD total", f"{tot_new:,.0f}", f"vs {tot_old:,.0f} in {y_old}"))
    st.markdown(f'<div style="display:grid;grid-template-columns:repeat({len(tiles)},1fr);gap:0.85rem;margin:0.5rem 0 1.25rem;">{"".join(tiles)}</div>',
                unsafe_allow_html=True)

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
    disp[f"Δ %"] = disp.apply(
        lambda r: (f"{((r[b]-r[a])/r[a]*100):+.0f}%" if r.get(a) else "—"), axis=1)
for yc in year_cols:
    if yc in disp.columns:
        disp[yc] = disp[yc].map(lambda x: f"{x:,.1f}" if pd.notna(x) else "—")
if f"Δ {str(years[-1])} vs {str(years[-2])}" in disp.columns:
    dcol = f"Δ {str(years[-1])} vs {str(years[-2])}"
    disp[dcol] = disp[dcol].map(lambda x: f"{x:+,.1f}")
st.dataframe(disp, use_container_width=True, hide_index=True)
st.download_button("Download CSV", disp.to_csv(index=False),
                   f"yoy_{metric_col}.csv", "text/csv")
