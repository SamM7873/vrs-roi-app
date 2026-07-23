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


def _lang(v):
    n = norm(v)
    if n in ("en", "english"):
        return "EN"
    if n in ("es", "spanish", "español", "espanol"):
        return "ES"
    return (v or "").strip().upper() or "—"


def _addr_key(p):
    """Match on street + city + state (zip ignored). Street+city/state, or
    city+state if street is blank."""
    street = norm(p.get("street1") or "")
    city   = norm(p.get("city") or "")
    state  = norm(p.get("state") or "")
    if street and (city or state):
        return "|".join([street, city, state])
    if city and state:
        return "|".join(["", city, state])
    return ""


def _addr_display(p):
    line1 = " ".join(a for a in [(p.get("street1") or "").strip(), (p.get("street2") or "").strip()] if a)
    line2 = ", ".join(a for a in [(p.get("city") or "").strip(), (p.get("state") or "").strip(),
                                  (p.get("zip_code") or "").strip()] if a)
    return ", ".join(a for a in [line1, line2] if a) or "—"


def _domain(e):
    e = (e or "").strip().lower()
    return e.split("@")[-1] if "@" in e else ""


# Common consumer email providers and frequent misspellings that should be corrected
KNOWN_DOMAINS = {
    "gmail.com", "icloud.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "comcast.net", "me.com", "live.com", "msn.com", "sbcglobal.net",
    "verizon.net", "att.net", "protonmail.com", "proton.me", "mac.com",
}
DOMAIN_TYPOS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.co": "gmail.com",
    "gmail.con": "gmail.com", "gmaill.com": "gmail.com", "gmail.cm": "gmail.com",
    "gmailcom": "gmail.com", "gnail.com": "gmail.com", "gamil.com": "gmail.com",
    "googlemail.com": "gmail.com",
    "iclould.com": "icloud.com", "icloud.co": "icloud.com", "iclod.com": "icloud.com",
    "icoud.com": "icloud.com", "icloude.com": "icloud.com",
    "yahoo.co": "yahoo.com", "yaho.com": "yahoo.com", "yahoo.con": "yahoo.com",
    "ymail.com": "yahoo.com", "yahooo.com": "yahoo.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com", "hotmail.co": "hotmail.com",
    "hotmail.con": "hotmail.com", "hotmil.com": "hotmail.com",
    "outlook.co": "outlook.com", "outlok.com": "outlook.com", "outloo.com": "outlook.com",
    "aol.co": "aol.com", "aol.com": "aol.com",
}


# Root names of common providers, to catch cut-off domains like "@gmail", "@gami", "@icloud"
PROVIDER_ROOTS = {
    "gmail": "gmail.com", "googlemail": "gmail.com", "icloud": "icloud.com",
    "yahoo": "yahoo.com", "ymail": "yahoo.com", "hotmail": "hotmail.com",
    "outlook": "outlook.com", "aol": "aol.com", "live": "live.com",
    "msn": "msn.com", "comcast": "comcast.net", "me": "me.com", "mac": "mac.com",
    "proton": "proton.me", "protonmail": "protonmail.com",
}


def _domain_issue(e):
    """Return (suggestion, reason) if the domain looks wrong, else ('', '')."""
    d = _domain(e)
    if not d:
        return "", ""
    # 1) known misspelling with a fix
    if d in DOMAIN_TYPOS:
        return DOMAIN_TYPOS[d], "typo"
    # 2) no TLD at all — e.g. "gmail", "gami", "alo"
    if "." not in d:
        root = d
        if root in PROVIDER_ROOTS:
            return PROVIDER_ROOTS[root], "missing .com"
        # fuzzy: cut-off of a known provider (gmai, gmial, iclou, hotmai…)
        for pr, full in PROVIDER_ROOTS.items():
            if root and (pr.startswith(root) or root.startswith(pr[:4])) and len(root) >= 3:
                return full, "incomplete domain"
        return "", "no TLD (missing .com)"
    # 3) has a dot but the provider root is a near-match to a known one
    root = d.split(".")[0]
    if root not in PROVIDER_ROOTS and d not in KNOWN_DOMAINS:
        for pr, full in PROVIDER_ROOTS.items():
            # close but not exact (e.g. gmial, gmai, iclould)
            if root != pr and (pr.startswith(root[:4]) or root.startswith(pr[:4])) and abs(len(root) - len(pr)) <= 3 and len(root) >= 3:
                return full, "likely typo"
    return "", ""


