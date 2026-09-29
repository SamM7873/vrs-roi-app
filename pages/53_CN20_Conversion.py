import streamlit as st
import pandas as pd
import time
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="CN20 Conversion", layout="wide", page_icon="🔁")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("CN20 Conversion")

report_header("CN20 w/o Convo VRS: Conversion",
              "Submissions (CN20 campaign) → Registration (email) → Number (live VRS)",
              section="Numbers")

SUB_OBJECT = "2-49942763"   # submission form records
REG_OBJECT = "2-58833629"   # registration
NUM_OBJECT = "2-40974683"   # Number object
_key = "cn20_conversion_v1"

CAMPAIGN_DEFAULT = "51155747-VRS/IVCS Cross Sell via CN20 - US B2C Sign Up - Q3 2026"


@st.cache_data(ttl=3600, show_spinner=False)
def _list_props(obj):
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{obj}", headers=_H, timeout=30)
        if r.status_code == 200:
            return [p.get("name") for p in r.json().get("results", [])]
    except Exception:
        pass
    return []


st.markdown("Counts **submissions** in the CN20 cross-sell campaign, then follows "
            "**Submission → Registration** (matched by **email**), and **Registration → Number** "
            "(by **email or number**), keeping numbers whose **service type = VRS** and **status = Live**.")

prop_names = _list_props(SUB_OBJECT)
_utm = next((n for n in prop_names if n.lower() == "utm_campaign"), None) \
    or next((n for n in prop_names if "utm" in n.lower() and "campaign" in n.lower()), None) \
    or next((n for n in prop_names if "campaign" in n.lower()), "utm_campaign")

c1, c2 = st.columns([3, 1])
campaign = c1.text_input(f"UTM Campaign (submission field `{_utm}`)", value=CAMPAIGN_DEFAULT)
c2.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
run = c2.button("▶ Run", type="primary", use_container_width=True)


def _norm(v):
    return str(v or "").strip().lower()

if run:
    # 1) submissions in the campaign
    sub_props = [_utm, "hs_createdate"] + [p for p in ("email", "firstname", "lastname") if p in prop_names]
    with dash_spinner("Reading campaign submissions…"):
        subs = fetch_all(SUB_OBJECT, sub_props, filter_groups=[{"filters": [
            {"propertyName": _utm, "operator": "EQ", "value": campaign}]}])
    if not subs:
        st.warning("No submissions found for that campaign / field.")
        report_header_close(); st.stop()
    sub_emails = sorted({_norm(s.get("properties", {}).get("email")) for s in subs} - {""})

    # 2) registrations matched by email
    reg_by_email = defaultdict(list)
    with dash_spinner(f"Matching {len(sub_emails):,} emails to registrations…"):
        for i in range(0, len(sub_emails), 100):
            chunk = sub_emails[i:i + 100]
            for r in fetch_all(REG_OBJECT, ["email", "number", "first_name", "last_name"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                em = _norm(r.get("properties", {}).get("email"))
                if em:
                    reg_by_email[em].append(r.get("properties", {}))
    reg_emails = sorted(reg_by_email.keys())
    reg_numbers = sorted({str(p.get("number") or "").strip()
                          for ps in reg_by_email.values() for p in ps
                          if str(p.get("number") or "").strip()})

    # 3) Number objects by email OR number → keep VRS + Live
    live_emails, live_numbers, num_rows = set(), set(), {}
    nprops = ["number", "email", "service_type", "number_status"]

    def _keep(o):
        p = o.get("properties", {})
        if "vrs" not in _norm(p.get("service_type")):
            return None
        if _norm(p.get("number_status")) != "live":
            return None
        return p

    with dash_spinner("Matching registrations to live VRS numbers…"):
        for i in range(0, len(reg_emails), 100):
            chunk = reg_emails[i:i + 100]
            for o in fetch_all(NUM_OBJECT, nprops, filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}]):
                p = _keep(o)
                if p:
                    em = _norm(p.get("email")); nb = str(p.get("number") or "").strip()
                    if em:
                        live_emails.add(em)
                    if nb:
                        live_numbers.add(nb)
                        num_rows[nb] = p
        for i in range(0, len(reg_numbers), 100):
            chunk = reg_numbers[i:i + 100]
            for o in fetch_all(NUM_OBJECT, nprops, filter_groups=[{"filters": [
                    {"propertyName": "number", "operator": "IN", "values": chunk},
                    {"propertyName": "service_type", "operator": "EQ", "value": "VRS"}]}]):
                p = _keep(o)
                if p:
                    em = _norm(p.get("email")); nb = str(p.get("number") or "").strip()
                    if em:
                        live_emails.add(em)
                    if nb:
                        live_numbers.add(nb)
                        num_rows[nb] = p

    # 4) per-submission conversion
    rows = []
    for s in subs:
        p = s.get("properties", {})
        em = _norm(p.get("email"))
        regs = reg_by_email.get(em, [])
        registered = bool(regs)
        # live VRS if the reg email is a live VRS email, or any reg number is a live VRS number
        _rnums = [str(r.get("number") or "").strip() for r in regs if str(r.get("number") or "").strip()]
        live = (em in live_emails) or any(n in live_numbers for n in _rnums)
        _live_num = next((n for n in _rnums if n in live_numbers), "")
        rows.append({
            "Email": em or "—",
            "Name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
            "Created": str(p.get("hs_createdate") or "")[:10],
            "Registered": "Yes" if registered else "No",
            "VRS Number": _live_num or (", ".join(sorted(set(_rnums))) if _rnums else "—"),
            "Live VRS": "Yes" if live else "No",
        })
    df = pd.DataFrame(rows)
    # dedupe by email (one row per person)
    df = df.sort_values("Live VRS", ascending=False).drop_duplicates("Email", keep="first")
    save_report(_key, {"df": df, "campaign": campaign,
                       "n_sub": int(df.shape[0]),
                       "n_reg": int((df["Registered"] == "Yes").sum()),
                       "n_live": int((df["Live VRS"] == "Yes").sum())})

