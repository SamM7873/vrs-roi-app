import re
import time
import requests
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, dash_spinner, save_report, load_report, saved_at_label,
                   headers as _H, BASE_URL as _B)

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("URD Error Codes",
              "Registrations by URD / LEX error code (e.g. 21 — Registering Numbering Directory TDN Failure)",
              section="Numbers")

REG_OBJECT = "2-58833629"
HS_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-58833629/{id}"
_key = "urd_error_codes_v5"
TICKET_URL = "https://app.hubspot.com/contacts/46779160/record/0-5/{id}"
NUM_OBJECT = "2-40974683"
NUM_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-40974683/{id}"
NUM_PROPS = ["number", "master_record_id", "bandwidth_order_type", "bandwidth_callback_status",
             "losing_carrier", "number_status", "urd_status", "urd_id",
             "urd_filling_error_message", "bandwidth_order_foc_date",
             "urd_registration_created_at", "urd_registration_updated_at", "hs_createdate"]

# Code fields on the registration object (comma-separated code lists, e.g. "4,21").
CODE_FIELDS = {
    "URD Filling Errors": "urd_filling_errors",
    "URD Identity Errors": "urd_identity_errors",
    "LEX Errors": "lex_errors",
}

# Rolka Loube URD filling error descriptions seen in HubSpot. The registration
# object stores only the codes, so meanings are mapped here.
URD_FILLING_CODES = {
    "3": "Duplicate Registration",
    "4": "Submission Type Failure",
    "6": "Previous RegistrationRequestID Failure",
    "21": "Registering Numbering Directory TDN Failure",
}

PROPS = ["number", "first_name", "last_name", "email", "state",
         "registration_type", "portin_status", "is_cancelled",
         "lex_verification_status", "lex_errors", "lex_error_message",
         "urd_status", "urd_filling_errors", "urd_identity_errors",
         "registered_at", "submitted_at",
         "urd_registration_created_at", "urd_registration_updated_at",
         "registration_uuid"]

