#!/usr/bin/env python3
"""Index Bloat Pruning Auditor — reference implementation.

Scores every indexed URL on search value (GSC clicks/impressions/position),
engagement (GA4), internal links (crawl), and crawl cost (Googlebot log hits),
isolates zero-value and redundant pages inflating the index, and recommends a
per-URL action — keep / consolidate / noindex / remove-410 — with the
consolidation target and the expected crawl-efficiency gain from pruning.

Auth:   GSC value signal is REQUIRED — pass --gsc-file OR --site + GSC_OAUTH_TOKEN.
        GA4 optional via --ga4-file OR GA4_PROPERTY_ID + GA4_OAUTH_TOKEN.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 index_bloat_audit.py --site sc-domain:example.com \
      [--ga4] [--crawl crawl.json] [--logs access.log] \
      [--window-days 90] [--min-impr 10]
"""
from __future__ import annotations
import argparse, json, os, sys, time, re, datetime
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict
from urllib.parse import urlparse, urlunparse

GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
GA4_ENDPOINT = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
ROW_LIMIT = 25000
LOG_RE = re.compile(r'"(?:GET|HEAD|POST)\s+(\S+)\s+HTTP/[0-9.]+"\s+(\d{3})\b.*?"[^"]*"\s+"([^"]*)"')
GOOGLEBOT_RE = re.compile(r"Googlebot|Google-InspectionTool|Storebot-Google", re.I)


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def norm(u):
    if not u:
        return u
    p = urlparse(u.strip())
    return urlunparse(p._replace(fragment="")).rstrip("/")


def path_of(u):
    p = urlparse(u)
    return (p.path or "/") + (("?" + p.query) if p.query else "")


def default_dates(window):
    end = datetime.date.today() - datetime.timedelta(days=3)
    return (end - datetime.timedelta(days=window - 1)).isoformat(), end.isoformat()


def _post_json(url, token, body, timeout, fatal403):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                fail(fatal403[0], fatal403[1])
            if e.code == 400:
                fail("BAD_REQUEST", e.read()[:300].decode("utf-8", "ignore"))
            if e.code in (429, 500, 503):
                if attempt == 5:
                    fail("RATE_LIMITED", "API quota exhausted after retries.")
                time.sleep(min(60, 2 ** attempt))
                continue
            fail("REQUEST_FAILED", "HTTP %s" % e.code)
        except Exception:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            fail("REQUEST_FAILED", "Request to %s failed." % url)
    return {}


def fetch_gsc(site, start, end, timeout):
    token = os.environ.get("GSC_OAUTH_TOKEN")
    if not token:
        fail("AUTH_MISSING_CREDENTIALS",
             "Set GSC_OAUTH_TOKEN (OAuth access token, webmasters.readonly) or pass --gsc-file.")
    url = GSC_ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    out, start_row = {}, 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["page"],
                "dataState": "final", "rowLimit": ROW_LIMIT, "startRow": start_row}
        res = _post_json(url, token, body, timeout,
                         ("AUTH_NO_SITE_ACCESS", "Token has no access to GSC property %s." % site))
        rows = res.get("rows", [])
        for row in rows:
            out[norm(row["keys"][0])] = {"clicks": row.get("clicks", 0),
                                         "impressions": row.get("impressions", 0),
                                         "position": row.get("position", 0)}
        if len(rows) < ROW_LIMIT:
            return out
        start_row += ROW_LIMIT


def fetch_ga4(pid, window, timeout):
    token = os.environ.get("GA4_OAUTH_TOKEN")
    if not token:
        return {}, "no_token"
    url = GA4_ENDPOINT.format(pid=pid)
    out, offset = {}, 0
    while True:
        body = {"dateRanges": [{"startDate": "%ddaysAgo" % window, "endDate": "yesterday"}],
                "dimensions": [{"name": "landingPagePlusQueryString"}],
                "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "conversions"}],
                "limit": 100000, "offset": offset}
        res = _post_json(url, token, body, timeout,
                         ("AUTH_NO_PROPERTY_ACCESS", "Token lacks access to GA4 property %s." % pid))
        rows = res.get("rows", [])
        for row in rows:
            key = row["dimensionValues"][0]["value"]
            m = [mv.get("value", "0") for mv in row.get("metricValues", [])]
            sess = float(m[0]) if len(m) > 0 else 0.0
            eng = float(m[1]) if len(m) > 1 else 0.0
            conv = float(m[2]) if len(m) > 2 else 0.0
            out[key] = {"sessions": sess, "engaged_sessions": eng,
                        "engagement_rate": (eng / sess) if sess else 0.0, "conversions": conv}
        if len(rows) < 100000:
            return out, "api"
        offset += 100000


