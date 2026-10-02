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
_key = "consumer_excellence_v11_regat"


def _seg(container, label, options, key, default=None, format_func=None):
    """Segmented pill control (st.segmented_control) with a radio fallback."""
    default = default if default is not None else options[0]
    fn = getattr(container, "segmented_control", None)
    if fn is not None:
        kw = {"default": default, "key": key}
        if format_func:
            kw["format_func"] = format_func
        try:
            v = fn(label, options, **kw)
            return v if v is not None else default
        except Exception:
            pass
    kw = {"horizontal": True, "key": key + "_r"}
    if format_func:
        kw["format_func"] = format_func
    return container.radio(label, options, **kw)

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

with st.expander("ℹ️ How to read this page (plain-language guide)"):
    st.markdown("""
**What this page answers:** *Of the people who signed up for a VRS number recently, how many
actually got activated and started making calls — and where do they drop off?*

**Who is counted (the population)**
- **VRS numbers only** (`service_type` = VRS) — Convo Now / other services are excluded.
- **English only** (`language_preference` = English; blank is treated as English).
- **One row per phone number** (deduped). If a number has two records, we keep the **live** one.
- Within the **date window** you pick at the top (by *number created* date).

**The acquisition funnel (the 5 cards).** Each stage is a *subset* of the one before it, and the
big **%** is the share **of the previous stage** (with the count in parentheses):
| Stage | What it means |
|---|---|
| **Sign-ups** | New VRS + English numbers created in the window |
| **Live consumers** | …that have a working (Live) PSTN number ready to call |
| **First login rate** | …that then logged into the app (`ursa_first_login`) |
| **First-call rate** | …that then made their first outbound call (`ursa_first_outbound_call`) |
| **Keep calling** | …that made a **second** outbound call (`ursa_second_outbound_call`) — i.e. stuck around |

*Example:* "First-call rate 42% (95)" means 95 people made a first call, and that's 42% of the people
who had logged in. A **low %** at any stage = that's where consumers are dropping off.

**The small bars under each card** = that metric's **last 12 months** (by month the number was
created). The brightest bar is the current month. Hover a bar to see its value.

**The Sankey (flow diagram)** shows the same funnel left→right: the colored band is people who
**progressed**, the grey **"Dropped (n)"** branches are people who stopped at that stage.

**The Type / Numbers pills** re-slice the *entire* funnel, Sankey and tables live:
- **Type** — Personal (B2C consumers) vs Organisations.
- **Numbers** — Direct (New) = brand-new number, Ported in = brought from another carrier.

**The detail table** lists every number behind the funnel (name, email, number, status, the stage
they reached, registered/created/deleted dates, delete reason, and the three URSA call milestones),
with its own filters and a CSV export.

**The bottom "UTM attribution" section** takes the same live VRS numbers and shows how many have
marketing attribution (UTM tags) vs none — useful for "where did these sign-ups come from?".
""")

w1, w2 = st.columns([2.4, 1])
_win = _seg(w1, "Rolling window", [7, 14, 28, 30, 56, 60, 84, 90], "cep_win",
            default=28, format_func=lambda d: f"{d}d")
_custom = w2.checkbox("Custom start date", value=False, key="cep_custom")
def _pretty(d):
    return d.strftime("%b %-d, %Y")


if _custom:
    since = w2.date_input("Numbers created on/after", value=date(2026, 9, 1), key="cep_since")
    _until = date.today()
    w1.caption(f"📅 **{_pretty(since)} – {_pretty(_until)}**")
else:
    _until = date.today() - timedelta(days=1)            # last completed day (PST)
    since = _until - timedelta(days=int(_win) - 1)
    w1.caption(f"📅 past {_win} completed days (PST) · **{_pretty(since)} – {_pretty(_until)}**")
run = st.button("▶ Run", type="primary")

