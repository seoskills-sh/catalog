#!/usr/bin/env python3
"""Search Console Cannibalization Analyzer — reference implementation.

Auth:   GOOGLE_APPLICATION_CREDENTIALS (service account added as a user on the
        GSC property) OR user OAuth with webmasters.readonly.
Output: JSON on stdout conforming to ../references/output.schema.json.

Usage:
  python3 cannibalization.py --site sc-domain:example.com \
      --start 2026-08-15 --end 2026-09-11 --min-impr 50 --threshold 2
"""
from __future__ import annotations
import argparse, json, os, sys, time, random, datetime, urllib.parse
from collections import defaultdict

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000
HERE = os.path.dirname(os.path.abspath(__file__))
CTR_CURVE = json.load(open(os.path.join(HERE, "..", "references", "ctr_curve.json")))["curve"]


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def ctr_for(position: float) -> float:
    bucket = min(max(int(round(position)), 1), 21)  # 21 == "20+"
    return CTR_CURVE.get(str(bucket), CTR_CURVE["21"])


def default_dates():
    end = datetime.date.today() - datetime.timedelta(days=3)   # GSC lag
    return (end - datetime.timedelta(days=27)).isoformat(), end.isoformat()


def session():
    if not (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GSC_OAUTH_TOKEN")):
        fail("AUTH_MISSING_CREDENTIALS", "Provide a service account or OAuth token with webmasters.readonly.")
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def query_all(sess, site):
    url = ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    start_row, rows = 0, []
    while True:
        body = {"startDate": ARGS.start, "endDate": ARGS.end,
                "dimensions": ["query", "page"], "type": ARGS.search_type,
                "dataState": "final", "rowLimit": ROW_LIMIT, "startRow": start_row}
        if ARGS.country:
            body["dimensionFilterGroups"] = [{"filters": [
                {"dimension": "country", "operator": "equals", "expression": ARGS.country}]}]
        for attempt in range(6):
            r = sess.post(url, json=body, timeout=60)
            if r.status_code == 200:
                break
            if r.status_code == 403:
                fail("AUTH_NO_SITE_ACCESS", "Identity is not a user on GSC property %s." % site)
            if r.status_code == 400:
                fail("BAD_REQUEST", r.text[:300])
            if r.status_code in (429, 500, 503):
                if attempt == 5:
                    fail("RATE_LIMITED", "GSC API quota/again after retries.")
                time.sleep(min(60, 2 ** attempt) + random.random()); continue
            fail("REQUEST_FAILED", "HTTP %s %s" % (r.status_code, r.text[:200]))
        page = r.json().get("rows", [])
        rows.extend(page)
        if len(page) < ROW_LIMIT:
            return rows
        start_row += ROW_LIMIT


def analyze(rows):
    by_query = defaultdict(list)
    for row in rows:
        q, page = row["keys"][0], row["keys"][1]
        by_query[q].append({"page": page, "clicks": row.get("clicks", 0),
                            "impressions": row.get("impressions", 0),
                            "position": row.get("position", 0)})
    clusters = []
    for q, urls in by_query.items():
        total_impr = sum(u["impressions"] for u in urls)
        if total_impr < ARGS.min_impr:
            continue
        competing = [u for u in urls if u["impressions"] > 0]
        if len(competing) < ARGS.threshold:
            continue
        primary = max(competing, key=lambda u: (u["clicks"], -u["position"]))
        contenders = [u for u in competing if u["page"] != primary["page"]]
        positions = [u["position"] for u in competing if u["position"] > 0]
        spread = (max(positions) - min(positions)) if positions else 0
        exp_clicks = sum(c["impressions"] for c in contenders) * ctr_for(primary["position"])
        lost = max(0, round(exp_clicks - sum(c["clicks"] for c in contenders), 1))
        severity = "high" if spread >= 5 and lost >= 10 else "medium" if lost >= 3 else "low"
        clusters.append({
            "query": q, "distinct_urls": len(competing),
            "position_spread": round(spread, 1), "estimated_lost_clicks": lost,
            "severity": severity, "clicks_basis": "impressions_only" if sum(u["clicks"] for u in competing) == 0 else "clicks",
            "consolidate_to": primary["page"],
            "primary": {k: round(primary[k], 2) if isinstance(primary[k], float) else primary[k] for k in primary},
            "contenders": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()} for c in contenders[:10]],
        })
    clusters.sort(key=lambda c: c["estimated_lost_clicks"], reverse=True)
    return clusters


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--min-impr", type=int, default=50, dest="min_impr")
    ap.add_argument("--threshold", type=int, default=2)
    ap.add_argument("--country", default=None)
    ap.add_argument("--search-type", default="web", dest="search_type")
    ARGS = ap.parse_args()
    if not ARGS.start or not ARGS.end:
        ARGS.start, ARGS.end = default_dates()
    sess = session()
    rows = query_all(sess, ARGS.site)
    if not rows:
        json.dump({"status": "no_data", "site_url": ARGS.site, "clusters": []}, sys.stdout); return
    clusters = analyze(rows)
    json.dump({"status": "ok", "site_url": ARGS.site, "date_range": [ARGS.start, ARGS.end],
               "cluster_count": len(clusters), "clusters": clusters}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