def parse_logs(path, host):
    """Count verified-by-UA Googlebot hits per normalized path."""
    if not os.path.exists(path):
        fail("INPUT_FILE_MISSING", "Log file not found: %s" % path)
    hits = defaultdict(int)
    with open(path, "r", errors="ignore") as f:
        for line in f:
            m = LOG_RE.search(line)
            if not m:
                continue
            req_path, _status, ua = m.group(1), m.group(2), m.group(3)
            if not GOOGLEBOT_RE.search(ua):
                continue
            if req_path.startswith("http"):
                req_path = path_of(req_path)
            hits[req_path.rstrip("/") or "/"] += 1
    return hits


def norm_title(t):
    return " ".join((t or "").split()).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site")
    ap.add_argument("--gsc-file", dest="gsc_file")
    ap.add_argument("--ga4", action="store_true")
    ap.add_argument("--ga4-file", dest="ga4_file")
    ap.add_argument("--crawl")
    ap.add_argument("--logs")
    ap.add_argument("--window-days", type=int, default=90, dest="window")
    ap.add_argument("--min-impr", type=int, default=10, dest="min_impr")
    ap.add_argument("--stale-days", type=int, default=180, dest="stale_days")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    # --- GSC (required value signal) ---
    if args.gsc_file:
        if not os.path.exists(args.gsc_file):
            fail("INPUT_FILE_MISSING", "GSC file not found: %s" % args.gsc_file)
        raw = json.load(open(args.gsc_file))
        gsc = {norm(k): v for k, v in raw.items()}
    elif args.site:
        start, end = (args.start, args.end) if args.start and args.end else default_dates(args.window)
        gsc = fetch_gsc(args.site, start, end, args.timeout)
    else:
        fail("INPUT_INVALID", "Provide --gsc-file OR --site (with GSC_OAUTH_TOKEN).")

    # --- GA4 (optional engagement signal) ---
    ga4, ga4_source = {}, "off"
    if args.ga4_file:
        if not os.path.exists(args.ga4_file):
            fail("INPUT_FILE_MISSING", "GA4 file not found: %s" % args.ga4_file)
        rawg = json.load(open(args.ga4_file))
        ga4 = {(k if k.startswith("/") else path_of(k)).rstrip("/") or "/": v for k, v in rawg.items()}
        ga4_source = "file"
    elif args.ga4:
        pid = os.environ.get("GA4_PROPERTY_ID")
        if not pid:
            fail("AUTH_MISSING_CREDENTIALS", "Set GA4_PROPERTY_ID (and GA4_OAUTH_TOKEN) or pass --ga4-file.")
        raw_ga4, ga4_source = fetch_ga4(pid, args.window, args.timeout)
        ga4 = {(k.rstrip("/") or "/"): v for k, v in raw_ga4.items()}

    # --- crawl (optional inlinks/title/status) ---
    crawl = {}
    if args.crawl:
        if not os.path.exists(args.crawl):
            fail("INPUT_FILE_MISSING", "Crawl file not found: %s" % args.crawl)
        rc = json.load(open(args.crawl))
        if isinstance(rc, list):
            for r in rc:
                if isinstance(r, dict) and r.get("url"):
                    crawl[norm(r["url"])] = r
        elif isinstance(rc, dict):
            crawl = {norm(k): v for k, v in rc.items()}

    # --- logs (optional crawl-cost signal) ---
    log_hits, host = {}, (urlparse(next(iter(gsc), "https://x")).netloc if gsc else "")
    if args.logs:
        log_hits = parse_logs(args.logs, host)
    has_logs = bool(log_hits)

    urls = list(gsc.keys())
    # duplicate-title clusters for consolidation targets
    title_groups = defaultdict(list)
    for u in urls:
        t = norm_title((crawl.get(u) or {}).get("title"))
        if t:
            title_groups[t].append(u)

    def clicks_of(u):
        return gsc.get(u, {}).get("clicks", 0)

    dup_primary = {}
    clusters = []
    for t, members in title_groups.items():
        if len(members) < 2:
            continue
        primary = max(members, key=lambda u: (clicks_of(u), gsc.get(u, {}).get("impressions", 0)))
        for u in members:
            if u != primary:
                dup_primary[u] = primary
        clusters.append({"title": t[:120], "urls": members, "primary": primary, "size": len(members)})

    # normalization maxima
    def gv(d, k):
        return float(d.get(k, 0) or 0)
    max_clicks = max((gv(v, "clicks") for v in gsc.values()), default=0) or 1
    max_impr = max((gv(v, "impressions") for v in gsc.values()), default=0) or 1
    max_conv = max((gv(ga4.get(path_of(u).rstrip("/") or "/", {}), "conversions") for u in urls), default=0) or 1
    max_inl = max((float((crawl.get(u) or {}).get("internal_links", 0) or 0) for u in urls), default=0) or 1

    records, action_counts = [], defaultdict(int)
    total_cost, pruned_cost = 0.0, 0.0
    for u in urls:
        g = gsc.get(u, {})
        clicks, impr, pos = gv(g, "clicks"), gv(g, "impressions"), gv(g, "position")
        gp = ga4.get(path_of(u).rstrip("/") or "/", {})
        sessions, eng_rate, conv = gv(gp, "sessions"), gv(gp, "engagement_rate"), gv(gp, "conversions")
        cr = crawl.get(u) or {}
        inlinks = float(cr.get("internal_links", 0) or 0)
        status = cr.get("status")
        cost = float(log_hits.get(path_of(u).rstrip("/") or "/", 0)) if has_logs else 1.0
        total_cost += cost

        # value_score (weights renormalised to available signals)
        parts = [(0.40, clicks / max_clicks), (0.15, impr / max_impr), (0.10, inlinks / max_inl)]
        if ga4_source != "off":
            parts += [(0.10, min(eng_rate, 1.0)), (0.25, conv / max_conv)]
        wsum = sum(w for w, _ in parts)
        value = sum(w * x for w, x in parts) / wsum if wsum else 0.0

        reasons = []
        target = None
        if isinstance(status, int) and status in (404, 410):
            action = "already_gone"
            reasons.append("crawl status %s" % status)
        elif u in dup_primary and clicks <= max(1.0, clicks_of(dup_primary[u]) * 0.2):
            action = "consolidate"
            target = dup_primary[u]
            reasons.append("duplicate title of higher-value URL")
        elif clicks == 0 and impr == 0:
            if inlinks <= 1 and cost > (1.0 if not has_logs else 0):
                action = "remove_410"
                reasons.append("zero clicks+impressions, orphaned, still crawled")
            else:
                action = "noindex"
                reasons.append("zero search value but internally linked")
        elif clicks == 0 and impr < args.min_impr:
            action = "noindex"
            reasons.append("negligible impressions, no clicks")
        elif clicks == 0 and impr >= args.min_impr:
            action = "keep"
            reasons.append("has impressions but zero CTR: improve title/meta")
        else:
            action = "keep"
            reasons.append("earns clicks")

        if action in ("remove_410", "noindex", "consolidate"):
            pruned_cost += cost
        bloat = round(cost * (1.0 - value), 3)
        action_counts[action] += 1
        records.append({
            "url": u, "clicks": round(clicks, 1), "impressions": round(impr, 1),
            "position": round(pos, 1), "sessions": round(sessions, 1),
            "engagement_rate": round(eng_rate, 3), "conversions": round(conv, 1),
            "internal_links": int(inlinks), "crawl_cost": round(cost, 1),
            "value_score": round(value, 4), "bloat_score": bloat,
            "action": action, "consolidation_target": target, "reasons": reasons,
        })

    records.sort(key=lambda r: r["bloat_score"], reverse=True)
    prunable = sum(action_counts[a] for a in ("remove_410", "noindex", "consolidate"))
    summary = {
        "indexed_urls": len(urls),
        "prunable_urls": prunable,
        "bloat_ratio": round(prunable / len(urls), 3) if urls else 0,
        "projected_crawl_savings_pct": round(100 * pruned_cost / total_cost, 1) if total_cost else 0,
        "action_distribution": dict(action_counts),
        "crawl_cost_source": "logs" if has_logs else "uniform_proxy",
        "ga4_source": ga4_source,
        "duplicate_clusters": len(clusters),
    }
    json.dump({"status": "ok", "summary": summary, "consolidation_clusters": clusters[:200],
               "results": records}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
