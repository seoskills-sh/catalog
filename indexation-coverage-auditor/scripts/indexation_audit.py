#!/usr/bin/env python3
"""Indexation Coverage Auditor — reference implementation.

Builds a single URL ledger by joining four sources — the XML sitemap set, GSC
index-coverage / URL-Inspection states, a crawl, and analytics traffic — then
classifies every URL (indexed-earning, submitted-not-indexed, discovered-not-
indexed, excluded-crawled, orphaned-earning, ...) and groups the gaps by root
cause with the most likely fix per cluster.

Auth:   URL Inspection API needs GSC_OAUTH_TOKEN (webmasters). Optional; a
        --coverage export file works with no credentials.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 indexation_audit.py --sitemap https://example.com/sitemap.xml \
      --site sc-domain:example.com [--inspect --max-inspect 200] \
      [--coverage coverage.json] [--crawl crawl.json] [--traffic traffic.json]
"""
from __future__ import annotations
import argparse, gzip, json, os, sys, time, re
import urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

UA = "seoskills-indexation-auditor/1.0"
INSPECT_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

CAUSE_FIX = {
    "submitted_not_indexed": "In the sitemap but not indexed: raise content depth/uniqueness and internal links, or drop non-priority URLs from the sitemap.",
    "discovered_not_indexed": "Discovered, never crawled (crawl-budget starvation): strengthen internal links to these URLs and prune low-value URLs competing for budget.",
    "crawled_not_indexed": "Fetched but judged low value: consolidate thin/near-duplicate pages or strengthen E-E-A-T and uniqueness.",
    "excluded_noindex": "Excluded by a noindex directive while still in the sitemap: remove the noindex if it should rank, else remove it from the sitemap.",
    "excluded_canonical": "Google chose a different canonical: align canonical tag + internal links to the intended URL, or accept the consolidation.",
    "excluded_redirect": "Sitemap URL redirects: sitemaps should list only final 200 canonical URLs; replace it with the destination.",
    "orphaned_earning": "Earns traffic but has no internal links and is absent from the sitemap: add internal links from relevant hubs and add it to the sitemap.",
    "crawl_error_in_sitemap": "Sitemap lists a URL returning 4xx/5xx: fix the page or remove the entry.",
    "not_in_sitemap_indexable": "Indexable and reachable but missing from the sitemap: add it so Google can prioritise recrawls.",
    "coverage_unknown": "No coverage signal available for these URLs: run with --inspect or supply a --coverage export to classify them.",
}


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def norm(u):
    if not u:
        return u
    p = urlparse(u.strip())
    return urlunparse(p._replace(fragment="")).rstrip("/")


