#!/usr/bin/env python3
"""Content Decay Predictor — reference implementation.

Auth:   GOOGLE_APPLICATION_CREDENTIALS (SA on GSC property) or OAuth.
Output: JSON on stdout per ../references/output.schema.json.

Usage: python3 decay.py --site sc-domain:example.com --weeks 26 --horizon 8
"""
from __future__ import annotations
import argparse, json, os, sys, time, random, datetime, urllib.parse
from collections import defaultdict
from statistics import median

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout); sys.exit(1)


def session():
    if not (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GSC_OAUTH_TOKEN")):
        fail("AUTH_MISSING_CREDENTIALS", "Provide SA or OAuth with webmasters.readonly.")
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def fetch(sess, site, start, end):
    url = ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    start_row, rows = 0, []
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["page", "date"],
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


def theil_sen(y):
    """Median of pairwise slopes; robust to outliers. x = 0..n-1."""
    n = len(y)
    slopes = [(y[j] - y[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return median(slopes) if slopes else 0.0


def iso_week(d):
    y, w, _ = datetime.date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--min-clicks", type=int, default=20, dest="min_clicks")
    ap.add_argument("--horizon", type=int, default=8)
    a = ap.parse_args()
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(weeks=a.weeks)
    sess = session()
    rows = fetch(sess, a.site, start.isoformat(), end.isoformat())

    weekly = defaultdict(lambda: defaultdict(float))  # url -> week -> clicks
    for r in rows:
        weekly[r["keys"][0]][iso_week(r["keys"][1])] += r.get("clicks", 0)
    # rank by total clicks, cap 2000
    ranked = sorted(weekly.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    truncated = len(ranked) > 2000
    ranked = ranked[:2000]

    weeks_sorted = sorted({w for _, wk in ranked for w in wk})
    decaying, skipped = [], []
    for url, wk in ranked:
        series = [wk.get(w, 0) for w in weeks_sorted]
        # trim leading zeros (page may not have existed)
        nz = [i for i, v in enumerate(series) if v > 0]
        if not nz:
            continue
        series = series[nz[0]:-1] if len(series) > 1 else series  # drop trailing partial week
        if len(series) < 12:
            skipped.append({"url": url, "reason": "insufficient_history"}); continue
        peak = max(series) if series else 0
        if peak < a.min_clicks:
            continue
        # rolling 4-week means
        roll = [sum(series[max(0, i - 3):i + 1]) / min(i + 1, 4) for i in range(len(series))]
        peak_roll = max(roll) if roll else 0
        recent = sum(series[-4:]) / min(len(series), 4)
        ptr = recent / peak_roll if peak_roll else 1.0
        slope = theil_sen(series)
        if slope < 0 and ptr <= 0.7:
            infl = next((weeks_sorted[nz[0] + i] for i in range(len(roll)) if roll[i] == peak_roll), weeks_sorted[nz[0]])
            proj_end = max(0, recent + slope * a.horizon)
            add_loss = round(max(0, (recent - proj_end)) * a.horizon / 2)  # triangle area estimate
            decaying.append({"url": url, "current_weekly_clicks": round(recent, 1),
                             "peak_weekly_clicks": round(peak_roll, 1), "peak_to_recent": round(ptr, 2),
                             "weekly_slope": round(slope, 2), "inflection_week": infl,
                             "projected_weekly_at_horizon": round(proj_end, 1),
                             "projected_additional_loss": add_loss, "refresh_priority": add_loss})
    decaying.sort(key=lambda d: d["refresh_priority"], reverse=True)
    json.dump({"status": "ok", "site_url": a.site, "weeks": a.weeks, "horizon_weeks": a.horizon,
               "urls_analyzed": len(ranked), "truncated": truncated,
               "decaying_count": len(decaying), "decaying": decaying, "skipped": skipped[:100]},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
