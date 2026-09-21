#!/usr/bin/env python3
"""GSC Bulk Export Manager — reference implementation.

Validates that Search Console's daily Bulk Data Export to BigQuery is configured,
then runs partitioned, parameterized queries over the FULL unsampled export tables
(searchdata_url_impression / searchdata_site_impression) to deliver analyses the
16-month UI cannot: a complete query inventory, page x query click decay between two
windows, and daily anomaly detection. Every query is dry-run first and gated by a
bytes-scanned cost guard before it is allowed to bill.

Auth:   GCP_ACCESS_TOKEN (bearer) + BQ_PROJECT + BQ_DATASET. This std-lib reference
        uses a short-lived access token (e.g. `gcloud auth print-access-token`);
        it cannot sign a service-account JWT.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 bulk_export_manager.py --analysis all \
      [--start 2026-08-15 --end 2026-09-11] [--max-gb-scanned 5] [--limit 5000] [--z 2.5]
"""
from __future__ import annotations
import argparse, datetime, json, math, os, sys, time
import urllib.request, urllib.error

URL_TABLE = "searchdata_url_impression"
SITE_TABLE = "searchdata_site_impression"
GIB = 1024 ** 3


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def creds():
    tok = os.environ.get("GCP_ACCESS_TOKEN") or os.environ.get("GSC_OAUTH_TOKEN")
    project = os.environ.get("BQ_PROJECT")
    dataset = os.environ.get("BQ_DATASET")
    if not (tok and project and dataset):
        fail("AUTH_MISSING_CREDENTIALS",
             "Set GCP_ACCESS_TOKEN (bearer), BQ_PROJECT and BQ_DATASET for the GSC export dataset.")
    return tok, project, dataset


def bq_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("AUTH_INVALID_TOKEN", "GCP_ACCESS_TOKEN rejected (401).")
            if e.code == 404:
                return 404, {}
            if e.code in (429, 500, 503):
                if attempt == 5:
                    return e.code, {}
                time.sleep(2 ** attempt); continue
            return e.code, {"error_text": e.read().decode("utf-8", "replace")[:200]}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def validate_export(project, dataset, token):
    base = "https://bigquery.googleapis.com/bigquery/v2/projects/%s/datasets/%s/tables/%s"
    missing = []
    for t in (URL_TABLE, SITE_TABLE):
        st, _ = bq_get(base % (project, dataset, t), token)
        if st == 404:
            missing.append(t)
        elif st == 403:
            fail("BQ_ACCESS_DENIED", "Token lacks bigquery.dataViewer on %s.%s." % (project, dataset))
        elif st != 200:
            fail("BQ_VALIDATE_FAILED", "tables.get on %s returned HTTP %s." % (t, st))
    if missing:
        fail("EXPORT_NOT_CONFIGURED",
             "Bulk Data Export tables missing: %s. Enable Settings > Bulk data export in "
             "Search Console, pointing at %s.%s, and wait for the first daily load." % (
                 ", ".join(missing), project, dataset),
             missing_tables=missing)


def query_job(project, token, sql, params, dry_run, cap_bytes):
    url = "https://bigquery.googleapis.com/bigquery/v2/projects/%s/queries" % project
    body = {"query": sql, "useLegacySql": False, "parameterMode": "NAMED",
            "queryParameters": params, "dryRun": dry_run, "timeoutMs": 120000}
    if not dry_run:
        body["maximumBytesBilled"] = str(cap_bytes)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=130) as r:
                return 200, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("AUTH_INVALID_TOKEN", "GCP_ACCESS_TOKEN rejected (401).")
            if e.code == 403:
                fail("BQ_ACCESS_DENIED", "Token lacks bigquery.jobUser on %s." % project)
            if e.code in (429, 500, 503):
                if attempt == 5:
                    fail("RATE_LIMITED", "BigQuery quota after retries.")
                time.sleep(2 ** attempt); continue
            body_txt = e.read().decode("utf-8", "replace")
            if "Bytes billed limit" in body_txt or "maximumBytesBilled" in body_txt:
                fail("COST_GUARD_TRIPPED", "Query exceeded maximumBytesBilled at execution.",
                     detail=body_txt[:200])
            fail("BQ_QUERY_FAILED", "HTTP %s: %s" % (e.code, body_txt[:200]))
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            fail("BQ_UNREACHABLE", "BigQuery did not respond.")
    fail("RATE_LIMITED", "BigQuery quota after retries.")