st.markdown(
    "Searches the **registration object** for an error code in **URD Filling Errors**, "
    "**URD Identity Errors** and/or **LEX Errors**. Code **21** (*Registering Numbering Directory "
    "TDN Failure*) lives in **URD Filling Errors**. Note: on registrations the *URD Filling Error "
    "Message* text actually holds the LEX messages, so this page matches on the code fields instead."
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _code_list(v):
    return [c.strip() for c in str(v or "").split(",") if c.strip()]


def _fmt_phone(n):
    n = str(n or "")
    return f"({n[:3]}) {n[3:6]}-{n[6:]}" if len(n) == 10 and n.isdigit() else (n or "—")


def _lex_msgs(msg):
    """'• 26 - Unable to verify SSN/TIN' lines → de-duplicated 'code: desc' list."""
    out = []
    for line in str(msg or "").splitlines():
        line = line.strip().lstrip("•").strip()
        m = re.match(r"(\S+)\s+-\s+(.+)", line)
        item = f"{m.group(1)}: {m.group(2)}" if m else line
        if item and item not in out:
            out.append(item)
    return " | ".join(out)


def _filling_meaning(codes):
    return " | ".join(f"{c}: {URD_FILLING_CODES.get(c, 'Unknown')}" for c in codes)


def _search(codes, fields):
    """One HubSpot search per (code, field) — CONTAINS_TOKEN matches '21' inside '4,21' —
    then keep only records whose code list really contains the code."""
    by_id, capped = {}, False
    for code in codes:
        for field in fields:
            recs = fetch_all(REG_OBJECT, PROPS, filter_groups=[{"filters": [
                {"propertyName": field, "operator": "CONTAINS_TOKEN", "value": code}]}])
            capped = capped or len(recs) >= 10000
            for r in recs:
                if code in _code_list(r.get("properties", {}).get(field)):
                    by_id[r["id"]] = r
    return list(by_id.values()), capped


def _rows(recs, codes, fields):
    rows = []
    for r in recs:
        p = r.get("properties", {})
        fill = _code_list(p.get("urd_filling_errors"))
        ident = _code_list(p.get("urd_identity_errors"))
        lex = _code_list(p.get("lex_errors"))
        found_in = [label for label, f in CODE_FIELDS.items()
                    if f in fields and any(c in _code_list(p.get(f)) for c in codes)]
        rows.append({
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Number": _fmt_phone(p.get("number")),
            "Email": p.get("email") or "—",
            "State": p.get("state") or "—",
            "Type": {"port_in": "Port-In", "new": "New"}.get(p.get("registration_type"), p.get("registration_type") or "—"),
            "Port-In Status": (p.get("portin_status") or "—").upper() if p.get("portin_status") else "—",
            "URD Status": (p.get("urd_status") or "—").title(),
            "URD Filling Codes": ", ".join(fill),
            "URD Filling Meaning": _filling_meaning(fill),
            "URD Identity Codes": ", ".join(ident),
            "LEX Status": (p.get("lex_verification_status") or "—").replace("_", " ").title(),
            "LEX Codes": ", ".join(lex),
            "LEX Error Message": _lex_msgs(p.get("lex_error_message")),
            "Matched In": ", ".join(found_in),
            "Cancelled": str(p.get("is_cancelled") or "false").lower() == "true",
            "Registered": pd.to_datetime(p.get("registered_at"), utc=True, errors="coerce"),
            "URD Started": pd.to_datetime(p.get("urd_registration_created_at"), utc=True, errors="coerce"),
            "URD Completed": (pd.to_datetime(p.get("urd_registration_updated_at"), utc=True, errors="coerce")
                              if (p.get("urd_status") or "").lower() == "completed" else pd.NaT),
            "HubSpot": HS_RECORD_URL.format(id=r.get("id")),
            "_num": str(p.get("number") or "").strip(),
            "_uuid": str(p.get("registration_uuid") or "").strip(),
            "_registered_raw": p.get("registered_at"),
        })
    return pd.DataFrame(rows)


def _hours(a, b):
    """Hours from timestamp a to b (None if either is missing or b is before a)."""
    a = pd.to_datetime(a, utc=True, errors="coerce")
    b = pd.to_datetime(b, utc=True, errors="coerce")
    if pd.isna(a) or pd.isna(b) or b < a:
        return None
    return (b - a).total_seconds() / 3600


def _fetch_numbers_by(prop, values):
    """Number objects whose `prop` is in `values`, keyed by that value. When several objects
    share a value, prefer the port-in order, then the most recently created one."""
    best = {}
    for i in range(0, len(values), 100):
        for r in fetch_all(NUM_OBJECT, NUM_PROPS, filter_groups=[{"filters": [
                {"propertyName": prop, "operator": "IN", "values": values[i:i + 100]}]}]):
            p = r.get("properties", {})
            k = str(p.get(prop) or "").strip()
            rank = (p.get("bandwidth_order_type") == "portins", p.get("hs_createdate") or "")
            if k not in best or rank > best[k][0]:
                best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


def _fetch_numbers(port_rows):
    """Link registrations to number objects. Primary key: the number object's Master Record ID
    equals the registration's Registration UUID. Registrations with no ID match fall back to
    the phone number."""
    uuids = sorted({u for u in port_rows["_uuid"] if u})
    by_uuid = _fetch_numbers_by("master_record_id", uuids) if uuids else {}
    left = sorted({r["_num"] for _, r in port_rows.iterrows() if r["_num"] and r["_uuid"] not in by_uuid})
    by_num = _fetch_numbers_by("number", left) if left else {}
    return by_uuid, by_num


BASE_COLS = ["FOC Year", "FOC → Started", "Started → Completed", "FOC → Completed"]


def _baseline():
    """URD timing for every completed port-in number object (the norm to compare against).
    URD started = URD Registration Created At; URD completed = URD Registration Updated At on a
    URD-Completed number (HubSpot's own 'Time to Complete URD Registration' uses the same timestamp)."""
    recs = fetch_all(NUM_OBJECT, ["bandwidth_order_foc_date", "urd_registration_created_at",
                                  "urd_registration_updated_at"], filter_groups=[{"filters": [
        {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portins"},
        {"propertyName": "urd_status", "operator": "EQ", "value": "Completed"},
        {"propertyName": "bandwidth_order_foc_date", "operator": "HAS_PROPERTY"},
        {"propertyName": "urd_registration_updated_at", "operator": "HAS_PROPERTY"}]}])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        foc, start, done = (p.get("bandwidth_order_foc_date"), p.get("urd_registration_created_at"),
                            p.get("urd_registration_updated_at"))
        h = [_hours(foc, start), _hours(start, done), _hours(foc, done)]
        rows.append([str(pd.to_datetime(foc).year)] + h)
    return pd.DataFrame(rows, columns=BASE_COLS)


_CODE_RE = re.compile(r"Error Code:\s*(\S+)", re.I)


def _crosscheck(reg_df, by_uuid, by_num):
    """One row per port-in registration: registration vs number object, FOC → URD timing."""
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for _, r in reg_df.iterrows():
        rec, linked = by_uuid.get(r["_uuid"]), "Master Record ID"
        if rec is None:
            rec, linked = by_num.get(r["_num"]), "Phone number (no ID match)"
        if rec is None:
            linked = "—"
        p = (rec or {}).get("properties", {})
        num_urd = (p.get("urd_status") or "—").title() if rec else "—"
        foc = p.get("bandwidth_order_foc_date")
        foc_ts = pd.to_datetime(foc, utc=True, errors="coerce")
        # URD dates: number object first, registration as fallback (e.g. no number object).
        if rec and p.get("urd_registration_created_at"):
            src = "Number"
            updated = pd.to_datetime(p.get("urd_registration_updated_at"), utc=True, errors="coerce")
            started = pd.to_datetime(p.get("urd_registration_created_at"), utc=True, errors="coerce")
            completed = (pd.to_datetime(p.get("urd_registration_updated_at"), utc=True, errors="coerce")
                         if num_urd == "Completed" else pd.NaT)
        else:
            src = "Registration" if not pd.isna(r["URD Started"]) else "—"
            started, completed, updated = r["URD Started"], r["URD Completed"], pd.NaT
        approved = (num_urd if rec else r["URD Status"]) == "Completed"
        # FOC lives on the number object, so only time it against that same object's URD dates —
        # a registration can be from an older port of the same number.
        foc_for_timing = foc_ts if src == "Number" else pd.NaT
        rows.append({
            "Name": r["Name"], "Number": r["Number"], "Registered": r["Registered"],
            "Reg URD Status": r["URD Status"], "Reg URD Codes": r["URD Filling Codes"],
            "Number Object": "Yes" if rec else "MISSING",
            "Linked By": linked,
            "Num Order Type": p.get("bandwidth_order_type") or "—",
            "Bandwidth Order Status": p.get("bandwidth_callback_status") or "—",
            "Losing Carrier": p.get("losing_carrier") or "—",
            "Num Status": (p.get("number_status") or "—").title(),
            "Num URD Status": num_urd,
            "Status Match": ("Yes" if num_urd == r["URD Status"] else "NO") if rec else "—",
            "Num URD Codes": ", ".join(dict.fromkeys(_CODE_RE.findall(p.get("urd_filling_error_message") or ""))),
            "URD ID": p.get("urd_id") or "",
            "FOC Date": foc_ts,
            "URD Started": started,
            "URD Completed": completed,
            "URD Dates From": src,
            "FOC → URD Started (hrs)": _hours(foc_for_timing, started),
            "URD Started → Completed (hrs)": _hours(started, completed),
            "FOC → URD Completed (hrs)": _hours(foc_for_timing, completed),
            "FOC → URD Updated (hrs)": _hours(foc_for_timing, updated),
            "Days Since FOC (not completed)": (now - foc_ts).days if not approved and not pd.isna(foc_ts) else None,
            "Number HubSpot": NUM_RECORD_URL.format(id=rec["id"]) if rec else None,
            "Registration HubSpot": r["HubSpot"],
            "_num": r["_num"], "_registered_raw": r["_registered_raw"],
        })
    return pd.DataFrame(rows)


def _post(path, body):
    """POST to HubSpot with a couple of 429 retries; returns parsed JSON or {}."""
    for attempt in range(4):
        try:
            r = requests.post(f"{_B}{path}", headers=_H, json=body, timeout=60)
        except requests.exceptions.RequestException:
            time.sleep(1.5 * (attempt + 1)); continue
        if r.status_code == 429:
            time.sleep(1.5 * (attempt + 1)); continue
        return r.json() if r.status_code in (200, 207) else {}
    return {}


_PHONE_RE = re.compile(r"(?<!\d)1?(\d{10})(?!\d)")


def _port_outs(port_rows):
    """For each ported number: find the customer's contact (registration email), read the
    contact's tickets and take the first 'Port out order' ticket after the port-in started.
    A ticket that lists other phone numbers but not this one is ignored (customer may have
    ported out a different number); one that lists no number at all only counts within a year
    of the port-in. Returns {number: (port_out_ts, ticket_id, how matched)}."""
    emails = sorted({e.lower() for e in port_rows["Email"] if e and e != "—"})
    contact_by_email = {}
    for i in range(0, len(emails), 100):
        for c in fetch_all("contacts", ["email"], filter_groups=[{"filters": [
                {"propertyName": "email", "operator": "IN", "values": emails[i:i + 100]}]}]):
            contact_by_email[(c.get("properties", {}).get("email") or "").lower()] = c["id"]
    cids = sorted(set(contact_by_email.values()))
    tickets_by_contact = {}
    for i in range(0, len(cids), 100):
        res = _post("/crm/v4/associations/contacts/tickets/batch/read",
                    {"inputs": [{"id": c} for c in cids[i:i + 100]]})
        for row in res.get("results", []):
            tickets_by_contact[str(row["from"]["id"])] = [str(t["toObjectId"]) for t in row.get("to", [])]
    tids = sorted({t for ts in tickets_by_contact.values() for t in ts})
    tickets = {}
    for i in range(0, len(tids), 100):
        res = _post("/crm/v3/objects/tickets/batch/read",
                    {"properties": ["subject", "content", "createdate"], "inputs": [{"id": t} for t in tids[i:i + 100]]})
        for t in res.get("results", []):
            tickets[str(t["id"])] = t.get("properties", {})
    out = {}
    for _, r in port_rows.iterrows():
        cid = contact_by_email.get(str(r["Email"]).lower())
        start = pd.to_datetime(r["_registered_raw"], utc=True, errors="coerce")
        best = None
        for tid in tickets_by_contact.get(str(cid), []) if cid else []:
            t = tickets.get(tid, {})
            if not (t.get("subject") or "").strip().lower().startswith("port out order"):
                continue
            listed = set(_PHONE_RE.findall(f"{t.get('subject') or ''} {t.get('content') or ''}"))
            if listed and r["_num"] not in listed:
                continue
            ts = pd.to_datetime(t.get("createdate"), utc=True, errors="coerce")
            if pd.isna(ts) or (not pd.isna(start) and ts < start):
                continue
            if not listed and (pd.isna(start) or (ts - start).days > 365):
                continue
            how = "Number on ticket" if listed else "Contact's ticket (no number listed)"
            if best is None or ts < best[0]:
                best = (ts, tid, how)
        if best:
            out[r["_num"]] = best
    return out


# ── query ─────────────────────────────────────────────────────────────────────

saved = load_report(_key)
q1, q2, q3 = st.columns([1.2, 2.2, 0.9])
with q1:
    codes_in = st.text_input("Error code(s)", value=(saved or {}).get("codes_in", "21"),
                             help="One or more codes, comma-separated (e.g. 21 or 4,21 or RS)")
with q2:
    field_labels = st.multiselect("Look in", list(CODE_FIELDS),
                                  default=(saved or {}).get("field_labels", ["URD Filling Errors"]))
with q3:
    st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    run = st.button("Search", type="primary", use_container_width=True)

codes = [c.strip() for c in codes_in.split(",") if c.strip()]
fields = [CODE_FIELDS[l] for l in field_labels]

if run:
    if not codes or not fields:
        st.warning("Enter at least one code and pick at least one field.")
        report_header_close()
        st.stop()
    with dash_spinner(f"Searching registrations for code {', '.join(codes)}..."):
        recs, capped = _search(codes, fields)
        reg_df = _rows(recs, codes, fields)
    port_nums = sorted({n for n in reg_df.loc[reg_df["Type"] == "Port-In", "_num"] if n}) if not reg_df.empty else []
    with dash_spinner(f"Cross-checking {len(port_nums):,} ported numbers against number objects..."):
        by_uuid, by_num = (_fetch_numbers(reg_df[reg_df["Type"] == "Port-In"]) if port_nums else ({}, {}))
        baseline = _baseline()
    with dash_spinner("Checking contacts' tickets for numbers that were ported back out..."):
        port_outs = _port_outs(reg_df[reg_df["Type"] == "Port-In"]) if port_nums else {}
    save_report(_key, {"df": reg_df, "num_by_uuid": by_uuid, "num_by_number": by_num, "baseline": baseline,
                       "port_outs": port_outs,
                       "codes_in": codes_in, "field_labels": field_labels, "capped": capped})
    saved = load_report(_key)

if saved is None:
    st.info("Enter a code (default **21**) and click **Search**.")
    report_header_close()
    st.stop()

df_all = saved["df"]
st.caption(f"📌 Results for code **{saved['codes_in']}** in {', '.join(saved['field_labels'])} "
           f"· pulled {saved_at_label(saved)}")
if saved.get("capped"):
    st.warning("HubSpot search stops at 10,000 records per query — this code is very common, so the "
               "list may be incomplete.")
if df_all.empty:
    st.warning("No registrations found with that code.")
    report_header_close()
    st.stop()

# ── filters ───────────────────────────────────────────────────────────────────

f1, f2, f3, f4 = st.columns([1, 1, 1, 1.6])
with f1:
    type_sel = st.multiselect("Registration type", sorted(df_all["Type"].unique()), default=[])
with f2:
    urd_sel = st.multiselect("URD status", sorted(df_all["URD Status"].unique()), default=[])
with f3:
    hide_cancelled = st.checkbox("Hide cancelled", value=True)
with f4:
    search = st.text_input("Search name / number / email", "")

df = df_all.copy()
if type_sel:
    df = df[df["Type"].isin(type_sel)]
if urd_sel:
    df = df[df["URD Status"].isin(urd_sel)]
if hide_cancelled:
    df = df[~df["Cancelled"]]
if search.strip():
    s = search.strip().lower()
    match = (df["Name"] + " " + df["Number"] + " " + df["Email"]).str.lower().str.contains(s, regex=False)
    digits = re.sub(r"\D", "", s)
    if digits:
        match |= df["Number"].str.replace(r"\D", "", regex=True).str.contains(digits, regex=False)
    df = df[match]

stuck = df["URD Status"] != "Completed"

# ── KPI tiles ─────────────────────────────────────────────────────────────────

def _tile(label, val, color="#1F2937", border="#E5E7EB"):
    return (f'<div style="background:#fff;border:1px solid {border};border-radius:10px;padding:1rem 1.25rem;">'
            f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:#6B7280;margin-bottom:0.25rem;">{label}</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{color};">{val:,}</div></div>')


st.markdown(
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.85rem;margin:0.75rem 0 1.5rem;">'
    + _tile("Registrations", len(df))
    + _tile("URD Not Completed", int(stuck.sum()), "#EF4444", "#FEE2E2")
    + _tile("URD Completed", int((~stuck).sum()), "#00A651")
    + _tile("Port-In", int((df["Type"] == "Port-In").sum()), "#3B82F6")
    + _tile("New Number", int((df["Type"] == "New").sum()), "#8B5CF6")
    + "</div>", unsafe_allow_html=True)

# ── charts ────────────────────────────────────────────────────────────────────

def _bar(counts, col, color, sort="-x"):
    return alt.Chart(counts).mark_bar(color=color, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("Count:Q", title="Registrations"),
        y=alt.Y(f"{col}:N", sort=sort, title=None, axis=alt.Axis(labelLimit=420)),
        tooltip=[col, "Count"],
    ).properties(height=max(120, len(counts) * 28))


ch1, ch2 = st.columns(2)
with ch1:
    st.markdown("#### URD filling code combinations")
    combo = df.assign(Combo=df["URD Filling Codes"].replace("", "—")).groupby("Combo").size() \
              .reset_index(name="Count").sort_values("Count", ascending=False)
    st.altair_chart(_bar(combo, "Combo", "#DC2626"), use_container_width=True)
with ch2:
    st.markdown("#### Registered by year")
    yr = df.assign(Year=df["Registered"].dt.year.fillna(0).astype(int).astype(str).replace("0", "Unknown")) \
           .groupby("Year").size().reset_index(name="Count")
    st.altair_chart(_bar(yr, "Year", "#3B82F6", sort=alt.SortField("Year", order="descending")),
                    use_container_width=True)

st.markdown("#### LEX error codes on these registrations")
lex_cc = pd.Series([c.strip() for v in df["LEX Codes"] for c in v.split(",") if c.strip()],
                   dtype=str).value_counts().reset_index()
if lex_cc.empty:
    st.caption("No LEX error codes on these registrations.")
else:
    lex_cc.columns = ["LEX Code", "Count"]
    st.altair_chart(_bar(lex_cc, "LEX Code", "#F59E0B"), use_container_width=True)

with st.expander("URD filling error code meanings"):
    st.table(pd.DataFrame([{"Code": k, "Meaning": v} for k, v in URD_FILLING_CODES.items()]))
    st.caption("Codes 7, 9, 10, 22, 23 and 32 also appear on older (2018–2021) port-ins; "
               "HubSpot has no description stored for them.")

# ── detail tables ─────────────────────────────────────────────────────────────

display_cols = ["Name", "Number", "Email", "Type", "Port-In Status", "URD Status",
                "URD Filling Codes", "URD Filling Meaning", "URD Identity Codes",
                "LEX Status", "LEX Codes", "LEX Error Message",
                "Registered", "URD Started", "URD Completed", "Cancelled", "HubSpot"]
col_cfg = {
    "HubSpot": st.column_config.LinkColumn("HubSpot", display_text="Open"),
    "Registered": st.column_config.DatetimeColumn("Registered", format="MMM DD, YYYY"),
    "URD Started": st.column_config.DatetimeColumn("URD Started", format="MMM DD, YYYY",
                                                   help="URD Registration Created At"),
    "URD Completed": st.column_config.DatetimeColumn("URD Completed", format="MMM DD, YYYY",
                                                     help="URD Registration Updated At, when URD Status is Completed"),
    "URD Filling Meaning": st.column_config.TextColumn(width="large"),
    "LEX Error Message": st.column_config.TextColumn(width="large"),
}


def _table(sub, label, key):
    sub = sub.sort_values("Registered", ascending=False, na_position="last")[display_cols].reset_index(drop=True)
    st.markdown(f"**{len(sub):,} {label}**")
    st.dataframe(sub, use_container_width=True, hide_index=True, column_config=col_cfg)
    out = sub.copy()
    for c in ("Registered", "URD Started", "URD Completed"):
        out[c] = out[c].dt.strftime("%Y-%m-%d").fillna("")
    st.download_button("Download CSV", out.to_csv(index=False),
                       f"urd_error_{saved['codes_in'].replace(',', '_')}_{key}_{datetime.now().strftime('%Y%m%d')}.csv",
                       "text/csv", key=f"dl_{key}")


t_stuck, t_done, t_all = st.tabs(["URD Not Completed (stuck)", "URD Completed", "All"])
with t_stuck:
    _table(df[stuck], "registrations still not completed in URD", "stuck")
with t_done:
    _table(df[~stuck], "registrations completed in URD", "completed")
with t_all:
    _table(df, "registrations", "all")

# ── port-in cross-check: registration vs number object, FOC → URD approval ───

st.markdown("---")
st.markdown("### Ported numbers — Registration vs Number object")
st.caption("Each port-in registration above is linked to its number object by **Registration UUID = "
           "Master Record ID** (phone number is used only when there is no ID match — see **Linked By**). "
           "**FOC** = Bandwidth Order FOC Date (number object only). **URD Started** = URD Registration "
           "Created At. **URD Completed** = URD Registration Updated At on a record whose URD Status is "
           "Completed (HubSpot has no separate 'completed at' field). URD dates come from the number "
           "object, or from the registration when there is no number object. Filters above apply here too.")

xc = _crosscheck(df[df["Type"] == "Port-In"], saved.get("num_by_uuid", {}), saved.get("num_by_number", {}))
base = saved.get("baseline", pd.DataFrame(columns=BASE_COLS))

if xc.empty:
    st.info("No port-in registrations in this selection.")
else:
    has_obj = xc["Number Object"] == "Yes"
    f2s = xc["FOC → URD Started (hrs)"].dropna()
    s2c = xc["URD Started → Completed (hrs)"].dropna()
    f2c = xc["FOC → URD Completed (hrs)"].dropna()
    f2u = xc["FOC → URD Updated (hrs)"].dropna()

    def _avg(x):
        return x.mean() if len(x) else None

    def _fmt_h(h):
        if h is None or pd.isna(h):
            return "—"
        return f"{h / 24:.1f} days" if h >= 48 else f"{h:.1f} hrs"

    def _tile_txt(label, val, color="#1F2937", border="#E5E7EB", sub=""):
        sub_html = f'<div style="font-size:0.72rem;color:#6B7280;margin-top:0.15rem;">{sub}</div>' if sub else ""
        return (f'<div style="background:#fff;border:1px solid {border};border-radius:10px;padding:1rem 1.25rem;">'
                f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
                f'color:#6B7280;margin-bottom:0.25rem;">{label}</div>'
                f'<div style="font-size:1.4rem;font-weight:800;color:{color};">{val}</div>{sub_html}</div>')

    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.85rem;margin:0.75rem 0 1rem;">'
        + _tile_txt("Ported Numbers", f"{len(xc):,}")
        + _tile_txt("No Number Object", f"{int((~has_obj).sum()):,}", "#EF4444", "#FEE2E2")
        + _tile_txt("URD Status Mismatch", f"{int((xc['Status Match'] == 'NO').sum()):,}", "#F59E0B",
                    sub="registration ≠ number object")
        + _tile_txt("Ported Out Since", f"{int((xc['Num Order Type'] == 'portouts').sum()):,}", "#8B5CF6")
        + "</div>", unsafe_allow_html=True)

    st.markdown("#### URD timing — these numbers vs all completed port-ins")
    bf2s, bs2c, bf2c = (base[c].dropna() for c in BASE_COLS[1:])
    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0.85rem;margin:0.5rem 0 1rem;">'
        + _tile_txt("FOC → URD Approved (Avg)", _fmt_h(_avg(f2c)), "#00A651", "#BBF7D0",
                    sub=f"avg of {len(f2c)} approved (URD Completed) · norm {_fmt_h(_avg(bf2c))} "
                        f"(median {_fmt_h(bf2c.median() if len(bf2c) else None)})")
        + _tile_txt("FOC → URD Updated (Avg)", _fmt_h(_avg(f2u)), "#F59E0B",
                    sub=f"avg of {len(f2u)} incl. not approved · median {_fmt_h(f2u.median() if len(f2u) else None)}")
        + _tile_txt("FOC → URD Started", _fmt_h(_avg(f2s)), "#3B82F6",
                    sub=f"avg of {len(f2s)} · norm {_fmt_h(_avg(bf2s))} (median {_fmt_h(bf2s.median() if len(bf2s) else None)})")
        + _tile_txt("URD Started → Completed", _fmt_h(_avg(s2c)), "#8B5CF6",
                    sub=f"avg of {len(s2c)} · norm {_fmt_h(_avg(bs2c))} (median {_fmt_h(bs2c.median() if len(bs2c) else None)})")
        + "</div>", unsafe_allow_html=True)
    st.caption(f"**Approved** = URD Status Completed; time is FOC → URD Registration Updated At. "
               f"**URD Updated** counts every number with an FOC date, approved or not (for a Pending number "
               f"it is the last URD attempt). FOC exists only on number objects. "
               f"Norm = {len(base):,} completed port-in number objects with an FOC date. "
               "Averages are pulled up by a few very slow records; the median is the typical case.")

    if len(base):
        with st.expander("Norm by FOC year — all completed port-ins"):
            g = base.groupby("FOC Year")
            by_year = pd.DataFrame({"Count": g.size()})
            for c in BASE_COLS[1:]:
                by_year[f"{c} (avg)"] = g[c].mean().map(_fmt_h)
                by_year[f"{c} (median)"] = g[c].median().map(_fmt_h)
            by_year["FOC → Completed within 24 hrs"] = g["FOC → Completed"].apply(
                lambda h: f"{(h.dropna() <= 24).mean():.0%}" if h.notna().any() else "—")
            st.dataframe(by_year.reset_index().sort_values("FOC Year", ascending=False),
                         use_container_width=True, hide_index=True)

    xc_cfg = {
        "Registered": st.column_config.DatetimeColumn("Registered", format="MMM DD, YYYY"),
        "FOC Date": st.column_config.DatetimeColumn("FOC Date", format="MMM DD, YYYY"),
        "URD Started": st.column_config.DatetimeColumn("URD Started", format="MMM DD, YYYY"),
        "URD Completed": st.column_config.DatetimeColumn("URD Completed", format="MMM DD, YYYY"),
        "FOC → URD Started (hrs)": st.column_config.NumberColumn(format="%.1f"),
        "URD Started → Completed (hrs)": st.column_config.NumberColumn(format="%.1f"),
        "FOC → URD Completed (hrs)": st.column_config.NumberColumn(format="%.1f"),
        "FOC → URD Updated (hrs)": st.column_config.NumberColumn(format="%.1f"),
        "Number HubSpot": st.column_config.LinkColumn("Number HubSpot", display_text="Open"),
        "Registration HubSpot": st.column_config.LinkColumn("Registration HubSpot", display_text="Open"),
    }
    xc = xc.sort_values(["Number Object", "FOC Date"], ascending=[False, False], na_position="last").reset_index(drop=True)
    xc_cols = [c for c in xc.columns if not c.startswith("_")]
    x_all, x_obj, x_miss, x_mm = st.tabs(["All ported", "Has number object", "Missing number object", "Status mismatch"])
    for tab, sub, label in ((x_all, xc, "ported numbers"),
                            (x_obj, xc[has_obj], "with a number object"),
                            (x_miss, xc[~has_obj], "with NO number object"),
                            (x_mm, xc[xc["Status Match"] == "NO"], "where registration and number URD status differ")):
        with tab:
            st.markdown(f"**{len(sub):,} {label}**")
            st.dataframe(sub[xc_cols], use_container_width=True, hide_index=True, column_config=xc_cfg)

    out = xc[xc_cols].copy()
    for c in ("Registered", "FOC Date", "URD Started", "URD Completed"):
        out[c] = pd.to_datetime(out[c], utc=True).dt.strftime("%Y-%m-%d").fillna("")
    st.download_button("Download cross-check CSV", out.to_csv(index=False),
                       f"urd_error_{saved['codes_in'].replace(',', '_')}_portin_crosscheck_{datetime.now().strftime('%Y%m%d')}.csv",
                       "text/csv", key="dl_crosscheck")

    # ── ported back out: port-in started → port-out ticket ──────────────────
    st.markdown("#### Ported back out")
    st.caption("Ported numbers whose customer has a **Port out order** ticket after the port-in started "
               "(matched by registration email → contact → tickets). **Port-In Started** = registration "
               "Registered At. Tickets that list a different phone number are ignored; tickets with no "
               "number listed only count within a year of the port-in.")
    pos = saved.get("port_outs", {})
    pb = []
    for _, r in xc.iterrows():
        hit = pos.get(r["_num"])
        if not hit:
            continue
        start = pd.to_datetime(r["_registered_raw"], utc=True, errors="coerce")
        pb.append({"Name": r["Name"], "Ported Number": r["Number"], "Port-In Started": start,
                   "Ported Back Out": hit[0],
                   "Days": (hit[0] - start).days if not pd.isna(start) else None,
                   "Reg URD Status": r["Reg URD Status"], "URD Codes": r["Reg URD Codes"],
                   "Number Object": r["Number Object"], "Matched By": hit[2],
                   "Port-Out Ticket": TICKET_URL.format(id=hit[1])})
    if not pb:
        st.caption("No port-out tickets found for these ported numbers.")
    else:
        pb = pd.DataFrame(pb).sort_values("Port-In Started", ascending=False).reset_index(drop=True)
        days = pb["Days"].dropna()
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.85rem;margin:0.5rem 0 1rem;">'
            + _tile_txt("Ported Back Out", f"{len(pb):,}", "#EF4444", "#FEE2E2", sub=f"of {len(xc):,} ported numbers")
            + _tile_txt("Avg Days to Port Back", f"{days.mean():.1f}" if len(days) else "—", "#F59E0B",
                        sub=f"median {days.median():.0f} · range {days.min():.0f}–{days.max():.0f}" if len(days) else "")
            + "</div>", unsafe_allow_html=True)
        st.dataframe(pb, use_container_width=True, hide_index=True, column_config={
            "Port-In Started": st.column_config.DatetimeColumn("Port-In Started", format="MMM DD, YYYY"),
            "Ported Back Out": st.column_config.DatetimeColumn("Ported Back Out", format="MMM DD, YYYY"),
            "Days": st.column_config.NumberColumn("Days", format="%d"),
            "Port-Out Ticket": st.column_config.LinkColumn("Port-Out Ticket", display_text="Open"),
        })
        pb_out = pb.copy()
        for c in ("Port-In Started", "Ported Back Out"):
            pb_out[c] = pb_out[c].dt.strftime("%Y-%m-%d")
        st.download_button("Download ported-back-out CSV", pb_out.to_csv(index=False),
                           f"urd_error_{saved['codes_in'].replace(',', '_')}_ported_back_out_{datetime.now().strftime('%Y%m%d')}.csv",
                           "text/csv", key="dl_portback")

report_header_close()
