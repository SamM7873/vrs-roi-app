import re
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta, date
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   fetch_all, dash_spinner, save_report, load_report, saved_at_label)

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header("Port-In LEX / URD Issues",
              "Port-in LEX status, LEX errors and URD errors — number object and registration object",
              section="Numbers")

NUM_OBJECT = "2-40974683"
HS_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-40974683/{id}"
_key = "port_in_lex_urd_v2"

PROPS = ["number", "first_name", "last_name", "email", "state",
         "number_status", "usage_type", "bandwidth_order_type",
         "portin_status", "portin_message",
         "lex_verification_status", "lex_error_message",
         "urd_status", "urd_identity_error_message", "urd_filling_error_message",
         "number_created_at", "registered_at", "hs_createdate"]

LEX_OK = ("Automatic Success", "Manual Success")


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


_ERR_RE = re.compile(r"Error Code:\s*(.+?)\s*\n\s*Error Description:\s*(.+?)\s*(?=\n|$)", re.I)


def _errors(msg):
    """Split a HubSpot error blob into unique (code, description) pairs, keeping order.
    The raw text repeats 'Error Code: X / Error Description: Y' blocks, sometimes twice."""
    if not msg:
        return []
    pairs = _ERR_RE.findall(str(msg))
    if not pairs:
        return [("—", " ".join(str(msg).split()))]
    seen, out = set(), []
    for code, desc in pairs:
        k = (code.strip(), desc.strip().rstrip("."))
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _fmt_errors(pairs):
    return " | ".join(f"{c}: {d}" if c != "—" else d for c, d in pairs) or ""


def _codes(pairs):
    return ", ".join(c for c, _ in pairs)


# ── load ──────────────────────────────────────────────────────────────────────

def _load():
    with dash_spinner("Fetching port-in number objects..."):
        recs = fetch_all(NUM_OBJECT, PROPS, filter_groups=[{"filters": [
            {"propertyName": "bandwidth_order_type", "operator": "EQ", "value": "portins"}]}])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        lex_errs = _errors(p.get("lex_error_message"))
        urd_id_errs = _errors(p.get("urd_identity_error_message"))
        urd_fill_errs = _errors(p.get("urd_filling_error_message"))
        lex = p.get("lex_verification_status") or ""
        urd = p.get("urd_status") or ""
        created = _parse(p.get("number_created_at")) or _parse(p.get("hs_createdate"))
        rows.append({
            "Number": p.get("number") or "—",
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "State": p.get("state") or "—",
            "Port-In Status": p.get("portin_status") or "—",
            "Number Status": (p.get("number_status") or "—").title(),
            "LEX Status": lex or "—",
            "LEX Error Codes": _codes(lex_errs),
            "LEX Error Message": _fmt_errors(lex_errs),
            "URD Status": urd or "—",
            "URD Identified Codes": _codes(urd_id_errs),
            "URD Identified Message": _fmt_errors(urd_id_errs),
            "URD Filling Codes": _codes(urd_fill_errs),
            "URD Filling Error": _fmt_errors(urd_fill_errs),
            "Port-In Message": " ".join(str(p.get("portin_message") or "").split()),
            "Created": created,
            "HubSpot": HS_RECORD_URL.format(id=r.get("id")),
            "_lex_pairs": lex_errs,
            "_urd_pairs": urd_id_errs,
            "_fill_pairs": urd_fill_errs,
            "_lex_ok": lex in LEX_OK,
            "_urd_done": urd.lower() == "completed",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df})
    return df



# ── shared filter / display helpers ───────────────────────────────────────────

PRESETS = ["All Time", "Last 7 Days", "Last 30 Days", "Last 90 Days",
           "This Month", "Last Month", "This Year", "Custom Range"]


def _range(preset):
    today = date.today()
    if preset == "Last 7 Days":  return today - timedelta(days=6), today
    if preset == "Last 30 Days": return today - timedelta(days=29), today
    if preset == "Last 90 Days": return today - timedelta(days=89), today
    if preset == "This Month":   return today.replace(day=1), today
    if preset == "Last Month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if preset == "This Year":    return today.replace(month=1, day=1), today
    return None, None


