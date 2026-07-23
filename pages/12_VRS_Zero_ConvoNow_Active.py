import streamlit as st
import pandas as pd
import altair as alt
import os
from datetime import date, datetime, timezone
from collections import defaultdict
import time
from utils import (
    dash_spinner,
    require_auth, fetch_all, norm, to_float,
    COMMON_CSS, report_header, report_header_close,
    save_report, load_report,
)

st.set_page_config(page_title="VRS Zero / Convo Now Active", layout="wide", page_icon="🔄")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

CONVO_RATE = 2.60

report_header(
    "VRS Zero / Convo Now Active",
    "Contacts with 0 VRS minutes, 0 CfZ minutes, and active Convo Now usage in the selected period",
    section="Analytics"
)

# ── Filters ───────────────────────────────────────────────────────────────────
col_range, col_run = st.columns([3, 1])
with col_range:
    RANGE_OPTIONS = ["This Month", "This Year", "Jun 2026–Present", "Last 3 Months", "Last 6 Months", "Last 12 Months", "All Time"]
    range_label = st.selectbox("Date range (month_date)", RANGE_OPTIONS)
with col_run:
    st.markdown("<div style='margin-top:1.65rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run Report", use_container_width=True)

report_header_close()

# Clear cached results if the date range changed since last run
# (or if the cache is from an older version of this page's pipeline)
_CACHE_VERSION = 7  # bump when columns/fetch logic change
_report_key = f"vrs_zero_v{_CACHE_VERSION}_" + range_label.replace(" ", "_").replace("–", "_")

cached = st.session_state.get("_vrs_zero_cache")
if cached and (cached.get("range_label") != range_label
               or cached.get("version") != _CACHE_VERSION):
    del st.session_state["_vrs_zero_cache"]
    cached = None

# No in-memory cache and not an explicit run → try the saved (on-disk) report,
# so results persist across reloads / sign-outs and don't re-fetch every time.
if cached is None and not run:
    disk = load_report(_report_key)
    if disk and disk.get("version") == _CACHE_VERSION and disk.get("df_full") is not None:
        cached = {
            "version": _CACHE_VERSION,
            "range_label": range_label,
            "df_full": disk["df_full"],
            "email_cn_months": disk.get("email_cn_months", {}),
            "saved_at": disk.get("saved_at"),
        }
        st.session_state["_vrs_zero_cache"] = cached

if not run and not cached:
    st.info("Select a date range and click **Run Report**. "
            "Results are saved and reused automatically next time — no need to re-run.")
    st.stop()

# ── Resolve date floor ────────────────────────────────────────────────────────
today = date.today()
if range_label == "This Month":
    floor = date(today.year, today.month, 1)
elif range_label == "This Year":
    floor = date(today.year, 1, 1)
elif range_label == "Jun 2026–Present":
    floor = date(2026, 6, 1)
elif range_label == "Last 3 Months":
    m, y = today.month - 3, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 6 Months":
    m, y = today.month - 6, today.year
    if m <= 0: m += 12; y -= 1
    floor = date(y, m, 1)
elif range_label == "Last 12 Months":
    floor = date(today.year - 1, today.month, 1)
else:
    floor = date(2000, 1, 1)

floor_ms = str(int(datetime(floor.year, floor.month, 1, tzinfo=timezone.utc).timestamp() * 1000))
DATE_FILTER = {"propertyName": "month_date", "operator": "GTE", "value": floor_ms}

