#!/usr/bin/env python3
"""Faceted Navigation Index Planner — reference implementation.

Enumerates facet/filter URL combinations from a crawl export, joins each to
demand, Googlebot crawl frequency (from access logs), index status, and
optional GSC clicks, models crawl-budget waste, and emits a per-facet directive
(index / canonicalize / noindex / robots-disallow) with implementation rules.

Auth:   keyless for the crawl + logs. Optional: GSC_ACCESS_TOKEN with --site.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 faceted_index_planner.py --crawl crawl.csv --facet-params color,size,brand,sort,page \
      [--logs access.log] [--demand demand.json] [--site sc-domain:example.com]
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict

GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
# Params that are pure crawl noise regardless of demand: never index, disallow to save budget.
DEFAULT_NOISE = ["sort", "order", "sortby", "session", "sessionid", "sid",
                 "utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid", "ref"]
# Params that are pagination: canonicalize to the paginated series, keep crawlable.
DEFAULT_PAGINATION = ["page", "p", "offset", "start"]
LOG_RE = re.compile(r'"(?:GET|HEAD)\s+(?P<path>\S+)\s+HTTP/[0-9.]+"\s+(?P<code>\d{3})')
BOT_RE = re.compile(r"googlebot", re.I)
MAX_BACKOFF = 5


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm_url(u):
    return (u or "").strip()


def params_of(url):
    try:
        q = urllib.parse.urlsplit(url).query
    except ValueError:
        return {}
    return urllib.parse.parse_qs(q, keep_blank_values=False)


def path_of(url):
    parts = urllib.parse.urlsplit(url)
    return parts.path or "/"


def find_col(header, *needles):
    for i, h in enumerate(header):
        hl = h.strip().lower()
        if all(n in hl for n in needles):
            return i
    return -1


def parse_crawl(path, max_urls):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            fail("BAD_CRAWL", "Crawl export is empty.")
        ci_url = find_col(header, "address")
        if ci_url < 0:
            ci_url = find_col(header, "url")
        if ci_url < 0:
            ci_url = 0
        ci_status = find_col(header, "status", "code")
        ci_index = find_col(header, "indexability")
        ci_canon = find_col(header, "canonical")
        rows = []
        for row in reader:
            if not row or len(row) <= ci_url:
                continue
            url = norm_url(row[ci_url])
            if not url:
                continue
            rows.append({
                "url": url,
                "status_code": row[ci_status].strip() if 0 <= ci_status < len(row) else "",
                "indexability": row[ci_index].strip().lower() if 0 <= ci_index < len(row) else "",
                "canonical": row[ci_canon].strip() if 0 <= ci_canon < len(row) else "",
            })
            if len(rows) >= max_urls:
                break
    return rows


def parse_logs(path, max_lines):
    """Return {path_only: googlebot_hits} plus total bot hits."""
    hits = defaultdict(int)
    total = 0
    n = 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            n += 1
            if n > max_lines:
                break
            if not BOT_RE.search(line):
                continue
            m = LOG_RE.search(line)
            if not m:
                continue
            reqpath = m.group("path")
            # Full request-target may be a path or absolute URL; normalize to path+query.
            if reqpath.startswith("http"):
                sp = urllib.parse.urlsplit(reqpath)
                reqpath = sp.path + (("?" + sp.query) if sp.query else "")
            hits[reqpath] += 1
            total += 1
    return hits, total


def gsc_clicks_by_page(site, start, end):
    token = os.environ.get("GSC_ACCESS_TOKEN")
    if not token:
        fail("AUTH_MISSING_GSC", "--site set but GSC_ACCESS_TOKEN is unset.")
    url = GSC_ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    body = json.dumps({"startDate": start, "endDate": end, "dimensions": ["page"],
                       "rowLimit": 25000, "dataState": "final"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    for attempt in range(MAX_BACKOFF + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.loads(r.read()).get("rows", [])
                return {row["keys"][0]: row.get("clicks", 0) for row in rows}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail("AUTH_GSC_FORBIDDEN", "GSC token invalid or lacks access to %s." % site)
            if e.code in (429, 500, 503):
                if attempt >= MAX_BACKOFF:
                    return {}
                time.sleep(2 ** attempt)
                continue
            return {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {}
    return {}


def demand_lookup(demand, param, value):
    if not demand:
        return None
    for key in ("%s=%s" % (param, value), value, value.lower()):
        if key in demand:
            return demand[key]
    return None


def hits_for(url, log_hits):
    if log_hits is None:
        return None
    sp = urllib.parse.urlsplit(url)
    key = sp.path + (("?" + sp.query) if sp.query else "")
    return log_hits.get(key, log_hits.get(sp.path, 0))


def decide_single(param, max_demand, url_count, bot_hits, clicks, args, noise, pagination):
    """Directive + implementation rule for a single-select facet param."""
    if param in noise:
        return ("robots-disallow", "crawl_noise_param",
                "Disallow: /*?*%s= in robots.txt; strip from internal links." % param)
    if param in pagination:
        return ("canonicalize", "pagination_series",
                "rel-canonical each page to page 1 or use a self-canonical view-all; keep crawlable, exclude from sitemap.")
    if max_demand is None:
        # No demand data or genuinely no searches: do not index, preserve link equity.
        reason = "no_demand_data" if not args.have_demand else "no_measured_demand"
        return ("noindex", reason,
                'meta robots "noindex,follow"; exclude from sitemap; keep crawlable so equity flows.')
    if max_demand >= args.min_demand:
        return ("index", "validated_demand",
                "self-referencing rel-canonical; allow in robots; include in sitemap.")
    return ("canonicalize", "low_demand_overlaps_parent",
            "rel-canonical to the parent category; exclude from sitemap; keep crawlable.")


def decide_combo(param_count, sig_demand, bot_hits, args):
    if param_count >= args.combo_explosion_cap:
        return ("robots-disallow", "combinatorial_explosion",
                "Disallow multi-facet combinations in robots.txt; this is a crawl trap.")
    if sig_demand is not None and sig_demand >= args.min_demand:
        return ("index", "curated_high_demand_combo",
                "curate a static landing URL; self-canonical; include in sitemap.")
    return ("canonicalize", "combo_no_measured_demand",
            "rel-canonical to the highest-demand single facet parent; exclude from sitemap.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True, help="Screaming Frog / crawler CSV export.")
    ap.add_argument("--facet-params", required=True, dest="facet_params",
                    help="Comma list of query params that are facets.")
    ap.add_argument("--logs", default=None, help="Server access log (Combined Log Format).")
    ap.add_argument("--demand", default=None, help="JSON map of param=value or value -> monthly volume.")
    ap.add_argument("--site", default=None, help="GSC property for clicks join.")
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--min-demand", type=int, default=50, dest="min_demand")
    ap.add_argument("--combo-explosion-cap", type=int, default=3, dest="combo_explosion_cap")
    ap.add_argument("--noise-params", default="", dest="noise_params")
    ap.add_argument("--max-urls", type=int, default=200000, dest="max_urls")
    ap.add_argument("--max-log-lines", type=int, default=3000000, dest="max_log_lines")
    args = ap.parse_args()

    facet_set = [p.strip().lower() for p in args.facet_params.split(",") if p.strip()]
    if not facet_set:
        fail("BAD_INPUT", "--facet-params must list at least one param.")
    noise = set(DEFAULT_NOISE) | {p.strip().lower() for p in args.noise_params.split(",") if p.strip()}
    pagination = set(DEFAULT_PAGINATION)

    rows = parse_crawl(args.crawl, args.max_urls)
    demand = {}
    if args.demand:
        demand = json.load(open(args.demand))
        demand = {str(k).lower(): v for k, v in demand.items()}
    args.have_demand = bool(demand)

    log_hits, total_bot_hits = (None, 0)
    if args.logs:
        log_hits, total_bot_hits = parse_logs(args.logs, args.max_log_lines)
    clicks_by_page = gsc_clicks_by_page(args.site, args.start, args.end) if args.site else {}

    # Aggregate per single facet param and per multi-facet signature.
    param_values = defaultdict(set)
    param_urls = defaultdict(int)
    param_hits = defaultdict(int)
    param_clicks = defaultdict(float)
    sig_urls = defaultdict(int)
    sig_hits = defaultdict(int)
    sig_params = {}
    faceted_url_count = 0
    waste_hits = 0

    for r in rows:
        params = params_of(r["url"])
        present = [p for p in params if p in facet_set]
        present_noise = [p for p in params if p in noise]
        signature_keys = sorted(set(present) | set(present_noise))
        if not signature_keys:
            continue
        faceted_url_count += 1
        bot = hits_for(r["url"], log_hits)
        clk = clicks_by_page.get(r["url"], 0)
        # single-param aggregation (facet params only)
        for p in present:
            param_urls[p] += 1
            for v in params[p]:
                param_values[p].add(v)
            if bot is not None:
                param_hits[p] += bot
            param_clicks[p] += clk
        # signature aggregation
        sig = tuple(signature_keys)
        sig_urls[sig] += 1
        sig_params[sig] = signature_keys
        if bot is not None:
            sig_hits[sig] += bot
        # crawl-budget waste: bot hit on a facet URL with no demand-bearing param and no clicks
        if bot:
            any_demand = any(demand_lookup(demand, p, v) is not None and demand_lookup(demand, p, v) >= args.min_demand
                             for p in present for v in params[p])
            if not any_demand and not clk:
                waste_hits += bot

    # Build single-facet directives.
    facet_directives = []
    for p in sorted(param_values):
        demands = [demand_lookup(demand, p, v) for v in param_values[p]]
        demands = [d for d in demands if d is not None]
        max_demand = max(demands) if demands else None
        directive, reason, rule = decide_single(
            p, max_demand, param_urls[p], param_hits.get(p), param_clicks.get(p, 0),
            args, noise, pagination)
        facet_directives.append({
            "facet_param": p,
            "distinct_values": len(param_values[p]),
            "url_count": param_urls[p],
            "googlebot_hits": param_hits.get(p) if log_hits is not None else None,
            "gsc_clicks": round(param_clicks.get(p, 0), 1) if args.site else None,
            "max_value_demand": max_demand,
            "directive": directive,
            "reason": reason,
            "implementation": rule,
            "is_noise": p in noise,
        })

    # Build multi-facet combination directives (2+ params).
    combo_directives = []
    for sig, keys in sorted(sig_params.items(), key=lambda kv: -sig_hits.get(kv[0], 0)):
        if len(keys) < 2:
            continue
        sig_key = "&".join(keys)
        sig_demand = demand.get(sig_key.lower()) if demand else None
        directive, reason, rule = decide_combo(len(keys), sig_demand, sig_hits.get(sig), args)
        combo_directives.append({
            "facet_combination": keys,
            "param_count": len(keys),
            "url_count": sig_urls[sig],
            "googlebot_hits": sig_hits.get(sig) if log_hits is not None else None,
            "combination_demand": sig_demand,
            "directive": directive,
            "reason": reason,
            "implementation": rule,
        })

    # Projected crawl savings = bot hits on everything we disallow.
    disallowed_hits = 0
    if log_hits is not None:
        for d in facet_directives:
            if d["directive"] == "robots-disallow" and d["googlebot_hits"]:
                disallowed_hits += d["googlebot_hits"]
        for d in combo_directives:
            if d["directive"] == "robots-disallow" and d["googlebot_hits"]:
                disallowed_hits += d["googlebot_hits"]

    facet_directives.sort(key=lambda d: (d["directive"] != "index", -d["url_count"]))
    result = {
        "status": "ok",
        "urls_in_crawl": len(rows),
        "faceted_urls": faceted_url_count,
        "crawl_data": log_hits is not None,
        "demand_data": args.have_demand,
        "clicks_source": "gsc" if args.site else "none",
        "total_googlebot_hits": total_bot_hits if log_hits is not None else None,
        "crawl_budget": {
            "wasted_bot_hits_no_demand_no_clicks": waste_hits if log_hits is not None else None,
            "wasted_share": (round(waste_hits / total_bot_hits, 4) if (log_hits is not None and total_bot_hits) else None),
            "projected_savings_bot_hits_from_disallow": disallowed_hits if log_hits is not None else None,
        },
        "facet_directives": facet_directives,
        "combination_directives": combo_directives,
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
