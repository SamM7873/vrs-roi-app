import streamlit as st
import pandas as pd
import time
from utils import (require_auth, is_app_admin, COMMON_CSS, report_header,
                   report_header_close, log_report_view, save_report, load_report,
                   saved_at_label, fetch_all, dash_spinner)

st.set_page_config(page_title="Interactions vs Tickets", layout="wide", page_icon="🔀")
st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()
log_report_view("Interactions vs Tickets")

report_header("Interactions vs Tickets",
              "Reconcile Convo360 incoming interactions against tickets created",
              section="Analytics")

if not is_app_admin():
    st.warning("This page is restricted to administrators.")
    report_header_close(); st.stop()

SAVE_KEY = "interactions_vs_tickets"
TTL = 48 * 3600

st.markdown(
    "Upload **both** exports to compare **incoming interactions** (Convo360 — videophone, "
    "videochat, chat) against **tickets created** (HubSpot audit log). The goal: several "
    "contacts from the same consumer should roll up into **one ticket**, not one ticket per "
    "call. A high interactions-per-ticket ratio = good consolidation; near 1:1 = over-ticketing.")

st.info("These two exports share no consumer key, so matching is at the **day** level "
        "(and agent, best-effort) — not per individual consumer.", icon="ℹ️")

c1, c2 = st.columns(2)
with c1:
    up_conv = st.file_uploader("Convo360 interaction CSV", type=["csv"], key="ivt_conv")
with c2:
    up_tick = st.file_uploader("HubSpot ticket audit CSV", type=["csv"], key="ivt_tick")
run = st.button("▶ Run comparison", type="primary", disabled=(up_conv is None or up_tick is None))


def _find(cols, *names):
    low = {c.lower(): c for c in cols}
    for n in names:
        for lc, orig in low.items():
            if n in lc:
                return orig
    return None


def _parse_conv(file):
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    dcol = _find(df.columns, "date")
    tcol = _find(df.columns, "type")
    acol = _find(df.columns, "agent", "rep")
    if not dcol:
        return "Convo360: no date column found."
    ccol = _find(df.columns, "customer name", "customer", "name")
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    df = df[df["_day"].notna()].copy()
    df["_type"] = df[tcol].str.strip() if tcol else "—"
    df["_agent"] = df[acol].str.strip() if acol else "—"
    df["_customer"] = df[ccol].str.strip() if ccol else ""
    return df[["_day", "_type", "_agent", "_customer"]]


