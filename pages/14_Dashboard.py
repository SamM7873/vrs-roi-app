import streamlit as st
import pandas as pd
import requests
import os
import time
from datetime import date, datetime, timezone, timedelta
from utils import (
    require_auth, fetch_all, dash_spinner, to_float, vrs_rate_for_month,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="Dashboard", layout="wide", page_icon="📈")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

HUBSPOT_TOKEN = st.secrets.get("HUBSPOT_TOKEN", os.environ.get("HUBSPOT_TOKEN", ""))
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
BASE_URL = "https://api.hubapi.com"

report_header("Dashboard", "Live operations overview", section="Overview")
report_header_close()


def _count(object_id, filters):
    """Cheap total count via search API (limit 1)."""
    try:
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_id}/search",
            headers=_headers,
            json={"filterGroups": [{"filters": filters}], "properties": ["hs_object_id"], "limit": 1},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("total", 0)
    except Exception:
        pass
    return None


def _fmt(v):
    return f"{v:,}" if v is not None else "—"


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.5rem;font-weight:800;color:{color};">{value}</div>
  <div style="font-size:0.72rem;color:#9CA3AF;">{sub}</div>
</div>"""


# ── Live pulse (auto-refreshes every 60s) ────────────────────────────────────
@st.fragment(run_every=60)
def live_pulse():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    ms = lambda d: str(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000))

    counts = {
        "vrs_live":      _count("2-40974683", [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                               {"propertyName": "number_status", "operator": "EQ", "value": "Live"}]),
        "vrs_susp":      _count("2-40974683", [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                                               {"propertyName": "number_status", "operator": "EQ", "value": "Suspended"}]),
        "cn_live":       _count("2-40974683", [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                                               {"propertyName": "number_status", "operator": "EQ", "value": "Live"}]),
        "tickets_week":  _count("tickets",    [{"propertyName": "closed_date", "operator": "GTE", "value": ms(week_start)}]),
        "tickets_today": _count("tickets",    [{"propertyName": "closed_date", "operator": "GTE", "value": ms(today)}]),
    }

    now_ct = datetime.now(timezone(timedelta(hours=-5 if 3 <= today.month <= 11 else -6)))
    st.markdown(
        f"<div style='font-size:0.72rem;color:#6B7280;margin-bottom:0.5rem;'>"
        f"🟢 Live · auto-refreshes every 60s · last updated {now_ct.strftime('%I:%M:%S %p')}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin-bottom:1.5rem;">
  {tile("Live VRS Numbers", _fmt(counts["vrs_live"]), "service_type = VRS", "#00A651")}
  {tile("Suspended VRS", _fmt(counts["vrs_susp"]), "service_type = VRS", "#EF4444")}
  {tile("Live Convo Now", _fmt(counts["cn_live"]), "service_type = Convo Now", "#3B82F6")}
  {tile("Tickets Closed Today", _fmt(counts["tickets_today"]), "all pipelines")}
  {tile("Tickets Closed This Week", _fmt(counts["tickets_week"]), f"since {week_start.strftime('%b %d')}")}
</div>""", unsafe_allow_html=True)


live_pulse()

# ── Monthly usage summary (on demand, saved to disk) ─────────────────────────
st.markdown("#### Monthly Usage Summary")

cached = load_report("dashboard_monthly")
refresh = st.button("Refresh Monthly Summary", type="primary")

if refresh or cached is None:
    if refresh or cached is None and st.session_state.get("_dash_auto_load", True):
        today = date.today()
        m, y = today.month - 1, today.year
        if m <= 0:
            m += 12; y -= 1
        prev_floor = date(y, m, 1)
        floor_ms = str(int(datetime(prev_floor.year, prev_floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))

        with dash_spinner("Fetching monthly values (current + previous month)…"):
            mv_recs = fetch_all(
                "2-46246179",
                ["month_date", "service_type", "ursa_minutes", "cfz_minutes", "usage_minutes",
                 "fcc_cost_based_on_vrs_usage", "fcc_cost_based_on_cfz_usage"],
                filter_groups=[{"filters": [
                    {"propertyName": "month_date", "operator": "GTE", "value": floor_ms},
                    {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                ]}]
            )

        agg = {}
        for r in mv_recs:
            p = r.get("properties", {})
            mk  = (p.get("month_date") or "")[:7]
            svc = (p.get("service_type") or "").strip() or "Unknown"
            if not mk:
                continue
            key = (mk, svc)
            a = agg.setdefault(key, {"ursa": 0.0, "cfz": 0.0, "usage": 0.0, "fcc": 0.0, "numbers": 0})
            a["ursa"]  += to_float(p.get("ursa_minutes")) or 0.0
            a["cfz"]   += to_float(p.get("cfz_minutes")) or 0.0
            a["usage"] += to_float(p.get("usage_minutes")) or 0.0
            a["fcc"]   += (to_float(p.get("fcc_cost_based_on_vrs_usage")) or 0.0) + \
                          (to_float(p.get("fcc_cost_based_on_cfz_usage")) or 0.0)
            a["numbers"] += 1

        rows = [{"Month": mk, "Service": svc, **{
            "Active Numbers": v["numbers"],
            "URSA Min": round(v["ursa"], 1),
            "CfZ Min": round(v["cfz"], 1),
            "Usage Min": round(v["usage"], 1),
            "FCC Cost": round(v["fcc"], 2),
        }} for (mk, svc), v in sorted(agg.items())]
        cached = {"df": pd.DataFrame(rows)}
        save_report("dashboard_monthly", cached)
        cached = load_report("dashboard_monthly") or cached

if cached and not cached.get("df", pd.DataFrame()).empty:
    df = cached["df"]
    if cached.get("saved_at"):
        st.caption(f"📌 Data as of {saved_at_label(cached)} · click Refresh Monthly Summary to update")

    months = sorted(df["Month"].unique())
    cur = months[-1] if months else None
    if cur:
        cur_df = df[df["Month"] == cur]
        vrs_row = cur_df[cur_df["Service"] == "VRS"]
        cn_row  = cur_df[cur_df["Service"] == "Convo Now"]
        v = vrs_row.iloc[0] if not vrs_row.empty else None
        c = cn_row.iloc[0] if not cn_row.empty else None
        rate = vrs_rate_for_month(cur + "-01")
        st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:0.75rem 0 1.25rem;">
  {tile(f"VRS Usage · {cur}", f"{v['Usage Min']:,.0f} min" if v is not None else "—", f"{v['Active Numbers']:,} active numbers" if v is not None else "")}
  {tile("URSA Minutes", f"{v['URSA Min']:,.0f}" if v is not None else "—", "VRS sub-type", "#00A651")}
  {tile("CfZ Minutes", f"{v['CfZ Min']:,.0f}" if v is not None else "—", "VRS sub-type", "#8B5CF6")}
  {tile("VRS FCC Cost", f"${v['FCC Cost']:,.0f}" if v is not None else "—", f"@ ${rate}/min", "#00A651")}
  {tile(f"Convo Now · {cur}", f"{c['Usage Min']:,.0f} min" if c is not None else "—", f"{c['Active Numbers']:,} active numbers" if c is not None else "", "#3B82F6")}
</div>""", unsafe_allow_html=True)

    st.markdown("##### By Month & Service")
    st.dataframe(df.sort_values(["Month", "Service"], ascending=[False, True]),
                 use_container_width=True, hide_index=True)
else:
    st.info("Click **Refresh Monthly Summary** to load usage data.")
