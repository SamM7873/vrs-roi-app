import streamlit as st
import pandas as pd
import time
from calendar import monthrange
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="CN20 Active Breakdown", layout="wide", page_icon="📊")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("CN20 Active Breakdown")

report_header("CN20 Active Users — Breakdown",
              "Monthly-active Convo Now users: how many are CN20, and exclusively CN20 vs CN20 + another account",
              section="Customers")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
_key = "cn20_active_breakdown_v2_tree"

US_STATES = {"al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia","ks",
             "ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj","nm","ny",
             "nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt","va","wa","wv",
             "wi","wy","dc","district of columbia"}


def _norm(v):
    return " ".join(str(v or "").strip().lower().split())


def _ms(d):
    return str(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000))


def _seek(obj, props, filters):
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


st.markdown("Groups **Convo Now** numbers by **user (email)**. **CN20** = credit plan matching the "
            "20-minute complimentary plan. **Active** = the user had **Convo Now minutes used > 0** in "
            "the selected month (from Monthly Values). Each CN20 user is classed as **exclusively CN20** "
            "or **CN20 + another account** (another Convo Now plan, or a VRS number on the same email).")

c1, c2, c3 = st.columns([1.4, 1.4, 1.2])
_today = date.today()
_mon = c1.selectbox("Active month", [(_today.year, _today.month), (_today.year, _today.month - 1 if _today.month > 1 else 12)],
                    format_func=lambda ym: date(ym[0], ym[1] if ym[1] >= 1 else 12, 1).strftime("%B %Y"))
cn20_match = c2.text_input("CN20 plan contains", value="complimentary").strip().lower()
us_only = c3.checkbox("US only (by state)", value=False)
run = st.button("▶ Run report", type="primary")

if run:
    y, m = _mon
    mstart = date(y, m, 1)
    mend = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)

    nprops = ["number", "email", "account_status", "number_status", "credit_plan_name",
              "service_type", "state"]
    with dash_spinner("Reading Convo Now numbers…"):
        cn = _seek(NUM_OBJECT, nprops,
                   [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}])
    with dash_spinner("Reading VRS numbers (for overlap)…"):
        vrs = _seek(NUM_OBJECT, ["email", "number_status", "account_status", "service_type"],
                    [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"}])

    def _is_live(p):
        return _norm(p.get("account_status") or p.get("number_status")) == "live"

    vrs_emails = {_norm(o.get("properties", {}).get("email")) for o in vrs if _is_live(o.get("properties", {}))}
    vrs_emails.discard("")

    with dash_spinner("Reading Convo Now usage for the month…"):
        mv = _seek(MV_OBJECT, ["number", "email", "convo_now_minutes_used", "service_type", "month_date"],
                   [{"propertyName": "month_date", "operator": "GTE", "value": _ms(mstart)},
                    {"propertyName": "month_date", "operator": "LT", "value": _ms(mend)},
                    {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                    {"propertyName": "convo_now_minutes_used", "operator": "GT", "value": "0"}])
    active_numbers = {_norm(o.get("properties", {}).get("number")) for o in mv}
    active_emails_mv = {_norm(o.get("properties", {}).get("email")) for o in mv}
    active_numbers.discard(""); active_emails_mv.discard("")

    # per Convo Now number → plan / active / email / state (live only)
    def _is_cn20(plan):
        return bool(cn20_match) and cn20_match in _norm(plan)

    users = defaultdict(lambda: {"cn20": False, "other_cn": False, "active": False,
                                 "state": "", "plans": set(), "numbers": 0})
    for o in cn:
        p = o.get("properties", {})
        if not _is_live(p):
            continue
        em = _norm(p.get("email"))
        if not em:
            continue
        if us_only and _norm(p.get("state")) not in US_STATES:
            continue
        plan = (p.get("credit_plan_name") or "").strip() or "—"
        num = _norm(p.get("number"))
        u = users[em]
        u["numbers"] += 1
        u["plans"].add(plan)
        if not u["state"]:
            u["state"] = p.get("state") or ""
        if _is_cn20(plan):
            u["cn20"] = True
        else:
            u["other_cn"] = True
        if num in active_numbers or em in active_emails_mv:
            u["active"] = True

    rows = []
    for em, u in users.items():
        has_vrs = em in vrs_emails
        if u["cn20"]:
            if not u["other_cn"] and not has_vrs:
                cls = "Exclusively CN20"
            else:
                extras = []
                if u["other_cn"]:
                    extras.append("other Convo Now plan")
                if has_vrs:
                    extras.append("VRS")
                cls = "CN20 + " + " + ".join(extras)
        else:
            cls = "Convo Now (non-CN20)"
        rows.append({"Email": em, "Active": "Yes" if u["active"] else "No",
                     "CN20": "Yes" if u["cn20"] else "No",
                     "Has VRS": "Yes" if has_vrs else "No",
                     "Other Convo Now plan": "Yes" if u["other_cn"] else "No",
                     "Classification": cls, "Convo Now plans": ", ".join(sorted(u["plans"])),
                     "Convo Now numbers": u["numbers"], "State": u["state"] or "—"})
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "month": date(y, m, 1).strftime("%B %Y"),
                       "cn20_match": cn20_match, "us_only": us_only,
                       "n_cn_numbers": len(cn), "n_active_numbers": len(active_numbers)})

saved = load_report(_key)
if saved is None:
    st.info("Pick a month and click **▶ Run report**."); report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · active month {saved.get('month','')} · "
               f"CN20 = plan contains “{saved.get('cn20_match','')}”"
               + ("  ·  US only" if saved.get("us_only") else ""))
