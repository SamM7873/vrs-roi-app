import streamlit as st
import pandas as pd
from datetime import date, datetime, timezone, timedelta
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner, log_report_view)

st.set_page_config(page_title="Journey Funnel", layout="wide", page_icon="🫗")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Journey Funnel")

report_header("Journey Funnel",
              "Beginning → end: Submissions → Interactions (Convo360) → Tickets · with drop-off",
              section="Support")

SUB_OBJECT = "2-49942763"   # submission form records


@st.cache_data(ttl=3600, show_spinner=False)
def _sub_contact_props():
    """Discover the submission object's contact-info properties (name/email/phone/address…)."""
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{SUB_OBJECT}", headers=_H, timeout=30)
        if r.status_code != 200:
            return []
        _kw = ("email", "phone", "mobile", "firstname", "lastname", "first_name", "last_name",
               "name", "contact", "address", "city", "state", "zip", "country", "company")
        out = []
        for p in r.json().get("results", []):
            n = (p.get("name") or "")
            if any(k in n.lower() for k in _kw):
                out.append((n, p.get("label") or n))
        return out
    except Exception:
        return []

st.markdown("A single funnel across three sources: **submission forms** (HubSpot custom object), "
            "**interactions** (Convo360 CSV), and **tickets** (HubSpot audit-log CSV). Counts are the "
            "**volume at each stage** in the chosen date window, so you can see the drop-off from "
            "beginning to end.")
st.info("The three sources don't share a common customer key, so this is a **volume funnel** "
        "(totals per stage), not a per-person match.", icon="ℹ️")


def _find(cols, *names):
    low = {c.lower(): c for c in cols}
    for n in names:
        for lc, orig in low.items():
            if n in lc:
                return orig
    return None


def _parse_conv(file):
    """Convo360 interaction export → rows with a date."""
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    dcol = _find(df.columns, "date")
    if not dcol:
        return "Convo360: no date column found."
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    df = df[df["_day"].notna()].copy()
    return df[["_day"]]


def _parse_tick(file):
    """HubSpot audit-log export → ticket rows, flagged created vs touched."""
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    dcol = _find(df.columns, "date")
    if not dcol:
        return "Ticket audit: no date column found."
    for c in ("Subcategory", "Action", "Target object id"):
        if c not in df.columns:
            df[c] = ""
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    df = df[(df["Subcategory"] == "Ticket") & df["_day"].notna()].copy()
    df["_created"] = df["Action"] == "Create"
    return df[["_day", "_created", "Target object id"]]


def _ms(d):
    off = -5 if 3 <= d.month <= 11 else -6
    return str(int(datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=off))).timestamp() * 1000))


# ── inputs ──────────────────────────────────────────────────────────────────────────
c1, c2 = st.columns(2)
conv_file = c1.file_uploader("1) Interactions — Convo360 CSV", type=["csv"], key="jf_conv")
tick_file = c2.file_uploader("2) Tickets — HubSpot audit-log CSV", type=["csv"], key="jf_tick")

today = date.today()
d1, d2 = st.columns(2)
lo = d1.date_input("From date", value=today.replace(day=1) - timedelta(days=60))
hi = d2.date_input("To date", value=today)
if lo > hi:
    lo, hi = hi, lo
    st.warning("From was after To — swapped so the range isn't empty.")

run = st.button("▶ Build funnel", type="primary", disabled=not (conv_file and tick_file))

if run:
    # ── stage 1: submissions created in the window (HubSpot custom object) ──
    _cprops = _sub_contact_props()
    _cnames = [n for n, _ in _cprops]
    with dash_spinner("Reading submission forms…"):
        subs = fetch_all(SUB_OBJECT, ["hs_createdate"] + _cnames, filter_groups=[{"filters": [
            {"propertyName": "hs_createdate", "operator": "GTE", "value": _ms(lo)},
            {"propertyName": "hs_createdate", "operator": "LTE", "value": _ms(hi + timedelta(days=1))}]}])
    n_sub = len(subs)

    # submission contact-info detail table (all discovered contact fields)
    _sub_rows = []
    for s in subs:
        p = s.get("properties", {})
        cd = str(p.get("hs_createdate") or "")
        row = {"Created": cd[:10]}
        for n, lab in _cprops:
            row[lab] = p.get(n) or ""
        _sub_rows.append(row)
    _sub_df = pd.DataFrame(_sub_rows)

    # ── stage 2: interactions in the window (Convo360 CSV) ──
    cv = _parse_conv(conv_file)
    if isinstance(cv, str):
        st.error(cv); report_header_close(); st.stop()
    cv = cv[(cv["_day"] >= lo) & (cv["_day"] <= hi)]
    n_int = len(cv)

    # ── stage 3: tickets created in the window (audit-log CSV) ──
    tk = _parse_tick(tick_file)
    if isinstance(tk, str):
        st.error(tk); report_header_close(); st.stop()
    tk = tk[(tk["_day"] >= lo) & (tk["_day"] <= hi)]
    n_tick = int(tk["_created"].sum())

    st.session_state["_jf"] = {"n_sub": n_sub, "n_int": n_int, "n_tick": n_tick,
                               "lo": str(lo), "hi": str(hi), "sub_df": _sub_df}

