# Registrations Report — how it works

File: `pages/30_Registrations_Report.py` · HubSpot object: **Registrations** `2-58833629`

This doc explains the report's **filters** and the **Python** that powers them, so anyone
on the team can read, tweak, or extend it.

---

## 1. The big picture

Every time you click **Run**, the page does four things:

```
1. Read the object's field list from HubSpot        (_schema / _resolve_fields)
2. Fetch registrations in the date window           (_seek — paginated search)
3. Turn the raw records into a table                 (build rows → DataFrame)
4. Render KPIs, breakdowns, worklist                 (Streamlit widgets)
```

Results are **saved to disk** so re-opening the page is instant; you only re-fetch
when you click Run again.

---

## 2. The filters

### Period preset
A dropdown of `Today, Yesterday, This Week, This Month, This Quarter, This Year,
Last Month, Last Quarter, Last Year, Custom`. Each maps to a `(start_date, end_date)`
pair in `_range()`:

```python
def _range(preset, today=None):
    t = today or date.today()
    q = (t.month - 1) // 3                      # 0..3 = which quarter
    if preset == "This Month":
        return t.replace(day=1), t              # 1st of month → today
    if preset == "Last Quarter":
        ly, lq = (t.year, q - 1) if q > 0 else (t.year - 1, 3)
        s = date(ly, lq * 3 + 1, 1)             # first day of last quarter
        ...                                     # + 3 months − 1 day = last day
```

**Custom** shows a `st.date_input` range instead.

### "Filter dates by"
This is the important one. A registration has several dates (Registered At, Manually
Verified At, Manually Edited At, …). The dropdown lets you pick **which date the period
applies to**, so you can answer "who was *manually verified* this week" — not just "who
*registered* this week."

The available date fields are discovered from the object schema (`_resolve_fields`),
and the chosen one becomes `date_prop`, used directly in the search filter.

### Central-time boundaries
To match HubSpot's own reports, day boundaries use Central Time:

```python
def _ct_offset(m):
    return -5 if 3 <= m <= 11 else -6           # CDT Mar–Nov, else CST

def _ms(d, end=False):                          # date → epoch-ms at CT midnight/23:59
    off = _ct_offset(d.month)
    t = datetime(d.year, d.month, d.day, 23 if end else 0, ...,
                 tzinfo=timezone(timedelta(hours=off)))
    return str(int(t.timestamp() * 1000))
```

---

## 3. Field names resolved from the schema (the key trick)

Custom-object field names aren't always the obvious snake_case (e.g. "Manually Verified
By" might not be `manually_verified_by`). Guessing led to **empty columns**. So instead we
read the object's real property list and match by **HubSpot label**:

```python
@st.cache_data(ttl=1800)
def _schema():                                  # GET the object's properties once
    r = requests.get(f"{_B}/crm/v3/properties/{REG_OBJECT}", headers=_H)
    return [(p["name"], p["label"].strip(), p["type"]) for p in r.json()["results"]]

# WANTED = [(HubSpot label to match, short display label), ...]
def _resolve_fields():
    by_label = {lb.lower(): nm for nm, lb, _ in _schema()}
    val = [(by_label[lb.lower()], disp) for lb, disp in WANTED if lb.lower() in by_label]
    ...
```

`val` is a list of `(internal_name, display_label)`. We fetch the internal names and
label the columns with the friendly names. This is why "Manually Verified By" now shows
real names instead of `—`.

---

## 4. Fetching past the 10k cap (`_seek`)

HubSpot's Search API caps at 10,000 results with normal paging, so we page by
`hs_object_id` cursor instead:

```python
def _seek(date_prop, props, start_ms, end_ms, label):
    results, last = [], "0"
    while True:
        body = {"limit": 100, "properties": props + [date_prop],
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": [
                    {"propertyName": date_prop, "operator": "GTE", "value": start_ms},
                    {"propertyName": date_prop, "operator": "LTE", "value": end_ms},
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        batch = requests.post(url, json=body).json()["results"]
        results += batch
        if len(batch) < 100:
            break
        last = batch[-1]["id"]                   # cursor = last id seen
    return results
```

---

## 5. The "Verified" logic

Lex outcomes are `automatic_success`, `manual_success`, `not_verified` — **not** the word
"Verified". So:

```python
VERIFIED_STATUSES = {"automatic_success", "manual_success", "verified", "success"}
_lex_ok    = df["Lex Status"].str.lower().isin(VERIFIED_STATUSES)
_manual_ok = _nonblank("Manually Verified At") | df["Lex Status"].str.lower().eq("manual_success")
_verified  = (_lex_ok | _manual_ok).sum()       # auto + manual success
```

- **Verified** = automatic_success + manual_success
- **Not verified** = total − verified (the `not_verified` rows)
- **Manually verified** = has a Manually Verified At (or manual_success)

---

## 6. The views (all plain pandas)

| Section | Code idea |
|---|---|
| KPIs | `st.metric(...)` on the counts above |
| Breakdowns (reg type / usage / Lex) | `df.groupby(col).size()` + a `%` column |
| Reg Type × Lex cross-tab | `pd.crosstab(df["Reg Type"], df["Lex Status"], margins=True)` |
| Who manually verified/edited | `groupby(["Person", "Reg Type"])`, owner IDs → names via `_owners()` |
| Not-verified worklist | `df[~(_lex_ok | _manual_ok)]` + top error messages |
| Trend | bucket by day/week/month based on the span, `alt.Chart(...).mark_bar()` |

Owner IDs (like "Manually Edited By") are resolved to names with `_owners()` — but some
are raw system UUIDs HubSpot can't resolve, so those stay as-is.

---

## 7. Caching (why you don't re-run constantly)

```python
REPORT_VERSION = "v3-schema-fields"             # bump when fields/logic change
_key = f"registrations_{date_prop}_{start:%Y%m%d}_{end:%Y%m%d}"

# on Run: save_report(_key, {"df": df, ..., "v": REPORT_VERSION})
# on load: reuse the saved run unless its "v" != REPORT_VERSION (forces a refresh)
```

Bumping `REPORT_VERSION` invalidates old saves so new columns/logic take effect.

---

## 8. How to extend it

- **Add a field to the table:** add `("HubSpot Label", "Short Name")` to `WANTED`.
- **Add a date basis:** add the HubSpot label to `DATE_LABELS`.
- **Add a period preset:** add a branch in `_range()` and the label to `PRESETS`.
- **Change the "verified" rule:** edit `VERIFIED_STATUSES` or the `_manual_ok` condition.
- After any field/logic change, bump `REPORT_VERSION` so caches refresh.