if df.empty:
    st.warning("No Convo Now users found."); report_header_close(); st.stop()

active = df[df["Active"] == "Yes"]
cn20_active = active[active["CN20"] == "Yes"]
excl = cn20_active[cn20_active["Classification"] == "Exclusively CN20"]
plus = cn20_active[cn20_active["Classification"].str.startswith("CN20 +")]
plus_vrs = cn20_active[cn20_active["Has VRS"] == "Yes"]
plus_other = cn20_active[cn20_active["Other Convo Now plan"] == "Yes"]


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(active)
NC = len(cn20_active)
k = st.columns(4)
_card(k[0], "👥 Active Convo Now users", f"{N:,}", f"used Convo Now in {saved.get('month','')}", "#4C8DFF")
_card(k[1], "🎯 CN20 active users", f"{NC:,}", f"{NC/N*100:.0f}% of active" if N else "—", "#7A5CFF")
_card(k[2], "🟣 Exclusively CN20", f"{len(excl):,}", f"{len(excl)/NC*100:.0f}% of CN20" if NC else "—", "#2DB84B")
_card(k[3], "🔗 CN20 + another account", f"{len(plus):,}", f"{len(plus)/NC*100:.0f}% of CN20" if NC else "—", "#E8952A")
st.markdown("")

other_active = active[active["CN20"] == "No"]
k2 = st.columns(3)
_card(k2[0], "📵 Other Convo Now (non-CN20)", f"{len(other_active):,}",
      f"{len(other_active)/N*100:.0f}% of active · no CN20 plan" if N else "—", "#8792A2")
_card(k2[1], "CN20 + VRS", f"{len(plus_vrs):,}", "also have a live VRS number", "#4C9AE0")
_card(k2[2], "CN20 + another Convo Now plan", f"{len(plus_other):,}", "also have a non-CN20 CN plan", "#3FB07A")
st.markdown("")

st.markdown(
    f"""<div style="border:1px solid #E6E9F0;border-radius:12px;padding:14px 18px;background:rgba(127,127,127,0.03);">
    <div style="font-size:.8rem;font-weight:700;color:#1A2234;margin-bottom:6px;">
    Breakdown — monthly-active Convo Now users{' (US)' if saved.get('us_only') else ''}, {saved.get('month','')}</div>
    <div style="font-size:.86rem;color:#344054;line-height:1.7;">
    <b>{N:,}</b> total monthly-active Convo Now users<br>
    &nbsp;&nbsp;├─ <b>{NC:,}</b> have CN20
    ({NC/N*100:.0f}%)<br>
    &nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;├─ <b>{len(excl):,}</b> exclusively CN20<br>
    &nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;└─ <b>{len(plus):,}</b> CN20 + another account
    (VRS: {len(plus_vrs):,} · other CN plan: {len(plus_other):,})<br>
    &nbsp;&nbsp;└─ <b>{len(other_active):,}</b> use other Convo Now account types (non-CN20)
    </div></div>""", unsafe_allow_html=True)
st.markdown("")

tab1, tab2, tab3 = st.tabs(["CN20 active users", "Classification summary", "All users"])

with tab1:
    q = st.text_input("Search email", key="cn20_q").strip().lower()
    v = cn20_active if not q else cn20_active[cn20_active["Email"].str.contains(q, na=False)]
    st.caption(f"{len(v):,} CN20 active users")
    st.dataframe(v.sort_values("Classification"), use_container_width=True, hide_index=True, height=480)
    st.download_button("📥 Export CN20 active CSV", cn20_active.to_csv(index=False),
                       "cn20_active_users.csv", "text/csv")

with tab2:
    summ = (cn20_active["Classification"].value_counts().rename_axis("Classification")
            .reset_index(name="Users"))
    st.dataframe(summ, use_container_width=True, hide_index=True)
    st.markdown("**Convo Now plans among active users**")
    pc = (active["Convo Now plans"].value_counts().rename_axis("Plan(s) on account")
          .reset_index(name="Active users").head(25))
    st.dataframe(pc, use_container_width=True, hide_index=True, height=360)

with tab3:
    st.dataframe(df.sort_values(["Active", "CN20"], ascending=False),
                 use_container_width=True, hide_index=True, height=480)
    st.download_button("📥 Export all users CSV", df.to_csv(index=False),
                       "cn20_all_users.csv", "text/csv", key="all_dl")

report_header_close()
