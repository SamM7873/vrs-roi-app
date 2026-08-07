import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import date, datetime, timedelta, timezone
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, to_float, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Registrations Report", layout="wide", page_icon="📝")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Registrations Report")

report_header("Registrations Report",
              "New registrations over time — daily, weekly, monthly, quarterly, yearly",
              section="Numbers")

REG_OBJECT = "2-58833629"
REPORT_VERSION = "v3-schema-fields"  # bump to invalidate old saved runs when fields change


# ── central-time helpers (match HubSpot report boundaries) ──────────────────
def _ct_offset(m):
    return -5 if 3 <= m <= 11 else -6  # CDT Mar–Nov else CST


def _ms(d, end=False):
    off = _ct_offset(d.month)
    t = datetime(d.year, d.month, d.day, 23, 59, 59 if end else 0,
                 0 if end else 0, tzinfo=timezone(timedelta(hours=off)))
    if not end:
        t = t.replace(hour=0, minute=0, second=0)
    return str(int(t.timestamp() * 1000))


@st.cache_data(ttl=1800, show_spinner=False)
def _schema():
    """Full property list for the object: [(name, label, type)]. Read once from HubSpot."""
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{REG_OBJECT}", headers=_H, timeout=25)
        if r.status_code == 200:
            return [(p["name"], (p.get("label") or p["name"]).strip(), p.get("type"))
                    for p in r.json().get("results", [])]
    except Exception:
        pass
    return []


# (HubSpot label to match, short display label). We resolve the internal name from
# the schema by the HubSpot label, so empty "By" columns are fixed regardless of naming.
WANTED = [
    ("Registration Type", "Reg Type"), ("Usage Type", "Usage Type"),
    ("Lex Verification Status", "Lex Status"), ("Lex Verified At", "Lex Verified At"),
    ("Lex Errors", "Lex Errors"), ("Lex Error Message", "Lex Error Msg"),
    ("Urd Status", "URD Status"), ("Urd Filling Error Message", "URD Filling Error"),
    ("Urd Identity Error Message", "URD Identity Error"), ("Urd Terminated At", "URD Terminated At"),
    ("Manually Edited At", "Manually Edited At"), ("Manually Edited By", "Manually Edited By"),
    ("Manually Verified At", "Manually Verified At"), ("Manually Verified By", "Manually Verified By"),
    ("Registration Id", "Registration Id"), ("Registration Uuid", "Registration UUID"),
    ("Is Itrs Registered", "ITRS Registered"), ("Number", "Number"), ("Email", "Email"),
]
DATE_LABELS = [
    "Registered At", "Manually Verified At", "Manually Edited At",
    "Registration Created At", "Registration Updated At", "Lex Verified At",
]


@st.cache_data(ttl=1800, show_spinner=False)
def _resolve_fields():
    """Resolve wanted + date labels to actual internal names via the object schema.
    Returns (value_fields, date_fields) each as [(internal_name, display_label)]."""
    sch = _schema()
    if not sch:   # schema unreadable → fall back to best-guess internal names
        val = [(d.lower().replace(" ", "_"), disp) for d, disp in WANTED]
        val = [(n if n != "reg_type" else "registration_type", disp) for n, disp in val]
        return val, [("registered_at", "Registered At"), ("hs_createdate", "Record Created")]
    by_label = {lb.lower(): nm for nm, lb, _t in sch}
    names = {nm for nm, _lb, _t in sch}
    val, dat = [], []
    for match_lb, disp in WANTED:
        nm = by_label.get(match_lb.lower()) or (disp.lower() if disp.lower() in names else None)
        if nm:
            val.append((nm, disp))
    for lb in DATE_LABELS:
        nm = by_label.get(lb.lower())
        if nm:
            dat.append((nm, lb))
    # date fallbacks
    for nm, lb in (("registered_at", "Registered At"), ("hs_createdate", "Record Created")):
        if nm in names and not any(n == nm for n, _ in dat):
            dat.append((nm, lb))
    if not dat:
        dat = [("hs_createdate", "Record Created")]
    return val, dat


@st.cache_data(ttl=1800, show_spinner=False)
def _owners():
    """HubSpot user/owner id → display name (active + archived)."""
    out = {}
    for arch in (False, True):
        after = None
        for _ in range(40):
            url = (f"{_B}/crm/v3/owners?limit=100" + ("&archived=true" if arch else "")
                   + (f"&after={after}" if after else ""))
            try:
                r = requests.get(url, headers=_H, timeout=20)
            except Exception:
                break
            if r.status_code != 200:
                break
            d = r.json()
            for o in d.get("results", []):
                nm = f"{(o.get('firstName') or '').strip()} {(o.get('lastName') or '').strip()}".strip() \
                     or o.get("email", "")
                if nm:
                    out[str(o.get("id"))] = nm + (" (deactivated)" if arch else "")
                    if o.get("userId"):
                        out[str(o.get("userId"))] = out[str(o.get("id"))]
            after = d.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
    return out