if run:
    nprops = ["number", "number_created_at", "number_status", "email", "first_name", "last_name",
              "state", "service_type", "usage_type", "registration_type", "referrer", "portin_status",
              "language_preference", "number_deleted_at", "deleted_reason", "registered_at",
              "ursa_first_login", "ursa_first_outbound_call", "ursa_second_outbound_call"]
    with dash_spinner("Reading new Number objects…"):
        allnums = _seek(NUM_OBJECT, nprops, [
            {"propertyName": "number_created_at", "operator": "GTE", "value": _ms(since)},
            {"propertyName": "number_created_at", "operator": "LT",
             "value": _ms(_until + timedelta(days=1))}])
    if not allnums:
        st.warning("No numbers found on/after that date."); report_header_close(); st.stop()

    # ── acquisition funnel: VRS + English, deduped by phone (preferring the LIVE record) ──
    def _has(v):
        return bool(str(v or "").strip())

    def _en_ok(v):                       # English (blank treated as English, app convention)
        return _norm(v) in ("en", "english", "")

    def _is_live(p):
        return _norm(p.get("number_status")) == "live"

    best = {}
    for o in allnums:
        p = o.get("properties", {})
        if "vrs" not in _norm(p.get("service_type")):          # VRS only
            continue
        if not _en_ok(p.get("language_preference")):           # English only
            continue
        ph = str(p.get("number") or "").strip() or ("id:" + str(o.get("id")))
        cur = best.get(ph)
        if cur is None or (_is_live(p) and not _is_live(cur)):  # prefer the live record
            best[ph] = p
    acq = list(best.values())
    a_sign = len(acq)
    a_live = [p for p in acq if _is_live(p)]
    a_login = [p for p in a_live if _has(p.get("ursa_first_login"))]
    a_call = [p for p in a_login if _has(p.get("ursa_first_outbound_call"))]
    a_keep = [p for p in a_call if _has(p.get("ursa_second_outbound_call"))]
    acq_counts = {"sign": a_sign, "live": len(a_live), "login": len(a_login),
                  "call": len(a_call), "keep": len(a_keep)}

    # per-number detail table for the funnel population (VRS + English, deduped)
    def _stage_of(p):
        if not _is_live(p):
            return "Sign-up (not live)"
        if not _has(p.get("ursa_first_login")):
            return "Live"
        if not _has(p.get("ursa_first_outbound_call")):
            return "First login"
        if not _has(p.get("ursa_second_outbound_call")):
            return "First call"
        return "Keep calling"

    def _seg_type(p):
        _ut = _norm(p.get("usage_type"))
        return ("Personal" if "person" in _ut else
                "Organisations" if any(x in _ut for x in ("organ", "business", "company")) else "—")

    def _seg_numt(p):
        return ("Ported in" if _norm(p.get("portin_status")) not in
                ("", "none", "n/a", "not ported", "direct") else "Direct (New)")

    acq_counts["rows"] = sorted((
        {
            "Name": f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—",
            "Email": p.get("email") or "—",
            "Number": p.get("number") or "—",
            "Status": p.get("number_status") or "—",
            "Stage reached": _stage_of(p),
            "Type": _seg_type(p),
            "Number type": _seg_numt(p),
            "Registered at": _fmtd(p.get("registered_at")) or "—",
            "Number created at": _fmtd(p.get("number_created_at")) or "—",
            "Number deleted at": _fmtd(p.get("number_deleted_at")) or "—",
            "Delete reason": p.get("deleted_reason") or "—",
            "ursa_first_login": _fmtd(p.get("ursa_first_login")) or "—",
            "ursa_first_outbound_call": _fmtd(p.get("ursa_first_outbound_call")) or "—",
            "ursa_second_outbound_call": _fmtd(p.get("ursa_second_outbound_call")) or "—",
        } for p in acq), key=lambda r: r["Name"])

    # ── 12-month history for the per-card sparklines (VRS numbers by created month) ────
    def _ym(v):
        v = str(v or "").strip()
        if not v:
            return ""
        try:
            if v.isdigit():
                return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m")
            return v[:7]
        except Exception:
            return v[:7]

    _t = date.today()
    _months, _y, _m = [], _t.year, _t.month
    for _ in range(12):
        _months.append(f"{_y:04d}-{_m:02d}")
        _m -= 1
        if _m == 0:
            _m, _y = 12, _y - 1
    _months = _months[::-1]
    _hstart = date(int(_months[0][:4]), int(_months[0][5:7]), 1)
    with dash_spinner("Reading 12-month history…"):
        hnums = _seek(NUM_OBJECT, ["number", "number_created_at", "number_status", "service_type",
                                   "language_preference", "ursa_first_login",
                                   "ursa_first_outbound_call", "ursa_second_outbound_call"],
                      [{"propertyName": "number_created_at", "operator": "GTE", "value": _ms(_hstart)}])
    _hist = {mo: {"sign": 0, "live": 0, "login": 0, "call": 0, "keep": 0} for mo in _months}
    _hbest = {}
    for o in hnums:
        p = o.get("properties", {})
        if "vrs" not in _norm(p.get("service_type")):
            continue
        if not _en_ok(p.get("language_preference")):
            continue
        ph = str(p.get("number") or "").strip() or ("id:" + str(o.get("id")))
        cur = _hbest.get(ph)
        if cur is None or (_is_live(p) and not _is_live(cur)):
            _hbest[ph] = p
    for p in _hbest.values():
        mo = _ym(p.get("number_created_at"))
        if mo not in _hist:
            continue
        _hist[mo]["sign"] += 1
        if _norm(p.get("number_status")) == "live":
            _hist[mo]["live"] += 1
            if _has(p.get("ursa_first_login")):
                _hist[mo]["login"] += 1
                if _has(p.get("ursa_first_outbound_call")):
                    _hist[mo]["call"] += 1
                    if _has(p.get("ursa_second_outbound_call")):
                        _hist[mo]["keep"] += 1
    acq_counts["hist"] = [{"month": mo, **_hist[mo]} for mo in _months]

    # UTM attribution table: VRS + English + Live + deduped by number (aligned with the funnel's Live)
    nums, _seen_nt = [], set()
    for o in allnums:
        p = o.get("properties", {})
        if "vrs" not in _norm(p.get("service_type")):
            continue
        if not _en_ok(p.get("language_preference")):
            continue
        if _norm(p.get("number_status")) != "live":
            continue
        ph = str(p.get("number") or "").strip() or ("id:" + str(o.get("id")))
        if ph in _seen_nt:
            continue
        _seen_nt.add(ph)
        nums.append(o)
    if not nums:
        st.warning("No live VRS numbers found on/after that date."); report_header_close(); st.stop()

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
    save_report(_key, {"df": df, "since": str(since), "until": str(_until), "acq": acq_counts})

