import re
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, dash_spinner, save_report, load_report, saved_at_label)

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("URD Error Codes",
              "Registrations by URD / LEX error code (e.g. 21 — Registering Numbering Directory TDN Failure)",
              section="Numbers")

REG_OBJECT = "2-58833629"
HS_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-58833629/{id}"
_key = "urd_error_codes_v1"

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
         "registered_at", "submitted_at", "urd_registration_updated_at"]

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
            "URD Last Updated": pd.to_datetime(p.get("urd_registration_updated_at"), utc=True, errors="coerce"),
            "HubSpot": HS_RECORD_URL.format(id=r.get("id")),
        })
    return pd.DataFrame(rows)


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
    save_report(_key, {"df": _rows(recs, codes, fields), "codes_in": codes_in,
                       "field_labels": field_labels, "capped": capped})
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
                "Registered", "URD Last Updated", "Cancelled", "HubSpot"]
col_cfg = {
    "HubSpot": st.column_config.LinkColumn("HubSpot", display_text="Open"),
    "Registered": st.column_config.DatetimeColumn("Registered", format="MMM DD, YYYY"),
    "URD Last Updated": st.column_config.DatetimeColumn("URD Last Updated", format="MMM DD, YYYY"),
    "URD Filling Meaning": st.column_config.TextColumn(width="large"),
    "LEX Error Message": st.column_config.TextColumn(width="large"),
}


def _table(sub, label, key):
    sub = sub.sort_values("Registered", ascending=False, na_position="last")[display_cols].reset_index(drop=True)
    st.markdown(f"**{len(sub):,} {label}**")
    st.dataframe(sub, use_container_width=True, hide_index=True, column_config=col_cfg)
    out = sub.copy()
    for c in ("Registered", "URD Last Updated"):
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

report_header_close()
