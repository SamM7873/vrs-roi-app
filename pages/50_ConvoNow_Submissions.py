import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone, timedelta, time as dtime
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Convo Now Submissions", layout="wide", page_icon="📝")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Convo Now Submissions")

report_header("Convo Now Submissions",
              "Submission forms (create date) → Contact (email = email) → Number · Convo Now & VRS",
              section="Customers")

SUB_OBJECT = "2-49942763"   # submission form records
NUM_OBJECT = "2-40974683"   # Number object
_key = "convo_now_subs_v2_vrs"

WINDOWS = {"Last 3 months": 3, "Last 6 months": 6, "Last 9 months": 9,
           "Last 12 months": 12, "Last 24 months": 24}


def _fmt_created(v):
    """hs_createdate (ISO string or epoch ms) → 'YYYY-MM-DD HH:MM' in Central Time."""
    if not v:
        return ""
    try:
        if str(v).isdigit():
            dt = datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        off = -5 if 3 <= dt.month <= 11 else -6
        return dt.astimezone(timezone(timedelta(hours=off))).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(v)


def _months_ago_ms(n):
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12; y -= 1
    return str(int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000))


@st.cache_data(ttl=3600, show_spinner=False)
def _list_props(obj):
    try:
        r = requests.get(f"{_B}/crm/v3/properties/{obj}", headers=_H, timeout=30)
        if r.status_code == 200:
            return [p.get("name") for p in r.json().get("results", [])]
    except Exception:
        pass
    return []


def _assoc(from_obj, to_obj, from_ids):
    out = defaultdict(list)
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
        time.sleep(0.08)
    return out


def _seek(obj, props, filters):
    url = f"{_B}/crm/v3/objects/{obj}/search"
    out, last = [], "0"
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(6):
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
        last = str(batch[-1]["id"]); time.sleep(0.06)
    return out


def _batch_read(obj, ids, props):
    out = {}
    ids = [str(x) for x in ids]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            r = requests.post(f"{_B}/crm/v3/objects/{obj}/batch/read", headers=_H,
                              json={"inputs": [{"id": c} for c in chunk], "properties": props},
                              timeout=60)
            if r.status_code in (200, 207):
                for o in r.json().get("results", []):
                    out[str(o["id"])] = o.get("properties", {})
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.06)
    return out


def _is_convo_now(st_val):
    return "convo now" in str(st_val or "").lower()


def _is_vrs(st_val):
    return "vrs" in str(st_val or "").lower()


prop_names = _list_props(SUB_OBJECT)

st.markdown("Reads **submission form** records by **create date**, follows **Submission → Contact** "
            "(by CRM association *and* by matching **email = email**), then **Contact → Number**, and "
            "checks each for a **Convo Now** and a **VRS** number. No monthly values — just the number check.")

# ── create-date filter (drives the whole page) ──────────────────────────────────────
c1, c2 = st.columns([1.3, 1])
window_label = c1.selectbox("Submissions created in", list(WINDOWS.keys()), index=1)
use_custom = c2.checkbox("Custom date range", value=False)
created_lo = created_hi = None
_range_note = ""
if use_custom:
    d1, t1, d2, t2 = st.columns(4)
    _today = date.today()
    sd = d1.date_input("From date", value=_today.replace(day=1))
    stime = t1.time_input("From time", value=dtime(0, 0))
    ed = d2.date_input("To date", value=_today)
    etime = t2.time_input("To time", value=dtime(23, 59))

    def _ct_to_ms(d, t):
        off = -5 if 3 <= d.month <= 11 else -6
        dt = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=timezone(timedelta(hours=off)))
        return str(int(dt.timestamp() * 1000))
    created_lo = _ct_to_ms(sd, stime)
    created_hi = _ct_to_ms(ed, etime)
    if int(created_lo) > int(created_hi):
        created_lo, created_hi = created_hi, created_lo
        st.warning("From was after To — I swapped them so the range isn't empty.")
    _range_note = f"{sd} {stime.strftime('%H:%M')} → {ed} {etime.strftime('%H:%M')} CT"
    st.caption(f"Submissions created {_range_note}")

run = st.button("▶ Run", type="primary")