d = st.session_state.get("_jf")
if not d:
    st.info("Upload both CSVs, set the date range, and click **▶ Build funnel**.")
    report_header_close(); st.stop()

n_sub, n_int, n_tick = d["n_sub"], d["n_int"], d["n_tick"]
st.caption(f"📌 Window: **{d['lo']} → {d['hi']}**")

stages = [
    ("📝 Submissions", n_sub, "#7A5CFF"),
    ("📞 Interactions", n_int, "#0FB5AE"),
    ("🎫 Tickets created", n_tick, "#2DB84B"),
]
top = stages[0][1] or 1


def _pct(a, b):
    return (a / b * 100) if b else None


# ── overall conversion cards ────────────────────────────────────────────────────────
def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


_c = st.columns(4)
_card(_c[0], "Submissions", f"{n_sub:,}", "top of funnel", "#7A5CFF")
_si = _pct(n_int, n_sub)
_card(_c[1], "Sub → Interaction", f"{_si:.0f}%" if _si is not None else "—",
      f"{100-_si:.0f}% drop-off" if _si is not None else "—", "#0FB5AE")
_it = _pct(n_tick, n_int)
_card(_c[2], "Interaction → Ticket", f"{_it:.0f}%" if _it is not None else "—",
      f"{100-_it:.0f}% drop-off" if _it is not None else "—", "#2DB84B")
_ov = _pct(n_tick, n_sub)
_card(_c[3], "Overall (Sub → Ticket)", f"{_ov:.0f}%" if _ov is not None else "—",
      "end-to-end conversion", "#4C8DFF")
st.markdown("")

# ── funnel bars ─────────────────────────────────────────────────────────────────────
st.markdown("##### Funnel")
_html = '<div style="display:flex;flex-direction:column;gap:14px;">'
prev = None
for lab, cnt, color in stages:
    pct_top = (cnt / top * 100) if top else 0
    width = max(8, pct_top)
    conv = ""
    if prev is not None:
        c = (cnt / prev * 100) if prev else 0
        conv = f"→ {c:.0f}% from previous stage · {100 - c:.0f}% drop-off"
    _html += f'''<div>
      <div style="display:flex;justify-content:space-between;font-size:.82rem;color:#475467;margin-bottom:4px;">
        <span style="font-weight:800;">{lab}</span>
        <span>{cnt:,} &middot; {pct_top:.0f}% of top</span></div>
      <div style="background:#EEF1F6;border-radius:10px;overflow:hidden;height:38px;">
        <div style="width:{width}%;min-width:60px;background:{color};color:#fff;height:100%;
             display:flex;align-items:center;padding:0 14px;font-weight:800;border-radius:10px;">{cnt:,}</div></div>
      <div style="font-size:.74rem;color:#8792A2;margin-top:3px;">{conv}</div></div>'''
    prev = cnt
_html += '</div>'
st.markdown(_html, unsafe_allow_html=True)

st.markdown("")
_tbl = pd.DataFrame({
    "Stage": [s[0] for s in stages],
    "Count": [s[1] for s in stages],
    "% of top": [f"{(s[1]/top*100):.0f}%" for s in stages],
})
st.dataframe(_tbl, use_container_width=True, hide_index=True)
st.caption("Volume funnel: each stage is the total count in the window. Drop-off between two stages "
           "= 1 − (next stage ÷ previous stage). Because the sources aren't joined per person, a later "
           "stage can exceed an earlier one (e.g. more interactions than submissions) — that just means "
           "the stages draw from different populations, not a negative drop-off.")

# ── submission contact info (top of funnel) ─────────────────────────────────────────
_sub_df = d.get("sub_df")
if _sub_df is not None and not _sub_df.empty:
    st.markdown("##### 📝 Submission contact info")
    _s = st.text_input("Search submissions (name / email / phone…)").strip().lower()
    _sv = _sub_df
    if _s:
        _sv = _sv[_sv.apply(lambda r: _s in " ".join(str(x).lower() for x in r.values), axis=1)]
    st.caption(f"{len(_sv):,} of {len(_sub_df):,} submissions · all contact fields on the submission object")
    st.dataframe(_sv, use_container_width=True, hide_index=True, height=460)
    st.download_button("📥 Export submissions (CSV)", _sv.to_csv(index=False),
                       "submission_contact_info.csv", "text/csv", key="jf_sub_csv")

report_header_close()
