#!/usr/bin/env python3
"""Weekly HubSpot snapshot → Google Drive.

Runs headless (GitHub Actions). Pulls report data straight from HubSpot and
uploads dated Parquet/CSV snapshots to a Google Drive folder so nothing is lost
when Streamlit Cloud's ephemeral disk is wiped.

Env vars (set as GitHub Actions secrets):
  HUBSPOT_TOKEN                 - HubSpot private-app token
  GDRIVE_FOLDER_ID             - target Drive folder id (shared with the service account)
  GOOGLE_SERVICE_ACCOUNT_JSON  - the service-account JSON (full contents)
"""
import io
import os
import sys
import json
import time
from datetime import datetime, timezone, date

import requests
import pandas as pd

BASE = "https://api.hubapi.com"
NUM_OBJECT = "2-40974683"
MV_OBJECT = "2-46246179"
TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _ms(d: date) -> str:
    return str(int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000))


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def seek(object_id, props, filters):
    """Seek-paginate any object past the 10k Search cap via hs_object_id cursor."""
    url = f"{BASE}/crm/v3/objects/{object_id}/search"
    out, last = [], "0"
    while True:
        body = {"limit": 100, "properties": props,
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
                "filterGroups": [{"filters": filters + [
                    {"propertyName": "hs_object_id", "operator": "GT", "value": last}]}]}
        r = None
        for attempt in range(6):
            r = requests.post(url, headers=HEADERS, json=body, timeout=90)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1)); continue
            break
        if r is None or r.status_code != 200:
            raise RuntimeError(f"HubSpot {getattr(r,'status_code','?')}: {getattr(r,'text','')[:300]}")
        batch = r.json().get("results", [])
        out.extend(batch)
        if len(batch) < 100:
            break
        last = str(batch[-1]["id"]); time.sleep(0.05)
    return out