with st.expander("ℹ️ What each check means"):
    st.markdown("""
This report scans every **Contact** and cross-references its emails against the **Number
objects** (which carry their own `email`). Flags:

- **Invalid primary format** — the primary `email` isn't a well-formed address (typos, spaces, missing `@`/domain).
- **Missing primary email** — the contact has no primary email at all.
- **Duplicate primary email** — the same primary email appears on more than one contact (likely duplicate records).
- **Primary not on any Number** — the primary email doesn't match the `email` on any Number object, so the contact isn't tied to a live/registered number by email.
- **Suspicious domain** — the email domain looks wrong: a misspelled provider (`gmial.com`, `iclould.com`), a cut-off domain with no `.com` (`@gami`, `@alo`, `@gmail`), or a near-match typo. The **Domain Suggestion** column shows the likely correct address.
- **Bounced / bad address** — HubSpot's own record from **real email sends**: the address hard-bounced or is flagged invalid (`hs_email_bad_address`, `hs_email_hard_bounce_reason_enum`, `hs_email_bounce`). This is actual deliverability, not a guess — the strongest signal an email is dead. The **Deliverability** column shows the reason.
- **Quarantined** — HubSpot blocked the address for anti-abuse reasons (spam trap, complaints).
- **Secondary email mismatch** — the contact has additional email(s) (`hs_additional_emails`) and one or more of them **don't** match any Number object's email.
- **Invalid secondary format** — an additional email is malformed.

A clean contact has a valid, unique primary email that matches a Number object, and any
secondary emails also matching Number objects.
""")

if st.button("Run Data Quality Scan", type="primary"):
    # list_all renders its own progress card — don't wrap it in dash_spinner
    num_recs = list_all(
        "2-40974683",
        ["number", "email", "service_type", "number_status",
         "first_name", "last_name", "language_preference",
         "street1", "street2", "city", "state", "zip_code"],
        progress_label="Fetching Number objects")
    number_emails = defaultdict(list)   # email -> [numbers]
    for r in num_recs:
        p = r.get("properties", {})
        em = norm(p.get("email") or "")
        num = str(p.get("number") or "").strip()
        if em:
            number_emails[em].append(num)

    # ── Address-duplicate analysis (same address, same language = duplicate) ──
    addr_groups = defaultdict(list)
    for r in num_recs:
        p = r.get("properties", {})
        akey = _addr_key(p)
        if akey:
            addr_groups[akey].append(p)

    addr_rows = []
    for akey, members in addr_groups.items():
        if len(members) < 2:
            continue
        lang_counts = defaultdict(int)
        for p in members:
            lang_counts[_lang(p.get("language_preference"))] += 1
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
            addr_rows.append({
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
                "_addr_key":     akey,
            })
    addr_df = pd.DataFrame(addr_rows)

    con_recs = list_all("contacts",
                        ["email", "hs_additional_emails", "firstname", "lastname",
                         "phone", "createdate",
                         # HubSpot's own deliverability signals from real sends
                         "hs_email_bad_address", "hs_email_bounce",
                         "hs_email_hard_bounce_reason_enum", "hs_email_quarantined",
                         "hs_email_quarantined_reason", "hs_email_optout"],
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

        # Domain check on the primary email (typo / cut-off / missing TLD)
        dom_fix, dom_reason = _domain_issue(primary)
        domain_suggestion = ""
        if dom_reason:
            issues.append("Suspicious domain")
            local = primary.split("@")[0] if "@" in primary else primary
            domain_suggestion = (f"{local}@{dom_fix} ({dom_reason})" if dom_fix
                                 else f"{dom_reason}")

        # HubSpot deliverability signals (real send outcomes)
        bad_addr   = str(p.get("hs_email_bad_address") or "").lower() == "true"
        bounce_cnt = 0
        try:
            bounce_cnt = int(float(p.get("hs_email_bounce") or 0))
        except (TypeError, ValueError):
            bounce_cnt = 0
        hard_reason = (p.get("hs_email_hard_bounce_reason_enum") or "").strip()
        quarantined = str(p.get("hs_email_quarantined") or "").lower() == "true"
        quar_reason = (p.get("hs_email_quarantined_reason") or "").strip()

        deliver_status = "OK"
        if bad_addr or hard_reason:
            issues.append("Bounced / bad address")
            deliver_status = "Hard bounce" + (f" ({hard_reason})" if hard_reason else " (invalid)")
        elif bounce_cnt > 0:
            issues.append("Bounced / bad address")
            deliver_status = f"Bounced ×{bounce_cnt}"
        if quarantined:
            issues.append("Quarantined")
            deliver_status = "Quarantined" + (f" ({quar_reason})" if quar_reason else "")

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
            "Email Domain":       _domain(primary) or "—",
            "Domain Suggestion":  domain_suggestion or "—",
            "Deliverability":     deliver_status,
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
            "_domain":     "Suspicious domain" in issues,
            "_bounced":    "Bounced / bad address" in issues,
            "_quarantined":"Quarantined" in issues,
        })

    df = pd.DataFrame(rows)
    save_report("data_quality", {"df": df, "addr_df": addr_df,
                                 "n_numbers": len(num_recs), "n_number_emails": len(number_emails)})