if run:
    # 1) submission records in the create-date window
    sub_props = ["hs_createdate"] + [p for p in ("email", "firstname", "lastname") if p in prop_names]
    if use_custom:
        _sub_filters = []
        if created_lo:
            _sub_filters.append({"propertyName": "hs_createdate", "operator": "GTE", "value": created_lo})
        if created_hi:
            _sub_filters.append({"propertyName": "hs_createdate", "operator": "LTE", "value": created_hi})
    else:
        _sub_filters = [{"propertyName": "hs_createdate", "operator": "GTE",
                         "value": _months_ago_ms(WINDOWS[window_label])}]
    with dash_spinner("Reading submissions…"):
        subs = fetch_all(SUB_OBJECT, sub_props, filter_groups=[{"filters": _sub_filters}])
    if not subs:
        st.warning("No submission records found for the chosen date range.")
        report_header_close(); st.stop()
    st.caption(f"{len(subs):,} submissions in range")

    sub_ids = [str(s["id"]) for s in subs]
    sub_meta = {}
    for s in subs:
        p = s.get("properties", {})
        sub_meta[str(s["id"])] = {
            "email": (p.get("email") or "").strip().lower(),
            "created": _fmt_created(p.get("hs_createdate")),
            "name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip()}

    # 2) Submission → Contact : CRM association ∪ email = email
    with dash_spinner(f"Linking {len(sub_ids):,} submissions to contacts…"):
        sub_to_cids = _assoc(SUB_OBJECT, "contacts", sub_ids)
        sub_emails = sorted({m["email"] for m in sub_meta.values() if m["email"]})
        email_to_cid = {}
        for i in range(0, len(sub_emails), 100):
            chunk = sub_emails[i:i + 100]
            for c in fetch_all("contacts", ["email", "firstname", "lastname", "state"],
                               filter_groups=[{"filters": [
                                   {"propertyName": "email", "operator": "IN", "values": chunk}]}]):
                em = (c.get("properties", {}).get("email") or "").strip().lower()
                if em:
                    email_to_cid.setdefault(em, str(c["id"]))
        for sid, m in sub_meta.items():
            cid = email_to_cid.get(m["email"])
            if cid and cid not in sub_to_cids[sid]:
                sub_to_cids[sid].append(cid)
        all_cids = sorted({c for cids in sub_to_cids.values() for c in cids})
        contact_of = _batch_read("contacts", all_cids, ["email", "firstname", "lastname", "state"])

    # resolve one email/name/state per submission (prefer the contact record)
    sub_person = {}
    for sid, meta in sub_meta.items():
        email, name, state = meta["email"], meta["name"], ""
        for cid in sub_to_cids.get(sid, []):
            cm = contact_of.get(cid, {})
            state = state or (cm.get("state") or "").strip()
            if cm.get("email"):
                email = (cm.get("email") or "").strip().lower()
                name = name or f"{(cm.get('firstname') or '').strip()} {(cm.get('lastname') or '').strip()}".strip()
                break
        sub_person[sid] = {"email": email, "name": name, "created": meta["created"], "state": state}

    # 3) Contact → Number (association) ∪ Number.email = email ; keep Convo Now only
    nprops = ["number", "email", "service_type", "number_status", "account_status"]
    num_of = {}
    cid_to_nids = _assoc("contacts", NUM_OBJECT, all_cids)
    assoc_nids = sorted({n for nids in cid_to_nids.values() for n in nids})
    if assoc_nids:
        with dash_spinner(f"Reading {len(assoc_nids):,} associated Number records…"):
            num_of.update(_batch_read(NUM_OBJECT, assoc_nids, nprops))

    # email fallback against the Number object (VRS + Convo Now numbers whose own email matches)
    want_emails = {p["email"] for p in sub_person.values() if p["email"]}
    email_to_nids = defaultdict(list)
    if want_emails:
        with dash_spinner("Matching VRS / Convo Now numbers by email…"):
            for rr in _seek(NUM_OBJECT, nprops,
                            [{"propertyName": "service_type", "operator": "IN",
                              "values": ["VRS", "Convo Now"]}]):
                pp = rr.get("properties", {})
                em = (pp.get("email") or "").strip().lower()
                if em in want_emails:
                    nid = str(rr["id"])
                    num_of[nid] = pp
                    email_to_nids[em].append(nid)

    def _nums_for(nids, kind):
        """(numbers, statuses) for a submission's numbers of the given service kind."""
        test = _is_convo_now if kind == "cn" else _is_vrs
        sel = [n for n in nids if test(num_of.get(n, {}).get("service_type"))]
        numbers = [x for x in (str(num_of.get(n, {}).get("number") or "").strip() for n in sel) if x]
        statuses = sorted({s for s in ((num_of.get(n, {}).get("number_status")
                          or num_of.get(n, {}).get("account_status") or "").strip().title()
                          for n in sel if num_of.get(n)) if s})
        return sel, numbers, statuses

    rows = []
    for sid, p in sub_person.items():
        nids = {n for cid in sub_to_cids.get(sid, []) for n in cid_to_nids.get(cid, [])}
        nids |= set(email_to_nids.get(p["email"], []))
        cn_sel, cn_nums, cn_stat = _nums_for(nids, "cn")
        vrs_sel, vrs_nums, vrs_stat = _nums_for(nids, "vrs")
        rows.append({
            "Created": p["created"] or "—",
            "Month": (p["created"] or "")[:7] or "—",
            "Name": p["name"] or "—",
            "Email": p["email"] or "—",
            "State": p["state"] or "—",
            "Convo Now Number(s)": ", ".join(cn_nums) or "—",
            "Convo Now Status": ", ".join(cn_stat) or "—",
            "Has Convo Now": "Yes" if cn_sel else "No",
            "VRS Number(s)": ", ".join(vrs_nums) or "—",
            "VRS Status": ", ".join(vrs_stat) or "—",
            "Has VRS": "Yes" if vrs_sel else "No",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "window": window_label if not use_custom else _range_note})

saved = load_report(_key)
if saved is None:
    st.info("Pick a create-date range and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · created: **{saved.get('window','')}**")
if df.empty:
    st.warning("No submissions."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


# ── filters ─────────────────────────────────────────────────────────────────────────
f1, f2, f3 = st.columns([1.2, 1.2, 2])
mopts = sorted(m for m in df["Month"].unique() if m and m != "—")
mpick = f1.multiselect("Create month", mopts, default=[])
_NO_STATE = "(No state)"
_states = sorted(s for s in df["State"].unique() if s and s != "—")
_state_opts = _states + ([_NO_STATE] if (df["State"] == "—").any() else [])
stpick = f2.multiselect("State", _state_opts, default=[])
search = f3.text_input("Search name / email / number").strip().lower()

view = df.copy()
if mpick:
    view = view[view["Month"].isin(mpick)]
if stpick:
    _sel = [("—" if s == _NO_STATE else s) for s in stpick]
    view = view[view["State"].isin(_sel)]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]

TOTAL = len(df)
N = len(view)
n_cn = int((view["Has Convo Now"] == "Yes").sum())
n_vrs = int((view["Has VRS"] == "Yes").sum()) if "Has VRS" in view.columns else 0
_cn_pct = (n_cn / N * 100) if N else None
_vrs_pct = (n_vrs / N * 100) if N else None
_filtered = bool(mpick or stpick or search)
k = st.columns(5)
_card(k[0], "📝 Submissions", f"{N:,}", (f"of {TOTAL:,} total" if _filtered else "in range"), "#7A5CFF")
_card(k[1], "📈 Convo Now %", f"{_cn_pct:.1f}%" if _cn_pct is not None else "—",
      f"{n_cn:,} with a CN number", "#2DB84B")
_card(k[2], "📉 VRS %", f"{_vrs_pct:.1f}%" if _vrs_pct is not None else "—",
      f"{n_vrs:,} with a VRS number", "#4C8DFF")
_card(k[3], "📱 Have Convo Now", f"{n_cn:,}", f"of {N:,} submissions" if N else "—", "#0FB5AE")
_card(k[4], "📞 Have VRS", f"{n_vrs:,}", f"of {N:,} submissions" if N else "—", "#0E7C86")
st.caption("**Convo Now % / VRS % = submissions with a Convo Now / VRS number ÷ all submissions in range.**")
st.markdown("")

# ── by create month (every month) ───────────────────────────────────────────────────
st.markdown("##### By create month")
if len(view):
    bm = (view.groupby("Month").agg(Submissions=("Month", "size"),
          **{"Have Convo Now": ("Has Convo Now", lambda s: (s == "Yes").sum()),
             "Have VRS": ("Has VRS", lambda s: (s == "Yes").sum())}).reset_index())
    bm["% with CN"] = (bm["Have Convo Now"] / bm["Submissions"] * 100).round(0).astype(int).astype(str) + "%"
    bm["% with VRS"] = (bm["Have VRS"] / bm["Submissions"] * 100).round(0).astype(int).astype(str) + "%"
    st.dataframe(bm.sort_values("Month")[["Month", "Submissions", "Have Convo Now", "% with CN",
                                          "Have VRS", "% with VRS"]],
                 use_container_width=True, hide_index=True)
else:
    st.caption("No rows match the current filters.")

# ── records ─────────────────────────────────────────────────────────────────────────
st.markdown("##### Records")
tview = view.sort_values("Created", ascending=False)
st.caption(f"{len(tview):,} of {TOTAL:,}")
st.dataframe(tview.drop(columns=["Month"]), use_container_width=True, hide_index=True, height=460)
st.download_button("📥 Export CSV", tview.to_csv(index=False), "convo_now_submissions.csv", "text/csv")

report_header_close()
