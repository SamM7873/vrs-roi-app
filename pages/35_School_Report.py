import streamlit as st
import pandas as pd
from datetime import date, datetime, timezone
from collections import defaultdict
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, to_float, dash_spinner, save_report, load_report,
                   saved_at_label, log_report_view)

st.set_page_config(page_title="School Report", layout="wide", page_icon="🎓")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("School Report")

report_header("School Report",
              "Consumers on a .edu email domain — schools & education accounts",
              section="Analytics")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
CACHE_VERSION = 1
LOOKBACK = 6


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


today = date.today()
current = pd.Period(today.strftime("%Y-%m"), freq="M")
months = [current - i for i in range(LOOKBACK - 1, -1, -1)]
month_labels = [m.strftime("%b") for m in months]

st.markdown("Pulls **all Number records whose email ends in `.edu`** (VRS + Convo Now), "
            "then aggregates their last 6 months of billable minutes (VRS + CfZ + CN20).")
run = st.button("Run school report", type="primary")
_key = f"school_report_v{CACHE_VERSION}"

if run:
    # ── 1) all .edu numbers (server-side token filter, then confirm client-side) ─
    with dash_spinner("Finding .edu numbers…"):
        recs = fetch_all(
            NUM_OBJECT,
            ["number", "email", "first_name", "last_name", "service_type",
             "account_status", "usage_type", "number_created_at", "registered_at", "state"],
            filter_groups=[{"filters": [
                {"propertyName": "email", "operator": "CONTAINS_TOKEN", "value": "*.edu"}]}])
    num_info = {}
    for r in recs:
        p = r.get("properties", {})
        email = (p.get("email") or "").strip().lower()
        if not email.endswith(".edu"):
            continue
        n = str(p.get("number") or "").strip()
        if not n:
            continue
        svc = (p.get("service_type") or "").strip()
        info = num_info.setdefault(n, {
            "email": email, "domain": email.split("@")[-1] if "@" in email else "",
            "name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "status": (p.get("account_status") or "").strip(),
            "usage_type": (p.get("usage_type") or "").strip(),
            "state": (p.get("state") or "").strip(),
            "created": (str(p.get("registered_at") or p.get("number_created_at") or "")[:10]),
            "has_vrs": False, "has_cn": False})
        if svc == "VRS":
            info["has_vrs"] = True
        elif svc == "Convo Now":
            info["has_cn"] = True
        if (p.get("account_status") or "").strip().lower() == "live":
            info["status"] = "Live"

    numbers = list(num_info.keys())
    # ── 2) 6-month usage split VRS / CfZ / CN20 ─────────────────────────────────
    usage = defaultdict(lambda: {"vrs": 0.0, "ivcs": 0.0, "cn20": 0.0, "trend": [0.0] * len(months)})
    if numbers:
        with dash_spinner(f"Pulling usage for {len(numbers):,} numbers…"):
            for i in range(0, len(numbers), 100):
                chunk = numbers[i:i + 100]
                mv = fetch_all(
                    MV_OBJECT,
                    ["number", "month_date", "ursa_ios_minutes", "ursa_android_minutes",
                     "ursa_web_minutes", "cfz_minutes", "usage_minutes", "service_type"],
                    filter_groups=[{"filters": [
                        {"propertyName": "number", "operator": "IN", "values": chunk}]}])
                for o in mv:
                    p = o.get("properties", {})
                    n = str(p.get("number") or "").strip()
                    per = _period_of(p.get("month_date"))
                    if n not in num_info or per not in months:
                        continue
                    idx = months.index(per)
                    svc = (p.get("service_type") or "").strip()
                    if svc == "Convo Now":
                        v = to_float(p.get("usage_minutes")) or 0.0
                        usage[n]["cn20"] += v
                    else:
                        v = ((to_float(p.get("ursa_ios_minutes")) or 0)
                             + (to_float(p.get("ursa_android_minutes")) or 0)
                             + (to_float(p.get("ursa_web_minutes")) or 0))
                        usage[n]["vrs"] += v
                        usage[n]["ivcs"] += (to_float(p.get("cfz_minutes")) or 0)
                        v += (to_float(p.get("cfz_minutes")) or 0)
                    usage[n]["trend"][idx] += v

    rows = []
    for n, meta in num_info.items():
        u = usage.get(n, {"vrs": 0, "ivcs": 0, "cn20": 0, "trend": [0] * len(months)})
        total6 = round(u["vrs"] + u["ivcs"] + u["cn20"], 1)
        rows.append({
            "School Domain": meta["domain"], "Name": meta["name"], "Email": meta["email"],
            "Number": n, "Status": meta["status"] or "—", "Usage Type": meta["usage_type"] or "—",
            "State": meta["state"] or "—", "Registered": meta["created"] or "—",
            "VRS Min": round(u["vrs"], 1), "CfZ Min": round(u["ivcs"], 1),
            "CN20 Min": round(u["cn20"], 1), "Total Min (6mo)": total6,
            "Trend": [round(x, 1) for x in u["trend"]],
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "month_labels": month_labels})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run school report** to pull all .edu consumers.")
    report_header_close(); st.stop()

