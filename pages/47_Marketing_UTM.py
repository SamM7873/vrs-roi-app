import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timezone
from collections import defaultdict
import requests
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   headers as _H, BASE_URL as _B, fetch_all, dash_spinner,
                   save_report, load_report, saved_at_label, log_report_view)

st.set_page_config(page_title="Marketing (UTM)", layout="wide", page_icon="📣")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Marketing (UTM)")

report_header("Marketing (UTM)",
              "Contacts by UTM campaign — and whether they have a VRS number",
              section="Analytics")

NUM_OBJECT = "2-40974683"   # Number object
_key = "marketing_utm_v1"


def _ms(d, end=False):
    t = datetime(d.year, d.month, d.day, 23 if end else 0, 59 if end else 0,
                 59 if end else 0, tzinfo=timezone.utc)
    return str(int(t.timestamp() * 1000))


@st.cache_data(ttl=3600, show_spinner=False)
def _contact_props():
    try:
        r = requests.get(f"{_B}/crm/v3/properties/contacts", headers=_H, timeout=30)
        if r.status_code == 200:
            return {p.get("name") for p in r.json().get("results", [])}
    except Exception:
        pass
    return set()


def _assoc(from_obj, to_obj, from_ids):
    """from_obj IDs → associated to_obj IDs (v4 batch read)."""
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
        time.sleep(0.06)
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
        time.sleep(0.05)
    return out


st.markdown("Pulls **contacts** for a UTM campaign and checks whether each one has a **VRS number** "
            "(via the contact → Number association). Flow: **Contact (UTM) → Number**.")

# ── discover UTM fields on the contact ──────────────────────────────────────────────
_props = _contact_props()
_utm_all = sorted(n for n in _props if n.startswith("utm_") or "utm" in n.lower())
_source_fields = [n for n in ("hs_analytics_source", "hs_analytics_source_data_1",
                              "hs_analytics_source_data_2") if n in _props]
# best guess for the campaign field
_camp_default = next((n for n in ("utm_campaign", "hs_analytics_source_data_1")
                      if n in _props), (_utm_all[0] if _utm_all else "utm_campaign"))
_field_choices = sorted(set(_utm_all + _source_fields)) or ["utm_campaign"]

c1, c2 = st.columns([1.4, 1.4])
with c1:
    camp_field = st.selectbox("Campaign field", _field_choices,
                              index=(_field_choices.index(_camp_default) if _camp_default in _field_choices else 0),
                              help="Which contact property holds the UTM campaign.")
with c2:
    campaigns = st.multiselect(
        "Campaign(s)", ["52568641-Beyond SL2T - US B2C Giveaway - Q3 2026"],
        default=["52568641-Beyond SL2T - US B2C Giveaway - Q3 2026"],
        accept_new_options=True, help="Type or paste the exact UTM campaign value(s) to include.")

d1, d2, d3 = st.columns([1.2, 1.2, 1.4])
use_date = d1.checkbox("Filter by created date", value=False)
start_d = d2.date_input("From", value=date(2026, 7, 1), disabled=not use_date)
end_d = d3.date_input("To", value=date.today(), disabled=not use_date)
_group_opts = [c for c in ([camp_field] + [f for f in _field_choices if f != camp_field])]
group_by = st.selectbox("Break down by", _group_opts, index=0,
                        help="Which UTM/source field to group the breakdown on.")

n1, n2 = st.columns([1.4, 1.4])
num_after = n1.checkbox("Only count numbers created after", value=True,
                        help="Count a VRS number only if number_created_at is on/after this date.")
num_after_d = n2.date_input("Number created on/after", value=date(2026, 9, 1), disabled=not num_after)

run = st.button("▶ Run", type="primary", disabled=(not campaigns))

