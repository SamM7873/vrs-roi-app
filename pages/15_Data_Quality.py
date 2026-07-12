import streamlit as st
import pandas as pd
import re
from collections import defaultdict
from utils import (
    require_auth, list_all, dash_spinner, norm,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="Data Quality", layout="wide", page_icon="🧹")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Data Quality — Contacts",
    "Email format, duplicates, and primary/secondary email vs Number object",
    section="Data Ops",
)
report_header_close()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(e):
    return bool(EMAIL_RE.match((e or "").strip()))


def _split_secondary(v):
    """hs_additional_emails is a semicolon-separated list."""
    if not v:
        return []
    return [x.strip().lower() for x in str(v).replace(",", ";").split(";") if x.strip()]


with st.expander("ℹ️ What each check means"):
    st.markdown("""
This report scans every **Contact** and cross-references its emails against the **Number
objects** (which carry their own `email`). Flags:

- **Invalid primary format** — the primary `email` isn't a well-formed address (typos, spaces, missing `@`/domain).
- **Missing primary email** — the contact has no primary email at all.
- **Duplicate primary email** — the same primary email appears on more than one contact (likely duplicate records).
- **Primary not on any Number** — the primary email doesn't match the `email` on any Number object, so the contact isn't tied to a live/registered number by email.
- **Secondary email mismatch** — the contact has additional email(s) (`hs_additional_emails`) and one or more of them **don't** match any Number object's email.
- **Invalid secondary format** — an additional email is malformed.

A clean contact has a valid, unique primary email that matches a Number object, and any
secondary emails also matching Number objects.
""")

if st.button("Run Data Quality Scan", type="primary"):
    with dash_spinner("Fetching Number objects…"):
        num_recs = list_all("2-40974683", ["number", "email", "service_type", "number_status"],
                            progress_label="Fetching Number objects")
    number_emails = defaultdict(list)   # email -> [numbers]
    for r in num_recs:
        p = r.get("properties", {})
        em = norm(p.get("email") or "")
        num = str(p.get("number") or "").strip()
        if em:
            number_emails[em].append(num)

    with dash_spinner("Fetching Contacts…"):
        con_recs = list_all("contacts",
                            ["email", "hs_additional_emails", "firstname", "lastname",
                             "phone", "createdate"],
                            progress_label="Fetching Contacts")

    # First pass: count primary email occurrences for duplicate detection
    primary_counts = defaultdict(int)
    for r in con_recs:
        em = norm(r.get("properties", {}).get("email") or "")
        if em:
            primary_counts[em] += 1

    rows = []
    for r in con_recs:
        p = r.get("properties", {})
        primary = norm(p.get("email") or "")
        secondary = _split_secondary(p.get("hs_additional_emails"))
        name = f"{(p.get('firstname') or '').strip()} {(p.get('lastname') or '').strip()}".strip() or "—"

        issues = []
        if not primary:
            issues.append("Missing primary email")
        elif not _valid_email(primary):
            issues.append("Invalid primary format")
        if primary and primary_counts.get(primary, 0) > 1:
            issues.append("Duplicate primary email")
        if primary and _valid_email(primary) and primary not in number_emails:
            issues.append("Primary not on any Number")

        sec_mismatch = [s for s in secondary if s not in number_emails]
        sec_invalid  = [s for s in secondary if not _valid_email(s)]
        if sec_mismatch:
            issues.append("Secondary email mismatch")
        if sec_invalid:
            issues.append("Invalid secondary format")

        primary_num = ", ".join(number_emails.get(primary, [])) if primary else ""

        rows.append({
            "Contact ID":         str(r.get("id") or ""),
            "Name":               name,
            "Primary Email":      primary or "—",
            "Primary → Number":   primary_num or "—",
            "Secondary Emails":   "; ".join(secondary) if secondary else "—",
            "Secondary Mismatch": "; ".join(sec_mismatch) if sec_mismatch else "—",
            "Phone":              (p.get("phone") or "").strip(),
            "Issues":             ", ".join(issues) if issues else "✅ Clean",
            "Issue Count":        len(issues),
            # boolean flags for filtering
            "_missing":    "Missing primary email" in issues,
            "_invalid_p":  "Invalid primary format" in issues,
            "_dup":        "Duplicate primary email" in issues,
            "_no_num":     "Primary not on any Number" in issues,
            "_sec_mis":    "Secondary email mismatch" in issues,
            "_sec_inv":    "Invalid secondary format" in issues,
        })

    df = pd.DataFrame(rows)
    save_report("data_quality", {"df": df, "n_numbers": len(num_recs), "n_number_emails": len(number_emails)})