# Skip data fetch if results are already cached for this range
if run or not cached:
    # ── Step 1: Convo Now MV records with usage > 0 ───────────────────────────
    with dash_spinner("Fetching Convo Now monthly values with usage > 0…"):
        cn_mvs = fetch_all(
            "2-46246179",
            ["number", "month_date", "usage_minutes", "service_type"],
            filter_groups=[{"filters": [
                DATE_FILTER,
                {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
            ]}]
        )

    cn_numbers = set()
    cn_num_month_usage: dict = defaultdict(lambda: defaultdict(float))
    for r in cn_mvs:
        p = r.get("properties", {})
        num = str(p.get("number") or "").strip()
        mk  = (p.get("month_date") or "")[:7]
        usage = to_float(p.get("usage_minutes")) or 0.0
        if num and mk:
            cn_numbers.add(num)
            cn_num_month_usage[num][mk] += usage

    if not cn_numbers:
        st.warning("No Convo Now records with usage > 0 found in this date range.")
        st.stop()

    # ── Step 2: Number objects — filter by usage_type & credit_plan_name ──────
    with dash_spinner(f"Looking up {len(cn_numbers):,} Convo Now number objects…"):
        cn_obj_records = []
        for i in range(0, len(cn_numbers), 100):
            chunk = list(cn_numbers)[i:i+100]
            cn_obj_records.extend(fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name", "service_type",
                 "number_status", "usage_type", "credit_plan_name", "age_bucket", "state"],
                filter_groups=[{"filters": [
                    {"propertyName": "number",           "operator": "IN", "values": chunk},
                    {"propertyName": "usage_type",       "operator": "EQ", "value": "Personal"},
                    {"propertyName": "credit_plan_name", "operator": "EQ", "value": "Convo Now: Access Complimentary"},
                ]}]
            ))

    cn_emails: set = set()
    num_to_email: dict = {}
    email_to_name: dict = {}
    email_to_pendo: dict = {}
    email_to_age: dict = {}
    email_to_state: dict = {}
    email_to_jobtitle: dict = {}
    email_to_companyid: dict = {}
    _statuses_seen: dict = defaultdict(int)
    for r in cn_obj_records:
        p = r.get("properties", {})
        _status = (p.get("number_status") or "").strip()
        _statuses_seen[_status or "(blank)"] += 1
        if norm(_status) != "live":
            continue
        num   = str(p.get("number") or "").strip()
        email = str(p.get("email")  or "").strip().lower()
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name")  or "").strip()
        if email:
            cn_emails.add(email)
            num_to_email[num] = email
            if fn or ln:
                email_to_name[email] = f"{fn} {ln}".strip()
            age = (p.get("age_bucket") or "").strip()
            if age:
                email_to_age.setdefault(email, age)
            state = (p.get("state") or "").strip()  # State from the Number object
            if state:
                email_to_state.setdefault(email, state)

    if not cn_emails:
        st.warning("No qualifying Convo Now numbers found (Personal + Convo Now: Access Complimentary + Live). "
                   f"Statuses on matching numbers: {dict(_statuses_seen) or '—'}")
        st.stop()

    # ── Step 2b: Pendo ID (convo_now_account_id) from the Contact records ────
    # Map the Pendo ID under EVERY email a contact has (primary + additional),
    # because the report keys rows by the number's email, which may be the
    # contact's secondary email rather than its primary.
    with dash_spinner(f"Fetching contact details for {len(cn_emails):,} contacts…"):
        _email_list = sorted(cn_emails)
        for i in range(0, len(_email_list), 100):
            chunk = _email_list[i:i+100]
            c_recs = fetch_all(
                "contacts",
                ["email", "hs_additional_emails", "convo_now_account_id",
                 "jobtitle", "associatedcompanyid"],
                filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk},
                ]}]
            )
            for c in c_recs:
                cp = c.get("properties", {})
                pendo    = (cp.get("convo_now_account_id") or "").strip()
                jobtitle = (cp.get("jobtitle") or "").strip()
                companyid = (cp.get("associatedcompanyid") or "").strip()
                # NOTE: State comes from the Number object (contact-level state is
                # unreliable), captured in Step 2 / Step 3 — not from contacts here.
                all_emails = [(cp.get("email") or "").strip().lower()]
                all_emails += [x.strip().lower() for x in
                               str(cp.get("hs_additional_emails") or "").replace(",", ";").split(";")
                               if x.strip()]
                for _e in all_emails:
                    if not _e:
                        continue
                    if pendo:     email_to_pendo.setdefault(_e, pendo)
                    if jobtitle:  email_to_jobtitle.setdefault(_e, jobtitle)
                    if companyid: email_to_companyid.setdefault(_e, companyid)

    # ── Step 2c: associated Company (0-2) info ───────────────────────────────
    company_info: dict = {}
    _comp_ids = sorted({c for c in email_to_companyid.values() if c})
    if _comp_ids:
        with dash_spinner(f"Fetching {len(_comp_ids):,} associated companies…"):
            for i in range(0, len(_comp_ids), 100):
                chunk = _comp_ids[i:i+100]
                comp_recs = fetch_all(
                    "companies",
                    ["name", "description", "industry_type",
                     "deaf_owned_business_", "non_profit_organization_"],
                    filter_groups=[{"filters": [
                        {"propertyName": "hs_object_id", "operator": "IN", "values": chunk},
                    ]}]
                )
                for cr in comp_recs:
                    company_info[str(cr.get("id"))] = cr.get("properties", {})

    # ── Step 3: All numbers for those contacts (VRS + Convo Now) ─────────────
    with dash_spinner(f"Fetching all numbers for {len(cn_emails):,} contacts…"):
        all_num_objs = []
        for i in range(0, len(cn_emails), 100):
            chunk = list(cn_emails)[i:i+100]
            all_num_objs.extend(fetch_all(
                "2-40974683",
                ["number", "email", "first_name", "last_name", "service_type", "number_status", "age_bucket", "state"],
                filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk},
                ]}]
            ))

    email_vrs_nums: dict = defaultdict(set)
    email_cn_nums:  dict = defaultdict(set)
    all_numbers_by_email: dict = defaultdict(set)

    for r in all_num_objs:
        p     = r.get("properties", {})
        num   = str(p.get("number") or "").strip()
        email = str(p.get("email")  or "").strip().lower()
        svc   = norm(p.get("service_type") or "")
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name")  or "").strip()
        if not email or not num:
            continue
        num_to_email[num] = email
        all_numbers_by_email[email].add(num)
        if fn or ln:
            email_to_name.setdefault(email, f"{fn} {ln}".strip())
        age = (p.get("age_bucket") or "").strip()
        if age:
            email_to_age.setdefault(email, age)
        state = (p.get("state") or "").strip()  # State from the Number object
        if state:
            email_to_state.setdefault(email, state)
        if svc == "vrs":
            email_vrs_nums[email].add(num)
        elif svc == "convo now":
            email_cn_nums[email].add(num)

    all_nums_flat = list({n for nums in all_numbers_by_email.values() for n in nums})

    # ── Step 4: MV records for all numbers in the period ─────────────────────
    with dash_spinner(f"Fetching monthly values for {len(all_nums_flat):,} numbers…"):
        all_mvs = []
        for i in range(0, len(all_nums_flat), 100):
            chunk = all_nums_flat[i:i+100]
            all_mvs.extend(fetch_all(
                "2-46246179",
                ["number", "month_date", "usage_minutes", "ursa_minutes", "cfz_minutes", "service_type"],
                filter_groups=[{"filters": [
                    DATE_FILTER,
                    {"propertyName": "number",       "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                ]}]
            ))

    # ── Step 5: Aggregate per contact ────────────────────────────────────────
    email_vrs_total:  dict = defaultdict(float)
    email_cfz_total:  dict = defaultdict(float)
    email_ursa_total: dict = defaultdict(float)
    email_cn_total:   dict = defaultdict(float)
    email_vrs_months: dict = defaultdict(lambda: defaultdict(float))
    email_cn_months:  dict = defaultdict(lambda: defaultdict(float))

    for r in all_mvs:
        p    = r.get("properties", {})
        num  = str(p.get("number") or "").strip()
        mk   = (p.get("month_date") or "")[:7]
        svc  = norm(p.get("service_type") or "")
        email = num_to_email.get(num)
        if not email or not mk:
            continue
        usage = to_float(p.get("usage_minutes")) or 0.0
        if svc == "vrs":
            cfz_min  = to_float(p.get("cfz_minutes"))  or 0.0
            ursa_min = to_float(p.get("ursa_minutes")) or 0.0
            email_vrs_total[email]       += usage
            email_cfz_total[email]       += cfz_min
            email_ursa_total[email]      += ursa_min
            email_vrs_months[email][mk]  += usage
        elif svc == "convo now":
            email_cn_total[email]        += usage
            email_cn_months[email][mk]   += usage

    # ── Step 6: Filter — VRS = 0 AND CfZ = 0 AND Convo Now > 0 ──────────────
    rows = []
    for email in sorted(cn_emails):
        vrs_total  = email_vrs_total.get(email,  0.0)
        cfz_total  = email_cfz_total.get(email,  0.0)
        ursa_total = email_ursa_total.get(email, 0.0)
        cn_total   = email_cn_total.get(email,   0.0)
        if vrs_total > 0 or cfz_total > 0 or cn_total == 0:
            continue
        cn_months     = email_cn_months.get(email, {})
        active_months = len(cn_months)
        latest_month  = max(cn_months.keys()) if cn_months else ""
        latest_cn_min = cn_months.get(latest_month, 0.0) if latest_month else 0.0
        cn_cost  = cn_total * CONVO_RATE
        vrs_nums = sorted(email_vrs_nums.get(email, set()))
        cn_nums  = sorted(email_cn_nums.get(email,  set()))
        _cid = email_to_companyid.get(email, "")
        _cinfo = company_info.get(_cid, {})
        rows.append({
            "Name":              email_to_name.get(email, "—"),
            "Email":             email,
            "Pendo ID":          email_to_pendo.get(email, "—"),
            "Age Bucket":        email_to_age.get(email, "—"),
            "State":             email_to_state.get(email, "—"),
            "Job Title":         email_to_jobtitle.get(email, "—"),
            "Company Name":        (_cinfo.get("name") or "—"),
            "Company Description": (_cinfo.get("description") or "—"),
            "Industry Type":       (_cinfo.get("industry_type") or "—"),
            "Deaf-Owned Business": (_cinfo.get("deaf_owned_business_") or "—"),
            "Nonprofit Org":       (_cinfo.get("non_profit_organization_") or "—"),
            "VRS Numbers":       ", ".join(vrs_nums) if vrs_nums else "—",
            "Convo Now Numbers": ", ".join(cn_nums)  if cn_nums  else "—",
            "VRS Minutes":       round(vrs_total,  1),
            "URSA Minutes":      round(ursa_total, 1),
            "CfZ Minutes":       round(cfz_total,  1),
            "Convo Now Min":     round(cn_total,   1),
            "Convo Now Cost":    round(cn_cost,    2),
            "Active Months":     active_months,
            "Latest Month":      latest_month,
            "Latest Month Min":  round(latest_cn_min, 1),
        })

    # ── Step 6b: recover Pendo ID for rows still missing it ──────────────────
    # These are contacts whose number-email is their SECONDARY email; a batch
    # `email IN` search returns the contact but only its primary email, so the
    # row keyed by the secondary email missed. Search each missing email
    # individually (HubSpot's email search matches secondary emails) and tie
    # the Pendo ID directly to that email.
    _missing_pendo = [r["Email"] for r in rows if r["Pendo ID"] in ("", "—", None)]
    if _missing_pendo:
        with dash_spinner(f"Recovering Pendo IDs for {len(_missing_pendo):,} contacts…"):
            for em in _missing_pendo:
                recs = fetch_all(
                    "contacts",
                    ["email", "convo_now_account_id"],
                    filter_groups=[{"filters": [
                        {"propertyName": "email", "operator": "EQ", "value": em},
                    ]}]
                )
                for c in recs:
                    pid = (c.get("properties", {}).get("convo_now_account_id") or "").strip()
                    if pid:
                        email_to_pendo[em] = pid
                        break
        # re-apply to rows
        for r in rows:
            if r["Pendo ID"] in ("", "—", None):
                r["Pendo ID"] = email_to_pendo.get(r["Email"], "—")

    if not rows:
        st.success("No contacts found matching VRS = 0, CfZ = 0, Convo Now > 0 in this period.")
        st.stop()

    df_full = pd.DataFrame(rows).sort_values("Convo Now Min", ascending=False).reset_index(drop=True)

    # Persist to session state (fast reruns) AND to disk (survives reloads/sign-outs)
    _payload = {
        "version":       _CACHE_VERSION,
        "range_label":   range_label,
        "df_full":       df_full,
        "email_cn_months": {k: dict(v) for k, v in email_cn_months.items()},
    }
    save_report(_report_key, _payload)              # adds saved_at timestamp on disk
    _saved_at = time.time()
    st.session_state["_vrs_zero_cache"] = {**_payload, "saved_at": _saved_at}
else:
    df_full          = cached["df_full"]
    email_cn_months  = cached["email_cn_months"]
    _saved_at        = cached.get("saved_at")

# ── Cached-result banner ──────────────────────────────────────────────────────
if _saved_at:
    _age_s = max(0, int(time.time() - _saved_at))
    if _age_s < 90:
        _ago = "just now"
    elif _age_s < 3600:
        _ago = f"{_age_s // 60} min ago"
    elif _age_s < 86400:
        _ago = f"{_age_s // 3600} h ago"
    else:
        _ago = f"{_age_s // 86400} d ago"
    st.caption(f"📌 Saved report · last refreshed **{_ago}**. "
               f"It's reused automatically — click **Run Report** above only when you want fresh data.")

# ── Summary tiles ─────────────────────────────────────────────────────────────
total_contacts = len(df_full)
total_cn_min   = df_full["Convo Now Min"].sum()
total_cn_cost  = df_full["Convo Now Cost"].sum()
avg_months     = df_full["Active Months"].mean()

def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.4rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;line-height:1.15;">{value}</div>
  {f'<div style="font-size:0.72rem;color:#9CA3AF;margin-top:0.2rem;">{sub}</div>' if sub else ''}
</div>"""

st.markdown(f"""
<div style="font-size:0.8rem;color:#6B7280;margin-bottom:1rem;">
  Period: <strong>{range_label}</strong>
  &nbsp;·&nbsp; VRS = 0 min &nbsp;·&nbsp; CfZ = 0 min &nbsp;·&nbsp; Convo Now &gt; 0 min
</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin-bottom:1.5rem;">
  {tile("Contacts", f"{total_contacts:,}", "VRS=0, CfZ=0, CN active")}
  {tile("VRS Minutes", "0", "confirmed zero", "#6B7280")}
  {tile("CfZ Minutes", "0", "confirmed zero", "#6B7280")}
  {tile("Convo Now Min", f"{total_cn_min:,.1f}", "total across period", "#3B82F6")}
  {tile("Convo Now Cost", f"${total_cn_cost:,.2f}", f"@ ${CONVO_RATE}/min", "#3B82F6")}
</div>""", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────────
filtered_emails = set(df_full["Email"])

# Monthly Convo Now usage
month_totals: dict = defaultdict(float)
for email in filtered_emails:
    for mk, usage in email_cn_months.get(email, {}).items():
        month_totals[mk] += usage

ch_left, ch_right = st.columns(2)

with ch_left:
    if month_totals:
        chart_df = pd.DataFrame([
            {"Month": mk, "Convo Now Minutes": round(v, 1)}
            for mk, v in sorted(month_totals.items())
        ])
        bar = (
            alt.Chart(chart_df)
            .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Month:N", sort=list(chart_df["Month"]),
                        axis=alt.Axis(title=None, labelAngle=-20)),
                y=alt.Y("Convo Now Minutes:Q", title="Minutes"),
                tooltip=["Month", alt.Tooltip("Convo Now Minutes:Q", format=",.1f")],
            )
            .properties(height=220, title="Convo Now Usage by Month")
        )
        st.altair_chart(bar, use_container_width=True)

with ch_right:
    # Top 15 contacts by Convo Now minutes
    top_df = df_full.head(15)[["Name", "Email", "Convo Now Min"]].copy()
    top_df["Label"] = top_df.apply(
        lambda r: r["Name"] if r["Name"] != "—" else r["Email"], axis=1
    )
    top_chart = (
        alt.Chart(top_df)
        .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Convo Now Min:Q", title="Minutes"),
            y=alt.Y("Label:N", sort="-x", title=None,
                    axis=alt.Axis(labelLimit=200)),
            tooltip=["Label", alt.Tooltip("Convo Now Min:Q", format=",.1f")],
        )
        .properties(height=max(220, min(15, len(top_df)) * 26),
                    title="Top 15 Contacts by Convo Now Usage")
    )
    st.altair_chart(top_chart, use_container_width=True)

ch2_left, ch2_right = st.columns(2)

with ch2_left:
    # Active months distribution
    month_dist_df = df_full["Active Months"].value_counts().reset_index()
    month_dist_df.columns = ["Active Months", "Count"]
    month_dist_df = month_dist_df.sort_values("Active Months")
    dist_chart = (
        alt.Chart(month_dist_df)
        .mark_bar(color="#8B5CF6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("Active Months:O", title="Active Months"),
            y=alt.Y("Count:Q", title="# Contacts"),
            tooltip=["Active Months", "Count"],
        )
        .properties(height=220, title="Distribution: Active Months per Contact")
    )
    st.altair_chart(dist_chart, use_container_width=True)

with ch2_right:
    # Age bucket breakdown — contacts and CN minutes per age group
    if "Age Bucket" in df_full.columns:
        AGE_ORDER = ["Under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+",
                     "18 - 35", "36 - 50", "51 - 64", "65 and Over", "—"]
        age_df = (
            df_full.groupby("Age Bucket", as_index=False)
            .agg(Contacts=("Email", "count"), CN_Min=("Convo Now Min", "sum"))
        )
        age_df["CN_Min"] = age_df["CN_Min"].round(1)
        order_map = {b: i for i, b in enumerate(AGE_ORDER)}
        age_df = age_df.sort_values("Age Bucket", key=lambda s: s.map(lambda x: order_map.get(x, 998)))
        age_chart = (
            alt.Chart(age_df)
            .mark_bar(color="#3B82F6", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Age Bucket:N", sort=list(age_df["Age Bucket"]),
                        axis=alt.Axis(title=None, labelAngle=-20)),
                y=alt.Y("CN_Min:Q", title="Convo Now Minutes"),
                tooltip=[alt.Tooltip("Age Bucket:N"),
                         alt.Tooltip("Contacts:Q", format=","),
                         alt.Tooltip("CN_Min:Q", title="CN Minutes", format=",.1f")],
            )
            .properties(height=220, title="Convo Now Usage by Age Bucket")
        )
        st.altair_chart(age_chart, use_container_width=True)

# ── Search + table ────────────────────────────────────────────────────────────
st.markdown("<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#6B7280;margin:1.25rem 0 0.5rem;'>Contact Detail</div>", unsafe_allow_html=True)

_age_set   = int((df_full["Age Bucket"] != "—").sum()) if "Age Bucket" in df_full.columns else 0
_pendo_set = int((df_full["Pendo ID"] != "—").sum()) if "Pendo ID" in df_full.columns else 0
st.caption(f"Age Bucket set on {_age_set:,} of {len(df_full):,} contacts · "
           f"Pendo ID set on {_pendo_set:,} of {len(df_full):,}")

search = st.text_input(
    "Search contacts",
    placeholder="Filter by name, email, or phone number…",
    label_visibility="collapsed",
)

df_view = df_full.copy()
if search.strip():
    q = search.strip().lower()
    mask = (
        df_view["Name"].str.lower().str.contains(q, na=False) |
        df_view["Email"].str.lower().str.contains(q, na=False) |
        df_view["VRS Numbers"].str.lower().str.contains(q, na=False) |
        df_view["Convo Now Numbers"].str.lower().str.contains(q, na=False) |
        df_view["Pendo ID"].str.lower().str.contains(q, na=False)
    )
    df_view = df_view[mask]
    st.caption(f'{len(df_view):,} of {total_contacts:,} contacts match "{search}"')

display_df = df_view[[
    "Name", "Email", "Pendo ID", "Age Bucket", "State", "Job Title",
    "Company Name", "Company Description", "Industry Type",
    "Deaf-Owned Business", "Nonprofit Org",
    "Convo Now Numbers", "VRS Numbers",
    "VRS Minutes", "URSA Minutes", "CfZ Minutes",
    "Convo Now Min", "Convo Now Cost", "Active Months", "Latest Month", "Latest Month Min"
]]

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "VRS Minutes":      st.column_config.NumberColumn(format="%.1f"),
        "URSA Minutes":     st.column_config.NumberColumn(format="%.1f"),
        "CfZ Minutes":      st.column_config.NumberColumn(format="%.1f"),
        "Convo Now Min":    st.column_config.NumberColumn("CN Min (Total)", format="%.1f"),
        "Convo Now Cost":   st.column_config.NumberColumn("CN Cost ($)",    format="$%.2f"),
        "Latest Month Min": st.column_config.NumberColumn(format="%.1f"),
    }
)

# ── CSV download ──────────────────────────────────────────────────────────────
csv = df_view.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV",
    csv,
    file_name=f"vrs_zero_convo_active_{range_label.replace(' ', '_').replace('–','_')}.csv",
    mime="text/csv",
)
from utils import pdf_download_button
_pdf_metrics = [
    ("Contacts", f"{total_contacts:,}"),
    ("Convo Now Min", f"{total_cn_min:,.0f}"),
    ("Convo Now Cost", f"${total_cn_cost:,.0f}"),
    ("Avg Active Months", f"{avg_months:.1f}"),
]
_pdf_charts = []
if "State" in df_view.columns:
    _st = (df_view.assign(State=df_view["State"].replace("", "—"))
           .groupby("State", as_index=False)["Convo Now Min"].sum()
           .sort_values("Convo Now Min", ascending=False))
    _pdf_charts.append({"data": _st, "kind": "bar", "x": "State", "y": "Convo Now Min",
                        "title": "Convo Now Minutes by State"})
pdf_download_button(df_view, "vrs_zero.pdf", "VRS Zero / Convo Now Active",
                    subtitle="VRS = 0 · CfZ = 0 · Convo Now active",
                    metrics=_pdf_metrics, charts=_pdf_charts, key="vrszero")
