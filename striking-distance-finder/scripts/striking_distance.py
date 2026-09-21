#!/usr/bin/env python3
"""Striking Distance Opportunity Finder — reference implementation.

Auth:   GOOGLE_APPLICATION_CREDENTIALS (SA on the GSC property) or OAuth
        (webmasters.readonly).
Output: JSON on stdout per ../references/output.schema.json.

Usage:
  python3 striking_distance.py --site sc-domain:example.com \
      --min-impr 100 --target 7 [--start 2026-08-15 --end 2026-09-11] [--verify]
"""
from __future__ import annotations
import argparse, json, os, sys, time, random, datetime, urllib.parse, re
from collections import defaultdict

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000
HERE = os.path.dirname(os.path.abspath(__file__))
CURVE = json.load(open(os.path.join(HERE, "..", "references", "ctr_curve.json")))["curve"]


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def ctr(pos: float) -> float:
    return CURVE.get(str(min(max(int(round(pos)), 1), 21)), CURVE["21"])


def default_dates():
    end = datetime.date.today() - datetime.timedelta(days=3)
    return (end - datetime.timedelta(days=27)).isoformat(), end.isoformat()


def session():
    if not (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GSC_OAUTH_TOKEN")):
        fail("AUTH_MISSING_CREDENTIALS", "Provide SA or OAuth with webmasters.readonly.")
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def fetch(sess, site, start, end):
    url = ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    start_row, out = 0, []
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["query", "page"],
                "dataState": "final", "rowLimit": ROW_LIMIT, "startRow": start_row}
        for attempt in range(6):
            r = sess.post(url, json=body, timeout=60)
            if r.status_code == 200:
                break
            if r.status_code == 403:
                fail("AUTH_NO_SITE_ACCESS", "Identity not a user on %s." % site)
            if r.status_code in (429, 500, 503):
                if attempt == 5:
                    fail("RATE_LIMITED", "GSC quota after retries.")
                time.sleep(min(60, 2 ** attempt) + random.random()); continue
            fail("REQUEST_FAILED", "HTTP %s %s" % (r.status_code, r.text[:200]))
        page = r.json().get("rows", [])
        out.extend(page)
        if len(page) < ROW_LIMIT:
            return out
        start_row += ROW_LIMIT


def onpage_gap(url: str, query: str):
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "seoskills-striking-distance/1.0"})
        html = urllib.request.urlopen(req, timeout=10).read(200000).decode("utf-8", "ignore")
    except Exception:
        return None, "fetch_failed"
    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1].lower()
    h1 = (re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S) or [None, ""])[1].lower()
    toks = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    return {
        "title_missing_term": not all(t in title for t in toks),
        "h1_missing_term": not all(t in h1 for t in toks),
    }, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--min-impr", type=int, default=100, dest="min_impr")
    ap.add_argument("--band", default="11,20")
    ap.add_argument("--target", type=int, default=7)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.band.split(","))
    start, end = (a.start, a.end) if a.start and a.end else default_dates()
    sess = session()
    rows = fetch(sess, a.site, start, end)

    best = {}  # query -> candidate (highest impressions in band)
    extra = defaultdict(list)
    for row in rows:
        q, page = row["keys"]
        pos, impr, clicks = row.get("position", 0), row.get("impressions", 0), row.get("clicks", 0)
        if not (lo <= pos <= hi and impr >= a.min_impr):
            continue
        cur_ctr = (clicks / impr) if clicks else ctr(pos)
        cand = {
            "query": q, "url": page, "position": round(pos, 1), "impressions": int(impr),
            "clicks": int(clicks), "ctr_basis": "observed" if clicks else "modeled",
            "projected_uplift_clicks": max(0, round(impr * (ctr(a.target) - cur_ctr))),
            "effort_hint": "low" if pos <= 15 else "medium",
        }
        if q not in best or cand["impressions"] > best[q]["impressions"]:
            if q in best:
                extra[q].append(best[q]["url"])
            best[q] = cand
        else:
            extra[q].append(page)

    host_blocked = set()
    results = sorted(best.values(), key=lambda c: c["projected_uplift_clicks"], reverse=True)[:a.limit]
    for c in results:
        c["also_ranking_urls"] = extra.get(c["query"], [])
        if a.verify:
            host = urllib.parse.urlparse(c["url"]).netloc
            if host in host_blocked:
                c["gap"], c["gap_status"] = None, "rate_limited_host"
            else:
                c["gap"], c["gap_status"] = onpage_gap(c["url"], c["query"])

    status = "ok" if results else "no_opportunities"
    json.dump({"status": status, "site_url": a.site, "date_range": [start, end],
               "band": [lo, hi], "target_position": a.target,
               "opportunity_count": len(results), "opportunities": results},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