saved = load_report(_key)
if saved is None:
    st.info("Set the campaign and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · campaign: {saved.get('campaign','')[:48]}…")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()

_ns, _nr, _nl = saved.get("n_sub", len(df)), saved.get("n_reg", 0), saved.get("n_live", 0)


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


k = st.columns(4)
_card(k[0], "📝 Submissions", f"{_ns:,}", "in the CN20 campaign", "#7A5CFF")
_card(k[1], "📋 Registered", f"{_nr:,}", f"{_nr/_ns*100:.0f}% of submissions" if _ns else "—", "#0EA5E9")
_card(k[2], "📞 Live VRS", f"{_nl:,}", f"{_nl/_ns*100:.0f}% of submissions" if _ns else "—", "#2DB84B")
_card(k[3], "🎯 Conversion", f"{_nl/_ns*100:.0f}%" if _ns else "—", "submission → live VRS", "#4C8DFF")
st.markdown("")

st.markdown("##### Conversion funnel")
stages = [("📝 Submissions", _ns, "#7A5CFF"), ("📋 Registered", _nr, "#0EA5E9"), ("📞 Live VRS", _nl, "#2DB84B")]
_scale = max((s[1] for s in stages), default=1) or 1
_html = '<div style="display:flex;flex-direction:column;gap:12px;">'
for lab, cnt, color in stages:
    w = max(8, cnt / _scale * 100)
    _html += (f'<div><div style="font-size:.82rem;font-weight:800;color:#475467;margin-bottom:3px;">{lab}</div>'
              f'<div style="background:#EEF1F6;border-radius:10px;overflow:hidden;height:34px;">'
              f'<div style="width:{w}%;min-width:56px;background:{color};color:#fff;height:100%;'
              f'display:flex;align-items:center;padding:0 12px;font-weight:800;border-radius:10px;">{cnt:,}</div>'
              f'</div></div>')
_html += '</div>'
st.markdown(_html, unsafe_allow_html=True)
st.markdown("")

st.markdown("##### Submissions")
f1, f2, f3 = st.columns([1, 1, 2])
rp = f1.radio("Registered", ["All", "Yes", "No"], horizontal=True, key="cn20_reg")
lp = f2.radio("Live VRS", ["All", "Yes", "No"], horizontal=True, key="cn20_live")
q = f3.text_input("Search email / name / number").strip().lower()
view = df.copy()
if rp != "All":
    view = view[view["Registered"] == rp]
if lp != "All":
    view = view[view["Live VRS"] == lp]
if q:
    view = view[view.apply(lambda r: q in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {len(df):,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", view.to_csv(index=False), "cn20_conversion.csv", "text/csv")

report_header_close()
