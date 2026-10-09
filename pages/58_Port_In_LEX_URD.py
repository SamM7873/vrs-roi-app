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
              "Port-in numbers from the number object — LEX status, LEX error message and URD identified message",
              section="Numbers")

NUM_OBJECT = "2-40974683"
HS_RECORD_URL = "https://app.hubspot.com/contacts/46779160/record/2-40974683/{id}"
_key = "port_in_lex_urd_v1"

PROPS = ["number", "first_name", "last_name", "email", "state",
         "number_status", "usage_type", "bandwidth_order_type",
         "portin_status", "portin_message",
         "lex_verification_status", "lex_error_message",
         "urd_status", "urd_identity_error_message", "urd_filling_error_message",
         "number_created_at", "registered_at", "hs_createdate"]

LEX_OK = ("Automatic Success", "Manual Success")

st.markdown(
    "Pulls every number object with **Bandwidth Order Type = portins** and shows the "
    "**LEX Verification Status**, **LEX Error Message** and **URD Identified Message** "
    "(`URD Identity Error Message`, e.g. *\"identified as a user already registered in the URD\"*). "
    "Messages are de-duplicated and split into individual error codes so issues can be grouped."
)

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
            "URD Filling Error": _fmt_errors(urd_fill_errs),
            "Port-In Message": " ".join(str(p.get("portin_message") or "").split()),
            "Created": created,
            "HubSpot": HS_RECORD_URL.format(id=r.get("id")),
            "_lex_pairs": lex_errs,
            "_urd_pairs": urd_id_errs,
            "_lex_ok": lex in LEX_OK,
            "_urd_done": urd.lower() == "completed",
        })
    df = pd.DataFrame(rows)
    save_report(_key, {"df": df})
    return df


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
    report_header_close()
    st.stop()
with c_info:
    st.caption(f"📌 Data pulled {saved_at_label(saved)}")

if df_all.empty:
    st.warning("No port-in number objects found.")
    report_header_close()
    st.stop()

# ── filters ───────────────────────────────────────────────────────────────────

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


f1, f2, f3, f4 = st.columns([1.3, 1.3, 1.3, 1.6])
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
if search.strip():
    s = search.strip().lower()
    hay = (df["Number"] + " " + df["Name"] + " " + df["Email"] + " "
           + df["LEX Error Message"] + " " + df["URD Identified Message"]).str.lower()
    df = df[hay.str.contains(s, regex=False)]

has_lex_err = df["LEX Error Message"] != ""
has_urd_msg = df["URD Identified Message"] != ""
lex_not_ok = ~df["_lex_ok"]
urd_not_done = ~df["_urd_done"]

# ── KPI tiles ─────────────────────────────────────────────────────────────────

def _tile(label, val, color="#1F2937", border="#E5E7EB"):
    return (f'<div style="background:#fff;border:1px solid {border};border-radius:10px;padding:1rem 1.25rem;">'
            f'<div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;'
            f'color:#6B7280;margin-bottom:0.25rem;">{label}</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{color};">{val:,}</div></div>')


st.markdown(
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.85rem;margin:0.75rem 0 1.5rem;">'
    + _tile("Port-In Numbers", len(df))
    + _tile("LEX Verified", int(df["_lex_ok"].sum()), "#00A651")
    + _tile("LEX Not Verified", int(lex_not_ok.sum()), "#EF4444", "#FEE2E2")
    + _tile("Has LEX Error Msg", int(has_lex_err.sum()), "#F59E0B")
    + _tile("Has URD Identified Msg", int(has_urd_msg.sum()), "#8B5CF6")
    + _tile("URD Not Completed", int(urd_not_done.sum()), "#3B82F6")
    + "</div>", unsafe_allow_html=True)

# ── charts ────────────────────────────────────────────────────────────────────

def _bar(counts, col, color):
    return alt.Chart(counts).mark_bar(color=color, cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        x=alt.X("Count:Q", title="Numbers"),
        y=alt.Y(f"{col}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=420)),
        tooltip=[col, "Count"],
    ).properties(height=max(120, len(counts) * 30))


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

# ── LEX status × URD status cross-tab ─────────────────────────────────────────

st.markdown("#### LEX Status × URD Status")
xt = pd.crosstab(df["LEX Status"], df["URD Status"], margins=True, margins_name="Total")
st.dataframe(xt, use_container_width=True)

# ── detail tabs ───────────────────────────────────────────────────────────────

display_cols = ["Number", "Name", "Email", "State", "Port-In Status", "Number Status",
                "LEX Status", "LEX Error Codes", "LEX Error Message",
                "URD Status", "URD Identified Codes", "URD Identified Message", "URD Filling Error",
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


t_issue, t_urd, t_lex, t_lexnv, t_all = st.tabs([
    "Any LEX / URD Issue", "URD Identified Message", "LEX Error Message", "LEX Not Verified", "All Port-Ins"])
with t_issue:
    _table(df[has_lex_err | has_urd_msg | lex_not_ok], "port-ins with a LEX or URD issue", "issues")
with t_urd:
    _table(df[has_urd_msg], "port-ins with a URD identified message", "urd")
with t_lex:
    _table(df[has_lex_err], "port-ins with a LEX error message", "lex")
with t_lexnv:
    _table(df[lex_not_ok], "port-ins where LEX is not Automatic/Manual Success", "lexnv")
with t_all:
    _table(df, "port-in numbers", "all")

report_header_close()
