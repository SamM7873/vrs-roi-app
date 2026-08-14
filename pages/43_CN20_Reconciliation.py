import streamlit as st
import pandas as pd
import re
from utils import (require_auth, COMMON_CSS, report_header, report_header_close,
                   log_report_view)

st.set_page_config(page_title="CN20 Reconciliation", layout="wide", page_icon="🔀")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("CN20 Reconciliation")

report_header("CN20 Reconciliation — Streamlit vs Hex",
              "Compare our \"Convo Now without VRS\" export against a Hex \"CN20 accounts\" export",
              section="Customers")

st.markdown(
    "Upload the **Streamlit** export (from *Convo Now without VRS*) and the **Hex** export "
    "(*CN20 accounts only*). They're matched on the **account number** "
    "(`Convo Now #` ↔ `Guest number`). The page shows how much they overlap and characterizes "
    "each side so you can align the two definitions.")


def _digits(v):
    """Normalize a phone/account number to bare digits for a safe join."""
    s = re.sub(r"\D", "", str(v or ""))
    return s.lstrip("1") if len(s) == 11 and s.startswith("1") else s


def _pick_col(cols, *cands):
    low = {c.lower().strip(): c for c in cols}
    for cand in cands:
        if cand.lower() in low:
            return low[cand.lower()]
    # fuzzy contains
    for cand in cands:
        for lc, orig in low.items():
            if cand.lower() in lc:
                return orig
    return None


c1, c2 = st.columns(2)
with c1:
    f_sl = st.file_uploader("Streamlit export (Convo Now without VRS)", type=["csv"], key="sl")
with c2:
    f_hex = st.file_uploader("Hex export (CN20 accounts only)", type=["csv"], key="hx")

if not (f_sl and f_hex):
    st.info("Upload **both** CSVs to run the reconciliation.")
    report_header_close(); st.stop()

sl = pd.read_csv(f_sl, dtype=str).fillna("")
hx = pd.read_csv(f_hex, dtype=str).fillna("")

sl_key = _pick_col(sl.columns, "Convo Now #", "number", "Guest number", "phone")
hx_key = _pick_col(hx.columns, "Guest number", "Convo Now #", "number", "phone")
if not sl_key or not hx_key:
    st.error(f"Couldn't find a number column. Streamlit cols: {list(sl.columns)} · "
             f"Hex cols: {list(hx.columns)}")
    report_header_close(); st.stop()

sl["_k"] = sl[sl_key].map(_digits)
hx["_k"] = hx[hx_key].map(_digits)
sl = sl[sl["_k"] != ""].copy()
hx = hx[hx["_k"] != ""].copy()

sl_keys = set(sl["_k"])
hx_keys = set(hx["_k"])
both = sl_keys & hx_keys
only_sl = sl_keys - hx_keys
only_hx = hx_keys - sl_keys

n_sl, n_hx, n_both = len(sl_keys), len(hx_keys), len(both)
n_only_sl, n_only_hx = len(only_sl), len(only_hx)
total = max(n_only_sl + n_both + n_only_hx, 1)


def _card(col, t, v, s, c):
    col.markdown(f"""<div style="border:1px solid #E6E9F0;border-left:4px solid {c};border-radius:12px;
        padding:16px 18px 13px;background:rgba(127,127,127,0.03);">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;color:#667085;">{t}</div>
        <div style="font-size:2.1rem;font-weight:800;color:{c};line-height:1.1;margin:4px 0 2px;">{v:,}</div>
        <div style="font-size:.72rem;color:#8792A2;">{s}</div></div>""", unsafe_allow_html=True)


k = st.columns(3)
_card(k[0], "Streamlit", n_sl, "Convo Now, no VRS", "#3563E9")
_card(k[1], "Hex", n_hx, "CN20 accounts only", "#E8952A")
_card(k[2], "In both", n_both, f"{n_both/total*100:.0f}% overlap", "#1F9D55")
st.markdown("")

# stacked overlap bar
w_sl = n_only_sl / total * 100
w_both = n_both / total * 100
w_hx = n_only_hx / total * 100
st.markdown(f"""
<div style="display:flex;height:34px;border-radius:8px;overflow:hidden;margin:6px 0 4px;
     font-size:.72rem;font-weight:700;color:#fff;">
  <div style="width:{w_sl}%;background:#3563E9;display:flex;align-items:center;justify-content:center;">
      Only Streamlit · {n_only_sl:,}</div>
  <div style="width:{w_both}%;background:#1F9D55;display:flex;align-items:center;justify-content:center;">
      Both · {n_both:,}</div>
  <div style="width:{w_hx}%;background:#E8952A;display:flex;align-items:center;justify-content:center;">
      Only Hex · {n_only_hx:,}</div>
</div>""", unsafe_allow_html=True)
st.caption(f"The lists overlap on **{n_both:,}** accounts. Low overlap means they measure "
           "**different populations**, not the same list with a small filter difference.")

sl_only_df = sl[sl["_k"].isin(only_sl)].drop(columns=["_k"])
hx_only_df = hx[hx["_k"].isin(only_hx)].drop(columns=["_k"])
both_sl_df = sl[sl["_k"].isin(both)].drop(columns=["_k"])

