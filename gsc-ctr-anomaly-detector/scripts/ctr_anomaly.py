#!/usr/bin/env python3
"""GSC CTR Anomaly Detector — reference implementation.

Auth:   GOOGLE_APPLICATION_CREDENTIALS (SA on the GSC property) or OAuth.
Output: JSON on stdout per ../references/output.schema.json.

Usage: python3 ctr_anomaly.py --site sc-domain:example.com --min-impr 100 --sigma 2.0
"""
from __future__ import annotations
import argparse, json, os, sys, time, random, datetime, urllib.parse, re
from statistics import median

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000
HERE = os.path.dirname(os.path.abspath(__file__))
CURVE = json.load(open(os.path.join(HERE, "..", "references", "ctr_curve.json")))["curve"]


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout); sys.exit(1)


def expected(pos):
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
    start_row, rows = 0, []
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
        rows.extend(page)
        if len(page) < ROW_LIMIT:
            return rows
        start_row += ROW_LIMIT


def title_terms_present(page, query):
    try:
        import urllib.request
        req = urllib.request.Request(page, headers={"User-Agent": "seoskills-ctr/1.0"})
        html = urllib.request.urlopen(req, timeout=10).read(120000).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = (m.group(1).lower() if m else "")
        toks = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
        return all(t in title for t in toks)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--min-impr", type=int, default=100, dest="min_impr")
    ap.add_argument("--sigma", type=float, default=2.0)
    a = ap.parse_args()
    start, end = (a.start, a.end) if a.start and a.end else default_dates()
    sess = session()
    rows = [r for r in fetch(sess, a.site, start, end) if r.get("impressions", 0) >= a.min_impr]
    if not rows:
        json.dump({"status": "no_data", "site_url": a.site, "underperformers": [], "overperformers": []}, sys.stdout); return

    residuals = []
    for r in rows:
        impr, clicks, pos = r["impressions"], r.get("clicks", 0), r.get("position", 0)
        r["_ctr"] = clicks / impr if impr else 0
        r["_exp"] = expected(pos)
        r["_res"] = r["_ctr"] - r["_exp"]
        residuals.append(r["_res"])
    med = median(residuals)
    mad = median([abs(x - med) for x in residuals]) or (sum(abs(x - med) for x in residuals) / len(residuals)) or 1e-9
    sigma = 1.4826 * mad
    confidence = "low" if len(rows) < 30 else "normal"
    thr = a.sigma + (0.5 if confidence == "low" else 0)

    under, over = [], []
    for r in rows:
        z = (r["_res"] - med) / sigma
        base = {"query": r["keys"][0], "url": r["keys"][1], "position": round(r["position"], 1),
                "impressions": int(r["impressions"]), "actual_ctr": round(r["_ctr"], 4),
                "expected_ctr": round(r["_exp"], 4), "robust_z": round(z, 2)}
        if z <= -thr:
            base["opportunity_clicks"] = max(0, round(r["impressions"] * (r["_exp"] - r["_ctr"])))
            present = title_terms_present(r["keys"][1], r["keys"][0])
            base["cause"] = ("title_mismatch" if present is False
                             else "serp_feature_suppression" if r["position"] <= 3
                             else "weak_snippet" if present is True else "unknown")
            under.append(base)
        elif z >= thr:
            over.append(base)
    under.sort(key=lambda x: x["opportunity_clicks"], reverse=True)
    over.sort(key=lambda x: x["robust_z"], reverse=True)
    json.dump({"status": "ok", "site_url": a.site, "date_range": [start, end], "confidence": confidence,
               "rows_evaluated": len(rows), "underperformers": under, "overperformers": over[:15]},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
