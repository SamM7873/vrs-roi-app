import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone, timedelta
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner, log_report_view,
                   save_report, load_report, saved_at_label)

st.set_page_config(page_title="Journey Funnel", layout="wide", page_icon="🫗")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Journey Funnel")

report_header("Journey Funnel",
              "Beginning → end: Submissions → Interactions (Convo360) → Tickets · with drop-off",
              section="Support")

SUB_OBJECT = "2-49942763"   # submission form records
NUM_OBJECT = "2-40974683"   # Number object
_JF_KEY = "journey_funnel_v16_colorder2"


@st.cache_data(ttl=3600, show_spinner=False)
def _sub_contact_props():
    """Discover the submission object's contact-info properties.

    Includes name/email/phone/address/state/zip, plus UTM fields, B2C & B2B
    service interest, and referral source. Excludes event-location fields
    (event city/state/name) and any plain 'city' field.
    """
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{SUB_OBJECT}", headers=_H, timeout=30)
        if r.status_code != 200:
            return []
        _kw = ("email", "phone", "mobile", "firstname", "lastname", "first_name", "last_name",
               "name", "contact", "address", "state", "zip", "country", "company",
               "utm", "service_interest", "b2c", "b2b", "referral")
        out = []
        for p in r.json().get("results", []):
            n = (p.get("name") or "")
            nl = n.lower()
            if "event" in nl or "city" in nl:      # drop event-location + city fields
                continue
            if any(k in nl for k in _kw):
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


def _norm_name(v):
    """Normalize a person name for loose matching (lowercase, collapse spaces)."""
    return " ".join(str(v or "").lower().split())


def _parse_conv(file):
    """Convo360 interaction export → date, type, and the customer name/number.

    For SIP/video calls the Customer Name field holds a phone number, so it is
    parsed into `_num` (last-10 digits). For call/chat it's a person name in
    `_cust`.
    """
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    dcol = _find(df.columns, "date")
    if not dcol:
        return "Convo360: no date column found."
    ccol = _find(df.columns, "customer name", "customer", "name")
    tcol = _find(df.columns, "type")
    acol = _find(df.columns, "agent", "rep")
    qcol = _find(df.columns, "customer query", "query", "reason")
    ctcol = _find(df.columns, "call time", "duration", "talk time")
    wtcol = _find(df.columns, "wait time", "wait")
    wcol = _find(df.columns, "website", "site", "url")
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    _raw = df[ccol].astype(str) if ccol else pd.Series([""] * len(df))
    # Bucket by the Customer Name itself (not the Type): if it's a phone number
    # → number bucket; otherwise → name bucket. This covers ALL types
    # (call, SIP, query, chat), incl. SIP rows that carry a name.
    _num10 = _raw.map(_dig10)
    _is_number = _num10.str.len().eq(10)
    df["_num"] = _num10.where(_is_number, "")
    df["_cust"] = _raw.where(~_is_number, "").map(_norm_name)
    def _type_lbl(v):
        u = str(v).upper()
        if "SIP" in u or "VIDEO" in u:
            return "SIP"
        if "CHAT" in u:
            return "Chat"
        if "QUERY" in u:
            return "Query"
        if "CALL" in u:
            return "Call"
        return str(v).strip() or "—"
    df["_type"] = df[tcol].map(_type_lbl) if tcol else ""
    df["_agent"] = df[acol].astype(str).str.split("@").str[0].str.strip() if acol else ""
    df["_query"] = df[qcol].astype(str).str.strip() if qcol else ""
    df["_calltime"] = df[ctcol].astype(str).str.strip() if ctcol else ""
    df["_wait"] = df[wtcol].astype(str).str.strip() if wtcol else ""
    df["_web"] = df[wcol].astype(str).str.strip() if wcol else ""
    df = df[df["_day"].notna()].copy()
    return df[["_day", "_cust", "_num", "_type", "_agent", "_query", "_calltime", "_wait", "_web"]]


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


def _dig10(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


def _hms(v):
    """Format a duration as 'Xh Ym Zs'. Accepts HH:MM:SS / MM:SS, or decimal minutes."""
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none", "n/a", "—"):
        return "—"
    secs = None
    if ":" in s:
        try:
            parts = [int(float(x)) for x in s.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            secs = parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception:
            return s
    else:
        try:
            secs = int(round(float(s) * 60))   # decimal minutes → seconds
        except Exception:
            return s
    h, rem = divmod(int(secs), 3600)
    m, sec = divmod(rem, 60)
    out = (f"{h}h " if h else "") + (f"{m}m " if (m or h) else "") + f"{sec}s"
    return out.strip()


@st.cache_data(ttl=3600, show_spinner=False)
def _owner_names():
    out = {}
    try:
        r = requests.get(f"{_B}/crm/v3/owners?limit=500", headers=_H, timeout=15)
        if r.status_code == 200:
            for o in r.json().get("results", []):
                nm = f"{(o.get('firstName') or '').strip()} {(o.get('lastName') or '').strip()}".strip()
                out[str(o["id"])] = nm or o.get("email", str(o["id"]))
    except Exception:
        pass
    return out


def _batch_read(obj, ids, props):
    out = {}
    ids = [str(x) for x in ids]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{obj}/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk], "properties": props}, timeout=60)
            if r.status_code in (200, 207):
                for o in r.json().get("results", []):
                    out[str(o["id"])] = o.get("properties", {})
        except requests.exceptions.RequestException:
            pass
    return out


