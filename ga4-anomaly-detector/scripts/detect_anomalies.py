#!/usr/bin/env python3
"""GA4 Anomaly Detector — reference implementation.

Auth:   service account via GOOGLE_APPLICATION_CREDENTIALS (Analytics Data API
        enabled; SA granted >= Viewer on the property).
Method: seasonality-aware robust z-score (per-weekday median + MAD).
Output: JSON on stdout, conforming to ../references/output.schema.json.

Usage:
  python3 detect_anomalies.py --property 123456789 \
      --metrics sessions,conversions,totalRevenue \
      --eval 7 --baseline 90 --sensitivity medium [--organic]
"""
from __future__ import annotations
import argparse, json, os, sys, time, random
from statistics import median

import google.auth  # google-auth
from google.auth.transport.requests import AuthorizedSession

DATA_API = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
THRESHOLD = {"low": 4.0, "medium": 3.5, "high": 3.0}


def fail(code: str, message: str) -> None:
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def session() -> AuthorizedSession:
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        fail("AUTH_MISSING_CREDENTIALS",
             "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account key with the "
             "Analytics Data API enabled and Viewer on the GA4 property.")
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def run_report(sess: AuthorizedSession, pid: str, body: dict) -> dict:
    url = DATA_API.format(pid=pid)
    for attempt in range(6):
        r = sess.post(url, json=body, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403:
            fail("AUTH_NO_PROPERTY_ACCESS",
                 "Service account lacks access to property %s." % pid)
        if r.status_code == 429 or "RESOURCE_EXHAUSTED" in r.text:
            if attempt == 5:
                fail("RATE_LIMITED", "GA4 Data API quota exhausted after retries.")
            time.sleep(min(60, 2 ** attempt) + random.random())
            continue
        if 500 <= r.status_code < 600:
            if attempt >= 3:
                fail("UPSTREAM_5XX", "GA4 Data API 5xx after retries: %s" % r.text[:300])
            time.sleep(min(60, 2 ** attempt) + random.random())
            continue
        fail("REQUEST_FAILED", "HTTP %s: %s" % (r.status_code, r.text[:300]))
    fail("RATE_LIMITED", "Exceeded retry budget.")


def daily_series(sess, pid, metrics, baseline_days, organic):
    body = {
        "dateRanges": [{"startDate": f"{baseline_days}daysAgo", "endDate": "yesterday"}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": m} for m in metrics],
        "limit": 100000,
        "orderBys": [{"dimension": {"dimensionName": "date"}}],
    }
    if organic:
        body["dimensionFilter"] = {"filter": {
            "fieldName": "sessionDefaultChannelGroup",
            "stringFilter": {"matchType": "EXACT", "value": "Organic Search"}}}
    resp = run_report(sess, pid, body)
    series = {m: {} for m in metrics}
    for row in resp.get("rows", []):
        date = row["dimensionValues"][0]["value"]  # YYYYMMDD
        for i, m in enumerate(metrics):
            series[m][date] = float(row["metricValues"][i]["value"] or 0)
    return series, resp.get("propertyQuota")


def weekday(yyyymmdd: str) -> int:
    import datetime
    return datetime.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8])).weekday()


def score(series, metrics, eval_days, threshold):
    anomalies, skipped = [], []
    dates = sorted({d for m in metrics for d in series[m]})
    if len(dates) < 56:
        status, threshold, confidence = "insufficient_history", threshold + 0.5, "low"
    else:
        status, confidence = "ok", "normal"
    eval_dates = dates[-eval_days:]
    for m in metrics:
        vals = series[m]
        if not vals or all(v == 0 for v in vals.values()):
            skipped.append(m)
            continue
        groups: dict[int, list[float]] = {}
        for d, v in vals.items():
            if d in eval_dates:
                continue
            groups.setdefault(weekday(d), []).append(v)
        for d in eval_dates:
            if d not in vals:
                continue
            g = groups.get(weekday(d), [])
            if len(g) < 3:
                continue
            med = median(g)
            mad = median([abs(x - med) for x in g]) or (sum(abs(x - med) for x in g) / len(g))
            sigma = 1.4826 * mad
            if sigma == 0:
                continue
            z = (vals[d] - med) / sigma
            if abs(z) >= threshold:
                anomalies.append({
                    "date": d, "metric": m, "value": vals[d],
                    "expected": round(med, 2), "robust_z": round(z, 2),
                    "direction": "drop" if z < 0 else "spike",
                })
    anomalies.sort(key=lambda a: abs(a["robust_z"]), reverse=True)
    return status, confidence, anomalies, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--property", required=True)
    ap.add_argument("--metrics", default="sessions,conversions,totalRevenue")
    ap.add_argument("--eval", type=int, default=7)
    ap.add_argument("--baseline", type=int, default=90)
    ap.add_argument("--sensitivity", choices=list(THRESHOLD), default="medium")
    ap.add_argument("--organic", action="store_true")
    a = ap.parse_args()
    metrics = [m.strip() for m in a.metrics.split(",") if m.strip()]
    baseline = max(a.baseline, 56)
    sess = session()
    series, quota = daily_series(sess, a.property, metrics, baseline, a.organic)
    status, confidence, anomalies, skipped = score(series, metrics, a.eval, THRESHOLD[a.sensitivity])
    json.dump({
        "status": status, "property_id": a.property, "confidence": confidence,
        "sensitivity": a.sensitivity, "metrics_evaluated": metrics,
        "skipped_metrics": skipped, "anomaly_count": len(anomalies),
        "anomalies": anomalies, "quota": quota,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