def param(name, ptype, value):
    return {"name": name, "parameterType": {"type": ptype}, "parameterValue": {"value": value}}


def rows_of(resp):
    fields = [f["name"] for f in resp.get("schema", {}).get("fields", [])]
    out = []
    for row in resp.get("rows", []):
        vals = [c.get("v") for c in row.get("f", [])]
        out.append(dict(zip(fields, vals)))
    return out


def estimate_and_run(project, token, sql, params, cap_bytes, label, spend):
    st, dry = query_job(project, token, sql, params, True, cap_bytes)
    est = int(dry.get("totalBytesProcessed", "0") or "0")
    spend["queries"].append({"analysis": label, "estimated_bytes": est,
                             "estimated_gb": round(est / GIB, 3)})
    spend["total_estimated_bytes"] += est
    if est > cap_bytes:
        fail("COST_GUARD_TRIPPED",
             "%s would scan %.2f GiB, over the %.2f GiB guard. Narrow the window or raise --max-gb-scanned."
             % (label, est / GIB, cap_bytes / GIB),
             analysis=label, estimated_gb=round(est / GIB, 3), guard_gb=round(cap_bytes / GIB, 3))
    _, real = query_job(project, token, sql, params, False, cap_bytes)
    return rows_of(real)


def default_windows():
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=27)
    prior_end = start - datetime.timedelta(days=1)
    prior_start = prior_end - datetime.timedelta(days=27)
    return start.isoformat(), end.isoformat(), prior_start.isoformat(), prior_end.isoformat()


def q_inventory(ds):
    return ("SELECT query, SUM(impressions) AS impressions, SUM(clicks) AS clicks, "
            "SAFE_DIVIDE(SUM(sum_position), SUM(impressions)) + 1 AS avg_position "
            "FROM `%s.%s` "
            "WHERE data_date BETWEEN @start AND @end AND is_anonymized_query = FALSE "
            "GROUP BY query ORDER BY impressions DESC LIMIT @limit") % (ds, URL_TABLE)


def q_anon(ds):
    return ("SELECT SUM(IF(is_anonymized_query, impressions, 0)) AS anon_impressions, "
            "SUM(impressions) AS total_impressions FROM `%s.%s` "
            "WHERE data_date BETWEEN @start AND @end") % (ds, URL_TABLE)


def q_decay(ds):
    return ("WITH recent AS (SELECT url, query, SUM(clicks) c FROM `%s.%s` "
            "WHERE data_date BETWEEN @start AND @end AND is_anonymized_query = FALSE GROUP BY url, query), "
            "prior AS (SELECT url, query, SUM(clicks) c FROM `%s.%s` "
            "WHERE data_date BETWEEN @prior_start AND @prior_end AND is_anonymized_query = FALSE "
            "GROUP BY url, query) "
            "SELECT COALESCE(r.url, p.url) AS url, COALESCE(r.query, p.query) AS query, "
            "IFNULL(p.c, 0) AS prior_clicks, IFNULL(r.c, 0) AS recent_clicks, "
            "IFNULL(r.c, 0) - IFNULL(p.c, 0) AS clicks_delta "
            "FROM recent r FULL OUTER JOIN prior p USING (url, query) "
            "WHERE IFNULL(p.c, 0) > 0 ORDER BY clicks_delta ASC LIMIT @limit") % (ds, URL_TABLE, ds, URL_TABLE)


def q_daily(ds):
    return ("SELECT data_date, SUM(clicks) AS clicks, SUM(impressions) AS impressions "
            "FROM `%s.%s` WHERE data_date BETWEEN @start AND @end "
            "GROUP BY data_date ORDER BY data_date") % (ds, SITE_TABLE)


