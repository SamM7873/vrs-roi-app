import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
import os
from datetime import datetime
from collections import defaultdict
from utils import dash_spinner, require_auth, fetch_all, norm, to_float, COMMON_CSS, report_header, report_header_close, vrs_rate_for_month

st.set_page_config(page_title="Bulk Search", layout="wide", page_icon="🔎")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

from utils import get_secret
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
BASE_URL = "https://api.hubapi.com"
headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

CONVO_RATE = 2.60

def month_sort_key(m):
    try:
        return datetime.strptime(m, "%m/%d/%Y")
    except Exception:
        return datetime.min

def month_key(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        return s

report_header("Bulk Search", "Paste emails or numbers — get aggregated VRS & Convo Now stats", section="Analytics")

# ── Mode selector ──
from utils import list_all, persistent_cache
mode = st.radio("Select Mode", ["Bulk Search", "Data Explorer"], horizontal=True)

if mode == "Data Explorer":
    st.markdown("### 📊 Data Explorer - Browse All Custom Objects")

    object_type = st.selectbox(
        "Select Custom Object",
        ["VRS Numbers", "Registrations", "Monthly Values"],
        key="explorer_object"
    )

    @persistent_cache(ttl_seconds=600)
    def fetch_explorer_data(obj_type):
        if obj_type == "VRS Numbers":
            return list_all("2-40974683",
                ["number", "email", "first_name", "last_name", "number_status", "service_type",
                 "usage_type", "number_created_at", "credit_type", "ursa_sum_of_total_billable_inbound_minutes"],
                progress_label="Fetching VRS Numbers")
        elif obj_type == "Registrations":
            return list_all("2-58833629",
                ["registration_id", "registration_type", "email", "first_name", "last_name",
                 "number", "submitted_at", "registered_at"],
                progress_label="Fetching Registrations")
        else:
            return list_all("2-46246179",
                ["number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"],
                progress_label="Fetching Monthly Values")

    records = fetch_explorer_data(object_type)

    if not records:
        st.warning(f"No records found in {object_type}")
    else:
        df = pd.DataFrame([r.get("properties", {}) for r in records])

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Records", len(df))
        col2.metric("Columns", len(df.columns))
        col3.metric("Fetched", "Just now")

        search = st.text_input("🔍 Search all fields", "")
        if search:
            mask = df.astype(str).apply(lambda x: x.str.contains(search, case=False, na=False)).any(axis=1)
            df = df[mask]
            st.write(f"**Found {len(df)} records matching '{search}'**")

        st.dataframe(df, use_container_width=True, height=500)

        csv = df.to_csv(index=False)
        st.download_button(
            "📥 Download CSV",
            csv,
            f"{object_type.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv"
        )

else:
    # ── Input ──
    st.markdown("##### Paste emails or phone numbers (one per line, or comma-separated)")
    raw_input = st.text_area("bulk_input", height=140, placeholder="john@example.com\njane@example.com\n7325551234\n...", label_visibility="collapsed")
    
    agg_col, _, run_col = st.columns([2, 3, 1])
    with agg_col:
        agg_mode = st.selectbox("Aggregation", ["Sum", "Average", "Min", "Max"], label_visibility="collapsed")
    with run_col:
        run_clicked = st.button("Search", use_container_width=True)
    
    if run_clicked and raw_input.strip():
        # Parse tokens
        import re
        tokens = [t.strip() for t in re.split(r"[\n,;]+", raw_input) if t.strip()]
        emails = [t.lower() for t in tokens if "@" in t]
        numbers = [t for t in tokens if "@" not in t]
    
        if not tokens:
            st.warning("No valid emails or numbers found.")
            st.stop()
    
        st.info(f"Searching {len(emails)} email(s) and {len(numbers)} number(s)...")
    
        # Build filter groups (batch 100)
        filter_groups = []
        for i in range(0, len(numbers), 100):
            chunk = numbers[i:i+100]
            filter_groups.append({"filters": [{"propertyName": "number", "operator": "IN", "values": chunk},
                                               {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}]})
        for i in range(0, len(emails), 100):
            chunk = emails[i:i+100]
            filter_groups.append({"filters": [{"propertyName": "email", "operator": "IN", "values": chunk},
                                               {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}]})
    
        if not filter_groups:
            st.warning("Nothing to search.")
            st.stop()
    
        with dash_spinner("Fetching number records..."):
            matched = fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name", "number_status",
                 "service_type", "usage_type", "credit_type", "number_created_at"],
                filter_groups=filter_groups
            )
    
        if not matched:
            st.warning("No records found.")
            st.stop()
    
        # Group by email (person)
        num_to_person = {}
        person_nums = defaultdict(set)
        person_email = {}
        person_name = {}
        num_to_status = {}
    
        for r in matched:
            p = r.get("properties", {})
            num = str(p.get("number") or "").strip()
            email = str(p.get("email") or "").strip().lower()
            person_key = email or f"num:{num}"
            num_to_person[num] = person_key
            person_nums[person_key].add(num)
            if email and person_key not in person_email:
                person_email[person_key] = email
            fn = (p.get("first_name") or "").strip()
            ln = (p.get("last_name") or "").strip()
            if (fn or ln) and person_key not in person_name:
                person_name[person_key] = f"{fn} {ln}".strip()
            if num:
                num_to_status[num] = p.get("number_status") or ""
    
        all_nums = list(num_to_person.keys())
    
        # Fetch monthly records
        with dash_spinner("Fetching monthly values..."):
            monthly = []
            for i in range(0, len(all_nums), 100):
                chunk = all_nums[i:i+100]
                monthly.extend(fetch_all(
                    "2-46246179",
                    ["number", "month_date", "usage_minutes", "service_type"],
                    filter_groups=[{"filters": [
                        {"propertyName": "number", "operator": "IN", "values": chunk},
                        {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]}
                    ]}]
                ))
    
        # Aggregate per person per month
        person_month = defaultdict(lambda: defaultdict(lambda: {"vrs": 0.0, "convo": 0.0}))
        for r in monthly:
            p = r.get("properties", {})
            num = str(p.get("number") or "").strip()
            pk = num_to_person.get(num)
            if not pk:
                continue
            mk = month_key(p.get("month_date") or "")
            usage = to_float(p.get("usage_minutes")) or 0.0
            svc = norm(p.get("service_type"))
            if svc == "vrs":
                person_month[pk][mk]["vrs"] += usage
            elif svc == "convo now":
                person_month[pk][mk]["convo"] += usage
    
        # Build per-person summary rows
        rows = []
        for pk in sorted(person_nums.keys()):
            months = person_month.get(pk, {})
            vrs_vals = [v["vrs"] for v in months.values()]
            convo_vals = [v["convo"] for v in months.values()]
            total_vrs = sum(vrs_vals)
            total_convo = sum(convo_vals)
            avg_vrs = (total_vrs / len(vrs_vals)) if vrs_vals else 0.0
            avg_convo = (total_convo / len(convo_vals)) if convo_vals else 0.0
            vrs_cost = sum(v["vrs"] * vrs_rate_for_month(mk) for mk, v in months.items())
            convo_cost = total_convo * CONVO_RATE
            saved = vrs_cost - convo_cost
            live_nums = sum(1 for n in person_nums[pk] if norm(num_to_status.get(n,"")) == "live")
            rows.append({
                "Name": person_name.get(pk, "—"),
                "Email": person_email.get(pk, pk),
                "Numbers": ", ".join(sorted(person_nums[pk])),
                "Live Numbers": live_nums,
                "Months": len(months),
                "Total VRS Min": round(total_vrs, 1),
                "Total Convo Now Min": round(total_convo, 1),
                "Avg VRS Min/Month": round(avg_vrs, 1),
                "Avg Convo Now Min/Month": round(avg_convo, 1),
                "VRS Cost ($)": round(vrs_cost, 2),
                "Convo Now Cost ($)": round(convo_cost, 2),
                "Cost Saved ($)": round(saved, 2),
            })
    
        if not rows:
            st.warning("No monthly data found.")
            st.stop()
    
        result_df = pd.DataFrame(rows)
    
        # ── Aggregate stat tiles ──
        def agg(col):
            s = pd.to_numeric(result_df[col], errors="coerce")
            if agg_mode == "Sum":    return s.sum()
            if agg_mode == "Average": return s.mean()
            if agg_mode == "Min":    return s.min()
            if agg_mode == "Max":    return s.max()
            return s.sum()
    
        total_people = len(result_df)
        total_numbers = sum(len(v) for v in person_nums.values())
        agg_vrs = agg("Total VRS Min")
        agg_convo = agg("Total Convo Now Min")
        agg_saved = agg("Cost Saved ($)")
        saved_color = "#00A651" if agg_saved >= 0 else "#EF4444"
    
        def tile(label, value, sub="", color="#1F2937"):
            return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
      <div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
      <div style="font-size:1.4rem;font-weight:800;color:{color};line-height:1.15;">{value}</div>
      {f'<div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
    </div>"""
    
        st.markdown(f"""<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.85rem;margin:1rem 0 1.5rem;">
      {tile("People Found", total_people)}
      {tile("Total Numbers", total_numbers)}
  {tile(f"{agg_mode} VRS Min", f"{agg_vrs:,.1f}")}
  {tile(f"{agg_mode} Convo Now Min", f"{agg_convo:,.1f}")}
  {tile(f"{agg_mode} Cost Saved", f"${agg_saved:,.2f}", color=saved_color)}
  {tile("Data Months", int(result_df['Months'].sum()))}
</div>""", unsafe_allow_html=True)

    # ── Charts ──
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("##### VRS Minutes by Person")
        bar_df = result_df.nlargest(20, "Total VRS Min")[["Name", "Email", "Total VRS Min"]].copy()
        bar_df["Label"] = bar_df.apply(lambda r: r["Name"] if r["Name"] != "—" else r["Email"][:24], axis=1)
        chart = alt.Chart(bar_df).mark_bar(color="#00A651", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Total VRS Min:Q", title="VRS Minutes"),
            y=alt.Y("Label:N", sort="-x", title=None),
            tooltip=["Label", "Total VRS Min"],
        ).properties(height=max(180, len(bar_df) * 26))
        st.altair_chart(chart, use_container_width=True)

    with chart_col2:
        st.markdown("##### Cost Saved by Person")
        saved_df = result_df.copy()
        saved_df["Label"] = saved_df.apply(lambda r: r["Name"] if r["Name"] != "—" else r["Email"][:24], axis=1)
        saved_df["Color"] = saved_df["Cost Saved ($)"].apply(lambda v: "#00A651" if v >= 0 else "#EF4444")
        saved_df = saved_df.nlargest(20, "Cost Saved ($)")
        chart2 = alt.Chart(saved_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Cost Saved ($):Q", title="Cost Saved ($)"),
            y=alt.Y("Label:N", sort="-x", title=None),
            color=alt.Color("Color:N", scale=None, legend=None),
            tooltip=["Label", "Cost Saved ($)", "Total VRS Min", "Total Convo Now Min"],
        ).properties(height=max(180, len(saved_df) * 26))
        st.altair_chart(chart2, use_container_width=True)

    # ── Summary table ──
    st.markdown("##### Full Results")

    display_cols = {
        "Sum":     ["Name", "Email", "Numbers", "Live Numbers", "Months", "Total VRS Min", "Total Convo Now Min", "VRS Cost ($)", "Convo Now Cost ($)", "Cost Saved ($)"],
        "Average": ["Name", "Email", "Numbers", "Live Numbers", "Months", "Avg VRS Min/Month", "Avg Convo Now Min/Month", "VRS Cost ($)", "Convo Now Cost ($)", "Cost Saved ($)"],
        "Min":     ["Name", "Email", "Numbers", "Live Numbers", "Months", "Total VRS Min", "Total Convo Now Min", "Cost Saved ($)"],
        "Max":     ["Name", "Email", "Numbers", "Live Numbers", "Months", "Total VRS Min", "Total Convo Now Min", "Cost Saved ($)"],
    }

    show_df = result_df[display_cols.get(agg_mode, display_cols["Sum"])].sort_values("Total VRS Min" if "Total VRS Min" in result_df.columns else "Avg VRS Min/Month", ascending=False).reset_index(drop=True)

    # Color cost saved column
    def color_saved(val):
        try:
            return "color: #15803D; font-weight:600" if float(val) >= 0 else "color: #B91C1C; font-weight:600"
        except Exception:
            return ""

    styler = show_df.style.map(color_saved, subset=["Cost Saved ($)"])
    st.dataframe(styler, use_container_width=True, hide_index=True)

    # ── CSV export ──
    csv = result_df.to_csv(index=False)
    st.download_button("Download CSV", csv, file_name=f"bulk_search_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", mime="text/csv")

report_header_close()