def _parse_tick(file):
    df = pd.read_csv(file, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    ucol = _find(df.columns, "modified by", "user", "performed")
    dcol = _find(df.columns, "date")
    if not dcol:
        return "Ticket audit: no date column found."
    for c in ("Subcategory", "Action", "Target object id"):
        if c not in df.columns:
            df[c] = ""
    df["_day"] = pd.to_datetime(df[dcol], errors="coerce").dt.date
    df = df[(df["Subcategory"] == "Ticket") & df["_day"].notna()].copy()
    df["_agent"] = df[ucol].str.split("@").str[0].str.strip() if ucol else "—"
    df["_created"] = df["Action"] == "Create"
    return df[["_day", "_agent", "Action", "_created", "Target object id"]]


if run and up_conv is not None and up_tick is not None:
    cv, tk = _parse_conv(up_conv), _parse_tick(up_tick)
    if isinstance(cv, str):
        st.error(cv); report_header_close(); st.stop()
    if isinstance(tk, str):
        st.error(tk); report_header_close(); st.stop()
    save_report(SAVE_KEY, {"cv": cv, "tk": tk})
    saved = load_report(SAVE_KEY)
else:
    saved = load_report(SAVE_KEY)
    if saved is None:
        st.info("Upload both CSVs, then click **▶ Run comparison**.")
        report_header_close(); st.stop()
    if time.time() - (saved.get("saved_at") or 0) > TTL:
        st.warning("Saved comparison is older than 48h — re-upload and Run.")
        report_header_close(); st.stop()
    cv, tk = saved["cv"], saved["tk"]

if saved and saved.get("saved_at"):
    st.caption(f"📌 Saved {saved_at_label(saved)} · upload new CSVs + Run to refresh.")

# ── interaction-type filter ─────────────────────────────────────────────────────
types = sorted(cv["_type"].unique())
pick = st.multiselect("Interaction types to count", types, default=types,
                      help="Choose which Convo360 types count as an 'incoming interaction'.")
cvf = cv[cv["_type"].isin(pick)] if pick else cv

# align to the overlapping date window
d_lo = max(cvf["_day"].min(), tk["_day"].min())
d_hi = min(cvf["_day"].max(), tk["_day"].max())
st.caption(f"Overlapping window: **{d_lo:%b %d}–{d_hi:%b %d, %Y}**.")
cvf = cvf[(cvf["_day"] >= d_lo) & (cvf["_day"] <= d_hi)]
tkf = tk[(tk["_day"] >= d_lo) & (tk["_day"] <= d_hi)]

n_int = len(cvf)
n_created = int(tkf["_created"].sum())
n_touched = tkf["Target object id"].replace("", pd.NA).nunique()
ratio = (n_int / n_created) if n_created else None

# ── KPIs ────────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("📞 Incoming interactions", f"{n_int:,}")
k[1].metric("🎫 Tickets created", f"{n_created:,}")
k[2].metric("Unique tickets touched", f"{n_touched:,}")
k[3].metric("Interactions per ticket", f"{ratio:.1f}" if ratio else "—",
            help="Interactions ÷ tickets created. Higher = better consolidation; ~1 = one ticket per contact.")

if ratio is not None:
    if ratio >= 2:
        st.success(f"✅ ~{ratio:.1f} interactions per ticket — good consolidation "
                   "(multiple contacts roll into fewer tickets).")
    elif ratio >= 1.3:
        st.warning(f"🟡 ~{ratio:.1f} interactions per ticket — moderate consolidation.")
    elif ratio >= 1.0:
        st.warning(f"🟡 ~{ratio:.1f} interactions per ticket — roughly one ticket per contact "
                   "(little consolidation).")
    else:
        st.error(f"🔴 ~{ratio:.1f} interactions per ticket — **more tickets than incoming "
                 f"interactions** ({n_created:,} tickets vs {n_int:,} interactions). Either agents "
                 "open multiple tickets per contact, or many tickets come from **other channels** "
                 "(email, chat, proactive outreach) not in the Convo360 export.")
st.caption("⚠️ Tickets can originate from channels beyond Convo360 (email, manual, follow-ups), "
           "so a ratio below 1 isn't proof of over-ticketing on its own — it's a flag to investigate.")

# ── interaction type breakdown ──────────────────────────────────────────────────
st.markdown("##### Incoming interactions by type")
bytype = cvf["_type"].value_counts().rename_axis("Type").reset_index(name="Interactions")
bytype["%"] = (bytype["Interactions"] / len(cvf) * 100).round(1) if len(cvf) else 0
st.dataframe(bytype, use_container_width=True, hide_index=True)

# ── day-by-day reconciliation ───────────────────────────────────────────────────
st.markdown("##### Day-by-day — interactions vs tickets created")
di = cvf.groupby("_day").size().rename("Interactions")
dc = tkf[tkf["_created"]].groupby("_day").size().rename("Tickets created")
daily = pd.concat([di, dc], axis=1).fillna(0).astype(int).reset_index()
daily = daily.rename(columns={"_day": "Day"})
daily["Interactions/ticket"] = daily.apply(
    lambda r: round(r["Interactions"] / r["Tickets created"], 1) if r["Tickets created"] else 0, axis=1)
daily["Day"] = daily["Day"].astype(str)
st.dataframe(daily, use_container_width=True, hide_index=True)
st.bar_chart(daily.set_index("Day")[["Interactions", "Tickets created"]], height=260)

# ── per-agent (best effort — names differ between systems) ──────────────────────
st.markdown("##### By agent (best-effort — Convo360 names vs HubSpot usernames differ)")
ai = cvf.groupby("_agent").size().rename("Interactions").reset_index()
ac = tkf[tkf["_created"]].groupby("_agent").size().rename("Tickets created").reset_index()
st.columns(2)[0].caption("Convo360 handled")
cA, cB = st.columns(2)
with cA:
    st.markdown("**Convo360 — interactions by agent**")
    st.dataframe(ai.sort_values("Interactions", ascending=False),
                 use_container_width=True, hide_index=True, height=300)
with cB:
    st.markdown("**HubSpot — tickets created by agent**")
    st.dataframe(ac.sort_values("Tickets created", ascending=False),
                 use_container_width=True, hide_index=True, height=300)

st.download_button("📥 Download daily reconciliation (CSV)", daily.to_csv(index=False),
                   "interactions_vs_tickets.csv", "text/csv")

# ── per-consumer match (live HubSpot) ───────────────────────────────────────────
st.markdown("---")
st.markdown("### 🧩 Per-consumer match (live)")
st.caption("Match each Convo360 **Customer Name** to the tickets **created in the window** by "
           "pulling ticket subjects/descriptions live from HubSpot. Answers: for a consumer who "
           "contacted N times, how many tickets were opened?")

if "_customer" not in cvf.columns or cvf["_customer"].str.strip().eq("").all():
    st.info("The Convo360 export has no Customer Name column — can't match per consumer.")
elif st.button("🔗 Run per-consumer match (queries HubSpot)"):
    ids = sorted(tkf[tkf["_created"]]["Target object id"].replace("", pd.NA).dropna().unique().tolist())
    subjmap = {}
    with dash_spinner(f"Fetching {len(ids):,} created tickets from HubSpot…"):
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            for rec in fetch_all("tickets", ["hs_object_id", "subject", "content", "createdate"],
                                 filter_groups=[{"filters": [
                                     {"propertyName": "hs_object_id", "operator": "IN", "values": chunk}]}]):
                p = rec.get("properties", {})
                hid = str(p.get("hs_object_id") or "").strip()
                if hid:
                    subjmap[hid] = f"{p.get('subject') or ''} {p.get('content') or ''}".lower()
    tickets_text = list(subjmap.values())

    def _match_count(name):
        name = name.strip().lower()
        if not name:
            return 0
        toks = [t for t in name.split() if len(t) > 1]
        c = 0
        for txt in tickets_text:
            if name in txt or (len(toks) >= 2 and all(t in txt for t in toks)):
                c += 1
        return c

    cust = (cvf.groupby("_customer")
            .agg(Interactions=("_customer", "size"),
                 Types=("_type", lambda s: ", ".join(sorted(set(s)))))
            .reset_index().rename(columns={"_customer": "Customer"}))
    cust = cust[cust["Customer"].str.strip() != ""]
    with dash_spinner("Matching customers to tickets…"):
        cust["Tickets"] = cust["Customer"].map(_match_count)
    cust["Int/ticket"] = cust.apply(
        lambda r: round(r["Interactions"] / r["Tickets"], 1) if r["Tickets"] else 0, axis=1)
    cust["Flag"] = cust.apply(
        lambda r: "🔴 more tickets than contacts" if r["Tickets"] > r["Interactions"]
        else ("⚠️ no ticket found" if r["Tickets"] == 0 else "✅"), axis=1)
    cust = cust.sort_values(["Interactions", "Tickets"], ascending=False)

    matched = int((cust["Tickets"] > 0).sum())
    st.caption(f"{len(cust):,} consumers · {matched:,} matched to ≥1 ticket · "
               f"{int((cust['Tickets'] > cust['Interactions']).sum()):,} have more tickets than contacts.")
    st.dataframe(cust[["Customer", "Interactions", "Types", "Tickets", "Int/ticket", "Flag"]],
                 use_container_width=True, hide_index=True, height=460)
    st.download_button("📥 Download per-consumer match (CSV)", cust.to_csv(index=False),
                       "per_consumer_match.csv", "text/csv", key="ivt_cust_csv")
    st.caption("Matching is name-substring on ticket subject/description — a common name may "
               "over-match, and a ticket that doesn't name the consumer won't match. Treat as a guide.")

report_header_close()
