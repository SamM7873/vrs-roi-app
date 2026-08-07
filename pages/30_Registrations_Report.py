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
REPORT_VERSION = "v2-manual-fields"  # bump to invalidate old saved runs when fields change


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


# Date fields you can filter the period on (internal name → label). Only existing ones show.
DATE_CANDS = [
    ("registered_at", "Registered At"),
    ("manually_verified_at", "Manually Verified At"),
    ("manually_edited_at", "Manually Edited At"),
    ("registration_created_at", "Registration Created At"),
    ("registration_updated_at", "Registration Updated At"),
    ("lex_verified_at", "Lex Verified At"),
    ("hs_createdate", "Record Created"),
]


@st.cache_data(ttl=1800, show_spinner=False)
def _date_fields():
    """Return [(internal, label)] date fields that exist on the object."""
    out = []
    for cand, label in DATE_CANDS:
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{REG_OBJECT}/search", headers=_H,
                              json={"limit": 1, "properties": [cand]}, timeout=15)
            if r.status_code == 200:
                out.append((cand, label))
        except Exception:
            pass
    return out or [("hs_createdate", "Record Created")]


# Registration object fields (internal name → column label). Only those that
# actually exist on the object are pulled — the rest are skipped automatically.
FIELD_MAP = [
    ("registration_type", "Reg Type"),
    ("usage_type", "Usage Type"),
    ("registration_id", "Registration Id"),
    ("registration_uuid", "Registration UUID"),
    ("is_itrs_registered", "ITRS Registered"),
    ("lex_verification_status", "Lex Status"),
    ("lex_verified_at", "Lex Verified At"),
    ("lex_errors", "Lex Errors"),
    ("lex_error_message", "Lex Error Msg"),
    ("urd_status", "URD Status"),
    ("urd_filling_error_message", "URD Filling Error"),
    ("urd_identity_error_message", "URD Identity Error"),
    ("urd_terminated_at", "URD Terminated At"),
    ("manually_edited_at", "Manually Edited At"),
    ("manually_edited_by", "Manually Edited By"),
    ("manually_verified_at", "Manually Verified At"),
    ("manually_verified_by", "Manually Verified By"),
    ("number", "Number"),
    ("email", "Email"),
]


@st.cache_data(ttl=1800, show_spinner=False)
def _extra_props(date_prop):
    """Which of the mapped fields actually exist on the Registrations object."""
    out = []
    for cand, _label in FIELD_MAP:
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{REG_OBJECT}/search", headers=_H,
                              json={"limit": 1, "properties": [cand]}, timeout=15)
            if r.status_code == 200:
                out.append(cand)
        except Exception:
            pass
    return out


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

_dfields = _date_fields()
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
    props = _extra_props(date_prop)
    with dash_spinner("Fetching registrations…"):
        recs = _seek(date_prop, props, _ms(start_d), _ms(end_d, end=True), "Loaded")
    _labels = {k: v for k, v in FIELD_MAP}
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

_lex_ok = (df["Lex Status"].astype(str).str.strip().str.lower().eq("verified")
           if "Lex Status" in df.columns else pd.Series(False, index=df.index))
_manual_ok = _nonblank("Manually Verified At")
_verified = int((_lex_ok | _manual_ok).sum())
_manual_n = int(_manual_ok.sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total registrations", f"{total:,}")
k2.metric("✅ Verified", f"{_verified:,}", f"{_verified/total*100:.0f}%" if total else "—",
          help="Lex status = Verified OR manually verified.")
k3.metric("❌ Not verified", f"{total - _verified:,}",
          help="Not Lex-verified and not manually verified (incl. 'Not Verified' and blanks).")
k4.metric("✍️ Manually verified", f"{_manual_n:,}",
          help="Has a 'Manually Verified At' timestamp.")

# breakdown helper
def _breakdown(col, title):
    if col in df.columns and (df[col].astype(str) != "—").any():
        st.markdown(f"##### {title}")
        b = (df.groupby(col).size().reset_index(name="Registrations")
             .sort_values("Registrations", ascending=False))
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
        g = (sub.assign(_p=sub[by_col].where(_nonblank(by_col)[mask].values, "(unknown)"))
             .groupby("_p").size().reset_index(name="Count")
             .rename(columns={"_p": "Person"}).sort_values("Count", ascending=False))
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
