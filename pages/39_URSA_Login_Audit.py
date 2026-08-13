import streamlit as st
import pandas as pd
from datetime import datetime, timezone
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, dash_spinner, save_report, load_report,
                   saved_at_label, log_report_view)

st.set_page_config(page_title="URSA Login Audit", layout="wide", page_icon="🔍")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("URSA Login Audit")

report_header("URSA Login Audit",
              "Data quality on ursa_first_login vs iOS / Android / Web logins & call activity",
              section="Data Quality")

NUM_OBJECT = "2-40974683"
CACHE_VERSION = 3
_key = f"ursa_login_audit_v{CACHE_VERSION}"

# activity signals that prove the number WAS used (so a blank first login = missing/error)
ACTIVITY = {
    "ursa_first_outbound_call": "First Outbound",
    "ursa_second_outbound_call": "Second Outbound",
    "ursa_last_outbound_call": "Last Outbound",
    "ursa_last_inbound_call": "Last Inbound",
    "last_login_ursa_convo_ios_date": "iOS login",
    "last_login_ursa_convo_android_date": "Android login",
    "last_login_ursa_convo_web_date": "Web login",
}


def _dt(v):
    """Parse a HubSpot date/datetime to a date (or None)."""
    if not v or str(v).strip() == "":
        return None
    try:
        s = str(v).strip()
        return (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc).date() if s.isdigit()
                else datetime.fromisoformat(s.replace("Z", "+00:00")).date())
    except Exception:
        return None


def _fmt(v):
    d = _dt(v)
    return d.strftime("%b %d, %Y") if d else ""


st.markdown("Audits the **Number custom object** for URSA login integrity. We flag a number when "
            "**`ursa_first_login` is empty but there IS activity** — a first/last outbound or "
            "inbound call, or an iOS / Android / Web login. That means first login is **missing or "
            "erroneous** (a first login should always exist before any activity).")

# ── direct single-number lookup (any service type / status) ─────────────────────
with st.expander("🔍 Look up one number directly (ignores all filters)", expanded=False):
    _q = st.text_input("Phone number", placeholder="7326389021", key="ursa_lookup").strip()
    if _q:
        _digits = "".join(ch for ch in _q if ch.isdigit())
        _last10 = _digits[-10:] if len(_digits) >= 10 else _digits
        _props = ["number", "email", "first_name", "last_name", "service_type", "account_status",
                  "number_status", "number_created_at", "ursa_first_login"] + list(ACTIVITY.keys())
        # try several stored formats: as typed, digits only, +1 prefix, then a contains-search
        _variants = list(dict.fromkeys([_q, _digits, _last10, f"+1{_last10}", f"1{_last10}"]))
        recs1 = []
        for v in _variants:
            recs1 = fetch_all(NUM_OBJECT, _props, filter_groups=[{"filters": [
                {"propertyName": "number", "operator": "EQ", "value": v}]}])
            if recs1:
                break
        if not recs1 and _last10:
            # last resort: token/contains search on the 10-digit core
            recs1 = fetch_all(NUM_OBJECT, _props, filter_groups=[{"filters": [
                {"propertyName": "number", "operator": "CONTAINS_TOKEN", "value": f"*{_last10}*"}]}])
        if not recs1:
            st.error(f"No Number record found for {_q} (tried {', '.join(_variants)} and a contains "
                     "search). The number may not exist in HubSpot, or is stored in a format none "
                     "of these matched.")
        for r in recs1:
            p = r.get("properties", {})
            st.markdown(f"**{p.get('number')}** · service_type: **{p.get('service_type') or '(blank)'}** "
                        f"· status: **{p.get('account_status') or p.get('number_status') or '(blank)'}**")
            det = {"Field": [], "Value": []}
            det["Field"].append("ursa_first_login"); det["Value"].append(_fmt(p.get("ursa_first_login")) or "(empty)")
            for k, lbl in ACTIVITY.items():
                det["Field"].append(f"{k}  ({lbl})"); det["Value"].append(_fmt(p.get(k)) or "(empty)")
            st.dataframe(pd.DataFrame(det), use_container_width=True, hide_index=True)
            if (p.get("account_status") or p.get("number_status") or "").strip().lower() != "live":
                st.caption("Note: this number isn't **Live** — keep 'Live only' unchecked to include it.")