def build_convo_now_only():
    """Replicates the Convo Now Only page's classification for the current month."""
    since = date.today().replace(day=1)

    vrs = seek(NUM_OBJECT, ["number", "email", "account_status"],
               [{"propertyName": "service_type", "operator": "EQ", "value": "VRS"}])
    vrs_emails, vrs_nums_by_email = set(), {}
    for r in vrs:
        p = r.get("properties", {})
        e = (p.get("email") or "").strip().lower()
        n = str(p.get("number") or "").strip()
        if e:
            vrs_emails.add(e)
            vrs_nums_by_email.setdefault(e, set()).add(n)

    cn_raw = seek(NUM_OBJECT,
                  ["number", "email", "first_name", "last_name", "account_status", "number_status",
                   "usage_type", "number_created_at", "number_deleted_at", "account_id",
                   "convo_now_account_id", "credit_type", "credit_plan_name"],
                  [{"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"}])
    cn, n_guest = [], 0
    for r in cn_raw:
        p = r.get("properties", {})
        if "guest" in (p.get("credit_type") or "").lower() or "guest" in (p.get("credit_plan_name") or "").lower():
            n_guest += 1
            continue
        cn.append(p)

    cn_numbers = sorted({str(p.get("number") or "").strip() for p in cn if str(p.get("number") or "").strip()})
    cn_usage = {}
    for i in range(0, len(cn_numbers), 100):
        chunk = cn_numbers[i:i + 100]
        for o in seek(MV_OBJECT, ["number", "usage_minutes", "service_type", "month_date"],
                      [{"propertyName": "number", "operator": "IN", "values": chunk},
                       {"propertyName": "service_type", "operator": "EQ", "value": "Convo Now"},
                       {"propertyName": "usage_minutes", "operator": "GT", "value": "0"},
                       {"propertyName": "month_date", "operator": "GTE", "value": _ms(since)}]):
            op = o.get("properties", {})
            nn = str(op.get("number") or "").strip()
            cn_usage[nn] = cn_usage.get(nn, 0.0) + _to_float(op.get("usage_minutes"))

    cn_emails = {(p.get("email") or "").strip().lower() for p in cn if (p.get("email") or "").strip()}
    vrs_nums = sorted({n for e in cn_emails for n in vrs_nums_by_email.get(e, ())})
    vrs_usage = {}
    for i in range(0, len(vrs_nums), 100):
        chunk = vrs_nums[i:i + 100]
        for o in seek(MV_OBJECT, ["number", "usage_minutes", "service_type", "month_date"],
                      [{"propertyName": "number", "operator": "IN", "values": chunk},
                       {"propertyName": "service_type", "operator": "EQ", "value": "VRS"},
                       {"propertyName": "month_date", "operator": "GTE", "value": _ms(since)}]):
            op = o.get("properties", {})
            nn = str(op.get("number") or "").strip()
            vrs_usage[nn] = vrs_usage.get(nn, 0.0) + _to_float(op.get("usage_minutes"))
    vrs_min_by_email = {e: sum(vrs_usage.get(n, 0.0) for n in nums) for e, nums in vrs_nums_by_email.items()}

    rows = []
    for p in cn:
        n = str(p.get("number") or "").strip()
        e = (p.get("email") or "").strip().lower()
        cn_min = round(cn_usage.get(n, 0.0), 1)
        has_vrs = bool(e) and e in vrs_emails
        vrs_min = round(vrs_min_by_email.get(e, 0.0), 1) if has_vrs else 0.0
        if not has_vrs:
            bucket = "No VRS number"
        elif cn_min > 0 and vrs_min <= 0:
            bucket = "CN minutes, VRS none"
        elif cn_min > 0 and vrs_min > 0:
            bucket = "CN + VRS minutes"
        else:
            bucket = "Has VRS, no CN minutes"
        rows.append({
            "number": n, "email": p.get("email") or "", "account_id": p.get("account_id") or "",
            "status": p.get("account_status") or p.get("number_status") or "",
            "credit_type": p.get("credit_type") or "", "credit_plan": p.get("credit_plan_name") or "",
            "cn_minutes": cn_min, "vrs_minutes": vrs_min, "has_vrs": has_vrs, "bucket": bucket,
        })
    df = pd.DataFrame(rows)
    df.attrs["n_guest"] = n_guest
    df.attrs["since"] = str(since)
    return df


# ── Google Drive upload ──────────────────────────────────────────────────────────
def drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload(svc, folder_id, name, data: bytes, mime="application/octet-stream"):
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    meta = {"name": name, "parents": [folder_id]}
    f = svc.files().create(body=meta, media_body=media, fields="id,name",
                           supportsAllDrives=True).execute()
    print(f"  uploaded {f['name']} ({f['id']})")
    return f["id"]


def main():
    if not TOKEN:
        sys.exit("HUBSPOT_TOKEN missing")
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "")
    if not folder_id:
        sys.exit("GDRIVE_FOLDER_ID missing")

    stamp = date.today().strftime("%Y-%m-%d")
    svc = drive_service()

    reports = {"convo_now_only": build_convo_now_only}
    summary = []
    for name, fn in reports.items():
        print(f"Building {name}…")
        df = fn()
        # data file (parquet) + a CSV twin for easy viewing
        upload(svc, folder_id, f"{name}_{stamp}.parquet",
               df.to_parquet(index=False), "application/octet-stream")
        upload(svc, folder_id, f"{name}_{stamp}.csv",
               df.to_csv(index=False).encode(), "text/csv")
        summary.append({"report": name, "rows": len(df),
                        "guest_excluded": df.attrs.get("n_guest", ""),
                        "window": df.attrs.get("since", ""), "snapshot": stamp})

    upload(svc, folder_id, f"_summary_{stamp}.csv",
           pd.DataFrame(summary).to_csv(index=False).encode(), "text/csv")
    print("Done.")


if __name__ == "__main__":
    main()