def _read_bytes(src, timeout):
    if os.path.exists(src):
        return open(src, "rb").read()
    req = urllib.request.Request(src, headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(20 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            fail("SITEMAP_FETCH_FAILED", "HTTP %s fetching %s" % (e.code, src))
        except Exception:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            fail("SITEMAP_FETCH_FAILED", "Could not fetch %s" % src)
    return b""


def _localname(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(src, timeout, max_sitemaps, max_urls, _seen=None, _urls=None):
    if _seen is None:
        _seen, _urls = set(), []
    if src in _seen or len(_seen) >= max_sitemaps or len(_urls) >= max_urls:
        return _urls
    _seen.add(src)
    raw = _read_bytes(src, timeout)
    if raw[:2] == b"\x1f\x8b" or src.endswith(".gz"):
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return _urls
    tag = _localname(root.tag)
    if tag == "sitemapindex":
        for sm in root:
            for child in sm:
                if _localname(child.tag) == "loc" and child.text:
                    parse_sitemap(child.text.strip(), timeout, max_sitemaps, max_urls, _seen, _urls)
                    if len(_urls) >= max_urls:
                        return _urls
    else:  # urlset
        for url_el in root:
            for child in url_el:
                if _localname(child.tag) == "loc" and child.text:
                    _urls.append(norm(child.text))
                    if len(_urls) >= max_urls:
                        return _urls
    return _urls


class CrawlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.meta_robots = ""

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        elif tag == "meta" and d.get("name", "").lower() == "robots":
            self.meta_robots = d.get("content", "").lower()


def live_crawl(root_url, max_crawl, max_depth, timeout):
    host = urlparse(root_url).netloc
    seen, out, q = set(), {}, deque([(root_url, 0)])
    inlink_counter = defaultdict(int)
    while q and len(seen) < max_crawl:
        url, depth = q.popleft()
        n = norm(url)
        if n in seen or depth > max_depth:
            continue
        seen.add(n)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        status, html = 0, ""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    status = r.status
                    if "text/html" in r.headers.get("Content-Type", ""):
                        html = r.read(400000).decode("utf-8", "ignore")
                break
            except urllib.error.HTTPError as e:
                status = e.code
                if e.code in (429, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception:
                status = 0
                break
        p = CrawlParser()
        if html:
            try:
                p.feed(html)
            except Exception:
                pass
        out[n] = {"status": status, "indexable": "noindex" not in p.meta_robots and 200 <= status < 300}
        for href in p.links:
            nxt = urljoin(url, href)
            if urlparse(nxt).netloc == host and nxt.startswith("http"):
                nn = norm(nxt)
                inlink_counter[nn] += 1
                q.append((nxt, depth + 1))
        time.sleep(0.15)
    for u, rec in out.items():
        rec["internal_links"] = inlink_counter.get(u, 0)
    return out


def inspect_urls(site, urls, max_inspect, timeout):
    token = os.environ.get("GSC_OAUTH_TOKEN")
    if not token:
        fail("AUTH_MISSING_CREDENTIALS",
             "Set GSC_OAUTH_TOKEN (OAuth access token, webmasters.readonly) or pass --coverage.")
    out = {}
    for u in urls[:max_inspect]:
        body = json.dumps({"inspectionUrl": u, "siteUrl": site}).encode()
        req = urllib.request.Request(INSPECT_ENDPOINT, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    res = json.loads(r.read()).get("inspectionResult", {}).get("indexStatusResult", {})
                    out[u] = {"verdict": res.get("verdict", ""),
                              "coverageState": res.get("coverageState", ""),
                              "robotsTxtState": res.get("robotsTxtState", ""),
                              "indexingState": res.get("indexingState", "")}
                break
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    fail("AUTH_NO_SITE_ACCESS", "Token has no access to GSC property %s." % site)
                if e.code == 429:
                    if attempt == 5:
                        # quota is a hard daily cap; return what we have.
                        return out, True
                    time.sleep(2 ** attempt)
                    continue
                if e.code in (500, 503) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                break
        time.sleep(0.3)
    return out, False


def coverage_class(cov):
    """Map a coverageState / verdict blob to (in_index, cause_or_None)."""
    state = (cov.get("coverageState") or "").lower()
    verdict = (cov.get("verdict") or "").upper()
    if verdict == "PASS" or "submitted and indexed" in state or state.startswith("indexed"):
        return True, None
    if "discovered" in state:
        return False, "discovered_not_indexed"
    if "crawled" in state and "not indexed" in state:
        return False, "crawled_not_indexed"
    if "noindex" in state:
        return False, "excluded_noindex"
    if "canonical" in state or "duplicate" in state:
        return False, "excluded_canonical"
    if "redirect" in state:
        return False, "excluded_redirect"
    if "not found" in state or "404" in state or "soft 404" in state:
        return False, "crawl_error_in_sitemap"
    if state:
        return False, "submitted_not_indexed"
    return None, "coverage_unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sitemap", required=True)
    ap.add_argument("--site")
    ap.add_argument("--coverage")
    ap.add_argument("--crawl")
    ap.add_argument("--traffic")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--live-crawl", action="store_true", dest="live_crawl")
    ap.add_argument("--max-inspect", type=int, default=200, dest="max_inspect")
    ap.add_argument("--max-crawl", type=int, default=2000, dest="max_crawl")
    ap.add_argument("--max-depth", type=int, default=6, dest="max_depth")
    ap.add_argument("--max-sitemaps", type=int, default=100, dest="max_sitemaps")
    ap.add_argument("--max-urls", type=int, default=100000, dest="max_urls")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    sitemap_urls = set(parse_sitemap(args.sitemap, args.timeout, args.max_sitemaps, args.max_urls))
    if not sitemap_urls:
        fail("EMPTY_SITEMAP", "No <loc> URLs parsed from %s." % args.sitemap)

    def load(path, kind):
        if not path:
            return {}
        if not os.path.exists(path):
            fail("INPUT_FILE_MISSING", "%s file not found: %s" % (kind, path))
        return json.load(open(path))

    coverage = {norm(k): v for k, v in (load(args.coverage, "coverage") or {}).items()}
    crawl = {}
    raw_crawl = load(args.crawl, "crawl")
    if isinstance(raw_crawl, dict):
        crawl = {norm(k): v for k, v in raw_crawl.items()}
    elif isinstance(raw_crawl, list):
        for r in raw_crawl:
            if isinstance(r, dict) and r.get("url"):
                crawl[norm(r["url"])] = r
    traffic = {norm(k): float(v) for k, v in (load(args.traffic, "traffic") or {}).items()}

    inspect_capped = False
    if args.inspect:
        if not args.site:
            fail("INPUT_INVALID", "--inspect requires --site (the GSC property).")
        to_inspect = sorted(sitemap_urls)
        got, inspect_capped = inspect_urls(args.site, to_inspect, args.max_inspect, args.timeout)
        for k, v in got.items():
            coverage.setdefault(norm(k), v)

    if args.live_crawl and not crawl:
        if not args.site:
            fail("INPUT_INVALID", "--live-crawl requires --site as the crawl root.")
        root = args.site if args.site.startswith("http") else "https://" + args.site.replace("sc-domain:", "")
        crawl = live_crawl(root, args.max_crawl, args.max_depth, args.timeout)

    coverage_source = ("inspection" if args.inspect else "file") if coverage else "none"

    universe = set(sitemap_urls) | set(crawl) | set(traffic) | set(coverage)
    ledger, class_counts, gap_groups = [], defaultdict(int), defaultdict(lambda: {"count": 0, "urls": []})
    for u in sorted(universe):
        in_sitemap = u in sitemap_urls
        cr = crawl.get(u, {})
        cr_status = cr.get("status")
        inlinks = cr.get("internal_links")
        indexable = cr.get("indexable")
        clicks = traffic.get(u, 0.0)
        cov = coverage.get(u)
        in_index, cause = (None, "coverage_unknown")
        if cov is not None:
            in_index, cause = coverage_class(cov)

        # Deterministic classification.
        if in_index is True:
            klass = "indexed_earning" if clicks > 0 else "indexed_no_traffic"
            cause = None
        elif clicks > 0 and not in_sitemap and (inlinks == 0):
            klass, cause = "orphaned_earning", "orphaned_earning"
        elif isinstance(cr_status, int) and cr_status >= 400 and in_sitemap:
            klass, cause = "crawl_error", "crawl_error_in_sitemap"
        elif cov is not None and in_index is False:
            klass = cause  # discovered/crawled/excluded/... class name mirrors cause
            if in_sitemap and cause in ("discovered_not_indexed", "crawled_not_indexed", "submitted_not_indexed"):
                pass
        elif cov is None and in_sitemap and indexable is False:
            klass, cause = "excluded_crawled", "excluded_noindex"
        elif cov is None and in_sitemap and indexable is True and clicks == 0:
            klass, cause = "submitted_status_unknown", "coverage_unknown"
        elif not in_sitemap and indexable is True:
            klass, cause = "not_in_sitemap_indexable", "not_in_sitemap_indexable"
        else:
            klass, cause = "coverage_unknown", "coverage_unknown"

        class_counts[klass] += 1
        if cause:
            g = gap_groups[cause]
            g["count"] += 1
            if len(g["urls"]) < 25:
                g["urls"].append(u)
        ledger.append({
            "url": u, "in_sitemap": in_sitemap, "crawl_status": cr_status,
            "internal_links": inlinks, "indexable": indexable,
            "in_index": in_index, "coverage_state": (cov or {}).get("coverageState"),
            "clicks": round(clicks, 1), "class": klass, "cause": cause,
        })

    gaps = []
    for cause, g in gap_groups.items():
        gaps.append({"cause": cause, "count": g["count"], "example_urls": g["urls"],
                     "likely_fix": CAUSE_FIX.get(cause, "Review these URLs manually.")})
    gaps.sort(key=lambda x: x["count"], reverse=True)

    status = "ok"
    if coverage_source == "none":
        status = "insufficient"  # ledger built, but index state unknown

    summary = {
        "total_urls": len(universe),
        "sitemap_urls": len(sitemap_urls),
        "crawled_urls": len(crawl),
        "urls_with_traffic": sum(1 for v in traffic.values() if v > 0),
        "coverage_source": coverage_source,
        "coverage_urls_known": len(coverage),
        "inspect_quota_capped": inspect_capped,
        "class_distribution": dict(class_counts),
    }
    json.dump({"status": status, "summary": summary, "gaps": gaps, "ledger": ledger[:5000]},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