saved = load_report(_key)
if saved is None:
    st.info("Set the cutoff and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    def _pp(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%b %-d, %Y")
        except Exception:
            return s
    _rng = f"{_pp(saved.get('since',''))} – {_pp(saved.get('until',''))}" if saved.get('until') else _pp(saved.get('since',''))
    st.caption(f"📌 Saved {saved_at_label(saved)} · numbers created {_rng}")
if df.empty:
    st.warning("No rows."); report_header_close(); st.stop()


# ── toolbar: Type · Numbers (drives the funnel, Sankey AND the table) ─────────────────
_tb1, _tb2 = st.columns(2)
typ = _seg(_tb1, "Type", ["All", "Personal", "Organisations"], "cep_type")
numt = _seg(_tb2, "Numbers", ["All", "Direct (New)", "Ported in"], "cep_numt")


# ── Acquisition funnel (dark) : Sign-ups → Live → First login → First call → Keep calling ──
_ac = saved.get("acq")
if _ac:
    _WHITE, _BLUE, _CYAN, _TEAL, _GREEN = "#E6EDF3", "#5B8DEF", "#4C9AE0", "#3FB07A", "#3FB950"
    sg, lv, lg, cl, kp = (_ac["sign"], _ac["live"], _ac["login"], _ac["call"], _ac["keep"])
    _hrows = _ac.get("hist") or []

    # Type/Numbers drive the funnel + Sankey — recompute gates from the filtered rows
    _frows = _ac.get("rows") or []
    if _frows:
        _ff = _frows
        if typ != "All":
            _ff = [r for r in _ff if r.get("Type") == typ]
        if numt != "All":
            _ff = [r for r in _ff if r.get("Number type") == numt]

        def _cnt(stages):
            return sum(1 for r in _ff if r.get("Stage reached") in stages)

        sg = len(_ff)
        lv = _cnt({"Live", "First login", "First call", "Keep calling"})
        lg = _cnt({"First login", "First call", "Keep calling"})
        cl = _cnt({"First call", "Keep calling"})
        kp = _cnt({"Keep calling"})

    def _pct(n):
        return f"{n/sg*100:.0f}% of sign-ups" if sg else "—"

    def _mlabel(mo):
        try:
            return datetime.strptime(mo, "%Y-%m").strftime("%b %Y")
        except Exception:
            return mo

    def _spark_fig(stage, color):
        import plotly.graph_objects as go
        xs = [_mlabel(r["month"]) for r in _hrows]
        ys = [r.get(stage, 0) for r in _hrows]
        bar_cols = [color if i == len(xs) - 1 else "#39414F" for i in range(len(xs))]
        f = go.Figure(go.Bar(x=xs, y=ys, marker_color=bar_cols,
                             hovertemplate="%{x}<br><b>%{y:,}</b><extra></extra>"))
        f.update_layout(height=74, margin=dict(l=4, r=4, t=2, b=2),
                        paper_bgcolor="#121722", plot_bgcolor="#121722",
                        xaxis=dict(visible=False), yaxis=dict(visible=False),
                        showlegend=False, bargap=0.28,
                        hoverlabel=dict(bgcolor="#1C2430", font=dict(color="#E6EDF3")))
        return f

    def _acard(col, t, v, s, c, stage):
        col.markdown(f"""<div style="border:1px solid #232A36;border-radius:16px 16px 0 0;
            border-bottom:none;padding:16px 18px 6px;background:#121722;">
            <div style="font-size:.74rem;font-weight:700;color:#C9D1D9;text-align:center;">{t}</div>
            <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:8px 0 4px;text-align:center;">{v}</div>
            <div style="font-size:.7rem;color:#8B949E;text-align:center;">{s}</div></div>""",
            unsafe_allow_html=True)
        if _hrows:
            col.plotly_chart(_spark_fig(stage, c), use_container_width=True,
                             config={"displayModeBar": False})
        col.markdown("""<div style="border:1px solid #232A36;border-top:none;border-radius:0 0 16px 16px;
            background:#121722;padding:0 18px 10px;margin-top:-18px;">
            <div style="font-size:.64rem;color:#6E7681;text-align:center;">1-year history</div></div>""",
            unsafe_allow_html=True)

    st.markdown("##### 🚀 Acquisition funnel")
    st.caption("New VRS (English) number registrations → activation milestones (deduped by number, "
               "preferring the live record). Sign-ups = new VRS + English numbers in the window; "
               "stages are nested (each is a subset of the prior). "
               "Bars show the last 12 months by number-created month — hover for the value.")
    def _rate(n, d):            # "91% (329)" — share of the previous gate
        return f"{n/d*100:.0f}% ({n:,})" if d else f"({n:,})"

    a = st.columns(5)
    _acard(a[0], "Sign-ups", f"{sg:,}", "new VRS/PSTN registrations", _WHITE, "sign")
    _acard(a[1], "Live consumers", _rate(lv, sg), "have a PSTN number ready to call / sign-ups", _BLUE, "live")
    _acard(a[2], "First login rate", _rate(lg, lv), "logged in to the app / live consumers", _CYAN, "login")
    _acard(a[3], "First-call rate", _rate(cl, lg), "made a first outbound call / consumers who logged in", _TEAL, "call")
    _acard(a[4], "Keep calling", _rate(kp, cl), "made a 2nd outbound call / first-call consumers", _GREEN, "keep")

    try:
        import plotly.graph_objects as go
        labels = [f"Sign-ups ({sg:,})", f"Live ({lv:,})", f"First login ({lg:,})",
                  f"First call ({cl:,})", f"Keep calling ({kp:,})"]
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

    # ── per-number detail table ───────────────────────────────────────────────────────
    _arows = _ff if _frows else (_ac.get("rows") or [])
    if _arows:
        _adf = pd.DataFrame(_arows)
        with st.expander(f"📋 Funnel detail — {len(_adf):,} numbers (name · email · number · URSA milestones)",
                         expanded=True):
            _sf1, _sf2, _sf3 = st.columns([1.3, 1.3, 2])
            _stg = _sf1.multiselect("Stage reached",
                                    ["Sign-up (not live)", "Live", "First login", "First call", "Keep calling"],
                                    default=[])
            _stat = _sf2.multiselect("Number status",
                                     sorted(x for x in _adf["Status"].unique() if x and x != "—"),
                                     default=[])
            _aq = _sf3.text_input("Search name / email / number", key="acq_tbl_q").strip().lower()
            _av = _adf.copy()
            if _stg:
                _av = _av[_av["Stage reached"].isin(_stg)]
            if _stat:
                _av = _av[_av["Status"].isin(_stat)]
            if _aq:
                _av = _av[_av.apply(lambda r: _aq in " ".join(str(x).lower() for x in r.values), axis=1)]
            st.caption(f"{len(_av):,} of {len(_adf):,}")
            st.dataframe(_av, use_container_width=True, hide_index=True, height=440)
            st.download_button("📥 Export funnel detail CSV", _av.to_csv(index=False),
                               "acquisition_funnel_detail.csv", "text/csv", key="acq_tbl_dl")
    st.markdown("")


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:1.9rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


# ── bottom table uses the same Type/Numbers toolbar selected above ───────────────────
base = df.copy()
if typ != "All" and "Type" in base.columns:
    base = base[base["Type"] == typ]
if numt != "All" and "Number type" in base.columns:
    base = base[base["Number type"] == numt]

N = len(base)
hu = int((base["Has UTM"] == "Yes").sum())
k = st.columns(3)
_card(k[0], "📞 New live VRS numbers", f"{N:,}", f"{_rng} · {typ}/{numt}", "#4C8DFF")
_card(k[1], "🎯 With UTM", f"{hu:,}", f"{hu/N*100:.0f}% attributed" if N else "—", "#2DB84B")
_card(k[2], "❓ No UTM", f"{N-hu:,}", f"{(N-hu)/N*100:.0f}% unattributed" if N else "—", "#E5484D")
st.markdown("")

st.markdown("##### New live VRS numbers")
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