cached = load_report("data_quality")
if cached is None or cached.get("df") is None or cached["df"].empty:
    st.info("Click **Run Data Quality Scan** to analyze all contacts.")
    st.stop()

tab_email, tab_addr = st.tabs(["📧 Email Quality", "🏠 Address Duplicates"])
df = cached["df"]

with tab_email:

    # Backfill any columns/flags added in newer versions so a stale saved
    # report never crashes the page — re-run the scan to fully populate them.
    for _col, _default in [
        ("Email Domain", "—"), ("Domain Suggestion", "—"), ("Deliverability", "—"),
        ("_domain", False), ("_bounced", False), ("_quarantined", False),
    ]:
        if _col not in df.columns:
            df[_col] = _default

    if cached.get("saved_at"):
        st.caption(f"📌 Data as of {saved_at_label(cached)} · scanned "
                   f"{cached.get('n_number_emails', 0):,} distinct Number emails · click Run to refresh")

    _stale = df["Deliverability"].eq("—").all()
    if _stale:
        st.info("This saved report predates the domain & deliverability checks — "
                "click **Run Data Quality Scan** to populate them.")

    total     = len(df)
    clean     = int((df["Issue Count"] == 0).sum())
    missing   = int(df["_missing"].sum())
    invalid_p = int(df["_invalid_p"].sum())
    dup       = int(df["_dup"].sum())
    no_num    = int(df["_no_num"].sum())
    sec_mis   = int(df["_sec_mis"].sum())
    sec_inv   = int(df["_sec_inv"].sum())
    bad_dom   = int(df["_domain"].sum())
    bounced   = int(df["_bounced"].sum()) if "_bounced" in df.columns else 0
    quaran    = int(df["_quarantined"].sum()) if "_quarantined" in df.columns else 0


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
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.85rem;margin-bottom:0.85rem;">
      {tile("Invalid Primary Format", f"{invalid_p:,}", "malformed email", "#EF4444")}
      {tile("Missing Primary", f"{missing:,}", "no email", "#EF4444")}
      {tile("Suspicious Domain", f"{bad_dom:,}", "typo / missing .com (e.g. @gami)", "#EF4444")}
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.85rem;margin-bottom:0.85rem;">
      {tile("Bounced / Bad Address", f"{bounced:,}", "HubSpot real send bounces", "#EF4444")}
      {tile("Quarantined", f"{quaran:,}", "blocked by HubSpot", "#EF4444")}
      {tile("Primary Not on Number", f"{no_num:,}", "no email match", "#F59E0B")}
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.85rem;margin-bottom:1.25rem;">
      {tile("Secondary Mismatch", f"{sec_mis:,}", "2nd email ≠ Number", "#F59E0B")}
      {tile("Invalid Secondary", f"{sec_inv:,}", "malformed 2nd email", "#EF4444")}
    </div>""", unsafe_allow_html=True)

    # ── Email provider breakdown ─────────────────────────────────────────────────
    with st.expander("📧 Email provider breakdown (by domain)"):
        dom_counts = (
            df[df["Email Domain"] != "—"]["Email Domain"]
            .value_counts().reset_index()
        )
        dom_counts.columns = ["Domain", "Contacts"]
        st.caption(f"{len(dom_counts):,} distinct domains across {int(dom_counts['Contacts'].sum()):,} contacts")
        st.dataframe(dom_counts.head(50), use_container_width=True, hide_index=True)

    # ── Filter + search ───────────────────────────────────────────────────────────
    fcol, scol = st.columns([1.4, 2])
    with fcol:
        view = st.selectbox("Show", [
            "All with issues", "All contacts", "Clean only",
            "Bounced / bad address", "Quarantined",
            "Invalid primary format", "Missing primary email", "Suspicious domain",
            "Duplicate primary email", "Primary not on any Number",
            "Secondary email mismatch", "Invalid secondary format",
        ])
    with scol:
        search = st.text_input("Search", placeholder="name, email, phone, contact ID…",
                               label_visibility="visible")

    view_map = {
        "Bounced / bad address": "_bounced",
        "Quarantined": "_quarantined",
        "Invalid primary format": "_invalid_p",
        "Missing primary email": "_missing",
        "Suspicious domain": "_domain",
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
    display_cols = [c for c in
                    ["Name", "Primary Email", "Email Domain", "Domain Suggestion",
                     "Deliverability", "Primary → Number", "Secondary Emails",
                     "Secondary Mismatch", "Phone", "Issues", "Contact ID"]
                    if c in d.columns]
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
    from utils import pdf_download_button
    pdf_download_button(d[display_cols], "data_quality.pdf", "Contact Data Quality", key="dq")


with tab_addr:
    addr_df = cached.get("addr_df")
    if addr_df is None or addr_df.empty:
        st.info("No addresses are shared by two or more numbers, or this saved report "
                "predates the address check — click **Run Data Quality Scan** to populate it.")
    else:
        st.markdown("""
