import streamlit as st
import pandas as pd
from collections import defaultdict
from utils import (
    require_auth, list_all, dash_spinner, norm,
    save_report, load_report, saved_at_label,
    COMMON_CSS, report_header, report_header_close,
)

st.set_page_config(page_title="Address Duplicates", layout="wide", page_icon="🏠")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Address Duplicates",
    "Numbers sharing an address — same language = duplicate, EN + ES = allowed",
    section="Data Ops",
)
report_header_close()


def _lang(v):
    n = norm(v)
    if n in ("en", "english"):
        return "EN"
    if n in ("es", "spanish", "español", "espanol"):
        return "ES"
    return (v or "").strip().upper() or "—"


def _addr_key(p):
    # Match on street + city + state only (zip ignored, so minor zip
    # differences don't split the same physical address).
    street = norm(p.get("street1") or "")
    city   = norm(p.get("city") or "")
    state  = norm(p.get("state") or "")
    if not street or not (city or state):
        return ""  # need at least a street plus a city or state to compare
    return "|".join([street, city, state])


def _addr_display(p):
    line1 = " ".join(a for a in [(p.get("street1") or "").strip(), (p.get("street2") or "").strip()] if a)
    line2 = ", ".join(a for a in [(p.get("city") or "").strip(), (p.get("state") or "").strip(),
                                  (p.get("zip_code") or "").strip()] if a)
    return ", ".join(a for a in [line1, line2] if a) or "—"


with st.expander("ℹ️ How duplicates are decided"):
    st.markdown("""
Every **Number object** with an address is grouped by its **address** (street + city + state — zip is ignored so minor zip differences don't split the same address).
For each address that has **two or more numbers**, the language preference decides:

- **🔴 Duplicate (same language)** — two or more numbers at the same address share the **same**
  language preference (e.g. two **EN** numbers). These are likely genuine duplicates to review.
- **🟢 Bilingual (EN + ES)** — the numbers at the address have **different** languages
  (one EN, one ES). This is expected — a household may keep one English and one Spanish line — so
  it is **not** flagged as a duplicate.

Only VRS/Convo Now numbers with a real address are counted. Numbers with no address are ignored.
""")

# ── Options ───────────────────────────────────────────────────────────────────
oc1, oc2 = st.columns([1.2, 1])
with oc1:
    svc_filter = st.selectbox("Service Type", ["All", "VRS", "Convo Now"])
with oc2:
    status_filter = st.selectbox("Number Status", ["All", "Live", "Suspended"])

run = st.button("Run Address Scan", type="primary")

if run:
    with dash_spinner("Fetching Number objects with addresses…"):
        recs = list_all(
            "2-40974683",
            ["number", "email", "first_name", "last_name", "language_preference",
             "street1", "street2", "city", "state", "zip_code",
             "service_type", "number_status"],
            progress_label="Fetching Number objects",
        )

    # group by address
    groups = defaultdict(list)
    for r in recs:
        p = r.get("properties", {})
        if svc_filter != "All" and norm(p.get("service_type") or "") != norm(svc_filter):
            continue
        if status_filter != "All" and norm(p.get("number_status") or "") != norm(status_filter):
            continue
        key = _addr_key(p)
        if not key:
            continue
        groups[key].append(p)

    rows = []
    for key, members in groups.items():
        if len(members) < 2:
            continue  # single number at an address — nothing to compare
        lang_counts = defaultdict(int)
        for p in members:
            lang_counts[_lang(p.get("language_preference"))] += 1

        # same-language duplicate if any language appears 2+ times
        dup_langs = [lg for lg, c in lang_counts.items() if c >= 2]
        if dup_langs:
            verdict = "🔴 Duplicate (same language)"
        elif len(lang_counts) >= 2:
            verdict = "🟢 Bilingual (EN + ES)"
        else:
            verdict = "🟢 OK"

        addr = _addr_display(members[0])
        lang_summary = ", ".join(f"{lg}×{c}" for lg, c in sorted(lang_counts.items()))
        emails = sorted({(p.get("email") or "").strip().lower() for p in members if (p.get("email") or "").strip()})
        for p in members:
            nm = f"{(p.get('first_name') or '').strip()} {(p.get('last_name') or '').strip()}".strip() or "—"
            rows.append({
                "Verdict":       verdict,
                "Address":       addr,
                "Numbers at Address": len(members),
                "Lang Mix":      lang_summary,
                "Number":        (p.get("number") or "").strip(),
                "Language":      _lang(p.get("language_preference")),
                "Name":          nm,
                "Email":         (p.get("email") or "").strip().lower(),
                "Service Type":  (p.get("service_type") or "").strip(),
                "Status":        (p.get("number_status") or "").strip(),
                "Same Email":    "Yes" if len(emails) <= 1 else "No (different people)",
                "_dup":          verdict.startswith("🔴"),
                "_addr_key":     key,
            })

    df = pd.DataFrame(rows)
    save_report("address_duplicates", {"df": df, "n_numbers": len(recs)})