# ── characterize the only-Streamlit bucket ───────────────────────────────────────
st.markdown("### What the *only-Streamlit* accounts look like")
ct_col = _pick_col(sl.columns, "Credit Type")
st_col = _pick_col(sl.columns, "CN Status", "Status")
lc_col = _pick_col(sl.columns, "Lifecycle")
link_col = _pick_col(sl.columns, "VRS Link", "Link")
b = st.columns(4)
if st_col:
    _card(b[0], "Live", int((sl_only_df[st_col].str.lower() == "live").sum()), "status = Live", "#2DB84B")
if ct_col:
    grp = int(sl_only_df[ct_col].str.contains("group", case=False, na=False).sum())
    _card(b[1], "Credit-Group", grp, "credit type", "#7A5CFF")
if lc_col:
    cust = int(sl_only_df[lc_col].str.contains("customer", case=False, na=False).sum())
    _card(b[2], "Customers", cust, "lifecycle", "#0FB5AE")
if link_col:
    noemail = int(sl_only_df[link_col].str.contains("No email", case=False, na=False).sum())
    _card(b[3], "No email", noemail, "can't associate", "#E5484D")

d1, d2 = st.columns(2)
if ct_col:
    with d1:
        st.markdown("##### By credit type")
        cc = sl_only_df[ct_col].value_counts().rename_axis("Credit Type").reset_index(name="Count")
        st.dataframe(cc, use_container_width=True, hide_index=True,
                     column_config={"Count": st.column_config.ProgressColumn(
                         "Count", min_value=0, max_value=int(cc["Count"].max()) if not cc.empty else 1,
                         format="%d")})
if lc_col:
    with d2:
        st.markdown("##### By lifecycle")
        lcx = sl_only_df[lc_col].value_counts().rename_axis("Lifecycle").reset_index(name="Count")
        st.dataframe(lcx, use_container_width=True, hide_index=True,
                     column_config={"Count": st.column_config.ProgressColumn(
                         "Count", min_value=0, max_value=int(lcx["Count"].max()) if not lcx.empty else 1,
                         format="%d")})

# ── characterize the only-Hex bucket ─────────────────────────────────────────────
st.markdown("### What the *only-Hex* accounts look like")
mins_col = _pick_col(hx.columns, "CN20 mins", "Current mins", "Recent")
ten_col = _pick_col(hx.columns, "Tenure (days)", "Tenure")
hb = st.columns(3)
if mins_col:
    hx_only_df["_mins"] = pd.to_numeric(hx_only_df[mins_col], errors="coerce").fillna(0)
    _card(hb[0], "With usage", int((hx_only_df["_mins"] > 0).sum()),
          f"{mins_col} > 0", "#E8952A")
if ten_col:
    tvals = pd.to_numeric(hx_only_df[ten_col], errors="coerce").dropna()
    if len(tvals):
        _card(hb[1], "Median tenure", int(tvals.median()), "days", "#3563E9")
_card(hb[2], "Only in Hex", n_only_hx, "absent from our list", "#7A5CFF")
st.info("These accounts are absent from our list entirely — the strongest clue to the definition "
        "gap. If Hex decides *no VRS* by an account-level association or a Pendo/product segment "
        "(rather than shared email, like us), it keeps accounts we drop on a shared-email collision.")

# ── downloadable diff lists ──────────────────────────────────────────────────────
st.markdown("### Diff lists")
t1, t2, t3 = st.tabs([f"Only Streamlit ({n_only_sl:,})",
                      f"Only Hex ({n_only_hx:,})",
                      f"In both ({n_both:,})"])
with t1:
    st.dataframe(sl_only_df, use_container_width=True, hide_index=True, height=380)
    st.download_button("📥 only_streamlit.csv", sl_only_df.to_csv(index=False),
                       "only_streamlit.csv", "text/csv")
with t2:
    st.dataframe(hx_only_df.drop(columns=["_mins"], errors="ignore"),
                 use_container_width=True, hide_index=True, height=380)
    st.download_button("📥 only_hex.csv", hx_only_df.drop(columns=["_mins"], errors="ignore").to_csv(index=False),
                       "only_hex.csv", "text/csv")
with t3:
    st.dataframe(both_sl_df, use_container_width=True, hide_index=True, height=380)
    st.download_button("📥 in_both.csv", both_sl_df.to_csv(index=False), "in_both.csv", "text/csv")

st.markdown("---")
st.markdown(
    "##### Questions to align the two reports\n"
    "1. **What defines the CN20 segment?** Every Convo Now account, or only those with usage / a "
    "computed baseline / a minimum tenure?\n"
    "2. **How does Hex decide an account has no VRS?** Shared email (like us), a formal account "
    "association, or a Pendo/product segment?\n"
    "3. **Does Hex include Credit-Group accounts?** Our only-us bucket is nearly all Credit-Group.\n"
    "4. **Is \"Guest number\" the phone number or an account ID?** Confirm it's the same identifier "
    "as our `Convo Now #` so the join is valid.")

report_header_close()
