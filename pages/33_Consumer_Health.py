import streamlit as st
import pandas as pd
import requests
import time
from datetime import date, datetime, timezone
from collections import defaultdict
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Consumer Health", layout="wide", page_icon="💚")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Consumer Health")

report_header("How are our consumers doing?",
              "Consumer Excellence Program — health scores from usage vs. each consumer's baseline",
              section="Analytics")

NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
CACHE_VERSION = 3
LOOKBACK = 6   # months of history for baseline + trend


# ── helpers ───────────────────────────────────────────────────────────────────
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


def _ms(period):
    return str(int(datetime(period.year, period.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _seek_mv(props, start_ms, label):
    """All Monthly Values since start_ms (past the 10k cap), by hs_object_id cursor."""
    url = f"{_B}/crm/v3/objects/{MV_OBJECT}/search"
    out, last = [], "0"
    ph = st.empty()
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": [
                    {"propertyName": "month_date", "operator": "GTE", "value": start_ms},
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(6):
            try:
                r = requests.post(url, headers=_H, json=body, timeout=30)
            except requests.exceptions.RequestException:
                # transient network/SSL blip — back off and retry
                r = None
                time.sleep(1.5 * (attempt + 1)); continue
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            ph.empty(); st.error(f"HubSpot error {getattr(r,'status_code','network/SSL')}"); break
        batch = r.json().get("results", [])
        time.sleep(0.08)
        out.extend(batch)
        ph.caption(f"{label} {len(out):,} usage rows…")
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"])
    ph.empty()
    return out


def _tenure_years(iso):
    try:
        d = datetime.fromisoformat(str(iso)[:10]).date()
        return round((date.today() - d).days / 365.25, 1)
    except Exception:
        return None


# ── window ─────────────────────────────────────────────────────────────────────
today = date.today()
current = pd.Period(today.strftime("%Y-%m"), freq="M")
months = [current - i for i in range(LOOKBACK - 1, -1, -1)]     # oldest → current
month_labels = [m.strftime("%b") for m in months]

c1, c2 = st.columns([1, 3])
_opts = [m.strftime("%b %Y") + (" (current, partial)" if m == current else "") for m in months]
with c1:
    # default to the last COMPLETED month (not the partial current month), so most
    # consumers actually have usage to compare — the current month is barely started.
    period_label = st.selectbox("Period (compared month)", _opts, index=len(months) - 2)
period = months[_opts.index(period_label)]
run = st.button("Run consumer health report", type="primary")

_key = f"consumer_health_v{CACHE_VERSION}"

if run:
    start_ms = _ms(months[0])
    # ── 1) all live consumer Number objects (VRS + Convo Now) ───────────────────
    with dash_spinner("Reading consumer numbers…"):
        num_recs = fetch_all(
            NUM_OBJECT,
            ["number", "email", "first_name", "last_name", "service_type",
             "account_status", "usage_type", "number_created_at"],
            filter_groups=[{"filters": [
                {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]}]}])
    num_info = {}
    for r in num_recs:
        p = r.get("properties", {})
        n = str(p.get("number") or "").strip()
        if not n:
            continue
        status = (p.get("account_status") or "").strip()
        svc = (p.get("service_type") or "").strip()
        info = num_info.setdefault(n, {
            "email": (p.get("email") or "").strip(),
            "name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip(),
            "status": status, "usage_type": (p.get("usage_type") or "").strip(),
            "created": (str(p.get("number_created_at") or "")[:10]),
            "has_vrs": False, "has_cn": False})
        if svc == "VRS":
            info["has_vrs"] = True
        elif svc == "Convo Now":
            info["has_cn"] = True
        # keep the "Live" status / earliest created if multiple rows
        if status.lower() == "live":
            info["status"] = "Live"

    # ── 2) monthly usage, split into VRS / IVCS / CN20 ──────────────────────────
    # VRS mins = URSA(ios+android+web) on VRS rows · IVCS = CfZ · CN20 = usage on Convo Now rows
    usage = defaultdict(lambda: defaultdict(lambda: {"vrs": 0.0, "ivcs": 0.0, "cn20": 0.0}))
    mv = _seek_mv(["number", "month_date", "ursa_ios_minutes", "ursa_android_minutes",
                   "ursa_web_minutes", "cfz_minutes", "usage_minutes", "service_type"],
                  start_ms, "Usage:")
    for o in mv:
        p = o.get("properties", {})
        n = str(p.get("number") or "").strip()
        per = _period_of(p.get("month_date"))
        if not n or per not in months:
            continue
        svc = (p.get("service_type") or "").strip()
        cell = usage[n][per]
        if svc == "Convo Now":
            cell["cn20"] += to_float(p.get("usage_minutes")) or 0.0
        else:  # VRS
            cell["vrs"] += ((to_float(p.get("ursa_ios_minutes")) or 0.0)
                            + (to_float(p.get("ursa_android_minutes")) or 0.0)
                            + (to_float(p.get("ursa_web_minutes")) or 0.0))
            cell["ivcs"] += to_float(p.get("cfz_minutes")) or 0.0

    # ── 3) build per-consumer rows ──────────────────────────────────────────────
    rows = []
    for n, meta in num_info.items():
        mm = usage.get(n, {})
        vrs = [round(mm.get(m, {}).get("vrs", 0.0), 1) for m in months]
        ivcs = [round(mm.get(m, {}).get("ivcs", 0.0), 1) for m in months]
        cn20 = [round(mm.get(m, {}).get("cn20", 0.0), 1) for m in months]
        total = [round(vrs[i] + ivcs[i] + cn20[i], 1) for i in range(len(months))]
        recent = mm.get(period, {})
        recent_total = round((recent.get("vrs", 0) + recent.get("ivcs", 0)
                              + recent.get("cn20", 0)), 1)
        # baseline = avg of months BEFORE the compared period that had usage
        pri = [total[i] for i, m in enumerate(months) if m < period and total[i] > 0]
        baseline = round(sum(pri) / len(pri), 1) if pri else 0.0
        rows.append({
            "Number": n, "Email": meta["email"], "Name": meta["name"] or "—",
            "Status": meta["status"], "UsageType": meta["usage_type"],
            "Tenure": _tenure_years(meta["created"]), "Created": meta["created"],
            "HasVRS": meta["has_vrs"], "HasCN": meta["has_cn"],
            "Baseline": baseline, "Recent": recent_total,
            "vrs_recent": round(recent.get("vrs", 0.0), 1),
            "ivcs_recent": round(recent.get("ivcs", 0.0), 1),
            "cn20_recent": round(recent.get("cn20", 0.0), 1),
            "Trend": total, "lifetime": round(sum(total), 1),
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "months": [str(m) for m in months],
                       "month_labels": month_labels, "period": str(period)})

saved = load_report(_key)
if saved is None:
    st.info("Click **Run consumer health report** to load the dashboard.")
    report_header_close(); st.stop()

df = saved["df"].copy()
months = [pd.Period(m, freq="M") for m in saved["months"]]
month_labels = saved["month_labels"]
period = pd.Period(saved["period"], freq="M")
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · period **{period.strftime('%b %Y')}**")


# ── scoring / classification ───────────────────────────────────────────────────
def _score(row):
    if row["Recent"] <= 0:
        return 0
    if row["Baseline"] <= 0:
        return 100                       # new usage, nothing to compare down against
    ratio = row["Recent"] / row["Baseline"]
    return int(max(0, min(100, round(min(ratio, 1.25) / 1.25 * 100))))


def _band(row):
    if row["Tenure"] is not None and row["Tenure"] * 365.25 <= 90:
        return "New"
    if row["Recent"] <= 0:
        return "Inactive"
    s = row["Score"]
    return "Healthy" if s >= 70 else ("Watch" if s >= 40 else "Action")


df["Score"] = df.apply(_score, axis=1)
df["Band"] = df.apply(_band, axis=1)
df["MinsAtRisk"] = (df["Baseline"] - df["Recent"]).clip(lower=0).round(1)
df["CN20only"] = (~df["HasVRS"]) & (df["HasCN"])

live = df[df["Status"].str.lower() == "live"]
active = live[live["Recent"] > 0]
n_active = len(active)


def _pct(n):
    return f"{(n / n_active * 100):.1f}% of active" if n_active else "—"


# ── KPI cards ──────────────────────────────────────────────────────────────────
st.caption(f"**{len(live):,} live consumer numbers.** Usage spans VRS (Ursa/Legacy) + IVCS "
           f"(Convo for Zoom) + CN20 (Convo Now). Health = compared month vs each consumer's "
           f"6-month baseline. ASA / Abandoned not available (no data).")

healthy = int((active["Band"] == "Healthy").sum())
watch = int((active["Band"] == "Watch").sum())
actn = int((active["Band"] == "Action").sum())
new90 = int((live["Band"] == "New").sum())
inactive = int((live["Recent"] <= 0).sum())
cn20only = int(live["CN20only"].sum())

cards = [
    ("💚 Healthy", healthy, _pct(healthy), "#2DB84B"),
    ("👀 Watch", watch, _pct(watch), "#E8952A"),
    ("🔴 Action", actn, _pct(actn), "#E5484D"),
    ("✨ New (first 90 days)", new90, f"{(new90/len(live)*100):.1f}% of live" if len(live) else "—", "#4C8DFF"),
    ("💤 Inactive", inactive, "Zero billable minutes", "#8792A2"),
    ("📱 CN20 account only", cn20only, "Without VRS number", "#B0B7C3"),
]
cols = st.columns(len(cards))
for col, (title, val, sub, color) in zip(cols, cards):
    col.markdown(
        f"""<div style="border:1px solid #E6E9F0;border-radius:12px;padding:14px 14px 10px;">
        <div style="font-size:.7rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
             color:#667085;">{title}</div>
        <div style="font-size:1.9rem;font-weight:800;color:#1A2234;line-height:1.15;">{val:,}</div>
        <div style="font-size:.72rem;color:{color};font-weight:600;">● {sub}</div></div>""",
        unsafe_allow_html=True)

st.markdown("")

# ── filters + performance list ─────────────────────────────────────────────────
st.markdown("#### Consumers by health score")
f1, f2, f3, f4 = st.columns([1.4, 1.4, 1.4, 2])
band_pick = f1.multiselect("Band", ["Healthy", "Watch", "Action", "New", "Inactive"],
                           default=["Healthy", "Watch", "Action"])
status_pick = f2.selectbox("Status", ["Live only", "All statuses"])
seg_opts = ["All usage types"] + sorted([u for u in df["UsageType"].dropna().unique() if u])
seg_pick = f3.selectbox("Usage type", seg_opts)
search = f4.text_input("Search phone # / email", placeholder="2025597251 or name@…").strip().lower()

view = df.copy()
if search:
    # A direct search overrides band/status/type filters — always find the number.
    view = view[view["Number"].str.contains(search, case=False, na=False)
                | view["Email"].str.contains(search, case=False, na=False)
                | view["Name"].str.contains(search, case=False, na=False)]
    st.caption("🔎 Showing search matches across **all** bands and statuses (filters ignored while searching).")
else:
    if status_pick == "Live only":
        view = view[view["Status"].str.lower() == "live"]
    if band_pick:
        view = view[view["Band"].isin(band_pick)]
    if seg_pick != "All usage types":
        view = view[view["UsageType"] == seg_pick]

view = view.sort_values("Score", ascending=False)
st.caption(f"{len(view):,} consumer numbers")

_badge = {"Healthy": "💚", "Watch": "👀", "Action": "🔴", "New": "✨", "Inactive": "💤"}
show = view.copy()
show["Health"] = show.apply(lambda r: f"{_badge.get(r['Band'],'')} {r['Score']}", axis=1)
tbl = show[["Number", "Email", "Tenure", "Health", "Trend", "Baseline", "Recent",
            "MinsAtRisk", "vrs_recent", "ivcs_recent", "cn20_recent"]].rename(columns={
    "MinsAtRisk": "Mins at Risk", "vrs_recent": "VRS Mins",
    "ivcs_recent": "IVCS Mins", "cn20_recent": "CN20 Mins"})

st.dataframe(
    tbl, use_container_width=True, hide_index=True, height=460,
    column_config={
        "Trend": st.column_config.BarChartColumn("Trend (6mo)", y_min=0),
        "Baseline": st.column_config.NumberColumn(format="%.1f"),
        "Recent": st.column_config.NumberColumn(format="%.1f"),
    })
st.download_button("📥 Export CSV",
                   tbl.assign(Trend=tbl["Trend"].map(lambda t: " ".join(map(str, t)))).to_csv(index=False),
                   f"consumer_health_{period.strftime('%Y%m')}.csv", "text/csv")

# ── individual drilldown ───────────────────────────────────────────────────────
st.markdown("---")
st.markdown("#### 🔍 Individual usage card")
pick = st.text_input("Enter a consumer phone # to open their card", key="drill").strip()
if pick:
    hit = df[df["Number"].str.contains(pick, na=False)]
    if hit.empty:
        st.warning(f"No consumer number matching '{pick}'.")
    else:
        r = hit.iloc[0]
        d1, d2 = st.columns([2, 1])
        with d1:
            st.markdown(f"### {r['Number']} &nbsp; {_badge.get(r['Band'],'')} **{r['Band']}**")
            reg = f"Registered {r['Created']}" + (f" · {r['Tenure']}y tenure" if r["Tenure"] else "")
            st.caption(f"{r['Name']} · {r['UsageType'] or 'Personal'} · {reg}")
        with d2:
            st.metric("Health score", f"{r['Score']}%")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Lifetime billable (6mo)", f"{r['lifetime']:,.0f}")
        m2.metric(f"{period.strftime('%b')} billable", f"{r['Recent']:,.1f}")
        m3.metric("Baseline / mo", f"{r['Baseline']:,.1f}")
        m4.metric("Mins at risk", f"{r['MinsAtRisk']:,.1f}")
        trend_df = pd.DataFrame({"Month": month_labels, "Billable minutes": r["Trend"]}).set_index("Month")
        st.markdown("###### Past 6 months (VRS + IVCS + CN20 billable minutes)")
        st.bar_chart(trend_df, height=260)
        st.caption(f"Baseline {r['Baseline']:.1f} min = average of prior months with usage. "
                   f"This month: VRS {r['vrs_recent']:.1f} · IVCS {r['ivcs_recent']:.1f} · "
                   f"CN20 {r['cn20_recent']:.1f}.")

report_header_close()