cached = load_report("data_quality")
if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Click **Run Data Quality Scan** to analyze all contacts.")
    st.stop()

df = cached["df"]
if cached.get("saved_at"):
    st.caption(f"📌 Data as of {saved_at_label(cached)} · scanned "
               f"{cached.get('n_number_emails', 0):,} distinct Number emails · click Run to refresh")

total     = len(df)
clean     = int((df["Issue Count"] == 0).sum())
missing   = int(df["_missing"].sum())
invalid_p = int(df["_invalid_p"].sum())
dup       = int(df["_dup"].sum())
no_num    = int(df["_no_num"].sum())
sec_mis   = int(df["_sec_mis"].sum())
sec_inv   = int(df["_sec_inv"].sum())


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.45rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;">{value}</div>
  <div style="font-size:0.72rem;color:#9CA3AF;">{sub}</div>
</div>"""

st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;margin:0.5rem 0 0.85rem;">
  {tile("Total Contacts", f"{total:,}")}
  {tile("Clean", f"{clean:,}", f"{clean/total*100:.0f}% of contacts" if total else "", "#00A651")}
  {tile("With Issues", f"{total-clean:,}", f"{(total-clean)/total*100:.0f}% of contacts" if total else "", "#EF4444")}
  {tile("Duplicate Primary", f"{dup:,}", "same email, 2+ contacts", "#F59E0B")}
</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.85rem;margin-bottom:1.25rem;">
  {tile("Invalid Primary Format", f"{invalid_p:,}", "malformed email", "#EF4444")}
  {tile("Missing Primary", f"{missing:,}", "no email", "#EF4444")}
  {tile("Primary Not on Number", f"{no_num:,}", "no email match", "#F59E0B")}
  {tile("Secondary Mismatch", f"{sec_mis:,}", "2nd email ≠ Number", "#F59E0B")}
  {tile("Invalid Secondary", f"{sec_inv:,}", "malformed 2nd email", "#EF4444")}
</div>""", unsafe_allow_html=True)

# ── Filter + search ───────────────────────────────────────────────────────────
fcol, scol = st.columns([1.4, 2])
with fcol:
    view = st.selectbox("Show", [
        "All with issues", "All contacts", "Clean only",
        "Invalid primary format", "Missing primary email", "Duplicate primary email",
        "Primary not on any Number", "Secondary email mismatch", "Invalid secondary format",
    ])
with scol:
    search = st.text_input("Search", placeholder="name, email, phone, contact ID…",
                           label_visibility="visible")

view_map = {
    "Invalid primary format": "_invalid_p",
    "Missing primary email": "_missing",
    "Duplicate primary email": "_dup",
    "Primary not on any Number": "_no_num",
    "Secondary email mismatch": "_sec_mis",
    "Invalid secondary format": "_sec_inv",
}
d = df.copy()
if view == "All with issues":
    d = d[d["Issue Count"] > 0]
elif view == "Clean only":
    d = d[d["Issue Count"] == 0]
elif view in view_map:
    d = d[d[view_map[view]]]

if search.strip():
    q = search.strip().lower()
    d = d[
        d["Name"].str.lower().str.contains(q, na=False, regex=False) |
        d["Primary Email"].str.lower().str.contains(q, na=False, regex=False) |
        d["Secondary Emails"].str.lower().str.contains(q, na=False, regex=False) |
        d["Phone"].str.lower().str.contains(q, na=False, regex=False) |
        d["Contact ID"].str.contains(q, na=False, regex=False)
    ]

st.caption(f"Showing {len(d):,} contact(s)")
display_cols = ["Name", "Primary Email", "Primary → Number", "Secondary Emails",
                "Secondary Mismatch", "Phone", "Issues", "Contact ID"]
st.dataframe(
    d.sort_values("Issue Count", ascending=False)[display_cols].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
)
st.download_button(
    "Download CSV",
    d[display_cols].to_csv(index=False),
    "contact_data_quality.csv",
    "text/csv",
)