if run:
    # 1) contacts for the selected campaign value(s), optional created-date window
    props = sorted({camp_field, group_by, "email", "firstname", "lastname", "createdate",
                    "lifecyclestage"} & _props | {camp_field, group_by})
    filters = [{"propertyName": camp_field, "operator": "IN", "values": campaigns}]
    if use_date:
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        filters += [{"propertyName": "createdate", "operator": "GTE", "value": _ms(start_d)},
                    {"propertyName": "createdate", "operator": "LTE", "value": _ms(end_d, end=True)}]
    with dash_spinner("Reading contacts…"):
        contacts = fetch_all("contacts", list(props), filter_groups=[{"filters": filters}])
    if not contacts:
        st.warning("No contacts found for that campaign / window.")
        report_header_close(); st.stop()

    cids = [str(c["id"]) for c in contacts]

    # 2) contact → Number association → is there a VRS number?
    with dash_spinner(f"Checking {len(cids):,} contacts for a VRS number…"):
        cid_to_nids = _assoc("contacts", NUM_OBJECT, cids)
        all_nids = sorted({n for ns in cid_to_nids.values() for n in ns})
        num_of = _batch_read(NUM_OBJECT, all_nids,
                             ["number", "service_type", "number_status", "number_created_at",
                              "registration_type"])

    def _created_ok(v):
        if not num_after:
            return True
        if not v:
            return False
        try:
            s = str(v)
            dt = (datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc) if s.isdigit()
                  else datetime.fromisoformat(s.replace("Z", "+00:00")))
            return dt.date() >= num_after_d
        except (ValueError, TypeError):
            return False

    rows = []
    for c in contacts:
        cid = str(c["id"])
        p = c.get("properties", {})
        vrs_nums, statuses, created, regtypes = [], [], [], []
        for nid in cid_to_nids.get(cid, []):
            np = num_of.get(nid, {})
            if (np.get("service_type") or "").strip().lower() != "vrs":
                continue
            if not _created_ok(np.get("number_created_at")):
                continue
            num = str(np.get("number") or "").strip()
            if num:
                vrs_nums.append(num)
                statuses.append((np.get("number_status") or "").strip().title())
                created.append((str(np.get("number_created_at") or ""))[:10])
                _rt = (np.get("registration_type") or "").strip().replace("_", " ").title()
                if _rt:
                    regtypes.append(_rt)
        rows.append({
            "Campaign": (p.get(camp_field) or "").strip() or "—",
            "Group": (p.get(group_by) or "").strip() or "—",
            "Name": f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—",
            "Email": (p.get("email") or "").strip() or "—",
            "Lifecycle": (p.get("lifecyclestage") or "").strip().title() or "—",
            "Has VRS #": "Yes" if vrs_nums else "No",
            "VRS Number(s)": ", ".join(vrs_nums) or "—",
            "VRS Status": ", ".join(sorted(set(s for s in statuses if s))) or "—",
            "Registration Type": ", ".join(sorted(set(regtypes))) or "—",
            "Number Created": ", ".join(sorted(set(x for x in created if x))) or "—",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df, "campaigns": campaigns, "camp_field": camp_field,
                       "group_by": group_by,
                       "date": (f"{start_d} → {end_d}" if use_date else "all dates"),
                       "num_after": (str(num_after_d) if num_after else None)})

saved = load_report(_key)
if saved is None:
    st.info("Enter a campaign and click **▶ Run**.")
    report_header_close(); st.stop()

df = saved["df"]
if saved.get("saved_at"):
    _na = saved.get("num_after")
    st.caption(f"📌 Saved {saved_at_label(saved)} · field `{saved.get('camp_field','')}` · "
               f"{len(saved.get('campaigns', []))} campaign(s) · {saved.get('date','')}"
               + (f" · number created ≥ {_na}" if _na else ""))
if df.empty:
    st.warning("No contacts."); report_header_close(); st.stop()


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:14px 16px 12px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


N = len(df)
has_n = int((df["Has VRS #"] == "Yes").sum())
no_n = N - has_n
k = st.columns(4)
_card(k[0], "📣 Contacts", f"{N:,}", "in this campaign", "#7A5CFF")
_card(k[1], "📞 Have VRS number", f"{has_n:,}", f"{has_n/N*100:.0f}% of contacts" if N else "—", "#2DB84B")
_card(k[2], "🚫 No VRS number", f"{no_n:,}", f"{no_n/N*100:.0f}% of contacts" if N else "—", "#E5484D")
_card(k[3], "Conversion", f"{has_n/N*100:.0f}%" if N else "—", "contacts → VRS number", "#4C8DFF")
st.markdown("")

# breakdown by the chosen group-by field, split by has-number
st.markdown(f"##### By `{saved.get('group_by','Group')}`")
g = df.groupby("Group").agg(Contacts=("Group", "size"),
                            **{"Have VRS": ("Has VRS #", lambda s: (s == "Yes").sum())}).reset_index()
g["No VRS"] = g["Contacts"] - g["Have VRS"]
g["Conversion"] = (g["Have VRS"] / g["Contacts"] * 100).round(0).astype(int).astype(str) + "%"
g = g.sort_values("Contacts", ascending=False)
st.dataframe(g, use_container_width=True, hide_index=True)

# registration-type breakdown (contacts that have a VRS number)
if "Registration Type" in df.columns:
    _rt = df[df["Has VRS #"] == "Yes"]
    if not _rt.empty and (_rt["Registration Type"] != "—").any():
        st.markdown("##### By registration type (contacts with a VRS number)")
        rt = (_rt.groupby("Registration Type").size().reset_index(name="Contacts")
              .sort_values("Contacts", ascending=False))
        st.dataframe(rt, use_container_width=True, hide_index=True)

# records
st.markdown("##### Contacts")
f1, f2 = st.columns([1.2, 2])
hpick = f1.selectbox("Has VRS #", ["All", "Yes", "No"], index=0)
search = f2.text_input("Search name / email / number").strip().lower()
view = df.copy()
if hpick != "All":
    view = view[view["Has VRS #"] == hpick]
if search:
    view = view[view.apply(lambda r: search in " ".join(str(x).lower() for x in r.values), axis=1)]
st.caption(f"{len(view):,} of {N:,}")
st.dataframe(view, use_container_width=True, hide_index=True, height=440)
st.download_button("📥 Export CSV", view.to_csv(index=False), "marketing_utm.csv", "text/csv")

report_header_close()
