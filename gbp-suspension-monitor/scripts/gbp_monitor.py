#!/usr/bin/env python3
"""GBP Suspension Monitor — reference implementation.

Auth:   GBP_OAUTH_TOKEN (scope business.manage) or GOOGLE_APPLICATION_CREDENTIALS.
        NOTE: Business Profile API requires Google-approved project access.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 gbp_monitor.py --account accounts/123 [--previous previous.json]
"""
from __future__ import annotations
import argparse, json, os, sys, time, datetime, urllib.request, urllib.error, urllib.parse

API = "https://mybusinessbusinessinformation.googleapis.com/v1"
WATCH = ["title", "storefrontAddress", "phoneNumbers", "categories", "regularHours", "websiteUri", "openInfo"]
READ_MASK = "name,title,storefrontAddress,phoneNumbers,categories,regularHours,websiteUri,openInfo,metadata"


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def token():
    t = os.environ.get("GBP_OAUTH_TOKEN")
    if t:
        return t
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            import google.auth
            from google.auth.transport.requests import Request
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/business.manage"])
            creds.refresh(Request())
            return creds.token
        except Exception as e:
            fail("AUTH_MISSING_TOKEN", "Could not obtain token: %s" % e)
    fail("AUTH_MISSING_TOKEN", "Set GBP_OAUTH_TOKEN (scope business.manage).")


def api_get(url, tok):
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = (e.read() or b"").decode("utf-8", "ignore")
            if e.code == 401:
                fail("AUTH_EXPIRED", "OAuth token expired.")
            if e.code == 403 and ("SERVICE_DISABLED" in body or "PERMISSION_DENIED" in body):
                fail("API_ACCESS_NOT_APPROVED", "Request Business Profile API access for this GCP project.")
            if e.code == 429:
                if attempt == 5:
                    return 429, {}
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def list_locations(account, tok, only):
    out, page = [], None
    while True:
        params = {"readMask": READ_MASK, "pageSize": 100}
        if page:
            params["pageToken"] = page
        status, data = api_get(f"{API}/{account}/locations?" + urllib.parse.urlencode(params), tok)
        if status == 429:
            fail("RATE_LIMITED", "GBP quota exhausted.", partial=len(out))
        if status != 200:
            fail("REQUEST_FAILED", "locations HTTP %s" % status)
        for loc in data.get("locations", []):
            if not only or loc.get("name") in only:
                out.append(loc)
        page = data.get("nextPageToken")
        if not page:
            return out


def snapshot(loc):
    meta = loc.get("metadata", {})
    return {"name": loc.get("name"), "title": loc.get("title"),
            "fields": {k: loc.get(k) for k in WATCH},
            "voice_of_merchant": meta.get("hasVoiceOfMerchant"),
            "can_post": meta.get("canOperateLocalPost")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--locations", default="")
    ap.add_argument("--previous")
    a = ap.parse_args()
    tok = token()
    only = set(x.strip() for x in a.locations.split(",") if x.strip())
    prev = {s["name"]: s for s in (json.load(open(a.previous)) if a.previous else [])}

    locs = list_locations(a.account, tok, only)
    snaps, alerts = [], []
    now = datetime.datetime.utcnow().isoformat() + "Z"
    present = set()
    for loc in locs:
        s = snapshot(loc); s["captured_at"] = now
        snaps.append(s); present.add(s["name"])
        p = prev.get(s["name"])
        suspended = s["voice_of_merchant"] is False
        if not p:
            alerts.append({"location": s["name"], "severity": "info", "type": "baseline"})
        else:
            if suspended and p.get("voice_of_merchant") is not False:
                alerts.append({"location": s["name"], "severity": "critical", "type": "suspension_detected",
                               "remediation": "File a reinstatement request with proof of legitimacy."})
            diffs = [{"field": k, "before": p["fields"].get(k), "after": s["fields"].get(k)}
                     for k in WATCH if json.dumps(p["fields"].get(k), sort_keys=True) != json.dumps(s["fields"].get(k), sort_keys=True)]
            for d in diffs:
                sev = "critical" if d["field"] in ("title", "storefrontAddress") else "warning"
                alerts.append({"location": s["name"], "severity": sev, "type": "field_changed",
                               "diff": d, "remediation": "Confirm the edit was authorized; revert via API if not."})
    for name, p in prev.items():
        if name not in present:
            alerts.append({"location": name, "severity": "critical", "type": "location_disappeared",
                           "remediation": "Location no longer returned — check for suspension or removal."})
            snaps.append(p)  # carry forward
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda x: order.get(x["severity"], 3))
    json.dump({"status": "ok", "account": a.account, "locations_checked": len(locs),
               "alert_count": len(alerts), "alerts": alerts, "snapshots": snaps}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