def _seek(date_prop, props, start_ms, end_ms, label):
    url = f"{_B}/crm/v3/objects/{REG_OBJECT}/search"
    results, last = [], "0"
    ph = st.empty()
    allprops = list(dict.fromkeys(props + [date_prop]))
    while True:
        body = {"limit": 100, "properties": allprops,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": [
                    {"propertyName": date_prop, "operator": "GTE", "value": start_ms},
                    {"propertyName": date_prop, "operator": "LTE", "value": end_ms},
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = requests.post(url, headers=_H, json=body, timeout=30)
        if r.status_code != 200:
            ph.empty()
            st.error(f"HubSpot error {r.status_code}: {r.text[:200]}")
            break
        batch = r.json().get("results", [])
        results.extend(batch)
        ph.caption(f"{label} {len(results):,} registrations…")
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"])
    ph.empty()
    return results


def _parse(v):
    if not v:
        return None
    try:
        s = str(v)
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ── period presets ──────────────────────────────────────────────────────────
def _range(preset, today=None):
    t = today or date.today()
    q = (t.month - 1) // 3
    if preset == "Today":
        return t, t
    if preset == "Yesterday":
        y = t - timedelta(days=1); return y, y
    if preset == "This Week":
        s = t - timedelta(days=t.weekday()); return s, t
    if preset == "This Month":
        return t.replace(day=1), t
    if preset == "This Quarter":
        return date(t.year, q * 3 + 1, 1), t
    if preset == "This Year":
        return date(t.year, 1, 1), t
    if preset == "Last Month":
        first = t.replace(day=1); le = first - timedelta(days=1)
        return le.replace(day=1), le
    if preset == "Last Quarter":
        ly, lq = (t.year, q - 1) if q > 0 else (t.year - 1, 3)
        s = date(ly, lq * 3 + 1, 1)
        em, ey = (s.month + 3, s.year)
        if em > 12: em, ey = em - 12, ey + 1
        return s, date(ey, em, 1) - timedelta(days=1)
    if preset == "Last Year":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    return t.replace(day=1), t


PRESETS = ["Today", "Yesterday", "This Week", "This Month", "This Quarter", "This Year",
           "Last Month", "Last Quarter", "Last Year", "Custom"]

_valfields, _dfields = _resolve_fields()
_dlabels = [lb for _n, lb in _dfields]
c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    preset = st.selectbox("Period", PRESETS, index=3)
    start_d, end_d = _range(preset)
    if preset == "Custom":
        dr = st.date_input("Custom range", value=(start_d, end_d))
        if isinstance(dr, tuple) and len(dr) == 2:
            start_d, end_d = dr
with c2:
    _basis_label = st.selectbox("Filter dates by", _dlabels, index=0,
                                help="Which date the period applies to. Pick 'Manually Verified At' "
                                     "to see all manual verifications in the period, etc.")
    date_prop = next(n for n, lb in _dfields if lb == _basis_label)
with c3:
    st.markdown("<div style='margin-top:1.7rem;'></div>", unsafe_allow_html=True)
    run = st.button("Run", type="primary", use_container_width=True)

st.caption(f"Showing registrations where **{_basis_label}** is between "
           f"**{start_d:%b %d, %Y}** and **{end_d:%b %d, %Y}**.")

_key = f"registrations_{date_prop}_{start_d:%Y%m%d}_{end_d:%Y%m%d}"

if run:
    props = [nm for nm, _lb in _valfields]
    with dash_spinner("Fetching registrations…"):
        recs = _seek(date_prop, props, _ms(start_d), _ms(end_d, end=True), "Loaded")
    _labels = {nm: lb for nm, lb in _valfields}
    rows = []
    for r in recs:
        p = r.get("properties", {})
        dt = _parse(p.get(date_prop))
        row = {"Date": dt.strftime("%Y-%m-%d") if dt else ""}
        for cand in props:
            row[_labels[cand]] = (p.get(cand) or "—")
        rows.append(row)
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "start": str(start_d), "end": str(end_d),
                       "date_prop": date_prop, "v": REPORT_VERSION})

saved = load_report(_key)
if saved is None or saved.get("v") != REPORT_VERSION:
    st.info("Pick a period and click **Run**. Results are saved and reload automatically. "
            "(Fields were updated — please Run once to refresh.)")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · click Run to refresh · date field: `{saved.get('date_prop','')}`")

if df.empty:
    st.warning("No registrations in this period.")
    report_header_close(); st.stop()

# ── KPIs ──
total = len(df)
_d = pd.to_datetime(df["Date"], errors="coerce")
span_days = (end_d - start_d).days + 1
per_day = total / span_days if span_days else total
# Verified = Lex status "Verified" OR manually verified (has a Manually Verified At).
def _nonblank(col):
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    s = df[col].astype(str).str.strip()
    return ~s.isin(["", "—", "nan", "None"])

# Lex "verified" = automatic_success or manual_success (also accept a plain "verified").
VERIFIED_STATUSES = {"automatic_success", "manual_success", "verified", "success"}
_lex = df["Lex Status"].astype(str).str.strip().str.lower() if "Lex Status" in df.columns \
    else pd.Series("", index=df.index)
