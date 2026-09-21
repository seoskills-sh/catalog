#!/usr/bin/env python3
"""SEO Metrics API Orchestrator — reference implementation.

Fetches a requested metric set (domain_authority, backlinks, volume, difficulty)
across whichever vendors are configured (Ahrefs, Semrush, Moz, DataForSEO),
coalescing calls so one vendor endpoint that returns several metrics is hit once,
enforcing a per-vendor credit budget with backoff, and degrading gracefully to a
partial result (with reasons) when a vendor is exhausted, unconfigured, or failing.
Returns a unified, source-attributed metric table plus a spend report.

Auth:   any of AHREFS_API_TOKEN | SEMRUSH_API_KEY | MOZ_ACCESS_ID+MOZ_SECRET_KEY |
        DATAFORSEO_LOGIN+DATAFORSEO_PASSWORD. At least one is required.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 metrics_orchestrator.py --domains example.com,foo.com \
      --keywords "seo tools,rank tracker" --metrics domain_authority,backlinks,volume,difficulty \
      [--budgets '{"ahrefs":100,"semrush":100,"moz":100,"dataforseo":100}']
"""
from __future__ import annotations
import argparse, base64, json, os, sys, time, urllib.parse
import urllib.request, urllib.error

VENDORS = ["ahrefs", "semrush", "moz", "dataforseo"]
# metric -> ordered vendor preference and the target type it applies to.
ROUTES = {
    "domain_authority": ("domain", [("moz", "moz"), ("ahrefs", "ahrefs_dr")]),
    "backlinks": ("domain", [("ahrefs", "ahrefs_bl"), ("moz", "moz"),
                             ("dataforseo", "dfs_bl"), ("semrush", "sr_bl")]),
    "volume": ("keyword", [("semrush", "sr_phrase"), ("dataforseo", "dfs_vol")]),
    "difficulty": ("keyword", [("semrush", "sr_phrase"), ("dataforseo", "dfs_diff")]),
}
# credits charged per actual network call (cache miss), by (vendor, group).
COST = {("semrush", "sr_phrase"): 10, ("semrush", "sr_bl"): 10,
        ("dataforseo", "dfs_vol"): 1, ("dataforseo", "dfs_diff"): 1, ("dataforseo", "dfs_bl"): 1,
        ("ahrefs", "ahrefs_dr"): 1, ("ahrefs", "ahrefs_bl"): 1, ("moz", "moz"): 1}
DEFAULT_BUDGET = 100
ENV = {}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def configured(vendor):
    if vendor == "ahrefs":
        return bool(ENV.get("AHREFS_API_TOKEN"))
    if vendor == "semrush":
        return bool(ENV.get("SEMRUSH_API_KEY"))
    if vendor == "moz":
        return bool(ENV.get("MOZ_ACCESS_ID") and ENV.get("MOZ_SECRET_KEY"))
    if vendor == "dataforseo":
        return bool(ENV.get("DATAFORSEO_LOGIN") and ENV.get("DATAFORSEO_PASSWORD"))
    return False


def http(method, url, headers=None, data=None, timeout=45):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, None
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, None
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, None
    return 429, None


def basic(user, pw):
    return "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- vendor group fetchers: each returns (ok, {metric: value}, reason) ----

def moz_fetch(target):
    hdr = {"Authorization": basic(ENV["MOZ_ACCESS_ID"], ENV["MOZ_SECRET_KEY"]),
           "Content-Type": "application/json"}
    body = json.dumps({"targets": [target]}).encode()
    st, raw = http("POST", "https://lsapi.seomoz.com/v2/url_metrics", hdr, body)
    if st != 200 or not raw:
        return False, {}, "http_%s" % st
    try:
        res = (json.loads(raw).get("results") or [{}])[0]
    except ValueError:
        return False, {}, "parse_error"
    return True, {"domain_authority": _num(res.get("domain_authority")),
                  "backlinks": _num(res.get("external_pages_to_root_domain")
                                    or res.get("pages_to_root_domain"))}, "ok"


def semrush_get(params, timeout=45):
    url = "https://api.semrush.com/?" + urllib.parse.urlencode(params)
    st, raw = http("GET", url, {}, None, timeout)
    if st != 200 or raw is None:
        return None, "http_%s" % st
    text = raw.decode("utf-8", "replace")
    if text.startswith("ERROR") or "NOTHING FOUND" in text.upper():
        return None, "semrush:%s" % text.strip()[:40]
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None, "empty"
    header = lines[0].split(";")
    cols = lines[1].split(";")
    return dict(zip(header, cols)), "ok"