def _assoc(from_obj, to_obj, from_ids):
    """v4 batch association {from_id: [to_ids]}."""
    from collections import defaultdict as _dd
    out = _dd(list)
    for i in range(0, len(from_ids), 100):
        chunk = [str(x) for x in from_ids[i:i + 100]]
        try:
            r = requests.post(f"{_B}/crm/v4/associations/{from_obj}/{to_obj}/batch/read",
                              headers=_H, json={"inputs": [{"id": s} for s in chunk]}, timeout=60)
            if r.status_code in (200, 207):
                for res in r.json().get("results", []):
                    fid = str(res.get("from", {}).get("id", ""))
                    for a in res.get("to", []):
                        tid = str(a.get("toObjectId") or a.get("id") or "")
                        if tid:
                            out[fid].append(tid)
        except requests.exceptions.RequestException:
            pass
    return out


def _acq_render(sg, lv, lg, cl, kp, note):
    """Render the 5 rate cards + drop-off Sankey for an acquisition funnel."""
    def _rate(a, b):
        return f"{a/b*100:.0f}% ({a:,})" if b else "—"

    def _acard(col, t, v, s, c):
        col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
            padding:14px 16px 12px;background:rgba(127,127,127,0.03);height:100%;">
            <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
            <div style="font-size:1.7rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
            <div style="font-size:.7rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)

    _ac = st.columns(5)
    _acard(_ac[0], "Sign-ups", f"{sg:,}", "new VRS/PSTN registrations", "#8792A2")
    _acard(_ac[1], "Live consumers", _rate(lv, sg), "PSTN number ready / sign-ups", "#4C8DFF")
    _acard(_ac[2], "First login rate", _rate(lg, lv), "logged in / live consumers", "#0EA5E9")
    _acard(_ac[3], "First-call rate", _rate(cl, lg), "first outbound / logged in", "#14B8A6")
    _acard(_ac[4], "Keep calling", _rate(kp, cl), "second outbound / first-call", "#22C55E")
    st.markdown("")
    st.markdown("**Where do consumers drop off on the way to calling?**")
    st.markdown(
        """<div style="display:flex;gap:20px;font-size:.8rem;color:#475467;margin:2px 0 6px;font-weight:600;">
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;background:#0EA5E9;
          margin-right:6px;"></span>Progressing</span>
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;background:#22C55E;
          margin-right:6px;"></span>Keep calling</span>
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;background:#94A3B8;
          margin-right:6px;"></span>Dropped off</span></div>""", unsafe_allow_html=True)
    try:
        import plotly.graph_objects as go
        labels = [f"Sign-ups  {sg:,}", f"Live consumers  {lv:,}", f"First login  {lg:,}",
                  f"First call  {cl:,}", f"Keep calling  {kp:,}",
                  f"Not live  {sg-lv:,}", f"Not logged in  {lv-lg:,}",
                  f"No first call  {lg-cl:,}", f"No second call yet  {cl-kp:,}"]
        node_colors = ["#6366F1", "#4C8DFF", "#0EA5E9", "#14B8A6", "#22C55E",
                       "#94A3B8", "#94A3B8", "#94A3B8", "#94A3B8"]
        src = [0, 0, 1, 1, 2, 2, 3, 3]
        tgt = [1, 5, 2, 6, 3, 7, 4, 8]
        val = [lv, sg-lv, lg, lv-lg, cl, lg-cl, kp, cl-kp]
        link_colors = ["rgba(76,141,255,0.5)", "rgba(148,163,184,0.3)",
                       "rgba(14,165,233,0.5)", "rgba(148,163,184,0.3)",
                       "rgba(20,184,166,0.5)", "rgba(148,163,184,0.3)",
                       "rgba(34,197,94,0.55)", "rgba(148,163,184,0.3)"]
        fig = go.Figure(go.Sankey(arrangement="snap",
            node=dict(label=labels, color=node_colors, pad=30, thickness=20,
                      line=dict(color="white", width=1)),
            link=dict(source=src, target=tgt, value=[max(0, v) for v in val], color=link_colors)))
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=10),
                          paper_bgcolor="white", plot_bgcolor="white",
                          font=dict(size=13, color="#1B2430"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as _e:
        st.caption(f"(Sankey unavailable: {_e})")
    st.caption(note)


# ── report mode ─────────────────────────────────────────────────────────────────────
_MODE_SIT = "Submission → Interaction → Ticket"
_MODE_ACQ = "Acquisition funnel — Sign-up → Live → First login → First call → Keep calling"
mode = st.selectbox("Report", [_MODE_SIT, _MODE_ACQ])

# ════════════════════════════════════════════════════════════════════════════════════
# ACQUISITION FUNNEL — Submission (Sign-up) → Contact → Number → URSA login/outbound
# ════════════════════════════════════════════════════════════════════════════════════
if mode == _MODE_ACQ:
    _AKEY = "journey_acq_v6_consumer"
    st.markdown("Where do **sign-ups** drop off on the way to calling? Follows **Submission (Sign-up) → "
                "Contact → Number** (service type **VRS**), then Live (**number status Live**) → the "
                "number's URSA milestones: **first login → first outbound call → second outbound call**.")
    _t = date.today()
    ac1, ac2, ac3 = st.columns(3)
    alo = ac1.date_input("Sign-ups from", value=_t - timedelta(days=28), key="acq_lo")
    ahi = ac2.date_input("to", value=_t, key="acq_hi")
    acut = ac3.date_input("New consumer on/after", value=date(2026, 9, 23), key="acq_cut",
                          help="Numbers created on/after this date = New consumer; earlier = Existing.")
    if alo > ahi:
        alo, ahi = ahi, alo
        st.warning("From was after To — swapped.")
    if st.button("▶ Build acquisition funnel", type="primary", key="acq_run"):
        with dash_spinner("Reading sign-ups (submissions)…"):
            _subs = fetch_all(SUB_OBJECT, ["hs_createdate", "email"], filter_groups=[{"filters": [
                {"propertyName": "hs_createdate", "operator": "GTE", "value": _ms(alo)},
                {"propertyName": "hs_createdate", "operator": "LTE", "value": _ms(ahi + timedelta(days=1))}]}])
        n_signup = len(_subs)
        _emails = sorted({(s.get("properties", {}).get("email") or "").strip().lower()
                          for s in _subs} - {""})
        _e2c = {}
        for i in range(0, len(_emails), 100):
            for c in fetch_all("contacts", ["email"], filter_groups=[{"filters": [
                    {"propertyName": "email", "operator": "IN", "values": _emails[i:i + 100]}]}]):
                em = (c.get("properties", {}).get("email") or "").strip().lower()
                if em:
                    _e2c.setdefault(em, str(c["id"]))
        with dash_spinner("Linking contacts → numbers…"):
            _c2n = _assoc("contacts", NUM_OBJECT, sorted(set(_e2c.values())))
        _nids = sorted({n for v in _c2n.values() for n in v})
        with dash_spinner(f"Reading {len(_nids):,} numbers (status + URSA milestones)…"):
            _numof = _batch_read(NUM_OBJECT, _nids, ["number", "number_status", "service_type",
                                 "usage_type", "number_created_at", "ursa_first_login",
                                 "ursa_first_outbound_call", "ursa_second_outbound_call"])

        def _fmtd(v):
            v = str(v or "").strip()
            if not v:
                return ""
            if v.isdigit():
                try:
                    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    return v
            return v[:10]

        # de-duplicate sign-ups: one per email (a person who submitted twice counts once);
        # blank-email submissions are kept individually (can't dedupe without a key)
        _persons = {}
        for s in _subs:
            em = (s.get("properties", {}).get("email") or "").strip().lower()
            _persons[em or f"sub:{s['id']}"] = em
        n_signup = len(_persons)
        n_live = n_login = n_call = n_keep = 0
        _arows = []
        for key, em in _persons.items():
            cid = _e2c.get(em) if em else None
            # VRS numbers only (service type must be VRS)
            nums = [_numof.get(n, {}) for n in _c2n.get(cid, [])] if cid else []
            nums = [p for p in nums if "vrs" in (p.get("service_type") or "").lower()]
            live = any((p.get("number_status") or "").strip().lower() == "live" for p in nums)
            login = live and any((p.get("ursa_first_login") or "") for p in nums)
            call = login and any((p.get("ursa_first_outbound_call") or "") for p in nums)
            keep = call and any((p.get("ursa_second_outbound_call") or "") for p in nums)
            n_live += int(live); n_login += int(login); n_call += int(call); n_keep += int(keep)
            _numbers = sorted({str(p.get("number") or "").strip() for p in nums} - {""})
            _stat = sorted({(p.get("number_status") or "").strip().title() for p in nums} - {""})
            _usage = sorted({(p.get("usage_type") or "").strip().title() for p in nums} - {""})
            _created = sorted({_fmtd(p.get("number_created_at")) for p in nums} - {""})
            # New (created on/after cutoff) vs Existing (before cutoff), by earliest number
            _cut = acut.strftime("%Y-%m-%d")
            _consumer = ("New" if (_created and min(_created) >= _cut) else
                         "Existing" if _created else "—")
            _stage = ("Keep calling" if keep else "First call" if call else "First login" if login
                      else "Live" if live else "Sign-up only")
            _arows.append({
                "Email": em or "(no email)",
                "Consumer": _consumer,
                "VRS Number(s)": ", ".join(_numbers) or "—",
                "Number Status": ", ".join(_stat) or "—",
                "Usage type": ", ".join(_usage) or "—",
                "Number created": ", ".join(_created) or "—",
                "Live": "Yes" if live else "No",
                "First login": "Yes" if login else "No",
                "First call": "Yes" if call else "No",
                "Keep calling": "Yes" if keep else "No",
                "Stage reached": _stage,
            })
        _adf = pd.DataFrame(_arows)
        save_report(_AKEY, {"n_signup": n_signup, "n_live": n_live, "n_login": n_login,
                            "n_call": n_call, "n_keep": n_keep, "lo": str(alo), "hi": str(ahi),
                            "df": _adf})

    ad = load_report(_AKEY)
    if not ad:
        st.info("Set the sign-up window and click **▶ Build acquisition funnel**.")
        report_header_close(); st.stop()
    if ad.get("saved_at"):
        st.caption(f"📌 Saved {saved_at_label(ad)} · sign-ups {ad['lo']} → {ad['hi']}")

    _adf = ad.get("df")
    # ── consumer segment drives the WHOLE funnel (cards + Sankey + table) ─────────────
    seg = st.radio("Consumer segment", ["All", "New", "Existing"], horizontal=True, key="acq_seg")
    _seg_df = _adf if _adf is not None else pd.DataFrame()
    if seg != "All" and _adf is not None and "Consumer" in _adf.columns:
        _seg_df = _adf[_adf["Consumer"] == seg]

    # derive the funnel counts from the (segmented) per-person data
    if _adf is not None and not _adf.empty:
        _sg = len(_seg_df)
        _lv = int((_seg_df["Live"] == "Yes").sum())
        _lg = int((_seg_df["First login"] == "Yes").sum())
        _cl = int((_seg_df["First call"] == "Yes").sum())
        _kp = int((_seg_df["Keep calling"] == "Yes").sum())
    else:   # older saved report without the df — fall back to stored aggregate (All only)
        _sg, _lv, _lg, _cl, _kp = (ad["n_signup"], ad["n_live"], ad["n_login"],
                                   ad["n_call"], ad["n_keep"])
    _acq_render(_sg, _lv, _lg, _cl, _kp,
                f"Segment: **{seg}** consumers. Sign-ups = **submissions** in the window "
                "(Submission → Contact → Number, VRS only). New = number created on/after the cutoff, "
                "Existing = before. Live = **number status Live**; then **ursa_first_login / "
                "ursa_first_outbound_call / ursa_second_outbound_call**. Each gate nests inside the previous.")

    # ── per-person detail table (same segment) ───────────────────────────────────────
    if _adf is not None and not _adf.empty:
        st.markdown("##### Sign-up detail")
        _sc1, _sc2 = st.columns([1.3, 2])
        _stopts = ["Sign-up only", "Live", "First login", "First call", "Keep calling"]
        _stpick = _sc1.multiselect("Stage reached (empty = all)",
                                   [s for s in _stopts if s in set(_seg_df["Stage reached"])],
                                   default=[], key="acq_stage_filter")
        _q = _sc2.text_input("Search email / number", key="acq_search").strip().lower()
        _v = _seg_df
        if _stpick:
            _v = _v[_v["Stage reached"].isin(_stpick)]
        if _q:
            _v = _v[_v.apply(lambda r: _q in " ".join(str(x).lower() for x in r.values), axis=1)]
        st.caption(f"{len(_v):,} of {len(_adf):,} sign-ups · segment: {seg}")
        st.dataframe(_v, use_container_width=True, hide_index=True, height=460)
        st.download_button("📥 Export sign-ups (CSV)", _v.to_csv(index=False),
                           "acquisition_signups.csv", "text/csv", key="acq_csv")
    report_header_close(); st.stop()

# ── inputs (Submission → Interaction → Ticket) ──────────────────────────────────────
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
    # ── stage 2 source first: interactions in the window (Convo360 CSV) ──
    cv = _parse_conv(conv_file)
    if isinstance(cv, str):
        st.error(cv); report_header_close(); st.stop()
    cv = cv[(cv["_day"] >= lo) & (cv["_day"] <= hi)]
    n_int = len(cv)
    _int_names = set(cv["_cust"].dropna()) - {""}          # call/chat customer names
    _int_numbers = set(cv["_num"].dropna()) - {""}         # SIP customer numbers (last-10)

    # name/number → interaction meta (type · agent · query) for the matched rows
    from collections import defaultdict as _ddict
    name_meta, num_meta = _ddict(list), _ddict(list)
    for _, rr in cv.iterrows():
        meta = (rr.get("_type", ""), rr.get("_agent", ""), rr.get("_query", ""),
                rr.get("_calltime", ""), rr.get("_wait", ""), rr.get("_web", ""))
        if rr.get("_cust"):
            name_meta[rr["_cust"]].append(meta)
        if rr.get("_num"):
            num_meta[rr["_num"]].append(meta)

    def _join(vals, limit=4):
        seen, out = set(), []
        for v in vals:
            v = str(v).strip()
            if v and v not in seen:
                seen.add(v); out.append(v)
        return ", ".join(out if limit is None else out[:limit])

    # ── stage 3: tickets created in the window (audit-log CSV) ──
    tk = _parse_tick(tick_file)
    if isinstance(tk, str):
        st.error(tk); report_header_close(); st.stop()
    tk = tk[(tk["_day"] >= lo) & (tk["_day"] <= hi)]
    n_tick = int(tk["_created"].sum())

    # ── stage 1: submissions created in the window (HubSpot custom object) ──
    _cprops = _sub_contact_props()

    def _col_order(item):
        lab = (item[1] or "").lower()
        # first match wins — specific before generic
        checks = [
            ("company domain", 15), ("company", 14),
            ("email", 1), ("mobile", 3), ("phone", 2),
            ("state", 4), ("city", 4), ("zip", 4), ("country", 4),
            ("utm source", 5), ("utm campaign", 6), ("utm medium", 7),
            ("utm content", 8), ("utm term", 9), ("utm", 9),
            ("b2c referral", 10), ("referral source b2c", 10),
            ("b2c service interest", 11), ("b2b service interest", 12),
            ("service interest", 11),
            ("referral source b2b", 13), ("b2b referral", 13), ("referral", 13),
            ("full name", 0), ("first name", 0), ("last name", 0), ("name", 0),
        ]
        for kw, pri in checks:
            if kw in lab:
                return (pri, lab)
        return (30, lab)
    _cprops = sorted(_cprops, key=_col_order)
    _cnames = [n for n, _ in _cprops]
    _fn = next((n for n in _cnames if n.lower() in ("firstname", "first_name")), None)
    _ln = next((n for n in _cnames if n.lower() in ("lastname", "last_name")), None)
    _nm = next((n for n in _cnames if n.lower() == "name"), None)
    _ph = next((n for n in _cnames if "phone" in n.lower() or "mobile" in n.lower()), None)
    _em = next((n for n in _cnames if n.lower() == "email"), None)
    with dash_spinner("Reading submission forms…"):
        subs = fetch_all(SUB_OBJECT, ["hs_createdate", "firstname", "lastname"] + _cnames,
                         filter_groups=[{"filters": [
                             {"propertyName": "hs_createdate", "operator": "GTE", "value": _ms(lo)},
                             {"propertyName": "hs_createdate", "operator": "LTE",
                              "value": _ms(hi + timedelta(days=1))}]}])
    n_sub = len(subs)

    # ── submission → contact → ticket (person match, via email) ──
    sub_emails = sorted({_norm_name(s.get("properties", {}).get(_em) or "") for s in subs
                         if _em and s.get("properties", {}).get(_em)})
    email_to_cid = {}
    for i in range(0, len(sub_emails), 100):
        chunk = sub_emails[i:i + 100]
        for c in fetch_all("contacts", ["email"], filter_groups=[{"filters": [
                {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
            em = (c.get("properties", {}).get("email") or "").strip().lower()
            if em:
                email_to_cid.setdefault(em, str(c["id"]))
    _cids = sorted(set(email_to_cid.values()))
    with dash_spinner("Linking contacts → tickets…"):
        cid_to_tids = _assoc("contacts", "tickets", _cids)
    # read the associated tickets (subject + owner) and resolve owner names
    _own = _owner_names()
    _all_tids = sorted({t for tids in cid_to_tids.values() for t in tids})
    _tk_of = _batch_read("tickets", _all_tids, ["subject", "hubspot_owner_id"]) if _all_tids else {}
    # contact → Number → phone (bridge to SIP call numbers)
    with dash_spinner("Linking contacts → numbers (phone bridge)…"):
        cid_to_nids = _assoc("contacts", NUM_OBJECT, _cids)
        _all_nids = sorted({n for v in cid_to_nids.values() for n in v})
        _num_of = _batch_read(NUM_OBJECT, _all_nids, ["number"]) if _all_nids else {}
        cid_to_phones = {cid: ({_dig10(_num_of.get(n, {}).get("number")) for n in nids} - {""})
                         for cid, nids in cid_to_nids.items()}

    # submission detail + interaction match (name for call/chat, number for SIP) + ticket match
    _sub_rows = []
    _n_match = _n_by_name = _n_by_num = _n_by_cnum = _n_has_tk = _n_int_tk = 0
    for s in subs:
        p = s.get("properties", {})
        cd = str(p.get("hs_createdate") or "")
        fn = p.get(_fn) or p.get("firstname") or ""
        ln = p.get(_ln) or p.get("lastname") or ""
        nm = _norm_name(f"{fn} {ln}") or _norm_name(p.get(_nm) or "")
        ph = _dig10(p.get(_ph)) if _ph else ""
        em = _norm_name(p.get(_em) or "") if _em else ""
        cid = email_to_cid.get(em)
        by_name = bool(nm) and nm in _int_names
        by_num = bool(ph) and ph in _int_numbers
        by_cnum = bool(cid) and bool(cid_to_phones.get(cid, set()) & _int_numbers)   # contact→number→SIP
        matched = by_name or by_num or by_cnum
        _n_match += int(matched); _n_by_name += int(by_name)
        _n_by_num += int(by_num); _n_by_cnum += int(by_cnum)
        _tids = cid_to_tids.get(cid, []) if cid else []
        has_ticket = bool(_tids)
        _n_has_tk += int(has_ticket)
        if matched and has_ticket:          # sequential: ticket AMONG those with an interaction
            _n_int_tk += 1
        _tk_subj = _join(((_tk_of.get(t, {}).get("subject") or f"#{t}") for t in _tids), limit=None)
        _tk_own = _join((_own.get(str(_tk_of.get(t, {}).get("hubspot_owner_id") or ""), "")
                         for t in _tids), limit=None)
        _mt = [x for x, ok in (("Name", by_name), ("Number", by_num), ("Contact#", by_cnum)) if ok]
        # gather the matched interaction rows' type/agent/query
        _metas = []
        if by_name:
            _metas += name_meta.get(nm, [])
        if by_num:
            _metas += num_meta.get(ph, [])
        if by_cnum:
            for _cph in (cid_to_phones.get(cid, set()) & _int_numbers):
                _metas += num_meta.get(_cph, [])
        # column order: SUBMISSION → INTERACTION → TICKET
        row = {"Created": cd[:10]}
        for n, lab in _cprops:                       # submission form fields
            row[lab] = p.get(n) or ""
        row.update({                                 # interaction fields
            "Had interaction": "Yes" if matched else "No",
            "Match type": " + ".join(_mt) or "—",
            "Interaction type": _join(m[0] for m in _metas) or "—",
            "Interaction agent": _join(m[1] for m in _metas) or "—",
            "Interaction query": _join(m[2] for m in _metas) or "—",
            "Call time": _join(_hms(m[3]) for m in _metas) or "—",
            "Wait time": _join(_hms(m[4]) for m in _metas) or "—",
            "Website": _join(m[5] for m in _metas) or "—",
        })
        row.update({                                 # ticket fields
            "Has ticket (via contact)": "Yes" if has_ticket else "No",
            "Ticket": _tk_subj or "—",
            "Ticket owner": _tk_own or "—",
        })
        _sub_rows.append(row)
    _sub_df = pd.DataFrame(_sub_rows)

    save_report(_JF_KEY, {"n_sub": n_sub, "n_int": n_int, "n_tick": n_tick,
                          "n_match": _n_match, "n_by_name": _n_by_name, "n_by_num": _n_by_num,
                          "n_by_cnum": _n_by_cnum, "n_has_tk": _n_has_tk, "n_int_tk": _n_int_tk,
                          "n_int_names": len(_int_names), "n_int_numbers": len(_int_numbers),
                          "n_sub_phone": int(sum(1 for s in subs if _ph and _dig10(s.get('properties', {}).get(_ph)))),
                          "n_sub_email": len(sub_emails),
                          "lo": str(lo), "hi": str(hi), "sub_df": _sub_df})

_RETAIN = 7 * 24 * 3600   # keep the saved record for 7 days
d = load_report(_JF_KEY)
if d and (time.time() - d.get("saved_at", 0)) > _RETAIN:
    d = None   # expired — older than 7 days
if not d:
    st.info("Upload both CSVs, set the date range, and click **▶ Build funnel**. "
            "The report is then **saved for 7 days** — you won't need to re-upload to view it again.")
    report_header_close(); st.stop()

n_sub, n_int, n_tick = d["n_sub"], d["n_int"], d["n_tick"]
if d.get("saved_at"):
    _rem = max(0, _RETAIN - (time.time() - d["saved_at"]))
    _left = f"~{int(_rem // 86400)}d {int((_rem % 86400) // 3600)}h left" if _rem >= 86400 \
        else f"~{int(_rem // 3600)}h left"
    st.caption(f"📌 Saved {saved_at_label(d)} · kept 7 days ({_left}) · window: "
               f"**{d['lo']} → {d['hi']}** · re-upload + Build to refresh")
else:
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


# Volume stages are DIFFERENT populations (all interactions, all tickets — not
# only those from submissions), so we show counts + a plain ratio, not
# conversion / drop-off (which would imply a per-person funnel — that's the
# Matched journey section below).
_c = st.columns(3)
_card(_c[0], "Submissions", f"{n_sub:,}", "form fills in window", "#7A5CFF")
_card(_c[1], "Interactions", f"{n_int:,}", "calls · chats · video in window", "#0FB5AE")
_card(_c[2], "Tickets created", f"{n_tick:,}", "new tickets in window", "#2DB84B")
st.caption("These are **volume counts** for the window — three separate populations, not the same "
           "people tracked through stages. For true per-person conversion & drop-off, see the "
           "**Matched journey** below.")
st.markdown("")

# ── funnel bars (counts only) ────────────────────────────────────────────────────────
st.markdown("##### Volume by stage")
_scale = max((s[1] for s in stages), default=1) or 1
_html = '<div style="display:flex;flex-direction:column;gap:14px;">'
for lab, cnt, color in stages:
    width = max(8, cnt / _scale * 100)
    _html += f'''<div>
      <div style="font-size:.82rem;color:#475467;margin-bottom:4px;font-weight:800;">{lab}</div>
      <div style="background:#EEF1F6;border-radius:10px;overflow:hidden;height:38px;">
        <div style="width:{width}%;min-width:60px;background:{color};color:#fff;height:100%;
             display:flex;align-items:center;padding:0 14px;font-weight:800;border-radius:10px;">{cnt:,}</div></div>
      </div>'''
_html += '</div>'
st.markdown(_html, unsafe_allow_html=True)

st.markdown("")
_tbl = pd.DataFrame({
    "Stage": [s[0] for s in stages],
    "Count": [s[1] for s in stages],
})
st.dataframe(_tbl, use_container_width=True, hide_index=True)
st.caption("Volume counts for the window. The three sources aren't joined per person, so a later "
           "stage can exceed an earlier one (more interactions/tickets than submissions) — they draw "
           "from different populations. True per-person conversion & drop-off is the Matched journey below.")

# ── matched journey (per person) ─────────────────────────────────────────────────────
_nm_match = d.get("n_match")
_tk_match = d.get("n_int_tk")          # tickets AMONG those with an interaction (sequential)
if _nm_match is not None:
    st.markdown("##### 🔗 Matched journey — Submission → Interaction → Ticket (per person)")
    _mpct = _pct(_nm_match, n_sub)
    _tpct = _pct(_tk_match or 0, _nm_match)
    _mc = st.columns(4)
    _card(_mc[0], "Submissions", f"{n_sub:,}", "start", "#7A5CFF")
    _card(_mc[1], "Had an interaction", f"{_nm_match:,}",
          f"{_mpct:.1f}% · {100-_mpct:.1f}% drop-off" if _mpct is not None else "—", "#0FB5AE")
    _card(_mc[2], "…and a ticket", f"{_tk_match or 0:,}",
          f"{_tpct:.0f}% of those with an interaction" if _tpct is not None else "—", "#2DB84B")
    _ovm = _pct(_tk_match or 0, n_sub)
    _card(_mc[3], "Overall (Sub → Ticket)", f"{_ovm:.1f}%" if _ovm is not None else "—",
          "end-to-end", "#4C8DFF")

    # diagnostic — see where the interaction match is (or isn't) landing
    with st.expander("🔧 Match diagnostic (why these counts)", expanded=False):
        st.markdown(
            f"- Interaction **names** (call/chat) seen: **{d.get('n_int_names',0):,}** · "
            f"submissions matched by name: **{d.get('n_by_name',0):,}**\n"
            f"- Interaction **numbers** (SIP) seen: **{d.get('n_int_numbers',0):,}** · "
            f"submissions with a phone: **{d.get('n_sub_phone',0):,}** · matched by form phone: "
            f"**{d.get('n_by_num',0):,}** · matched by **Contact→Number** phone: "
            f"**{d.get('n_by_cnum',0):,}**\n"
            f"- Submissions with an **email**: **{d.get('n_sub_email',0):,}** · with a **ticket via "
            f"contact** (any interaction or not): **{d.get('n_has_tk',0):,}**")
        st.caption("If 'matched by name' is tiny, Convo360 call/chat Customer Names are likely "
                   "first-name-only (they won't equal a full submission name). If 'matched by number' "
                   "is 0, the submission phone field may not be detected or SIP names aren't numbers.")
    # ── Sankey flow: progression vs drop-off ──────────────────────────────────────
    _nm = _nm_match or 0
    _tk = _tk_match or 0
    _no_int = max(0, n_sub - _nm)
    _no_tk = max(0, _nm - _tk)
    # palette
    _START, _PROG, _WIN, _DROP = "#6366F1", "#0EA5E9", "#22C55E", "#94A3B8"
    st.markdown("**Acquisition funnel — where do submissions drop off on the way to a ticket?**")
    st.markdown(
        f"""<div style="display:flex;gap:20px;align-items:center;font-size:.8rem;color:#475467;
        margin:2px 0 6px;font-weight:600;">
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;
          background:{_PROG};margin-right:6px;"></span>Progressing</span>
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;
          background:{_WIN};margin-right:6px;"></span>Reached a ticket</span>
        <span><span style="display:inline-block;width:11px;height:11px;border-radius:3px;
          background:{_DROP};margin-right:6px;"></span>Dropped off</span></div>""",
        unsafe_allow_html=True)
    try:
        import plotly.graph_objects as go
        labels = [f"Submissions  {n_sub:,}", f"Had interaction  {_nm:,}",
                  f"Reached a ticket  {_tk:,}", f"No interaction  {_no_int:,}",
                  f"No ticket  {_no_tk:,}"]
        node_colors = [_START, _PROG, _WIN, _DROP, _DROP]
        src = [0, 0, 1, 1]
        tgt = [1, 3, 2, 4]
        val = [_nm, _no_int, _tk, _no_tk]
        link_colors = ["rgba(14,165,233,0.55)", "rgba(148,163,184,0.28)",
                       "rgba(34,197,94,0.60)", "rgba(148,163,184,0.35)"]
        fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(label=labels, color=node_colors, pad=40, thickness=22,
                      line=dict(color="white", width=1),
                      hovertemplate="%{label}<extra></extra>"),
            link=dict(source=src, target=tgt, value=val, color=link_colors,
                      hovertemplate="%{source.label} → %{target.label}<br>%{value:,}<extra></extra>")))
        fig.update_layout(height=480, margin=dict(l=10, r=10, t=10, b=10),
                          paper_bgcolor="white", plot_bgcolor="white",
                          font=dict(size=13, color="#1B2430", family="Inter, system-ui, sans-serif"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as _e:
        st.caption(f"(Sankey unavailable: {_e})")

    st.caption("Per-person match. **Interaction:** a submission matches when its **name** matches a "
               "Convo360 call/chat Customer Name, **or** its **phone number** matches a SIP call's "
               "number (last-10 digits). **Ticket:** the submission's **email → Contact → associated "
               "Ticket** (HubSpot). Green = progressing to a ticket · dark = dropped off. "
               "Matching is loose (names/emails), so treat as a guide, not exact.")
    st.markdown("")

# ── submission contact info (top of funnel) ─────────────────────────────────────────
_sub_df = d.get("sub_df")
if _sub_df is not None and not _sub_df.empty:
    st.markdown("##### 📝 Submission contact info")
    _fc1, _fc2, _fc3 = st.columns([1, 1.3, 2])
    _match_col = "Had interaction"
    if _match_col in _sub_df.columns:
        _mfilt = _fc1.radio("Had interaction", ["All", "Yes", "No"],
                            horizontal=True, key="jf_match_filter")
    else:
        _mfilt = "All"
    if "Match type" in _sub_df.columns:
        _mtopts = sorted(v for v in _sub_df["Match type"].unique() if v and v != "—")
        _mtpick = _fc2.multiselect("Match type (empty = all)", _mtopts, default=[], key="jf_mt_filter",
                                   help="Name = call/chat name · Number = form phone · Contact# = Contact→Number phone")
    else:
        _mtpick = []
    # interaction type filter — always offer the full canonical set (+ any extras present)
    if "Interaction type" in _sub_df.columns:
        _present = {t.strip() for v in _sub_df["Interaction type"] for t in str(v).split(",")
                    if t.strip() and t.strip() != "—"}
        _ittoks = ["Call", "SIP", "Chat", "Query"] + \
                  sorted(_present - {"Call", "SIP", "Chat", "Query"})
        _itpick = _fc3.multiselect("Interaction type (empty = all)", _ittoks, default=[],
                                   key="jf_it_filter")
    else:
        _itpick = []
    _s = st.text_input("Search submissions (name / email / phone / query…)").strip().lower()
    _sv = _sub_df
    if _mfilt != "All" and _match_col in _sv.columns:
        _sv = _sv[_sv[_match_col] == _mfilt]
    if _mtpick:
        _sv = _sv[_sv["Match type"].isin(_mtpick)]
    if _itpick:
        _sv = _sv[_sv["Interaction type"].apply(lambda v: any(t in str(v) for t in _itpick))]
    if _s:
        _sv = _sv[_sv.apply(lambda r: _s in " ".join(str(x).lower() for x in r.values), axis=1)]
    st.caption(f"{len(_sv):,} of {len(_sub_df):,} submissions · all contact fields on the submission object")
    st.dataframe(_sv, use_container_width=True, hide_index=True, height=460)
    st.download_button("📥 Export submissions (CSV)", _sv.to_csv(index=False),
                       "submission_contact_info.csv", "text/csv", key="jf_sub_csv")

report_header_close()