def _tile(label, val, color="#1F2937", border="#E5E7EB"):
    return (f'<div style="background:#fff;border:1px solid {border};border-radius:10px;padding:1rem 1.25rem;">'
            f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:#6B7280;margin-bottom:0.25rem;">{label}</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{color};">{val:,}</div></div>')


def _bar(counts, col, color):
    return alt.Chart(counts).mark_bar(color=color, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("Count:Q", title="Count"),
        y=alt.Y(f"{col}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=420)),
        tooltip=[col, "Count"],
    ).properties(height=max(120, len(counts) * 30))


# ── Number object section ─────────────────────────────────────────────────────

def _number_section():
    st.markdown(
        "Pulls every number object with **Bandwidth Order Type = portins** and shows the "
        "**LEX Verification Status**, **LEX Error Message** and **URD Identified Message** "
        "(`URD Identity Error Message`, e.g. *\"identified as a user already registered in the URD\"*), "
        "plus the **URD Filling Error Message** (e.g. code 21 *Registering Numbering Directory TDN Failure*). "
        "Messages are de-duplicated and split into individual error codes so issues can be grouped."
    )

    c_run, c_info = st.columns([1, 3])
    with c_run:
        run = st.button("Run / Refresh", type="primary")
    saved = None if run else load_report(_key)
    if run:
        df_all = _load()
        saved = load_report(_key)
    elif saved is not None:
        df_all = saved["df"]
    else:
        st.info("Click **Run / Refresh** to pull port-in number objects from HubSpot.")
        return
    with c_info:
        st.caption(f"📌 Data pulled {saved_at_label(saved)}")

    if df_all.empty:
        st.warning("No port-in number objects found.")
        return

    # ── filters ───────────────────────────────────────────────────────────────────





    f1, f2, f3, f4, f5 = st.columns([1.2, 1.2, 1.2, 1.3, 1.5])
    with f1:
        preset = st.selectbox("Number created", PRESETS, index=0)
        if preset == "Custom Range":
            d_from = st.date_input("From", value=date.today() - timedelta(days=29))
            d_to = st.date_input("To", value=date.today())
        else:
            d_from, d_to = _range(preset)
    with f2:
        lex_opts = sorted(df_all["LEX Status"].unique())
        lex_sel = st.multiselect("LEX Status", lex_opts, default=[])
    with f3:
        urd_opts = sorted(df_all["URD Status"].unique())
        urd_sel = st.multiselect("URD Status", urd_opts, default=[])
    with f4:
        _all_codes = sorted(
            {c for col in ("_lex_pairs", "_urd_pairs", "_fill_pairs") for pairs in df_all[col] for c, _ in pairs if c != "—"},
            key=lambda c: (not c.isdigit(), int(c) if c.isdigit() else 0, c))
        code_sel = st.multiselect("Error code (LEX / URD)", _all_codes, default=[],
                                  help="Matches the code in the LEX error, URD identified or URD filling message")
    with f5:
        search = st.text_input("Search number / name / email / message", "")

    df = df_all.copy()
    if d_from and d_to:
        fs = datetime(d_from.year, d_from.month, d_from.day, tzinfo=timezone.utc)
        fe = datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59, tzinfo=timezone.utc)
        df = df[df["Created"].apply(lambda d: d is not None and not pd.isna(d) and fs <= d <= fe)]
    if lex_sel:
        df = df[df["LEX Status"].isin(lex_sel)]
    if urd_sel:
        df = df[df["URD Status"].isin(urd_sel)]
    if code_sel:
        _want = set(code_sel)
        df = df[df.apply(lambda r: any(c in _want for col in ("_lex_pairs", "_urd_pairs", "_fill_pairs")
                                       for c, _ in r[col]), axis=1)]
    if search.strip():
        s = search.strip().lower()
        hay = (df["Number"] + " " + df["Name"] + " " + df["Email"] + " "
               + df["LEX Error Message"] + " " + df["URD Identified Message"] + " "
               + df["URD Filling Error"]).str.lower()
        df = df[hay.str.contains(s, regex=False)]

    has_lex_err = df["LEX Error Message"] != ""
    has_urd_msg = df["URD Identified Message"] != ""
    has_fill_err = df["URD Filling Error"] != ""
    lex_not_ok = ~df["_lex_ok"]
    urd_not_done = ~df["_urd_done"]

    # ── KPI tiles ─────────────────────────────────────────────────────────────────



    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.85rem;margin:0.75rem 0 1.5rem;">'
        + _tile("Port-In Numbers", len(df))
        + _tile("LEX Verified", int(df["_lex_ok"].sum()), "#00A651")
        + _tile("LEX Not Verified", int(lex_not_ok.sum()), "#EF4444", "#FEE2E2")
        + _tile("Has LEX Error Msg", int(has_lex_err.sum()), "#F59E0B")
        + _tile("Has URD Identified Msg", int(has_urd_msg.sum()), "#8B5CF6")
        + _tile("Has URD Filling Error", int(has_fill_err.sum()), "#DC2626")
        + _tile("URD Not Completed", int(urd_not_done.sum()), "#3B82F6")
        + "</div>", unsafe_allow_html=True)

    # ── charts ────────────────────────────────────────────────────────────────────



    def _code_counts(pairs_col):
        rows = []
        for pairs in df[pairs_col]:
            for code, desc in pairs:
                rows.append({"Error": f"{code} — {desc}" if code != "—" else desc})
        if not rows:
            return pd.DataFrame(columns=["Error", "Count"])
        return (pd.DataFrame(rows).groupby("Error").size().reset_index(name="Count")
                .sort_values("Count", ascending=False))


    ch1, ch2 = st.columns(2)
    with ch1:
        st.markdown("#### LEX Status")
        lc = df.groupby("LEX Status").size().reset_index(name="Count")
        if not lc.empty:
            st.altair_chart(_bar(lc, "LEX Status", "#00A651"), use_container_width=True)
    with ch2:
        st.markdown("#### URD Status")
        uc = df.groupby("URD Status").size().reset_index(name="Count")
        if not uc.empty:
            st.altair_chart(_bar(uc, "URD Status", "#3B82F6"), use_container_width=True)

    st.markdown("#### LEX Error Messages (by error code)")
    lex_cc = _code_counts("_lex_pairs")
    if lex_cc.empty:
        st.caption("No LEX error messages in this selection.")
    else:
        st.altair_chart(_bar(lex_cc, "Error", "#F59E0B"), use_container_width=True)

    st.markdown("#### URD Identified Messages (by error code)")
    urd_cc = _code_counts("_urd_pairs")
    if urd_cc.empty:
        st.caption("No URD identified messages in this selection.")
    else:
        st.altair_chart(_bar(urd_cc, "Error", "#8B5CF6"), use_container_width=True)

    st.markdown("#### URD Filling Errors (by error code)")
    fill_cc = _code_counts("_fill_pairs")
    if fill_cc.empty:
        st.caption("No URD filling errors in this selection.")
    else:
        st.altair_chart(_bar(fill_cc, "Error", "#DC2626"), use_container_width=True)

    # ── LEX status × URD status cross-tab ─────────────────────────────────────────

    st.markdown("#### LEX Status × URD Status")
    xt = pd.crosstab(df["LEX Status"], df["URD Status"], margins=True, margins_name="Total")
    st.dataframe(xt, use_container_width=True)

    # ── detail tabs ───────────────────────────────────────────────────────────────

    display_cols = ["Number", "Name", "Email", "State", "Port-In Status", "Number Status",
                    "LEX Status", "LEX Error Codes", "LEX Error Message",
                    "URD Status", "URD Identified Codes", "URD Identified Message",
                    "URD Filling Codes", "URD Filling Error",
                    "Port-In Message", "Created", "HubSpot"]
    col_cfg = {
        "HubSpot": st.column_config.LinkColumn("HubSpot", display_text="Open"),
        "Created": st.column_config.DatetimeColumn("Created", format="MMM DD, YYYY"),
        "LEX Error Message": st.column_config.TextColumn(width="large"),
        "URD Identified Message": st.column_config.TextColumn(width="large"),
    }


    def _table(sub, label, key):
        sub = sub.sort_values("Created", ascending=False, na_position="last")[display_cols].reset_index(drop=True)
        st.markdown(f"**{len(sub):,} {label}**")
        st.dataframe(sub, use_container_width=True, hide_index=True, column_config=col_cfg)
        out = sub.copy()
        out["Created"] = out["Created"].apply(lambda d: d.strftime("%Y-%m-%d") if d is not None and not pd.isna(d) else "")
        st.download_button("Download CSV", out.to_csv(index=False),
                           f"port_in_lex_urd_{key}_{datetime.now().strftime('%Y%m%d')}.csv",
                           "text/csv", key=f"dl_{key}")


    t_issue, t_urd, t_fill, t_lex, t_lexnv, t_all = st.tabs([
        "Any LEX / URD Issue", "URD Identified Message", "URD Filling Error", "LEX Error Message",
        "LEX Not Verified", "All Port-Ins"])
    with t_issue:
        _table(df[has_lex_err | has_urd_msg | has_fill_err | lex_not_ok], "port-ins with a LEX or URD issue", "issues")
    with t_urd:
        _table(df[has_urd_msg], "port-ins with a URD identified message", "urd")
    with t_fill:
        _table(df[has_fill_err], "port-ins with a URD filling error", "fill")
    with t_lex:
        _table(df[has_lex_err], "port-ins with a LEX error message", "lex")
    with t_lexnv:
        _table(df[lex_not_ok], "port-ins where LEX is not Automatic/Manual Success", "lexnv")
    with t_all:
        _table(df, "port-in numbers", "all")