_lex_ok = _lex.isin(VERIFIED_STATUSES)
_manual_ok = _nonblank("Manually Verified At") | _lex.eq("manual_success")
_verified = int((_lex_ok | _manual_ok).sum())
_manual_n = int(_manual_ok.sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total registrations", f"{total:,}")
k2.metric("✅ Verified", f"{_verified:,}")
k2.caption(f"{_verified/total*100:.0f}% of total · auto + manual success")
k3.metric("❌ Not verified", f"{total - _verified:,}")
k3.caption("Lex not_verified (and any blanks)")
k4.metric("✍️ Manually verified", f"{_manual_n:,}")
k4.caption("manual_success / has Manually Verified At")

# breakdown helper
def _breakdown(col, title):
    if col in df.columns and (df[col].astype(str) != "—").any():
        st.markdown(f"##### {title}")
        b = (df.groupby(col).size().reset_index(name="Registrations")
             .sort_values("Registrations", ascending=False))
        _tot = b["Registrations"].sum()
        b["%"] = (b["Registrations"] / _tot * 100).round(1).astype(str) + "%" if _tot else "—"
        st.dataframe(b, use_container_width=True, hide_index=True)

bc1, bc2, bc3 = st.columns(3)
with bc1: _breakdown("Reg Type", "By registration type")
with bc2: _breakdown("Usage Type", "By usage type")
with bc3: _breakdown("Lex Status", "By Lex verification")

# ── who did the manual work (resolve owner IDs → names) ──
_own = _owners()
for _c in ("Manually Edited By", "Manually Verified By"):
    if _c in df.columns:
        df[_c] = df[_c].map(lambda v: _own.get(str(v).strip(), v))

def _who(by_col, at_col, title):
    if by_col not in df.columns and at_col not in df.columns:
        return
    mask = _nonblank(by_col) | _nonblank(at_col)
    sub = df[mask]
    if sub.empty:
        st.caption(f"{title}: none in this period.")
        return
    st.markdown(f"##### {title} — {len(sub):,}")
    if by_col in sub.columns and _nonblank(by_col)[mask].any():
        s2 = sub.assign(Person=sub[by_col].where(_nonblank(by_col)[mask].values, "(unknown)"))
        if "Reg Type" in s2.columns:
            g = (s2.groupby(["Person", "Reg Type"]).size().reset_index(name="Count")
                 .sort_values("Count", ascending=False))
        else:
            g = (s2.groupby("Person").size().reset_index(name="Count")
                 .sort_values("Count", ascending=False))
        st.dataframe(g, use_container_width=True, hide_index=True)

st.markdown("#### Manual activity")
wc1, wc2 = st.columns(2)
with wc1: _who("Manually Verified By", "Manually Verified At", "✍️ Manually verified by")
with wc2: _who("Manually Edited By", "Manually Edited At", "✏️ Manually edited by")

# ── trend (bucket by span) ──
st.markdown("##### Registrations over time")
tmp = df.assign(_dt=_d).dropna(subset=["_dt"])
if span_days <= 45:
    tmp["Bucket"] = tmp["_dt"].dt.date.astype(str); _title = "By day"
elif span_days <= 210:
    tmp["Bucket"] = tmp["_dt"].dt.to_period("W").apply(lambda p: p.start_time.date().isoformat()); _title = "By week"
else:
    tmp["Bucket"] = tmp["_dt"].dt.to_period("M").astype(str); _title = "By month"
trend = tmp.groupby("Bucket").size().reset_index(name="Registrations")
ch = (alt.Chart(trend).mark_bar(color="#0D3B26", cornerRadiusEnd=3)
      .encode(x=alt.X("Bucket:N", title=_title, sort=None),
              y=alt.Y("Registrations:Q"),
              tooltip=["Bucket", "Registrations"])
      .properties(height=260))
st.altair_chart(ch, use_container_width=True)

# ── detail ──
st.markdown("##### Registration detail")
for _mc in ("Manually Edited At", "Manually Edited By", "Manually Verified At", "Manually Verified By"):
    if _mc not in df.columns:
        df[_mc] = "—"
_pref = ["Date", "Reg Type", "Usage Type", "Lex Status", "URD Status",
         "Manually Verified At", "Manually Verified By", "Manually Edited At", "Manually Edited By",
         "Number", "Email", "Registration Id", "Lex Error Msg", "URD Filling Error",
         "URD Identity Error", "Registration UUID"]
_cols = [c for c in _pref if c in df.columns] + [c for c in df.columns if c not in _pref]
st.dataframe(df.sort_values("Date", ascending=False)[_cols],
             use_container_width=True, hide_index=True, height=420)
st.download_button("📥 Export CSV", df.to_csv(index=False),
                   f"registrations_{start_d:%Y%m%d}_{end_d:%Y%m%d}.csv", "text/csv")

report_header_close()