cached = load_report("address_duplicates")
if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Click **Run Address Scan** to find addresses with multiple numbers.")
    st.stop()

df = cached["df"]
if cached.get("saved_at"):
    st.caption(f"📌 Data as of {saved_at_label(cached)} · click Run to refresh")

# unique-address level stats
addr_level = df.drop_duplicates("_addr_key")
n_multi   = len(addr_level)
n_dup     = int(addr_level["_dup"].sum())
n_biling  = int((addr_level["Verdict"].str.startswith("🟢") & (addr_level["Lang Mix"].str.contains("EN") & addr_level["Lang Mix"].str.contains("ES"))).sum())
dup_nums  = int(df["_dup"].sum())


def tile(label, value, sub="", color="#1F2937"):
    return f"""<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:1rem 1.25rem;">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#6B7280;margin-bottom:0.3rem;">{label}</div>
  <div style="font-size:1.45rem;font-weight:800;color:{color};font-variant-numeric:tabular-nums;">{value}</div>
  <div style="font-size:0.72rem;color:#9CA3AF;">{sub}</div>
</div>"""

st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;margin:0.5rem 0 1.25rem;">
  {tile("Shared Addresses", f"{n_multi:,}", "2+ numbers at one address")}
  {tile("Duplicate Addresses", f"{n_dup:,}", "same language repeated", "#EF4444")}
  {tile("Bilingual (EN + ES)", f"{n_biling:,}", "allowed — not duplicates", "#00A651")}
  {tile("Duplicate Numbers", f"{dup_nums:,}", "numbers in duplicate addresses", "#EF4444")}
</div>""", unsafe_allow_html=True)

# ── Filter + search ───────────────────────────────────────────────────────────
fc, sc = st.columns([1.2, 2])
with fc:
    view = st.selectbox("Show", ["Duplicates only (same language)",
                                 "Bilingual (EN + ES)", "All shared addresses"])
with sc:
    search = st.text_input("Search", placeholder="address, number, name, email…")

d = df.copy()
if view == "Duplicates only (same language)":
    d = d[d["_dup"]]
elif view == "Bilingual (EN + ES)":
    d = d[d["Verdict"].str.startswith("🟢") &
          d["Lang Mix"].str.contains("EN") & d["Lang Mix"].str.contains("ES")]

if search.strip():
    q = search.strip().lower()
    d = d[
        d["Address"].str.lower().str.contains(q, na=False, regex=False) |
        d["Number"].str.lower().str.contains(q, na=False, regex=False) |
        d["Name"].str.lower().str.contains(q, na=False, regex=False) |
        d["Email"].str.lower().str.contains(q, na=False, regex=False)
    ]

st.caption(f"Showing {d['_addr_key'].nunique():,} address(es), {len(d):,} number(s)")
show_cols = ["Verdict", "Address", "Lang Mix", "Number", "Language",
             "Name", "Email", "Service Type", "Status", "Same Email"]
st.dataframe(
    d.sort_values(["_dup", "Address"], ascending=[False, True])[show_cols].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
)
st.download_button("Download CSV", d[show_cols].to_csv(index=False),
                   "address_duplicates.csv", "text/csv")