df = saved["df"]
month_labels = saved.get("month_labels", month_labels)
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh")

if df.empty:
    st.warning("No .edu email numbers found.")
    report_header_close(); st.stop()

# ── KPIs ────────────────────────────────────────────────────────────────────────
live = df[df["Status"].str.lower() == "live"]
active = df[df["Total Min (6mo)"] > 0]
k = st.columns(5)
k[0].metric("🎓 .edu numbers", f"{len(df):,}")
k[1].metric("Live", f"{len(live):,}")
k[2].metric("Active (6mo)", f"{len(active):,}")
k[3].metric("Unique schools", f"{df['School Domain'].nunique():,}")
k[4].metric("Total minutes (6mo)", f"{df['Total Min (6mo)'].sum():,.0f}")

# ── by school domain ────────────────────────────────────────────────────────────
st.markdown("##### By school (email domain)")
bysch = (df.groupby("School Domain")
         .agg(Numbers=("Number", "size"),
              Live=("Status", lambda s: int((s.str.lower() == "live").sum())),
              **{"Total Min (6mo)": ("Total Min (6mo)", "sum"),
                 "VRS Min": ("VRS Min", "sum"), "CfZ Min": ("CfZ Min", "sum"),
                 "CN20 Min": ("CN20 Min", "sum")})
         .reset_index().sort_values("Numbers", ascending=False))
for c in ("Total Min (6mo)", "VRS Min", "CfZ Min", "CN20 Min"):
    bysch[c] = bysch[c].round(1)
st.dataframe(bysch, use_container_width=True, hide_index=True, height=320)

# ── filters + full list ─────────────────────────────────────────────────────────
st.markdown("##### All .edu consumers")
f1, f2, f3 = st.columns([1.5, 1.5, 2])
sch_opts = ["All schools"] + sorted(df["School Domain"].unique().tolist())
sch = f1.selectbox("School", sch_opts)
status = f2.selectbox("Status", ["All", "Live only", "Active (6mo) only"])
search = f3.text_input("Search name / email / number").strip().lower()

view = df.copy()
if sch != "All schools":
    view = view[view["School Domain"] == sch]
if status == "Live only":
    view = view[view["Status"].str.lower() == "live"]
elif status == "Active (6mo) only":
    view = view[view["Total Min (6mo)"] > 0]
if search:
    view = view[view["Number"].str.contains(search, case=False, na=False)
                | view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)]
view = view.sort_values("Total Min (6mo)", ascending=False)
st.caption(f"{len(view):,} numbers")

st.dataframe(
    view[["School Domain", "Name", "Email", "Number", "Status", "Usage Type", "State",
          "Registered", "Trend", "VRS Min", "CfZ Min", "CN20 Min", "Total Min (6mo)"]],
    use_container_width=True, hide_index=True, height=460,
    column_config={"Trend": st.column_config.BarChartColumn("Trend (6mo)", y_min=0)})
st.download_button("📥 Export CSV",
                   view.assign(Trend=view["Trend"].map(lambda t: " ".join(map(str, t)))).to_csv(index=False),
                   "school_report.csv", "text/csv")

report_header_close()