def detect_anomalies(daily, z_thresh):
    series = [(r["data_date"], int(r["clicks"] or 0)) for r in daily]
    vals = [v for _, v in series]
    n = len(vals)
    if n < 7:
        return [], {"reason": "insufficient_days", "days": n}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    anomalies = []
    if std == 0:
        return [], {"mean": round(mean, 1), "std": 0.0, "days": n}
    for d, v in series:
        z = (v - mean) / std
        if abs(z) >= z_thresh:
            anomalies.append({"date": d, "clicks": v, "z_score": round(z, 2),
                              "direction": "spike" if z > 0 else "drop"})
    return anomalies, {"mean": round(mean, 1), "std": round(std, 1), "days": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", choices=["inventory", "decay", "anomaly", "all"], default="all")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--prior-start", dest="prior_start"); ap.add_argument("--prior-end", dest="prior_end")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--max-gb-scanned", type=float, default=5.0, dest="max_gb")
    ap.add_argument("--z", type=float, default=2.5)
    a = ap.parse_args()

    token, project, dataset = creds()
    ds = "%s.%s" % (project, dataset)
    validate_export(project, dataset, token)

    ds_start, ds_end, ds_ps, ds_pe = default_windows()
    start = a.start or ds_start
    end = a.end or ds_end
    prior_start = a.prior_start or ds_ps
    prior_end = a.prior_end or ds_pe
    cap_bytes = int(a.max_gb * GIB)
    limit = max(1, min(a.limit, 50000))

    base_params = [param("start", "DATE", start), param("end", "DATE", end),
                   param("limit", "INT64", str(limit))]
    decay_params = base_params + [param("prior_start", "DATE", prior_start),
                                  param("prior_end", "DATE", prior_end)]
    daily_params = [param("start", "DATE", start), param("end", "DATE", end)]

    spend = {"queries": [], "total_estimated_bytes": 0, "guard_gb": a.max_gb}
    result = {"status": "ok", "dataset": ds, "window": [start, end], "prior_window": [prior_start, prior_end],
              "analyses_run": []}

    if a.analysis in ("inventory", "all"):
        inv = estimate_and_run(project, token, q_inventory(ds), base_params, cap_bytes, "inventory", spend)
        anon = estimate_and_run(project, token, q_anon(ds), daily_params, cap_bytes, "anon_share", spend)
        anon_row = anon[0] if anon else {}
        total_i = int(anon_row.get("total_impressions") or 0)
        anon_i = int(anon_row.get("anon_impressions") or 0)
        result["query_inventory"] = {
            "distinct_queries_returned": len(inv),
            "anonymized_impression_share": round(anon_i / total_i, 4) if total_i else None,
            "top_queries": [{"query": r["query"], "impressions": int(r["impressions"] or 0),
                             "clicks": int(r["clicks"] or 0),
                             "avg_position": round(float(r["avg_position"]), 2) if r.get("avg_position") else None}
                            for r in inv[:1000]],
        }
        result["analyses_run"].append("inventory")

    if a.analysis in ("decay", "all"):
        decay = estimate_and_run(project, token, q_decay(ds), decay_params, cap_bytes, "decay", spend)
        decliners = [r for r in decay if int(r["clicks_delta"] or 0) < 0]
        result["page_query_decay"] = {
            "pairs_returned": len(decay),
            "decliners": len(decliners),
            "top_decay": [{"url": r["url"], "query": r["query"],
                           "prior_clicks": int(r["prior_clicks"] or 0),
                           "recent_clicks": int(r["recent_clicks"] or 0),
                           "clicks_delta": int(r["clicks_delta"] or 0)} for r in decliners[:1000]],
        }
        result["analyses_run"].append("decay")

    if a.analysis in ("anomaly", "all"):
        daily = estimate_and_run(project, token, q_daily(ds), daily_params, cap_bytes, "anomaly", spend)
        anomalies, stats = detect_anomalies(daily, a.z)
        result["anomaly_windows"] = {
            "z_threshold": a.z, "stats": stats,
            "series_days": len(daily),
            "anomalies": anomalies,
            "series": [{"date": r["data_date"], "clicks": int(r["clicks"] or 0),
                        "impressions": int(r["impressions"] or 0)} for r in daily],
        }
        result["analyses_run"].append("anomaly")

    spend["total_estimated_gb"] = round(spend["total_estimated_bytes"] / GIB, 3)
    result["spend_report"] = spend
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