def semrush_phrase(kw):
    row, reason = semrush_get({"type": "phrase_this", "key": ENV["SEMRUSH_API_KEY"], "phrase": kw,
                               "database": ENV.get("SEMRUSH_DB", "us"), "export_columns": "Ph,Nq,Kd"})
    if row is None:
        return False, {}, reason
    return True, {"volume": _num(row.get("Nq")), "difficulty": _num(row.get("Kd"))}, "ok"


def semrush_backlinks(domain):
    row, reason = semrush_get({"type": "backlinks_overview", "key": ENV["SEMRUSH_API_KEY"],
                               "target": domain, "target_type": "root_domain", "export_columns": "total"})
    if row is None:
        return False, {}, reason
    return True, {"backlinks": _num(row.get("total"))}, "ok"


def ahrefs_get(path, params):
    url = "https://api.ahrefs.com/v3/" + path + "?" + urllib.parse.urlencode(params)
    st, raw = http("GET", url, {"Authorization": "Bearer " + ENV["AHREFS_API_TOKEN"],
                                "Accept": "application/json"}, None)
    if st != 200 or not raw:
        return None, "http_%s" % st
    try:
        return json.loads(raw), "ok"
    except ValueError:
        return None, "parse_error"


def ahrefs_authority(domain):
    data, reason = ahrefs_get("site-explorer/domain-rating",
                              {"target": domain, "date": time.strftime("%Y-%m-%d")})
    if data is None:
        return False, {}, reason
    dr = data.get("domain_rating")
    if isinstance(dr, dict):
        dr = dr.get("domain_rating")
    return True, {"domain_authority": _num(dr)}, "ok"


def ahrefs_backlinks(domain):
    data, reason = ahrefs_get("site-explorer/backlinks-stats", {"target": domain, "mode": "domain"})
    if data is None:
        return False, {}, reason
    metrics = data.get("metrics", data)
    val = metrics.get("live") if isinstance(metrics, dict) else None
    if val is None and isinstance(metrics, dict):
        val = metrics.get("backlinks")
    return True, {"backlinks": _num(val)}, "ok"


def dfs_post(path, task):
    hdr = {"Authorization": basic(ENV["DATAFORSEO_LOGIN"], ENV["DATAFORSEO_PASSWORD"]),
           "Content-Type": "application/json"}
    st, raw = http("POST", "https://api.dataforseo.com/v3/" + path, hdr, json.dumps([task]).encode())
    if st == 402:
        return None, "payment_required"
    if st != 200 or not raw:
        return None, "http_%s" % st
    try:
        result = ((json.loads(raw).get("tasks") or [{}])[0].get("result") or [])
    except ValueError:
        return None, "parse_error"
    return result, "ok"


def dfs_volume(kw):
    res, reason = dfs_post("keywords_data/google_ads/search_volume/live",
                           {"keywords": [kw], "location_code": 2840, "language_code": "en"})
    if res is None:
        return False, {}, reason
    item = res[0] if res else {}
    return True, {"volume": _num(item.get("search_volume"))}, "ok"


def dfs_difficulty(kw):
    res, reason = dfs_post("dataforseo_labs/google/bulk_keyword_difficulty/live",
                           {"keywords": [kw], "location_name": "United States", "language_name": "English"})
    if res is None:
        return False, {}, reason
    item = res[0] if res else {}
    return True, {"difficulty": _num(item.get("keyword_difficulty"))}, "ok"


def dfs_backlinks(domain):
    res, reason = dfs_post("backlinks/summary/live", {"target": domain})
    if res is None:
        return False, {}, reason
    item = res[0] if res else {}
    return True, {"backlinks": _num(item.get("backlinks"))}, "ok"


