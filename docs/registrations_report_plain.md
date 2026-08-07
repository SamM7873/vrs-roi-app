# Registrations Report — plain guide

A simple walkthrough of the **Registrations Report** for anyone on the team — no code.

Find it in the app under **Numbers → Registrations Report**.

---

## What it tells you

How many registrations happened in a period, and how many got **verified** — plus who
did any manual verifying and what's still stuck.

The data comes live from HubSpot's **Registrations** records each time you run it.

---

## How to use it (3 steps)

1. **Pick a period** — Today, This Week, This Month, This Quarter, This Year, or Last
   Month / Quarter / Year (or a Custom date range).
2. **Pick "Filter dates by"** — which date the period should use:
   - *Registered At* → when the number registered (the usual choice)
   - *Manually Verified At* → to see everyone a person verified in that period
   - *Manually Edited At* → to see manual edits
3. **Click Run.** Numbers load and are saved, so you can revisit without re-running.

---

## Reading the top numbers

| Tile | Meaning |
|---|---|
| **Total registrations** | How many in the period |
| **✅ Verified** | Passed verification — automatically *or* by a person |
| **❌ Not verified** | Still not verified (needs attention) |
| **✍️ Manually verified** | A person had to verify these by hand |

**"Verified" counts two things:** automatic successes *and* manual successes. So if 51
were auto-verified and 29 were verified by a person, Verified = **80**.

---

## The breakdowns

- **By registration type** — new vs port-in, with %.
- **By usage type** — personal vs organization.
- **By Lex verification** — the verification outcome:
  - **automatic_success** — the system verified it on its own ✅
  - **manual_success** — a person verified it ✍️
  - **not_verified** — still not verified ❌
- **Reg type × Lex** — a grid showing, for example, how the **port-in** registrations
  were verified (auto vs manual vs not).

---

## Manual activity — who did the work

Two tables show **who** manually verified or edited registrations, how many each person
did, and the split by registration type. Names are pulled from HubSpot.

> Note: "Manually Edited By" sometimes shows a long code instead of a name — that's a
> system ID HubSpot itself can't turn into a name. "Manually Verified By" shows real names.

---

## The worklist — what still needs attention

**⚠️ Not verified — worklist** lists every registration that isn't verified yet, with its
**error message** (e.g. "Unable to verify address"). There's also a **Top errors** table
showing the most common reasons (with %), and a **CSV download** so the team can work
through them.

---

## Good to know

- **Times are Central** (to match HubSpot).
- The **current, in-progress day** is included when you pick "Today" — numbers may still
  grow.
- If a column looks empty, that field may be named differently in HubSpot — flag it and
  it can be fixed.
- Click **Run** only when you want fresh data; otherwise the last run is reused.

---

*For the technical version (filters + Python), see `registrations_report.md`.*