Numbers grouped by **address** (street + city + state, zip ignored). At an address with 2+ numbers:
**same language repeated = 🔴 duplicate**; different languages (**EN + ES**) = 🟢 allowed.
""")
        a_addr_level = addr_df.drop_duplicates("_addr_key")
        a_multi   = len(a_addr_level)
        a_dup     = int(a_addr_level["_dup"].sum())
        a_biling  = int((a_addr_level["Verdict"].str.startswith("🟢") &
                         a_addr_level["Lang Mix"].str.contains("EN") &
                         a_addr_level["Lang Mix"].str.contains("ES")).sum())
        a_dupnums = int(addr_df["_dup"].sum())

        st.markdown(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;margin:0.5rem 0 1.25rem;">
  {tile("Shared Addresses", f"{a_multi:,}", "2+ numbers at one address")}
  {tile("Duplicate Addresses", f"{a_dup:,}", "same language repeated", "#EF4444")}
  {tile("Bilingual (EN + ES)", f"{a_biling:,}", "allowed — not duplicates", "#00A651")}
  {tile("Duplicate Numbers", f"{a_dupnums:,}", "numbers in duplicate addresses", "#EF4444")}
</div>""", unsafe_allow_html=True)

        afc, asc = st.columns([1.2, 2])
        with afc:
            a_view = st.selectbox("Show", ["Duplicates only (same language)",
                                           "Bilingual (EN + ES)", "All shared addresses"],
                                  key="addr_view")
        with asc:
            a_search = st.text_input("Search", placeholder="address, number, name, email…",
                                     key="addr_search")

        ad = addr_df.copy()
        if a_view == "Duplicates only (same language)":
            ad = ad[ad["_dup"]]
        elif a_view == "Bilingual (EN + ES)":
            ad = ad[ad["Verdict"].str.startswith("🟢") &
                    ad["Lang Mix"].str.contains("EN") & ad["Lang Mix"].str.contains("ES")]
        if a_search.strip():
            q = a_search.strip().lower()
            ad = ad[
                ad["Address"].str.lower().str.contains(q, na=False, regex=False) |
                ad["Number"].str.lower().str.contains(q, na=False, regex=False) |
                ad["Name"].str.lower().str.contains(q, na=False, regex=False) |
                ad["Email"].str.lower().str.contains(q, na=False, regex=False)
            ]

        st.caption(f"Showing {ad['_addr_key'].nunique():,} address(es), {len(ad):,} number(s)")
        a_cols = ["Verdict", "Address", "Lang Mix", "Number", "Language",
                  "Name", "Email", "Service Type", "Status", "Same Email"]
        st.dataframe(
            ad.sort_values(["_dup", "Address"], ascending=[False, True])[a_cols].reset_index(drop=True),
            use_container_width=True, hide_index=True,
        )
        st.download_button("Download CSV", ad[a_cols].to_csv(index=False),
                           "address_duplicates.csv", "text/csv", key="addr_dl")