# ── Registration object section ───────────────────────────────────────────────

REG_OBJECT = "2-58833629"
REG_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-58833629/{id}"
_reg_key = "port_in_lex_urd_registrations_v1"
REG_PROPS = ["number", "first_name", "last_name", "email", "state",
             "portin_status", "portin_message", "is_cancelled",
             "lex_verification_status", "lex_errors", "lex_error_message", "lex_verified_at",
             "urd_status", "urd_filling_errors", "urd_identity_errors",
             "registered_at", "urd_registration_created_at", "urd_registration_updated_at"]
REG_LEX_OK = ("automatic_success", "manual_success")
# Rolka Loube URD filling error meanings seen in HubSpot (registrations store only the codes).
URD_FILLING_CODES = {
    "3": "Duplicate Registration",
    "4": "Submission Type Failure",
    "6": "Previous RegistrationRequestID Failure",
    "21": "Registering Numbering Directory TDN Failure",
}


def _seek_all(obj, props, filters):
    """Search with keyset paging on hs_object_id so results aren't capped at HubSpot's 10,000."""
    out, last = [], "0"
    while True:
        batch = fetch_all(obj, props, filter_groups=[{"filters": filters + [
            {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}])
        out.extend(batch)
        if len(batch) < 10000:
            return out
        last = str(max(int(r["id"]) for r in batch))


def _code_list(v):
    return [c.strip() for c in str(v or "").split(",") if c.strip()]


def _lex_msgs(msg):
    """'• 26 - Unable to verify SSN/TIN' lines → de-duplicated 'code: desc' text."""
    out = []
    for line in str(msg or "").splitlines():
        line = line.strip().lstrip("•").strip()
        m = re.match(r"(\S+)\s+-\s+(.+)", line)
        item = f"{m.group(1)}: {m.group(2)}" if m else line
        if item and item not in out:
            out.append(item)
    return " | ".join(out)


def _load_registrations():
    with dash_spinner("Fetching port-in registrations..."):
        recs = _seek_all(REG_OBJECT, REG_PROPS, [
            {"propertyName": "registration_type", "operator": "EQ", "value": "port_in"}])
    rows = []
    for r in recs:
        p = r.get("properties", {})
        lex = (p.get("lex_verification_status") or "").lower()
        urd = (p.get("urd_status") or "").lower()
        fill = _code_list(p.get("urd_filling_errors"))
        num = str(p.get("number") or "")
        rows.append({
            "Number": f"({num[:3]}) {num[3:6]}-{num[6:]}" if len(num) == 10 and num.isdigit() else (num or "—"),
            "Name": f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "—",
            "Email": p.get("email") or "—",
            "State": p.get("state") or "—",
            "Port-In Status": (p.get("portin_status") or "—").upper() if p.get("portin_status") else "—",
            "LEX Status": lex.replace("_", " ").title() or "—",
            "LEX Error Codes": ", ".join(_code_list(p.get("lex_errors"))),
            "LEX Error Message": _lex_msgs(p.get("lex_error_message")),
            "URD Status": urd.title() or "—",
            "URD Identity Codes": ", ".join(_code_list(p.get("urd_identity_errors"))),
            "URD Filling Codes": ", ".join(fill),
            "URD Filling Meaning": " | ".join(f"{c}: {URD_FILLING_CODES.get(c, 'Unknown')}" for c in fill),
            "Port-In Message": " ".join(str(p.get("portin_message") or "").split()),
            "Cancelled": str(p.get("is_cancelled") or "false").lower() == "true",
            "Registered": pd.to_datetime(p.get("registered_at"), utc=True, errors="coerce"),
            "LEX Verified": pd.to_datetime(p.get("lex_verified_at"), utc=True, errors="coerce"),
            "URD Started": pd.to_datetime(p.get("urd_registration_created_at"), utc=True, errors="coerce"),
            "URD Completed": (pd.to_datetime(p.get("urd_registration_updated_at"), utc=True, errors="coerce")
                              if urd == "completed" else pd.NaT),
            "HubSpot": REG_RECORD_URL.format(id=r.get("id")),
            "_lex": _code_list(p.get("lex_errors")),
            "_ident": _code_list(p.get("urd_identity_errors")),
            "_fill": fill,
            "_lex_ok": lex in REG_LEX_OK,
            "_urd_done": urd == "completed",
        })
    df = pd.DataFrame(rows)
    save_report(_reg_key, {"df": df})
    return df


def registration_section():
    st.markdown(
        "Pulls every **registration** with **Registration Type = Port-In** and shows its **LEX Verification "
        "Status** and **LEX Errors**, **URD Status**, **URD Identity Errors** and **URD Filling Errors** "
        "(e.g. code 21 *Registering Numbering Directory TDN Failure*). The registration object stores the "
        "error **codes**; LEX descriptions come from the LEX Error Message. Registrations and number objects "
        "are not linked in HubSpot, so this section stands on its own."
    )
    c_run, c_info = st.columns([1, 3])
    with c_run:
        run = st.button("Run / Refresh", type="primary", key="reg_run")
    saved = None if run else load_report(_reg_key)
    if run:
        df_all = _load_registrations()
        saved = load_report(_reg_key)
    elif saved is not None:
        df_all = saved["df"]
    else:
        st.info("Click **Run / Refresh** to pull port-in registrations from HubSpot.")
        return
    with c_info:
        st.caption(f"📌 Data pulled {saved_at_label(saved)}")
    if df_all.empty:
        st.warning("No port-in registrations found.")
        return

    # filters
    f1, f2, f3, f4, f5 = st.columns([1.2, 1.2, 1.2, 1.3, 1.5])
    with f1:
        preset = st.selectbox("Registered", PRESETS, index=0, key="reg_preset")
        if preset == "Custom Range":
            d_from = st.date_input("From", value=date.today() - timedelta(days=29), key="reg_from")
            d_to = st.date_input("To", value=date.today(), key="reg_to")
        else:
            d_from, d_to = _range(preset)
    with f2:
        lex_sel = st.multiselect("LEX Status", sorted(df_all["LEX Status"].unique()), default=[], key="reg_lex")
    with f3:
        urd_sel = st.multiselect("URD Status", sorted(df_all["URD Status"].unique()), default=[], key="reg_urd")
    with f4:
        all_codes = sorted({c for col in ("_lex", "_ident", "_fill") for v in df_all[col] for c in v},
                           key=lambda c: (not c.isdigit(), int(c) if c.isdigit() else 0, c))
        code_sel = st.multiselect("Error code (LEX / URD)", all_codes, default=[], key="reg_codes",
                                  help="Matches the code in LEX Errors, URD Identity Errors or URD Filling Errors")
    with f5:
        search = st.text_input("Search number / name / email", "", key="reg_search")
    g1, g2 = st.columns([1.2, 4])
    with g1:
        pi_sel = st.multiselect("Port-In Status", sorted(df_all["Port-In Status"].unique()), default=[], key="reg_pi")
    with g2:
        st.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
        hide_cancelled = st.checkbox("Hide cancelled", value=True, key="reg_hide_cancel")

    df = df_all.copy()
    if d_from and d_to:
        fs = pd.Timestamp(d_from, tz="UTC")
        fe = pd.Timestamp(d_to, tz="UTC") + pd.Timedelta(days=1)
        df = df[(df["Registered"] >= fs) & (df["Registered"] < fe)]
    if lex_sel:
        df = df[df["LEX Status"].isin(lex_sel)]
    if urd_sel:
        df = df[df["URD Status"].isin(urd_sel)]
    if pi_sel:
        df = df[df["Port-In Status"].isin(pi_sel)]
    if hide_cancelled:
        df = df[~df["Cancelled"]]
    if code_sel:
        want = set(code_sel)
        df = df[df.apply(lambda r: bool(want & set(r["_lex"] + r["_ident"] + r["_fill"])), axis=1)]
    if search.strip():
        s = search.strip().lower()
        match = (df["Name"] + " " + df["Number"] + " " + df["Email"]).str.lower().str.contains(s, regex=False)
        digits = re.sub(r"\D", "", s)
        if digits:
            match |= df["Number"].str.replace(r"\D", "", regex=True).str.contains(digits, regex=False)
        df = df[match]

    has_lex = df["_lex"].str.len() > 0
    has_ident = df["_ident"].str.len() > 0
    has_fill = df["_fill"].str.len() > 0
    lex_not_ok = ~df["_lex_ok"]
    urd_not_done = ~df["_urd_done"]

    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.85rem;margin:0.75rem 0 1.5rem;">'
        + _tile("Port-In Registrations", len(df))
        + _tile("LEX Verified", int(df["_lex_ok"].sum()), "#00A651")
        + _tile("LEX Not Verified", int(lex_not_ok.sum()), "#EF4444", "#FEE2E2")
        + _tile("Has LEX Errors", int(has_lex.sum()), "#F59E0B")
        + _tile("Has URD Identity Errors", int(has_ident.sum()), "#8B5CF6")
        + _tile("Has URD Filling Errors", int(has_fill.sum()), "#DC2626")
        + _tile("URD Not Completed", int(urd_not_done.sum()), "#3B82F6")
        + "</div>", unsafe_allow_html=True)

    def _counts(col):
        c = df.groupby(col).size().reset_index(name="Count")
        return c[c[col] != "—"] if not c.empty else c

    def _code_chart(col, label_map=None):
        vals = [c for v in df[col] for c in v]
        if not vals:
            return None
        c = pd.Series(vals, dtype=str).value_counts().reset_index()
        c.columns = ["Error", "Count"]
        if label_map:
            c["Error"] = c["Error"].map(lambda k: f"{k} — {label_map[k]}" if k in label_map else k)
        return c

    ch1, ch2, ch3 = st.columns(3)
    for col_box, col, color in ((ch1, "LEX Status", "#00A651"), (ch2, "URD Status", "#3B82F6"),
                                (ch3, "Port-In Status", "#8B5CF6")):
        with col_box:
            st.markdown(f"#### {col}")
            c = _counts(col)
            if not c.empty:
                st.altair_chart(_bar(c, col, color), use_container_width=True)

    for col, title, color, labels in (("_fill", "URD Filling Errors (by code)", "#DC2626", URD_FILLING_CODES),
                                      ("_ident", "URD Identity Errors (by code)", "#8B5CF6", None),
                                      ("_lex", "LEX Errors (by code)", "#F59E0B", None)):
        st.markdown(f"#### {title}")
        c = _code_chart(col, labels)
        if c is None:
            st.caption("None in this selection.")
        else:
            st.altair_chart(_bar(c, "Error", color), use_container_width=True)

    st.markdown("#### LEX Status × URD Status")
    st.dataframe(pd.crosstab(df["LEX Status"], df["URD Status"], margins=True, margins_name="Total"),
                 use_container_width=True)

    cols = ["Number", "Name", "Email", "State", "Port-In Status",
            "LEX Status", "LEX Error Codes", "LEX Error Message",
            "URD Status", "URD Identity Codes", "URD Filling Codes", "URD Filling Meaning",
            "Port-In Message", "Registered", "LEX Verified", "URD Started", "URD Completed", "Cancelled", "HubSpot"]
    cfg = {
        "HubSpot": st.column_config.LinkColumn("HubSpot", display_text="Open"),
        "Registered": st.column_config.DatetimeColumn("Registered", format="MMM DD, YYYY"),
        "LEX Verified": st.column_config.DatetimeColumn("LEX Verified", format="MMM DD, YYYY"),
        "URD Started": st.column_config.DatetimeColumn("URD Started", format="MMM DD, YYYY"),
        "URD Completed": st.column_config.DatetimeColumn("URD Completed", format="MMM DD, YYYY"),
        "LEX Error Message": st.column_config.TextColumn(width="large"),
        "URD Filling Meaning": st.column_config.TextColumn(width="large"),
    }

    def _reg_table(sub, label, key):
        sub = sub.sort_values("Registered", ascending=False, na_position="last")[cols].reset_index(drop=True)
        st.markdown(f"**{len(sub):,} {label}**")
        st.dataframe(sub, use_container_width=True, hide_index=True, column_config=cfg)
        out = sub.copy()
        for c in ("Registered", "LEX Verified", "URD Started", "URD Completed"):
            out[c] = out[c].dt.strftime("%Y-%m-%d").fillna("")
        st.download_button("Download CSV", out.to_csv(index=False),
                           f"port_in_registrations_{key}_{datetime.now().strftime('%Y%m%d')}.csv",
                           "text/csv", key=f"reg_dl_{key}")

    t_issue, t_fill, t_ident, t_lex, t_lexnv, t_all = st.tabs([
        "Any LEX / URD Issue", "URD Filling Errors", "URD Identity Errors", "LEX Errors",
        "LEX Not Verified", "All Port-In Registrations"])
    with t_issue:
        _reg_table(df[has_lex | has_ident | has_fill | lex_not_ok], "registrations with a LEX or URD issue", "issues")
    with t_fill:
        _reg_table(df[has_fill], "registrations with URD filling errors", "fill")
    with t_ident:
        _reg_table(df[has_ident], "registrations with URD identity errors", "ident")
    with t_lex:
        _reg_table(df[has_lex], "registrations with LEX errors", "lex")
    with t_lexnv:
        _reg_table(df[lex_not_ok], "registrations where LEX is not Automatic/Manual Success", "lexnv")
    with t_all:
        _reg_table(df, "port-in registrations", "all")

sec_num, sec_reg = st.tabs(["🔢 Number Object", "📝 Registration Object"])
with sec_num:
    _number_section()
with sec_reg:
    registration_section()

report_header_close()