GROUP_FETCH = {
    "moz": moz_fetch, "sr_phrase": semrush_phrase, "sr_bl": semrush_backlinks,
    "ahrefs_dr": ahrefs_authority, "ahrefs_bl": ahrefs_backlinks,
    "dfs_vol": dfs_volume, "dfs_diff": dfs_difficulty, "dfs_bl": dfs_backlinks,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", default="")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--metrics", default="domain_authority,backlinks,volume,difficulty")
    ap.add_argument("--budgets", default="")
    ap.add_argument("--max-requests", type=int, default=1000, dest="max_requests")
    a = ap.parse_args()

    for k in ("AHREFS_API_TOKEN", "SEMRUSH_API_KEY", "SEMRUSH_DB", "MOZ_ACCESS_ID", "MOZ_SECRET_KEY",
              "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"):
        if os.environ.get(k):
            ENV[k] = os.environ[k]

    configured_vendors = [v for v in VENDORS if configured(v)]
    if not configured_vendors:
        fail("AUTH_NO_VENDOR_CONFIGURED",
             "Set at least one of AHREFS_API_TOKEN, SEMRUSH_API_KEY, MOZ_ACCESS_ID+MOZ_SECRET_KEY, "
             "or DATAFORSEO_LOGIN+DATAFORSEO_PASSWORD.")

    domains = [d.strip() for d in a.domains.split(",") if d.strip()]
    keywords = [k.strip() for k in a.keywords.split(",") if k.strip()]
    metrics = [m.strip() for m in a.metrics.split(",") if m.strip() in ROUTES]
    if not metrics:
        fail("NO_VALID_METRICS", "Requested metrics must be a subset of %s." % list(ROUTES))

    budgets = {v: DEFAULT_BUDGET for v in VENDORS}
    if a.budgets:
        try:
            for v, b in json.loads(a.budgets).items():
                if v in budgets:
                    budgets[v] = int(b)
        except (ValueError, TypeError):
            fail("BAD_BUDGETS", "--budgets must be a JSON object of vendor->integer.")

    spent = {v: 0 for v in VENDORS}
    requests_made = {v: 0 for v in VENDORS}
    vendor_status = {v: ("ready" if v in configured_vendors else "unconfigured") for v in VENDORS}
    group_cache = {}   # (group_id, target) -> (ok, {metric:value}, reason)
    total_calls = 0
    coalesced = 0
    notes = []

    def run_group(vendor, group_id, target):
        nonlocal total_calls, coalesced
        ckey = (group_id, target)
        if ckey in group_cache:
            coalesced += 1              # deduped/coalesced hit: no network call, no extra credits
            return group_cache[ckey]
        cost = COST.get((vendor, group_id), 1)
        if spent[vendor] + cost > budgets[vendor]:
            vendor_status[vendor] = "exhausted"
            return (False, {}, "budget_exhausted")
        if total_calls >= a.max_requests:
            return (False, {}, "max_requests_reached")
        ok, values, reason = GROUP_FETCH[group_id](target)
        spent[vendor] += cost
        requests_made[vendor] += 1
        total_calls += 1
        if reason == "rate_limited" or reason == "http_429":
            vendor_status[vendor] = "rate_limited"
        elif ok and vendor_status[vendor] == "ready":
            vendor_status[vendor] = "ok"
        group_cache[ckey] = (ok, values, reason)
        time.sleep(0.2)  # pace across vendors
        return group_cache[ckey]

    rows = []
    unavailable = 0
    for metric in metrics:
        target_type, route = ROUTES[metric]
        targets = domains if target_type == "domain" else keywords
        if not targets:
            notes.append("metric '%s' skipped: no %s targets supplied" % (metric, target_type))
            continue
        for target in targets:
            values, reasons = [], []
            for vendor, group_id in route:
                if vendor not in configured_vendors:
                    reasons.append({"vendor": vendor, "reason": "unconfigured"})
                    continue
                ok, vals, reason = run_group(vendor, group_id, target)
                if ok and vals.get(metric) is not None:
                    values.append({"vendor": vendor, "value": vals[metric]})
                else:
                    reasons.append({"vendor": vendor, "reason": reason if not ok else "no_value"})
            row = {"metric": metric, "target": target, "target_type": target_type,
                   "values": values, "sources": [v["vendor"] for v in values]}
            if values:
                nums = [v["value"] for v in values]
                row["consensus"] = round(sum(nums) / len(nums), 2)
                row["source_count"] = len(values)
                row["status"] = "ok"
            else:
                row["status"] = "unavailable"
                row["reasons"] = reasons
                unavailable += 1
            rows.append(row)

    spend_report = []
    for v in VENDORS:
        spend_report.append({"vendor": v, "configured": v in configured_vendors,
                             "requests_made": requests_made[v], "credits_spent": spent[v],
                             "budget": budgets[v], "remaining": budgets[v] - spent[v],
                             "status": vendor_status[v]})
    degraded = unavailable > 0 or any(s["status"] in ("exhausted", "rate_limited") for s in spend_report) \
        or any(v not in configured_vendors for v in VENDORS)

    result = {
        "status": "partial" if degraded else "ok",
        "degraded": degraded,
        "requested_metrics": metrics,
        "targets": {"domains": domains, "keywords": keywords},
        "rows": rows,
        "unavailable_rows": unavailable,
        "spend_report": spend_report,
        "dedup_calls_saved": coalesced,
        "notes": notes,
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
