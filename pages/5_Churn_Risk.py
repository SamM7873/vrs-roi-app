import streamlit as st
import pandas as pd
import altair as alt
import time
from datetime import datetime
from collections import defaultdict
from utils import require_auth, list_all, fetch_all, norm, to_float, COMMON_CSS, report_header, report_header_close

st.set_page_config(page_title="Churn Risk Report", layout="wide", page_icon="🚨")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Churn Risk Report", "Segment D consumers — severe usage drop, highest churn risk", section="Analytics")

def month_key(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        return s

def month_sort_key(m):
    try:
        return datetime.strptime(m, "%m/%d/%Y")
    except Exception:
        return datetime.min

if st.button("Run Churn Risk Analysis", use_container_width=False):
    with st.spinner("Loading all live VRS numbers..."):
        records = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "number_status", "service_type",
             "number_created_at", "ursa_first_login", "ursa_last_outbound_call"],
            progress_label="Fetching VRS number records",
        )

    vrs_live = [
        r for r in records
        if norm(r.get("properties", {}).get("service_type") or "") == "vrs"
        and norm(r.get("properties", {}).get("number_status") or "") == "live"
    ]

    if not vrs_live:
        st.warning("No live VRS numbers found.")
        st.stop()

    all_nums = [str(r.get("properties", {}).get("number") or "").strip() for r in vrs_live if r.get("properties", {}).get("number")]
    num_props = {str(r.get("properties", {}).get("number") or "").strip(): r.get("properties", {}) for r in vrs_live}

    with st.spinner(f"Fetching monthly usage for {len(all_nums):,} numbers..."):
        monthly = []
        for i in range(0, len(all_nums), 100):
            chunk = all_nums[i:i + 100]
            monthly.extend(fetch_all(
                "2-46246179",
                ["number", "month_date", "usage_minutes", "service_type"],
                filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                ]}]
            ))

    num_month = defaultdict(dict)
    for r in monthly:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        mk = month_key(p.get("month_date") or "")
        usage = to_float(p.get("usage_minutes")) or 0.0
        num_month[num][mk] = num_month[num].get(mk, 0.0) + usage

    today_mk = datetime.now().strftime("%m/01/%Y")
    rows = []
    seg_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    for num, months in num_month.items():
        history_pairs = sorted(
            [(k, v) for k, v in months.items() if k != today_mk],
            key=lambda x: month_sort_key(x[0])
        )
        if len(history_pairs) < 2:
            continue
        history = [v for _, v in history_pairs]
        baseline = sum(history) / len(history)
        if baseline <= 0:
            continue
        last_mk, last_usage = history_pairs[-1]
        last_perf = (last_usage / baseline * 100) if baseline > 0 else 0.0

        if last_perf >= 100:   seg = "A"
        elif last_perf >= 75:  seg = "B"
        elif last_perf >= 40:  seg = "C"
        else:                  seg = "D"

        seg_counts[seg] += 1
        if seg != "D":
            continue

        p = num_props.get(num, {})
        name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—"
        rows.append({
            "Number": num,
            "Name": name,
            "Email": p.get("email") or "—",
            "Last Month": last_mk,
            "Last Month Usage (min)": round(last_usage, 1),
            "Historical Baseline (min)": round(baseline, 1),
            "Last Month Perf %": round(last_perf, 1),
            "History Months": len(history_pairs),
            "Last Login": p.get("ursa_first_login") or "",
            "Last Outbound Call": p.get("ursa_last_outbound_call") or "",
        })

    total_analyzed = sum(seg_counts.values())
    st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin:1rem 0 1.5rem;">
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">Analyzed</div>
    <div style="font-size:1.4rem;font-weight:800;color:#1F2937;">{total_analyzed:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">📈 A — Growth</div>
    <div style="font-size:1.4rem;font-weight:800;color:#2DB84B;">{seg_counts['A']:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">✅ B — Stable</div>
    <div style="font-size:1.4rem;font-weight:800;color:#3B82F6;">{seg_counts['B']:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.25rem;">⚠️ C — Declining</div>
    <div style="font-size:1.4rem;font-weight:800;color:#F59E0B;">{seg_counts['C']:,}</div>
  </div>
  <div style="background:#fff;border:1px solid #EF4444;border-radius:10px;padding:1rem 1.25rem;">
    <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#EF4444;margin-bottom:0.25rem;">🚨 D — At Risk</div>
    <div style="font-size:1.4rem;font-weight:800;color:#EF4444;">{seg_counts['D']:,}</div>
  </div>
</div>""", unsafe_allow_html=True)

    if not rows:
        st.success("No Segment D consumers found — no immediate churn risk detected.")
    else:
        risk_df = pd.DataFrame(rows).sort_values("Last Month Perf %")

        from datetime import datetime as _dt
        def _fmt(v):
            if not v: return "—"
            try:
                return _dt.fromisoformat(v.replace("Z", "+00:00")).strftime("%b %d, %Y")
            except Exception:
                return v

        risk_df["Last Login"] = risk_df["Last Login"].apply(_fmt)
        risk_df["Last Outbound Call"] = risk_df["Last Outbound Call"].apply(_fmt)

        st.markdown(f"##### {len(risk_df)} Segment D Consumers — sorted by worst performance first")

        chart_df = risk_df[["Name", "Email", "Last Month Perf %"]].copy()
        chart_df["Label"] = chart_df.apply(lambda r: r["Name"] if r["Name"] != "—" else r["Email"][:28], axis=1)
        bar = alt.Chart(chart_df.head(30)).mark_bar(color="#EF4444", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Last Month Perf %:Q", title="Last Month vs Baseline (%)"),
            y=alt.Y("Label:N", sort="x", title=None),
            tooltip=["Label", "Last Month Perf %"],
        ).properties(height=max(200, len(chart_df.head(30)) * 22), title="Bottom 30 — Last Month Performance vs Baseline")
        st.altair_chart(bar, use_container_width=True)

        display_cols = ["Number", "Name", "Email", "Last Month", "Last Month Usage (min)",
                        "Historical Baseline (min)", "Last Month Perf %", "History Months",
                        "Last Login", "Last Outbound Call"]
        st.dataframe(risk_df[display_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
        st.download_button("Download Churn Risk CSV", risk_df.to_csv(index=False),
                           f"churn_risk_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

report_header_close()
