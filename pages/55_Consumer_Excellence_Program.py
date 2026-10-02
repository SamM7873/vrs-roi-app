import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Consumer Excellence Program", layout="wide", page_icon="⭐")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Consumer Excellence Program")

report_header("Consumer Excellence Program",
              "New live numbers since Sept 1 + UTM / self-reported referral attribution (by email)",
              section="Numbers")

NUM_OBJECT = "2-40974683"   # Number object
SUB_OBJECT = "2-49942763"   # submission form records
_key = "consumer_excellence_v1"

UTM_PROPS = ["utm_campaign", "utm_source", "utm_medium", "utm_content",
             "referral_source", "referral_source_b2b"]


def _norm(v):
    return str(v or "").strip().lower()


def _ms(d):
    return str(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000))


def _fmtd(v):
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        if v.isdigit():
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return v[:10]
    except Exception:
        return v


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
        last = str(batch[-1]["id"]); time.sleep(0.05)
    return out


st.markdown("New **live Number objects** created on/after the cutoff, joined by **email** to "
            "**UTM + self-reported referral** fields from Contacts and the submission-form object. "
            "`has_utm` flags whether any UTM attribution was found.")
st.caption("Note: the Snowflake registration-tracking join (promo_code / rt_utm_*) isn't available "
           "through the HubSpot API, so those columns are omitted.")

c1, c2 = st.columns([1, 1])
since = c1.date_input("Numbers created on/after", value=date(2026, 9, 1))
run = c2.button("▶ Run", type="primary")
c2.markdown("<div style='height:.3rem'></div>", unsafe_allow_html=True)

if run:
    nprops = ["number", "number_created_at", "number_status", "email", "first_name", "last_name",
              "state", "service_type", "usage_type", "registration_type", "referrer"]
    with dash_spinner("Reading new Number objects…"):
        nums = _seek(NUM_OBJECT, nprops, [
            {"propertyName": "number_created_at", "operator": "GTE", "value": _ms(since)}])
    # keep live only (status compared case-insensitively)
    nums = [o for o in nums if _norm(o.get("properties", {}).get("number_status")) == "live"]
    if not nums:
        st.warning("No live numbers found on/after that date."); report_header_close(); st.stop()

    emails = sorted({_norm(o.get("properties", {}).get("email")) for o in nums} - {""})

    # UTM / referral by email, from Contacts (0-1) and submissions (2-49942763)
    utm_by_email = defaultdict(dict)

    def _merge(em, props):
        d = utm_by_email[em]
        for k in UTM_PROPS + ["submission_form"]:
            v = (props.get(k) or "").strip()
            if v and not d.get(k):
                d[k] = v

    with dash_spinner("Reading UTM / referral from contacts…"):
        for i in range(0, len(emails), 100):
            chunk = emails[i:i + 100]
            for c in fetch_all("contacts", ["email"] + UTM_PROPS, filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                p = c.get("properties", {}); em = _norm(p.get("email"))
                if em:
                    _merge(em, p)
    with dash_spinner("Reading UTM / referral from submissions…"):
        for i in range(0, len(emails), 100):
            chunk = emails[i:i + 100]
            for s in fetch_all(SUB_OBJECT, ["email", "submission_form"] + UTM_PROPS,
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                p = s.get("properties", {}); em = _norm(p.get("email"))
                if em:
                    _merge(em, p)

    rows = []
    for o in nums:
        p = o.get("properties", {})
        em = _norm(p.get("email"))
        u = utm_by_email.get(em, {})
        has_utm = any(u.get(k) for k in ("utm_campaign", "utm_source", "utm_medium", "utm_content"))
        rows.append({
            "Number created": _fmtd(p.get("number_created_at")),
            "Phone": p.get("number") or "—",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Email": em or "—",
            "Service type": p.get("service_type") or "—",
            "Usage type": p.get("usage_type") or "—",
            "Registration type": p.get("registration_type") or "—",
            "State": p.get("state") or "—",
            "Number referrer": p.get("referrer") or "—",
            "UTM Campaign": u.get("utm_campaign") or "—",
            "UTM Source": u.get("utm_source") or "—",
            "UTM Medium": u.get("utm_medium") or "—",
            "UTM Content": u.get("utm_content") or "—",
            "Referral source (B2C)": u.get("referral_source") or "—",
            "Referral source B2B": u.get("referral_source_b2b") or "—",
            "Submission form": u.get("submission_form") or "—",
            "Has UTM": "Yes" if has_utm else "No",
        })
    df = pd.DataFrame(rows).sort_values(["Has UTM", "Number created"], ascending=[False, True])
    save_report(_key, {"df": df, "since": str(since)})

saved = load_report(_key)
if saved is None:
    st.info("Set the cutoff and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · numbers since {saved.get('since','')}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
hu = int((df["Has UTM"] == "Yes").sum())
k = st.columns(3)
_card(k[0], "📞 New live numbers", f"{N:,}", f"since {saved.get('since','')}", "#4C8DFF")
_card(k[1], "🎯 With UTM", f"{hu:,}", f"{hu/N*100:.0f}% attributed" if N else "—", "#2DB84B")
_card(k[2], "❓ No UTM", f"{N-hu:,}", f"{(N-hu)/N*100:.0f}% unattributed" if N else "—", "#E5484D")
st.markdown("")

st.markdown("##### New live numbers")
f1, f2, f3 = st.columns([1, 1.4, 2])
hup = f1.radio("Has UTM", ["All", "Yes", "No"], horizontal=True, key="cep_hasutm")
svc = f2.multiselect("Service type", sorted(x for x in df["Service type"].unique() if x != "—"), default=[])
q = f3.text_input("Search name / email / number / campaign").strip().lower()
view = df.copy()
if hup != "All":
    view = view[view["Has UTM"] == hup]
if svc:
    view = view[view["Service type"].isin(svc)]
if q:
    view = view[view.apply(lambda r: q in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=520)
st.download_button("📥 Export CSV", view.to_csv(index=False), "consumer_excellence.csv", "text/csv")

report_header_close()