c1, c2, c3 = st.columns([1.3, 1.3, 2])
with c1:
    scope = st.selectbox("Service type", ["VRS only", "All service types", "Convo Now only"])
with c2:
    live_only = st.checkbox("Live only", value=True)
run = st.button("Run URSA login audit", type="primary")

if run:
    props = ["number", "email", "first_name", "last_name", "service_type", "account_status",
             "number_status", "number_created_at", "ursa_first_login"] + list(ACTIVITY.keys())
    # pull ALL numbers by default; optionally restrict to a service type
    fg = None
    if scope == "VRS only":
        fg = [{"filters": [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}]
    elif scope == "Convo Now only":
        fg = [{"filters": [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}]}]
    with dash_spinner("Reading number records…"):
        recs = fetch_all(NUM_OBJECT, props, filter_groups=fg)

    rows = []
    for r in recs:
        p = r.get("properties", {})
        status = (p.get("account_status") or p.get("number_status") or "").strip()
        if live_only and status.lower() != "live":
            continue
        n = str(p.get("number") or "").strip()
        if not n:
            continue
        first_login = p.get("ursa_first_login") or ""
        has_first = _dt(first_login) is not None
        # collect activity evidence
        evidence = [lbl for k, lbl in ACTIVITY.items() if _dt(p.get(k)) is not None]
        # earliest activity date (to detect first-login-after-activity)
        act_dates = [_dt(p.get(k)) for k in ACTIVITY if _dt(p.get(k)) is not None]
        earliest_act = min(act_dates) if act_dates else None
        fl_date = _dt(first_login)
        # platform last-login presence (iOS / Android / Web)
        _plat_keys = ["last_login_ursa_convo_ios_date", "last_login_ursa_convo_android_date",
                      "last_login_ursa_convo_web_date"]
        has_plat = any(_dt(p.get(k)) is not None for k in _plat_keys)
        if not has_first and evidence:
            flag = "🔴 Missing first login (but active)"
        elif has_first and earliest_act and fl_date > earliest_act:
            flag = "🟠 First login after activity"
        elif has_first and not has_plat:
            flag = "🟡 First login, no platform last login"
        elif not has_first and not evidence:
            flag = "⚪ No login, no activity"
        else:
            flag = "✅ OK"
        rows.append({
            "Number": n,
            "Email": (p.get("email") or "").strip(),
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Service": (p.get("service_type") or "").strip() or "—",
            "Status": status or "—",
            "First Login": _fmt(first_login),
            "iOS": _fmt(p.get("last_login_ursa_convo_ios_date")),
            "Android": _fmt(p.get("last_login_ursa_convo_android_date")),
            "Web": _fmt(p.get("last_login_ursa_convo_web_date")),
            "First Outbound": _fmt(p.get("ursa_first_outbound_call")),
            "Last Outbound": _fmt(p.get("ursa_last_outbound_call")),
            "Last Inbound": _fmt(p.get("ursa_last_inbound_call")),
            "Evidence": ", ".join(evidence) if evidence else "—",
            "Flag": flag,
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run URSA login audit** to scan the Number object.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh")
if df.empty:
    st.warning("No VRS numbers matched.")
    report_header_close(); st.stop()

# ── KPIs ────────────────────────────────────────────────────────────────────────
n_total = len(df)
n_missing = int((df["Flag"] == "🔴 Missing first login (but active)").sum())
n_after = int((df["Flag"] == "🟠 First login after activity").sum())
n_noplat = int((df["Flag"] == "🟡 First login, no platform last login").sum())
n_ok = int((df["Flag"] == "✅ OK").sum())
n_noact = int((df["Flag"] == "⚪ No login, no activity").sum())


def _card(col, title, val, sub, color):
    col.markdown(
        f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {color};border-radius:12px;
             padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{title}</div>
        <div style="font-size:2rem;font-weight:800;color:{color};line-height:1.1;margin:4px 0 2px;">{val:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{sub}</div></div>""", unsafe_allow_html=True)


k = st.columns(6)
_card(k[0], "🔢 Numbers audited", n_total, "VRS records", "#4C8DFF")
_card(k[1], "🔴 Missing first login", n_missing,
      f"{(n_missing/n_total*100):.1f}% — active but blank", "#E5484D")
_card(k[2], "🟡 No platform last login", n_noplat, "has first login, no iOS/Android/Web", "#D9A400")
_card(k[3], "🟠 Login after activity", n_after, "first login too late", "#E8952A")
_card(k[4], "⚪ No login / no activity", n_noact, "never used", "#8792A2")
_card(k[5], "✅ OK", n_ok, "first login + platform login", "#2DB84B")
st.markdown("")

if n_noplat:
    st.warning(f"🟡 **{n_noplat:,} numbers have a first login but NO iOS/Android/Web last-login date** "
               "— the per-platform last-login fields may be missing or not syncing.")
if n_missing:
    st.error(f"🔴 **{n_missing:,} numbers have activity but no first login** — these are missing or "
             "broken `ursa_first_login` values that need backfilling / investigation.")

# ── platform login coverage ─────────────────────────────────────────────────────
st.markdown("##### URSA login coverage by platform")
_plat = pd.DataFrame([
    {"Platform": "First Login (any)", "With value": int((df["First Login"] != "").sum())},
    {"Platform": "iOS", "With value": int((df["iOS"] != "").sum())},
    {"Platform": "Android", "With value": int((df["Android"] != "").sum())},
    {"Platform": "Web", "With value": int((df["Web"] != "").sum())},
])
_plat["% of numbers"] = (_plat["With value"] / n_total * 100).round(1)
st.dataframe(_plat, use_container_width=True, hide_index=True,
             column_config={"With value": st.column_config.ProgressColumn(
                 "With value", min_value=0, max_value=n_total, format="%d")})

# ── flagged records ─────────────────────────────────────────────────────────────
st.markdown("##### Records")
f1, f2 = st.columns([2, 2])
flag_opts = ["🔴 Missing first login (but active)", "🟡 First login, no platform last login",
             "🟠 First login after activity", "⚪ No login, no activity", "✅ OK"]
pick = f1.multiselect("Flag", flag_opts,
                      default=["🔴 Missing first login (but active)",
                               "🟡 First login, no platform last login",
                               "🟠 First login after activity"])
search = f2.text_input("Search number / email / name").strip().lower()

view = df.copy()
if search:
    # a direct search overrides the flag filter — always find the number
    view = view[view["Number"].str.contains(search, case=False, na=False)
                | view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)]
    st.caption("🔎 Showing search matches across **all** flags (flag filter ignored while searching).")
elif pick:
    view = view[view["Flag"].isin(pick)]
st.caption(f"{len(view):,} records")
_show_cols = [c for c in ["Number", "Email", "Name", "Service", "Status", "First Login", "iOS",
                          "Android", "Web", "First Outbound", "Last Outbound", "Last Inbound",
                          "Evidence", "Flag"] if c in view.columns]
st.dataframe(view[_show_cols], use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "ursa_login_audit.csv", "text/csv")

st.caption("**🔴 Missing first login (but active)** = `ursa_first_login` blank yet an outbound/inbound "
           "call or iOS/Android/Web login exists → the first-login timestamp is missing or errored. "
           "**🟠 First login after activity** = first login date is later than the earliest activity "
           "(logically impossible — likely a data error).")

report_header_close()
