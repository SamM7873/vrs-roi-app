import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone, timedelta
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
_key = "consumer_excellence_v4_vrsacq"

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

w1, w2 = st.columns([2.4, 1])
_win = w1.radio("Rolling window", [7, 14, 28, 30, 56, 60, 84, 90], index=2, horizontal=True,
                format_func=lambda d: f"{d}d", key="cep_win")
_custom = w2.checkbox("Custom start date", value=False, key="cep_custom")
if _custom:
    since = w2.date_input("Numbers created on/after", value=date(2026, 9, 1), key="cep_since")
else:
    since = date.today() - timedelta(days=int(_win))
    w1.caption(f"Numbers created on/after **{since}** (past {_win} days)")
run = st.button("▶ Run", type="primary")

if run:
    nprops = ["number", "number_created_at", "number_status", "email", "first_name", "last_name",
              "state", "service_type", "usage_type", "registration_type", "referrer", "portin_status",
              "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"]
    with dash_spinner("Reading new Number objects…"):
        allnums = _seek(NUM_OBJECT, nprops, [
            {"propertyName": "number_created_at", "operator": "GTE", "value": _ms(since)}])
    if not allnums:
        st.warning("No numbers found on/after that date."); report_header_close(); st.stop()

    # ── acquisition funnel: all new numbers = sign-ups, deduped by phone ──────────────
    def _has(v):
        return bool(str(v or "").strip())

    seen_ph, acq = set(), []
    for o in allnums:
        p = o.get("properties", {})
        if "vrs" not in _norm(p.get("service_type")):   # VRS sign-ups only
            continue
        ph = str(p.get("number") or "").strip() or ("id:" + str(o.get("id")))
        if ph in seen_ph:
            continue
        seen_ph.add(ph)
        acq.append(p)
    a_sign = len(acq)
    a_live = [p for p in acq if _norm(p.get("number_status")) == "live"]
    a_login = [p for p in a_live if _has(p.get("ursa_first_login"))]
    a_call = [p for p in a_login if _has(p.get("ursa_first_outbound_call"))]
    a_keep = [p for p in a_call if _has(p.get("ursa_second_outbound_call"))]
    acq_counts = {"sign": a_sign, "live": len(a_live), "login": len(a_login),
                  "call": len(a_call), "keep": len(a_keep)}

    # keep live only for the UTM attribution table (status compared case-insensitively)
    nums = [o for o in allnums if _norm(o.get("properties", {}).get("number_status")) == "live"]
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
        _ut = _norm(p.get("usage_type"))
        _useg = ("Personal" if "person" in _ut else
                 "Organisations" if any(x in _ut for x in ("organ", "business", "company")) else "—")
        _ported = _norm(p.get("portin_status")) not in ("", "none", "n/a", "not ported", "direct")
        rows.append({
            "Number created": _fmtd(p.get("number_created_at")),
            "Phone": p.get("number") or "—",
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Email": em or "—",
            "Type": _useg,
            "Number type": "Ported in" if _ported else "Direct (New)",
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
    save_report(_key, {"df": df, "since": str(since), "acq": acq_counts})

saved = load_report(_key)
if saved is None:
    st.info("Set the cutoff and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · numbers since {saved.get('since','')}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


# ── Acquisition funnel (dark) : Sign-ups → Live → First login → First call → Keep calling ──
_ac = saved.get("acq")
if _ac:
    _WHITE, _BLUE, _CYAN, _TEAL, _GREEN = "#E6EDF3", "#5B8DEF", "#4C9AE0", "#3FB07A", "#3FB950"

    def _acard(col, t, v, s, c):
        col.markdown(f"""<div style="border:1px solid #232A36;border-radius:16px;padding:18px 20px 16px;
            background:#121722;height:100%;">
            <div style="font-size:.74rem;font-weight:700;color:#C9D1D9;text-align:center;">{t}</div>
            <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:8px 0 6px;text-align:center;">{v}</div>
            <div style="font-size:.7rem;color:#8B949E;text-align:center;">{s}</div></div>""",
            unsafe_allow_html=True)

    sg, lv, lg, cl, kp = (_ac["sign"], _ac["live"], _ac["login"], _ac["call"], _ac["keep"])

    def _pct(n):
        return f"{n/sg*100:.0f}% of sign-ups" if sg else "—"

    st.markdown("##### 🚀 Acquisition funnel")
    st.caption("New VRS number registrations → activation milestones (deduped by number). "
               "Sign-ups = new VRS numbers in the window; stages are nested (each is a subset of the prior).")
    a = st.columns(5)
    _acard(a[0], "Sign-ups", f"{sg:,}", "new registrations", _WHITE)
    _acard(a[1], "Live", f"{lv:,}", _pct(lv), _BLUE)
    _acard(a[2], "First login", f"{lg:,}", _pct(lg), _CYAN)
    _acard(a[3], "First call", f"{cl:,}", _pct(cl), _TEAL)
    _acard(a[4], "Keep calling", f"{kp:,}", _pct(kp), _GREEN)

    try:
        import plotly.graph_objects as go
        # ── vertical bar funnel (one bar per stage) ──────────────────────────────
        stage_names = ["Sign-ups", "Live", "First login", "First call", "Keep calling"]
        stage_vals = [sg, lv, lg, cl, kp]
        stage_cols = [_WHITE, _BLUE, _CYAN, _TEAL, _GREEN]
        bar_text = [f"{v:,}<br>{(v/sg*100 if sg else 0):.0f}%" for v in stage_vals]
        bfig = go.Figure(go.Bar(
            x=stage_names, y=stage_vals, text=bar_text, textposition="outside",
            marker=dict(color=stage_cols, line=dict(color="#0D1117", width=0)),
            textfont=dict(color="#E6EDF3", size=13), cliponaxis=False))
        bfig.update_layout(
            height=340, margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor="#0D1117", plot_bgcolor="#0D1117",
            font=dict(color="#E6EDF3", size=12),
            yaxis=dict(showgrid=True, gridcolor="#232A36", zeroline=False,
                       tickfont=dict(color="#8B949E")),
            xaxis=dict(tickfont=dict(color="#C9D1D9")))
        st.plotly_chart(bfig, use_container_width=True, config={"displayModeBar": False})

        labels = [f"Sign-ups ({sg:,})", f"Live ({lv:,})", f"First login ({lg:,})",
                  f"First call ({cl:,})", f"Keep calling ({kp:,})"]
        # drop-off nodes between stages
        stages = [sg, lv, lg, cl, kp]
        node_labels = list(labels)
        node_colors = [_WHITE, _BLUE, _CYAN, _TEAL, _GREEN]
        src, tgt, val, lcol = [], [], [], []
        for i in range(4):
            keep = stages[i + 1]
            drop = stages[i] - stages[i + 1]
            if keep > 0:
                src.append(i); tgt.append(i + 1); val.append(keep)
                lcol.append("rgba(91,141,239,0.35)")
            if drop > 0:
                di = len(node_labels)
                node_labels.append(f"Dropped ({drop:,})")
                node_colors.append("#2B3444")
                src.append(i); tgt.append(di); val.append(drop)
                lcol.append("rgba(139,148,158,0.25)")
        fig = go.Figure(go.Sankey(
            node=dict(label=node_labels, color=node_colors, pad=18, thickness=16,
                      line=dict(color="#0D1117", width=0.5)),
            link=dict(source=src, target=tgt, value=val, color=lcol)))
        fig.update_layout(paper_bgcolor="#0D1117", plot_bgcolor="#0D1117",
                          font=dict(color="#E6EDF3", size=12), height=340,
                          margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        pass
    st.markdown("")


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


# ── toolbar: type · numbers (drive cards + table) ────────────────────────────────────
t1, t2 = st.columns(2)
typ = t1.radio("Type", ["All", "Personal", "Organisations"], horizontal=True, key="cep_type")
numt = t2.radio("Numbers", ["All", "Direct (New)", "Ported in"], horizontal=True, key="cep_numt")
base = df.copy()
if typ != "All" and "Type" in base.columns:
    base = base[base["Type"] == typ]
if numt != "All" and "Number type" in base.columns:
    base = base[base["Number type"] == numt]

N = len(base)
hu = int((base["Has UTM"] == "Yes").sum())
k = st.columns(3)
_card(k[0], "📞 New live numbers", f"{N:,}", f"since {saved.get('since','')} · {typ}/{numt}", "#4C8DFF")
_card(k[1], "🎯 With UTM", f"{hu:,}", f"{hu/N*100:.0f}% attributed" if N else "—", "#2DB84B")
_card(k[2], "❓ No UTM", f"{N-hu:,}", f"{(N-hu)/N*100:.0f}% unattributed" if N else "—", "#E5484D")
st.markdown("")

st.markdown("##### New live numbers")
f1, f2, f3 = st.columns([1, 1.4, 2])
hup = f1.radio("Has UTM", ["All", "Yes", "No"], horizontal=True, key="cep_hasutm")
svc = f2.multiselect("Service type", sorted(x for x in base["Service type"].unique() if x != "—"), default=[])
q = f3.text_input("Search name / email / number / campaign").strip().lower()
view = base.copy()
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
