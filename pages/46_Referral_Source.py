import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Referral Source", layout="wide", page_icon="🔗")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Referral Source")

report_header("Referral Source",
              "referral_source & referral_source_b2b — from the Contact and submission-form objects",
              section="Analytics")

SUB_OBJECT = "2-49942763"     # submission form records
_key = "referral_source_v1"
FIELDS = ["referral_source", "referral_source_b2b"]


def _ms(d, end=False):
    t = datetime(d.year, d.month, d.day, 23 if end else 0, 59 if end else 0,
                 59 if end else 0, tzinfo=timezone.utc)
    return str(int(t.timestamp() * 1000))


@st.cache_data(ttl=3600, show_spinner=False)
def _prop_names(obj):
    """Property names available on an object (runtime discovery, app token)."""
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{obj}", headers=_H, timeout=30)
        if r.status_code == 200:
            return {p.get("name") for p in r.json().get("results", [])}
    except Exception:
        pass
    return set()


st.markdown("Counts contacts and submission-form records by **referral source**. It reads the "
            "`referral_source` and `referral_source_b2b` properties from both the **Contact** object "
            "and the **submission forms** custom object, over the chosen date range.")

# ── date range (default Sept 1 → today) ─────────────────────────────────────────────
_today = date.today()
c1, c2, c3 = st.columns([1.2, 1.2, 1.4])
start_d = c1.date_input("From", value=date(2026, 9, 1))
end_d = c2.date_input("To", value=_today)
which = c3.multiselect("Referral field(s)", ["referral_source", "referral_source_b2b"],
                       default=["referral_source", "referral_source_b2b"])
run = st.button("▶ Run", type="primary", disabled=(not which))

if run:
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    lo, hi = _ms(start_d), _ms(end_d, end=True)
    contact_props = _prop_names("contacts")
    sub_props_all = _prop_names(SUB_OBJECT)

    rows = []

    # 1) Contacts — filter by createdate in range, read whichever referral fields exist
    _cf = [f for f in FIELDS if f in contact_props]
    if _cf:
        _rprops = ["email", "firstname", "lastname", "createdate"] + _cf
        with dash_spinner("Reading contacts…"):
            for c in fetch_all("contacts", _rprops, filter_groups=[{"filters": [
                    {"propertyName": "createdate", "operator": "GTE", "value": lo},
                    {"propertyName": "createdate", "operator": "LTE", "value": hi}]}]):
                p = c.get("properties", {})
                rs = (p.get("referral_source") or "").strip()
                rb = (p.get("referral_source_b2b") or "").strip()
                if not (rs or rb):
                    continue
                rows.append({
                    "Source object": "Contact",
                    "Date": (p.get("createdate") or "")[:10],
                    "Name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
                    "Email": (p.get("email") or "").strip() or "—",
                    "referral_source": rs or "—",
                    "referral_source_b2b": rb or "—",
                })

    # 2) Submission forms — filter by hs_createdate in range
    _sf = [f for f in FIELDS if f in sub_props_all]
    if _sf:
        _rprops = ["hs_createdate"] + [p for p in ("email", "firstname", "lastname") if p in sub_props_all] + _sf
        with dash_spinner("Reading submission-form records…"):
            for s in fetch_all(SUB_OBJECT, _rprops, filter_groups=[{"filters": [
                    {"propertyName": "hs_createdate", "operator": "GTE", "value": lo},
                    {"propertyName": "hs_createdate", "operator": "LTE", "value": hi}]}]):
                p = s.get("properties", {})
                rs = (p.get("referral_source") or "").strip()
                rb = (p.get("referral_source_b2b") or "").strip()
                if not (rs or rb):
                    continue
                rows.append({
                    "Source object": "Submission form",
                    "Date": (p.get("hs_createdate") or "")[:10],
                    "Name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
                    "Email": (p.get("email") or "").strip() or "—",
                    "referral_source": rs or "—",
                    "referral_source_b2b": rb or "—",
                })

    df = pd.DataFrame(rows)
    _missing = [f for f in FIELDS if f not in contact_props and f not in sub_props_all]
    save_report(_key, {"df": df, "start": str(start_d), "end": str(end_d),
                       "which": which, "missing": _missing,
                       "on_contact": _cf, "on_sub": _sf})

saved = load_report(_key)
if saved is None:
    st.info("Pick a date range and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · {saved.get('start','')} → {saved.get('end','')} · "
               f"on Contact: {', '.join(saved.get('on_contact') or ['—'])} · "
               f"on Submission: {', '.join(saved.get('on_sub') or ['—'])}")
if saved.get("missing"):
    st.warning("These fields weren't found on either object (skipped): "
               + ", ".join(f"`{m}`" for m in saved["missing"]))
if df.empty:
    st.warning("No records with a referral source in this range.")
    report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
n_rs = int((df["referral_source"] != "—").sum())
n_rb = int((df["referral_source_b2b"] != "—").sum())
n_contact = int((df["Source object"] == "Contact").sum())
n_sub = int((df["Source object"] == "Submission form").sum())
k = st.columns(4)
_card(k[0], "🔗 Records with a source", f"{N:,}", f"{saved.get('start','')} → {saved.get('end','')}", "#7A5CFF")
_card(k[1], "referral_source", f"{n_rs:,}", "records with a value", "#0FB5AE")
_card(k[2], "referral_source_b2b", f"{n_rb:,}", "records with a value", "#4C8DFF")
_card(k[3], "By object", f"{n_contact:,} / {n_sub:,}", "Contact / Submission", "#2DB84B")
st.markdown("")


def _breakdown(field, title):
    if field not in which:
        return
    st.markdown(f"##### {title}")
    sub = df[df[field] != "—"]
    if sub.empty:
        st.caption("No values in this range."); return
    b = (sub.groupby(field).size().reset_index(name="Count").sort_values("Count", ascending=False))
    b["%"] = (b["Count"] / b["Count"].sum() * 100).round(1).astype(str) + "%"
    st.dataframe(b.rename(columns={field: title}), use_container_width=True, hide_index=True,
                 column_config={"Count": st.column_config.ProgressColumn(
                     "Count", min_value=0, max_value=int(b["Count"].max()), format="%d")})


bc1, bc2 = st.columns(2)
with bc1:
    _breakdown("referral_source", "referral_source")
with bc2:
    _breakdown("referral_source_b2b", "referral_source_b2b")

# records
st.markdown("##### Records")
f1, f2, f3 = st.columns([1.2, 1.6, 1.6])
objpick = f1.multiselect("Object", sorted(df["Source object"].unique()), default=[])
srcpick = f2.multiselect("referral_source", sorted(v for v in df["referral_source"].unique() if v != "—"),
                         default=[])
search = f3.text_input("Search name / email").strip().lower()
view = df.copy()
if objpick:
    view = view[view["Source object"].isin(objpick)]
if srcpick:
    view = view[view["referral_source"].isin(srcpick)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view.sort_values("Date", ascending=False), use_container_width=True, hide_index=True, height=440)
st.download_button("📥 Export CSV", view.to_csv(index=False), "referral_source.csv", "text/csv")

report_header_close()
